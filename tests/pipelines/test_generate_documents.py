"""CLI tests. The command really commits, so each test cleans the tables afterwards.

The claims, lines and labels are put there the way a user would: by running the data pipeline
on the made-up fixture files first.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, select, update

from pipelines import run_data_pipeline
from pipelines.generate_documents import DEFAULT_SEED, main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DocumentType,
    GeneratedDocument,
    IssuerDenialStats,
    PlanDenialStats,
)
from src.synth import documents
from src.synth.clinical_note import ClinicalNoteAnswerKey
from src.synth.denial_letter import DenialLetterAnswerKey
from src.synth.documents import GENERATOR_VERSIONS
from src.synth.identity import MAX_SEED
from src.synth.prior_auth import PriorAuthAnswerKey
from tests.ingest.helpers import FIXTURE, claims_zip

TABLES: tuple[type[Base], ...] = (
    IssuerDenialStats,
    PlanDenialStats,
    ClaimSample,
    ClaimSampleLine,
    ClaimSampleLabel,
    GeneratedDocument,
)
# The claims fixture with one more denied claim, so that it passes the class balance gate of
# the data pipeline (see tests/pipelines/test_run_data_pipeline.py).
BALANCED: dict[tuple[int, str], str] = {
    (2, "LINE_PRCSG_IND_CD_1"): "O",
    (2, "LINE_NCH_PMT_AMT_1"): "0.00",
}
DENIED_CLAIMS = {"800000000000001", "800000000000002", "800000000000005"}
LETTER = DocumentType.DENIAL_LETTER
NOTE = DocumentType.CLINICAL_NOTE
PRIOR_AUTH = DocumentType.PRIOR_AUTH
# A letter and a note per denied claim; only claim 2 is denied as noncovered, so only it gets
# a prior-authorisation record.
DOCUMENTS = {(claim_id, kind) for claim_id in DENIED_CLAIMS for kind in (LETTER, NOTE)} | {
    ("800000000000002", PRIOR_AUTH)
}


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the commands at the test database, and empty the tables before and afterwards."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    _empty_tables(engine)  # in case an earlier run was killed before its clean-up
    yield engine
    get_settings.cache_clear()
    _empty_tables(engine)


@pytest.fixture
def loaded(database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Engine:
    """The test database after the data pipeline ran: six claims, three of them denied."""
    assert (
        run_data_pipeline.main(
            [str(FIXTURE), str(claims_zip(tmp_path, BALANCED)), "--plan-year", "2026"]
        )
        == 0
    )
    capsys.readouterr()  # drop the data pipeline's own output
    return database


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in reversed(TABLES):  # rows that point at others go first
            connection.execute(delete(table))


def _documents(engine: Engine) -> dict[tuple[str, DocumentType], tuple[int, str]]:
    """Stored documents as {(claim id, document type): (seed, text)}."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                GeneratedDocument.source_claim_id,
                GeneratedDocument.document_type,
                GeneratedDocument.seed,
                GeneratedDocument.text,
            )
        )
        return {(claim_id, kind): (seed, text) for claim_id, kind, seed, text in rows}


def test_writes_every_document_type_in_one_run_and_commits(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert set(_documents(loaded)) == DOCUMENTS
    assert capsys.readouterr().out == (
        "generated 7 documents (seed 42)\n"
        "  denial_letter: 3 (generator v1)\n"
        "  clinical_note: 3 (generator v1)\n"
        "  prior_auth: 1 (generator v1)\n"
    )


def test_stores_the_type_seed_generator_version_and_a_readable_answer_key(loaded: Engine) -> None:
    main([])

    with loaded.connect() as connection:
        rows = connection.execute(select(GeneratedDocument.__table__)).all()
    assert {row.seed for row in rows} == {DEFAULT_SEED} == {42}
    for row in rows:
        assert row.generator_version == GENERATOR_VERSIONS[row.document_type]
        if row.document_type is LETTER:
            letter = DenialLetterAnswerKey.model_validate(row.answer_key)
            assert letter.claim_number == row.source_claim_id
            assert letter.claim_number in row.text
        elif row.document_type is NOTE:
            assert ClinicalNoteAnswerKey.model_validate(row.answer_key).patient_name in row.text
        else:
            record = PriorAuthAnswerKey.model_validate(row.answer_key)
            assert record.authorization_number in row.text


def test_a_claim_with_no_prior_auth_record_still_prints_a_zero_count(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    # Claim 2's headline becomes "other", so no claim needs a record any more.
    with loaded.begin() as connection:
        connection.execute(
            update(ClaimSampleLabel)
            .where(ClaimSampleLabel.source_claim_id == "800000000000002")
            .values(denial_reason_category="other")
        )

    exit_code = main([])

    assert exit_code == 0
    assert "  prior_auth: 0 (generator v1)\n" in capsys.readouterr().out
    assert PRIOR_AUTH not in {kind for _, kind in _documents(loaded)}


def test_running_twice_gives_the_identical_documents(loaded: Engine) -> None:
    main([])
    first = _documents(loaded)

    exit_code = main([])

    assert exit_code == 0
    assert _documents(loaded) == first


def test_a_later_run_with_another_seed_replaces_every_document(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    main([])
    first = _documents(loaded)

    exit_code = main(["--seed", "7"])

    second = _documents(loaded)
    assert exit_code == 0
    assert "generated 7 documents (seed 7)" in capsys.readouterr().out
    assert set(second) == DOCUMENTS
    # No document of the first run is left, so the table never mixes two seeds.
    assert {seed for seed, _ in second.values()} == {7}
    assert all(second[document][1] != first[document][1] for document in DOCUMENTS)


def test_a_claim_that_is_no_longer_denied_loses_all_its_documents(loaded: Engine) -> None:
    main([])
    with loaded.begin() as connection:
        connection.execute(
            update(ClaimSampleLabel)
            .where(ClaimSampleLabel.source_claim_id == "800000000000002")
            .values(is_denied=False, denial_reason_category=None, appeal_success_proxy=None)
        )

    exit_code = main([])

    assert exit_code == 0
    assert set(_documents(loaded)) == {
        document for document in DOCUMENTS if document[0] != "800000000000002"
    }


def test_no_labelled_claims_exits_1_and_writes_nothing(
    database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "batch rejected" in captured.err
    assert "no claim is labelled denied: run pipelines.run_data_pipeline first" in captured.err
    assert captured.out == ""
    assert _documents(database) == {}


def test_labels_of_another_rule_version_exit_1_and_keep_the_old_documents(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    main([])
    before = _documents(loaded)
    with loaded.begin() as connection:
        connection.execute(update(ClaimSampleLabel).values(label_rule_version="v0"))

    exit_code = main(["--seed", "7"])

    assert exit_code == 1
    assert "rule version v0, the code is at v1" in capsys.readouterr().err
    assert set(before) == DOCUMENTS
    assert _documents(loaded) == before


def test_denied_claim_without_a_denied_line_exits_1_and_keeps_the_old_documents(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    main([])
    before = _documents(loaded)
    capsys.readouterr()  # drop the first run's own output
    # The claim's lines are loaded again as paid, but its label still says denied.
    with loaded.begin() as connection:
        connection.execute(
            update(ClaimSampleLine)
            .where(ClaimSampleLine.source_claim_id == "800000000000005")
            .values(processing_indicator="A")
        )

    exit_code = main(["--seed", "7"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "claim 800000000000005 is labelled denied but has no denied line" in captured.err
    assert "run pipelines.run_data_pipeline again" in captured.err
    assert captured.out == ""
    assert set(before) == DOCUMENTS
    assert _documents(loaded) == before


def test_claim_labelled_for_a_record_without_a_qualifying_line_exits_1_and_keeps_the_old(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    main([])
    before = _documents(loaded)
    capsys.readouterr()  # drop the first run's own output
    # Claim 2's denied line is loaded again as a duplicate, but its label still says noncovered.
    with loaded.begin() as connection:
        connection.execute(
            update(ClaimSampleLine)
            .where(ClaimSampleLine.source_claim_id == "800000000000002")
            .where(ClaimSampleLine.processing_indicator == "C")
            .values(processing_indicator="M")
        )

    exit_code = main(["--seed", "7"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "claim 800000000000002 is labelled for a prior-authorisation record but has no line"
        " that qualifies: run pipelines.run_data_pipeline again"
    ) in captured.err
    assert captured.out == ""
    assert set(before) == DOCUMENTS
    assert _documents(loaded) == before


def test_failure_after_the_old_documents_are_removed_brings_them_back(
    loaded: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    main([])
    before = _documents(loaded)

    def fail(*_: object) -> int:
        raise RuntimeError("database went away")

    # `replace_generated_document_rows` removes the old documents, then calls this to store
    # the new ones.
    monkeypatch.setattr(documents, "upsert_rows", fail)

    with pytest.raises(RuntimeError, match="database went away"):
        main(["--seed", "7"])

    assert set(before) == DOCUMENTS
    assert _documents(loaded) == before  # still every type, made with seed 42


@pytest.mark.parametrize("value", ["-1", str(MAX_SEED + 1), "lucky"])
def test_bad_seed_exits_2(capsys: pytest.CaptureFixture[str], value: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--seed", value])

    assert excinfo.value.code == 2
    assert "--seed" in capsys.readouterr().err
