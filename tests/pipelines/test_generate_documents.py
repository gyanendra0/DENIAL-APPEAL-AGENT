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
from src.synth.denial_letter import GENERATOR_VERSION, MAX_SEED, DenialLetterAnswerKey
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


def _documents(engine: Engine) -> dict[str, tuple[int, str]]:
    """Stored documents as {claim id: (seed, text)}."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                GeneratedDocument.source_claim_id, GeneratedDocument.seed, GeneratedDocument.text
            )
        )
        return {claim_id: (seed, text) for claim_id, seed, text in rows}


def test_writes_one_letter_per_denied_claim_and_commits(
    loaded: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert set(_documents(loaded)) == DENIED_CLAIMS
    assert capsys.readouterr().out == "generated 3 denial letters (seed 42, generator v1)\n"


def test_stores_the_type_seed_generator_version_and_a_readable_answer_key(loaded: Engine) -> None:
    main([])

    with loaded.connect() as connection:
        rows = connection.execute(select(GeneratedDocument.__table__)).all()
    assert {row.document_type for row in rows} == {DocumentType.DENIAL_LETTER}
    assert {row.seed for row in rows} == {DEFAULT_SEED} == {42}
    assert {row.generator_version for row in rows} == {GENERATOR_VERSION}
    for row in rows:
        key = DenialLetterAnswerKey.model_validate(row.answer_key)
        assert key.claim_number == row.source_claim_id
        assert key.claim_number in row.text


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
    assert "(seed 7, generator v1)" in capsys.readouterr().out
    assert set(second) == DENIED_CLAIMS
    # No document of the first run is left, so the table never mixes two seeds.
    assert {seed for seed, _ in second.values()} == {7}
    assert all(second[claim_id][1] != first[claim_id][1] for claim_id in DENIED_CLAIMS)


def test_a_claim_that_is_no_longer_denied_loses_its_letter(loaded: Engine) -> None:
    main([])
    with loaded.begin() as connection:
        connection.execute(
            update(ClaimSampleLabel)
            .where(ClaimSampleLabel.source_claim_id == "800000000000005")
            .values(is_denied=False, denial_reason_category=None, appeal_success_proxy=None)
        )

    exit_code = main([])

    assert exit_code == 0
    assert set(_documents(loaded)) == DENIED_CLAIMS - {"800000000000005"}


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
    assert len(before) == 3
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

    assert len(before) == 3
    assert _documents(loaded) == before  # still the three letters made with seed 42


@pytest.mark.parametrize("value", ["-1", str(MAX_SEED + 1), "lucky"])
def test_bad_seed_exits_2(capsys: pytest.CaptureFixture[str], value: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--seed", value])

    assert excinfo.value.code == 2
    assert "--seed" in capsys.readouterr().err
