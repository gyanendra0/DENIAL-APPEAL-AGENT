"""CLI tests. The made-up rows are committed and removed afterwards; the files go to a temp folder.

The command reads the database and writes two local files. No model is called and nothing
real is used.
"""

import csv
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from pipelines import review_drafts
from pipelines.review_drafts import main
from src.config.settings import get_settings
from src.db.models import (
    AgentRun,
    EndState,
    EvidenceChunk,
    GeneratedDocument,
    LlmCall,
    StoredAppealDraft,
)
from src.db.session import session_scope
from src.evaluation.review_export import REVIEW_FILE_NAME, SCORES_FILE_NAME
from src.evaluation.review_score import SCORE_COLUMNS
from tests.drafting.helpers import OPENING_TEXT, POLICY_TEXT
from tests.evaluation.review_helpers import (
    CITED_CHUNK_TEXT,
    CITED_SECTION_NUMBER,
    CITED_TITLE,
    HIDDEN_RULE_OUTCOME,
    HIDDEN_WIN_SCORE,
    add_reviewable,
    review_data,
    uncited_letter,
)
from tests.extraction.helpers import CLAIM_1, CLAIM_2, CLAIM_3, LETTER, PATIENT_NAME, document_text

HEADLINE = "drafts that can be reviewed (seed 42, agent v1, drafting prompt v1)"
HIDDEN_NOTE = "a made-up note nobody may print"


@pytest.fixture
def factory(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    """Point the command at the test database, with three drafts that can be reviewed.

    Claim 1 and claim 3 cite the made-up evidence; claim 2 makes no policy statement.
    """
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    with review_data(engine) as session_factory:
        with session_scope(session_factory) as session:
            add_reviewable(session, CLAIM_1)
            add_reviewable(session, CLAIM_2, uncited_letter())
            add_reviewable(session, CLAIM_3)
        yield session_factory
    get_settings.cache_clear()


def _export(folder: Path, *extra: str) -> int:
    return main(["export", "--out-dir", str(folder), "--size", "3", *extra])


def _rows(folder: Path) -> list[dict[str, str]]:
    with (folder / SCORES_FILE_NAME).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _fill_in(folder: Path, verdicts: dict[str, tuple[str, str]], note: str = "") -> Path:
    """Write a (verdict, reject reason) pair into the row of each claim id in `verdicts`."""
    path = folder / SCORES_FILE_NAME
    rows = _rows(folder)
    for row in rows:
        row["verdict"], row["reject_reason"] = verdicts.get(row["claim_id"], ("", ""))
        row["note"] = note
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SCORE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _score(path: Path, *extra: str) -> int:
    return main(["score", str(path), "--size", "3", *extra])


def _row_counts(factory: sessionmaker[Session]) -> dict[str, int]:
    with session_scope(factory) as session:
        return {
            model.__tablename__: session.execute(
                select(func.count()).select_from(model)
            ).scalar_one()
            for model in (AgentRun, StoredAppealDraft, GeneratedDocument, EvidenceChunk, LlmCall)
        }


def test_export_writes_the_file_to_read_and_the_score_file_into_the_given_folder(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "not" / "made" / "yet"

    exit_code = _export(folder)

    assert exit_code == 0
    assert sorted(path.name for path in folder.iterdir()) == [REVIEW_FILE_NAME, SCORES_FILE_NAME]
    assert [row["claim_id"] for row in _rows(folder)] == [CLAIM_1, CLAIM_2, CLAIM_3]
    assert all(
        (row["verdict"], row["reject_reason"], row["note"]) == ("", "", "") for row in _rows(folder)
    )
    text = (folder / REVIEW_FILE_NAME).read_text(encoding="utf-8")
    for shown in (
        OPENING_TEXT,
        POLICY_TEXT,
        document_text(CLAIM_1, LETTER),
        document_text(CLAIM_2, LETTER),
        CITED_CHUNK_TEXT,
        CITED_TITLE,
        f"section {CITED_SECTION_NUMBER}",
    ):
        assert shown in text
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        f"exported 3 of 3 {HEADLINE}",
        "  with at least one citation: 2",
        "  left out, the denial letter changed after the draft: 0",
        f"  file to read: {folder / REVIEW_FILE_NAME}",
        f"  file to fill in: {folder / SCORES_FILE_NAME}",
    ]


def test_the_files_hold_no_win_score_no_rules_verdict_and_no_proxy(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    _export(tmp_path)

    for name in (REVIEW_FILE_NAME, SCORES_FILE_NAME):
        text = (tmp_path / name).read_text(encoding="utf-8")
        for hidden in (str(HIDDEN_WIN_SCORE), HIDDEN_RULE_OUTCOME, "proxy", "appeal_success"):
            assert hidden not in text


def test_the_same_seed_gives_the_same_sample_and_another_seed_another_one(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    for name, seed in (("a", "42"), ("b", "42"), ("c", "1"), ("d", "2"), ("e", "3")):
        assert (
            main(["export", "--out-dir", str(tmp_path / name), "--size", "1", "--seed", seed]) == 0
        )

    def files(name: str) -> tuple[str, str]:
        folder = tmp_path / name
        return (
            (folder / REVIEW_FILE_NAME).read_text(encoding="utf-8"),
            (folder / SCORES_FILE_NAME).read_text(encoding="utf-8"),
        )

    assert files("a") == files("b")
    assert len({files(name)[1] for name in "acde"}) > 1


def test_only_needs_review_drafts_are_exported(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(AgentRun)
            .where(AgentRun.source_claim_id == CLAIM_2)
            .values(
                end_state=EndState.STOPPED_BY_RULES,
                trace={"end_state": "stopped_by_rules", "agent_version": "v1"},
            )
        )

    assert main(["export", "--out-dir", str(tmp_path), "--size", "2"]) == 0

    assert [row["claim_id"] for row in _rows(tmp_path)] == [CLAIM_1, CLAIM_3]
    assert capsys.readouterr().out.startswith("exported 2 of 2 drafts")


def test_a_draft_whose_letter_changed_is_left_out_and_counted(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(
                GeneratedDocument.source_claim_id == CLAIM_3,
                GeneratedDocument.document_type == LETTER,
            )
            .values(text="A newer made-up letter.")
        )

    assert main(["export", "--out-dir", str(tmp_path), "--size", "2"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"exported 2 of 2 {HEADLINE}"
    assert lines[2] == "  left out, the denial letter changed after the draft: 1"


def test_a_size_above_the_number_of_drafts_exits_1_and_writes_nothing(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "review"

    exit_code = main(["export", "--out-dir", str(folder)])

    assert exit_code == 1
    assert not folder.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "cannot export the sample: 30 drafts asked for, only 3 can be sampled\n"
    )


def test_a_cited_chunk_that_is_gone_exits_1_and_writes_nothing(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        for chunk in session.scalars(select(EvidenceChunk)):
            session.delete(chunk)

    exit_code = _export(tmp_path / "review")

    assert exit_code == 1
    assert not (tmp_path / "review").exists()
    captured = capsys.readouterr()
    assert captured.err.startswith("cannot export the sample: 2 citations of the sampled drafts")
    assert "Traceback" not in captured.err


def test_an_existing_score_file_is_never_overwritten(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    filled_in = _fill_in(tmp_path, {CLAIM_1: ("accept", "")}).read_text(encoding="utf-8")
    capsys.readouterr()

    exit_code = _export(tmp_path)

    assert exit_code == 1
    assert (tmp_path / SCORES_FILE_NAME).read_text(encoding="utf-8") == filled_in
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "already exists and is not overwritten" in captured.err


def test_score_prints_the_counts_and_exits_0_at_the_target(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(
        tmp_path,
        {
            CLAIM_1: ("accept", ""),
            CLAIM_2: ("accept_light_edits", ""),
            CLAIM_3: ("reject", "citation_does_not_fit"),
        },
    )
    capsys.readouterr()

    exit_code = _score(path)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "scored 3 drafts (acceptance target 60.00%)",
        "  accept: 1",
        "  accept_light_edits: 1",
        "  reject: 1",
        "    wrong_fact: 0",
        "    no_policy_argument: 0",
        "    citation_does_not_fit: 1",
        "    poor_wording: 0",
        "    other: 0",
        "  acceptance share: 2 of 3 (66.67%), meets the target",
        "  drafts with a citation: 1 of 2 (50.00%)",
        "  drafts without a citation: 1 of 1 (100.00%)",
    ]


def test_score_exits_1_below_the_target_and_still_prints_the_counts(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(
        tmp_path,
        {
            CLAIM_1: ("accept", ""),
            CLAIM_2: ("reject", "no_policy_argument"),
            CLAIM_3: ("reject", "wrong_fact"),
        },
    )
    capsys.readouterr()

    exit_code = _score(path)

    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[9] == "  acceptance share: 1 of 3 (33.33%), below the target"


def test_a_score_file_that_is_not_filled_in_prints_no_share_and_exits_1(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(tmp_path, {CLAIM_1: ("accept", "")})
    capsys.readouterr()

    exit_code = _score(path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "2 of 3 drafts have no verdict yet: fill in the score file, then run this step again."
        " No share is worked out before that\n"
    )


def test_a_bad_row_exits_1_with_a_plain_message_and_no_traceback(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(
        tmp_path,
        {CLAIM_1: ("maybe", ""), CLAIM_2: ("reject", ""), CLAIM_3: ("accept", "other")},
        note=HIDDEN_NOTE,
    )
    capsys.readouterr()

    exit_code = _score(path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "the score file cannot be used:",
        "  line 2: verdict: Input should be 'accept', 'accept_light_edits' or 'reject'",
        "  line 3: a reject needs a reject_reason",
        "  line 4: only a reject has a reject_reason",
    ]
    assert HIDDEN_NOTE not in captured.err


def test_only_the_first_problems_are_printed_and_the_rest_are_counted(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / SCORES_FILE_NAME
    path.write_text("\n".join([",".join(SCORE_COLUMNS), *["x,y"] * 12]) + "\n", encoding="utf-8")

    assert _score(path) == 1

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 12
    assert lines[-1] == "  (and 2 more)"


def test_a_row_for_a_draft_that_was_drafted_again_is_refused(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(
        tmp_path, {CLAIM_1: ("accept", ""), CLAIM_2: ("accept", ""), CLAIM_3: ("accept", "")}
    )
    with session_scope(factory) as session:
        session.execute(
            update(StoredAppealDraft)
            .where(StoredAppealDraft.source_claim_id == CLAIM_2)
            .values(letter=uncited_letter("A made-up letter, drafted again.").model_dump())
        )
    capsys.readouterr()

    exit_code = _score(path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "the score file cannot be used:",
        "  line 3: the stored draft is not the one this row was written for (it was drafted"
        " again): export the sample again",
    ]


def test_a_removed_row_is_refused(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["export", "--out-dir", str(tmp_path), "--size", "2"])
    path = _fill_in(tmp_path, dict.fromkeys((CLAIM_1, CLAIM_2, CLAIM_3), ("accept", "")))
    capsys.readouterr()

    assert _score(path) == 1

    assert "2 rows found, 3 expected" in capsys.readouterr().err


def test_a_row_for_a_claim_that_is_no_longer_needs_review_is_refused(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(tmp_path, dict.fromkeys((CLAIM_1, CLAIM_2, CLAIM_3), ("accept", "")))
    with session_scope(factory) as session:
        session.execute(
            update(AgentRun)
            .where(AgentRun.source_claim_id == CLAIM_1)
            .values(
                end_state=EndState.BELOW_THRESHOLD,
                trace={"end_state": "below_threshold", "agent_version": "v1"},
            )
        )
    capsys.readouterr()

    assert _score(path) == 1

    assert capsys.readouterr().err.splitlines()[1] == (
        "  line 2: no draft that can be reviewed is stored for this claim"
    )


def test_a_stored_draft_that_does_not_fit_the_schema_exits_1_with_a_plain_message(
    factory: sessionmaker[Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _export(tmp_path)
    path = _fill_in(tmp_path, dict.fromkeys((CLAIM_1, CLAIM_2, CLAIM_3), ("accept", "")))
    with session_scope(factory) as session:
        session.execute(
            update(StoredAppealDraft)
            .where(StoredAppealDraft.source_claim_id == CLAIM_1)
            .values(letter={"paragraphs": []})
        )
    capsys.readouterr()

    assert _score(path) == 1

    assert capsys.readouterr().err == (
        "cannot read the stored drafts: a stored draft of prompt version v1 does not fit the"
        " letter schema\n"
    )


def test_both_steps_write_nothing_to_the_database_and_build_no_gateway(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    before = _row_counts(factory)

    _export(tmp_path)
    _score(_fill_in(tmp_path, dict.fromkeys((CLAIM_1, CLAIM_2, CLAIM_3), ("accept", ""))))

    assert _row_counts(factory) == before
    # The command has no way to call a model: it never imports the gateway.
    assert not hasattr(review_drafts, "build_openai_gateway")
    assert not hasattr(review_drafts, "load_llm_gateway_settings")


def test_the_terminal_shows_no_letter_text_no_document_text_and_no_claim_id(
    factory: sessionmaker[Session],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    _export(tmp_path)
    _score(_fill_in(tmp_path, {CLAIM_1: ("accept", "")}, note=HIDDEN_NOTE))
    _score(
        _fill_in(
            tmp_path,
            {CLAIM_1: ("accept", ""), CLAIM_2: ("reject", "other"), CLAIM_3: ("accept", "")},
            note=HIDDEN_NOTE,
        )
    )

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        for hidden in (
            PATIENT_NAME,
            OPENING_TEXT,
            POLICY_TEXT,
            CITED_CHUNK_TEXT,
            HIDDEN_NOTE,
            CLAIM_1,
            CLAIM_2,
        ):
            assert hidden not in shown


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["export"],
        ["export", "--out-dir", "{folder}", "--size", "0"],
        ["export", "--out-dir", "{folder}", "--size", "many"],
        ["export", "--out-dir", "{folder}", "--seed", "lucky"],
        ["export", "--out-dir", "{file}"],
        ["score"],
        ["score", "{folder}/missing.csv"],
        ["score", "{folder}"],
        ["score", "{file}", "--size", "0"],
        ["review", "{file}"],
    ],
)
def test_bad_arguments_exit_2_and_write_nothing(
    factory: sessionmaker[Session], tmp_path: Path, arguments: list[str]
) -> None:
    folder = tmp_path / "review"
    a_file = tmp_path / "a-file.csv"
    a_file.write_text("made up", encoding="utf-8")
    filled = [argument.format(folder=folder, file=a_file) for argument in arguments]

    with pytest.raises(SystemExit) as raised:
        main(filled)

    assert raised.value.code == 2
    assert not folder.exists()
