"""CLI tests. The command really commits, so each test cleans the tables afterwards."""

from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select

from pipelines import run_data_pipeline
from pipelines.run_data_pipeline import DEFAULT_SPLIT_SEED, main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    IssuerDenialStats,
    PlanDenialStats,
)
from src.ingest.claims_sample import ClaimSampleBatch, read_claim_sample_rows
from src.ml import claim_labels
from src.ml.claim_labels import ClaimLabelRow, ClaimLine, build_claim_label_rows
from src.ml.splits import MAX_SPLIT_SEED
from tests.ingest.helpers import FIXTURE, claims_zip, edited_copy

TABLES: tuple[type[Base], ...] = (
    IssuerDenialStats,
    PlanDenialStats,
    ClaimSample,
    ClaimSampleLine,
    ClaimSampleLabel,
)
LOADED = (3, 6, 6, 9, 6)
NOTHING = (0, 0, 0, 0, 0)
# The fixture has two denied claims, both with a false proxy, so it fails the class balance
# gate as it is. Denying the first claim's only line (indicator O, payment 0) adds a denied
# claim whose proxy is true: one of three, which passes.
BALANCED: dict[tuple[int, str], str] = {
    (2, "LINE_PRCSG_IND_CD_1"): "O",
    (2, "LINE_NCH_PMT_AMT_1"): "0.00",
}


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the command at the test database, and empty the tables before and afterwards."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    _empty_tables(engine)  # in case an earlier run was killed before its clean-up
    yield engine
    get_settings.cache_clear()
    _empty_tables(engine)


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in reversed(TABLES):  # rows that point at others go first
            connection.execute(delete(table))


def _stored_counts(engine: Engine) -> tuple[int, ...]:
    """Row counts in the order of `TABLES`."""
    with engine.connect() as connection:
        return tuple(
            connection.scalar(select(func.count()).select_from(table)) or 0 for table in TABLES
        )


def _last_updates(engine: Engine) -> tuple[datetime | None, ...]:
    """The newest `updated_at` of each table, in the order of `TABLES`."""
    with engine.connect() as connection:
        return tuple(
            connection.scalar(select(func.max(table.__table__.c.updated_at))) for table in TABLES
        )


def _label_seeds(engine: Engine) -> dict[str, int]:
    """Stored labels as {claim id: split seed}."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(ClaimSampleLabel.source_claim_id, ClaimSampleLabel.split_seed)
        )
        return {claim_id: seed for claim_id, seed in rows}


def _args(claims: Path, marketplace: Path = FIXTURE) -> list[str]:
    return [str(marketplace), str(claims), "--plan-year", "2026"]


def test_loads_both_files_labels_the_claims_and_commits(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(_args(claims_zip(tmp_path, BALANCED)))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert _stored_counts(database) == LOADED
    assert "loaded 3 issuer rows, 6 plan rows, 6 claims, 9 service lines and 6 claim labels" in out


def test_prints_the_quality_report(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(_args(claims_zip(tmp_path, BALANCED)))

    out = capsys.readouterr().out
    assert "label rule version: v1 (the appeal-success label is a proxy" in out
    assert "split seed: 42" in out
    assert "claims labelled: 6" in out
    assert "all: 6 (100.00%) | 3 (50.00%) | 1 (33.33%)" in out
    assert "33.33%, passes" in out


def test_stores_each_claims_label_with_the_rule_version_split_and_seed(
    database: Engine, tmp_path: Path
) -> None:
    main(_args(claims_zip(tmp_path, BALANCED)))

    with database.connect() as connection:
        labels = {
            row.source_claim_id: row
            for row in connection.execute(select(ClaimSampleLabel.__table__))
        }
    assert {claim_id for claim_id, row in labels.items() if row.is_denied} == {
        "800000000000001",
        "800000000000002",
        "800000000000005",
    }
    assert labels["800000000000001"].appeal_success_proxy is True
    assert labels["800000000000002"].appeal_success_proxy is False
    assert labels["800000000000003"].appeal_success_proxy is None
    assert {row.label_rule_version for row in labels.values()} == {"v1"}
    assert {row.split_seed for row in labels.values()} == {DEFAULT_SPLIT_SEED} == {42}
    assert labels["800000000000001"].split == DatasetSplit.TRAIN


def test_running_twice_keeps_the_same_rows(database: Engine, tmp_path: Path) -> None:
    args = _args(claims_zip(tmp_path, BALANCED))
    main(args)

    exit_code = main(args)

    assert exit_code == 0
    assert _stored_counts(database) == LOADED


def test_split_seed_and_max_claims_are_passed_on(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def spy(
        claim_ids: Iterable[str], lines: Iterable[ClaimLine], split_seed: int
    ) -> list[ClaimLabelRow]:
        seen.append(split_seed)
        return build_claim_label_rows(claim_ids, lines, split_seed)

    monkeypatch.setattr(run_data_pipeline, "build_claim_label_rows", spy)

    # Claims 1 and 2 only: two denied, one true proxy.
    exit_code = main(
        [*_args(claims_zip(tmp_path, BALANCED)), "--max-claims", "2", "--split-seed", "7"]
    )

    assert exit_code == 0
    assert seen == [7]
    assert _stored_counts(database) == (3, 6, 2, 4, 2)
    with database.connect() as connection:
        assert set(connection.scalars(select(ClaimSampleLabel.split_seed))) == {7}


def test_a_later_run_replaces_the_labels_of_the_earlier_run(
    database: Engine, tmp_path: Path
) -> None:
    args = _args(claims_zip(tmp_path, BALANCED))
    main(args)  # six claims, seed 42

    exit_code = main([*args, "--max-claims", "2", "--split-seed", "7"])

    assert exit_code == 0
    # Only the two claims of the later run have a label, both with its seed. No label from
    # the earlier run is left, so the table never mixes two seeds.
    assert _label_seeds(database) == {"800000000000001": 7, "800000000000002": 7}
    assert _stored_counts(database) == (3, 6, 6, 9, 2)  # claims and lines are never removed


@pytest.mark.parametrize(
    "bad_edits",
    [{}, BALANCED | {(7, "LINE_NCH_PMT_AMT_1"): "-550.00"}],
    ids=["class balance fails", "claims file is invalid"],
)
def test_rejected_run_leaves_already_loaded_tables_exactly_as_they_were(
    database: Engine, tmp_path: Path, bad_edits: dict[tuple[int, str], str]
) -> None:
    good_dir, bad_dir = tmp_path / "good", tmp_path / "bad"
    good_dir.mkdir()
    bad_dir.mkdir()
    main(_args(claims_zip(good_dir, BALANCED)))
    before = (_stored_counts(database), _last_updates(database), _label_seeds(database))

    exit_code = main([*_args(claims_zip(bad_dir, bad_edits)), "--split-seed", "7"])

    assert exit_code == 1
    assert (_stored_counts(database), _last_updates(database), _label_seeds(database)) == before


def test_failure_after_the_old_labels_are_removed_brings_them_back(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(claims_zip(tmp_path, BALANCED))
    main(args)
    before = _label_seeds(database)

    def fail(*_: object) -> int:
        raise RuntimeError("database went away")

    # `replace_claim_label_rows` removes the old labels, then calls this to store the new ones.
    monkeypatch.setattr(claim_labels, "upsert_claim_label_rows", fail)

    with pytest.raises(RuntimeError, match="database went away"):
        main([*args, "--split-seed", "7"])

    assert len(before) == 6
    assert _label_seeds(database) == before  # still the six labels made with seed 42


def test_failed_class_balance_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(_args(claims_zip(tmp_path)))  # the fixture as it is: 0 of 2 true

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "batch rejected" in captured.err
    assert "true for 0.00% of denied claims, outside 20% to 80%" in captured.err
    assert "0.00%, fails" in captured.out  # the report is still shown
    assert _stored_counts(database) == NOTHING


def test_rejected_claims_file_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_claims = claims_zip(tmp_path, BALANCED | {(7, "LINE_NCH_PMT_AMT_1"): "-550.00"})

    exit_code = main(_args(bad_claims))

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "batch rejected" in err
    assert "row 7: line 1: payment_amount" in err
    assert _stored_counts(database) == NOTHING  # the good marketplace file is not loaded either


def test_rejected_marketplace_file_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_workbook = edited_copy(tmp_path, {"E4": 123})  # issuer id that is not five digits

    exit_code = main(_args(claims_zip(tmp_path, BALANCED), bad_workbook))

    assert exit_code == 1
    assert "batch rejected" in capsys.readouterr().err
    assert _stored_counts(database) == NOTHING  # the good claims file is not loaded either


def test_failure_while_writing_labels_undoes_the_other_four_tables(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> int:
        raise RuntimeError("database went away")

    monkeypatch.setattr(run_data_pipeline, "replace_claim_label_rows", fail)

    with pytest.raises(RuntimeError, match="database went away"):
        main(_args(claims_zip(tmp_path, BALANCED)))

    assert _stored_counts(database) == NOTHING  # four tables were written, then rolled back


def test_reads_50000_claims_when_no_limit_is_given(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked_for: list[int | None] = []

    def spy(path: Path, max_claims: int | None = None) -> ClaimSampleBatch:
        asked_for.append(max_claims)
        return read_claim_sample_rows(path, max_claims)

    monkeypatch.setattr(run_data_pipeline, "read_claim_sample_rows", spy)

    main(_args(claims_zip(tmp_path, BALANCED)))

    assert asked_for == [50_000]


@pytest.mark.parametrize("missing", ["marketplace", "claims"])
def test_missing_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    nope = tmp_path / "nope"
    args = _args(nope) if missing == "claims" else _args(claims_zip(tmp_path), nope)

    with pytest.raises(SystemExit) as excinfo:
        main(args)

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_missing_plan_year_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE), str(claims_zip(tmp_path))])

    assert excinfo.value.code == 2
    assert "--plan-year" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--split-seed", "-1"),
        ("--split-seed", str(MAX_SPLIT_SEED + 1)),
        ("--split-seed", "lucky"),
        ("--max-claims", "0"),
    ],
)
def test_bad_seed_or_claim_limit_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, value: str
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([*_args(claims_zip(tmp_path)), option, value])

    assert excinfo.value.code == 2
    assert option in capsys.readouterr().err
