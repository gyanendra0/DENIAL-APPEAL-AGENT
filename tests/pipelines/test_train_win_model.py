"""CLI tests. The command reads committed rows, so each test cleans the tables afterwards.

The claims are a few hundred made-up ones built in code (tests/ml/made_up_claims.py): the
six-claim fixture file has too few denied claims to train on. Every test trains its own tiny
model into a temporary folder; none loads a stored model file.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session

from pipelines.train_win_model import DEFAULT_SEED, main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    DenialReasonCategory,
)
from src.ml.features import FEATURE_VERSION
from src.ml.inference import load_win_model, predict_win_probabilities, read_model_facts
from src.ml.labels import LABEL_RULE_VERSION
from src.ml.training import FACTS_FILE_NAME, MODEL_FILE_NAME, MODEL_VERSION
from src.ml.win_model_run import MAX_TRAINING_SEED
from tests.ml.made_up_claims import DENIED_COUNT, SPLIT_SEED, store_made_up_claims
from tests.ml.made_up_rows import AMOUNTS, features

TABLES: tuple[type[Base], ...] = (ClaimSample, ClaimSampleLine, ClaimSampleLabel)
FEATURE_ROWS = [
    features(category, amount, fully_denied=fully_denied)
    for category in DenialReasonCategory
    for amount in AMOUNTS
    for fully_denied in (False, True)
]


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    return tmp_path / "trained"


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the command at the test database and a temporary model folder."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    get_settings.cache_clear()
    _empty_tables(engine)  # in case an earlier run was killed before its clean-up
    yield engine
    get_settings.cache_clear()
    _empty_tables(engine)


@pytest.fixture
def loaded(database: Engine) -> Engine:
    """The test database with the made-up claims, their lines and their labels."""
    with Session(database) as session:
        store_made_up_claims(session)
        session.commit()
    return database


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in reversed(TABLES):  # rows that point at others go first
            connection.execute(delete(table))


def test_trains_prints_every_split_and_saves_both_files(
    loaded: Engine, model_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert {path.name for path in model_dir.iterdir()} == {MODEL_FILE_NAME, FACTS_FILE_NAME}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"win-probability model {MODEL_VERSION} (features")
    assert "proxy, not an observed outcome" in lines[0]
    assert lines[1] == f"training seed: {DEFAULT_SEED} | split seed: {SPLIT_SEED}"
    assert [line.split(":")[0] for line in lines[3:6]] == ["train", "validation", "test"]
    assert lines[6].startswith("test AUC against the target 0.70 (reported, not a gate): ")
    assert lines[6].endswith("meets the target")
    assert lines[7] == f"saved {MODEL_FILE_NAME} and {FACTS_FILE_NAME} in {model_dir}"


def test_the_facts_file_holds_the_versions_seeds_counts_and_auc(
    loaded: Engine, model_dir: Path
) -> None:
    main(["--seed", "7"])

    facts = read_model_facts(model_dir)
    assert facts.feature_version == FEATURE_VERSION
    assert facts.model_version == MODEL_VERSION
    assert facts.label_rule_version == LABEL_RULE_VERSION
    assert facts.training_seed == 7
    assert facts.split_seed == SPLIT_SEED
    assert set(facts.splits) == set(DatasetSplit)
    assert sum(split.rows for split in facts.splits.values()) == DENIED_COUNT
    for split in facts.splits.values():
        assert 0 < split.proxy_true < split.rows
        assert split.auc is not None
        assert split.auc > 0.9


def test_the_saved_model_loads_and_scores_between_0_and_1(loaded: Engine, model_dir: Path) -> None:
    main([])

    scores = predict_win_probabilities(load_win_model(model_dir), FEATURE_ROWS)

    assert all(0 <= score <= 1 for score in scores)
    assert len(set(scores)) > 1


def test_two_runs_with_the_same_seed_give_identical_predictions(
    loaded: Engine, model_dir: Path
) -> None:
    main(["--seed", "3"])
    first = predict_win_probabilities(load_win_model(model_dir), FEATURE_ROWS)
    first_facts = read_model_facts(model_dir)

    main(["--seed", "3"])

    assert predict_win_probabilities(load_win_model(model_dir), FEATURE_ROWS) == first
    assert read_model_facts(model_dir).splits == first_facts.splits


def test_no_stored_claims_exits_1_and_writes_nothing(
    database: Engine, model_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    assert exit_code == 1
    assert not model_dir.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no claim is labelled denied: run pipelines.run_data_pipeline first" in captured.err


def test_labels_of_another_rule_version_exit_1_and_keep_the_old_model(
    loaded: Engine, model_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([])
    before = (model_dir / MODEL_FILE_NAME).read_bytes(), (model_dir / FACTS_FILE_NAME).read_bytes()
    capsys.readouterr()
    with loaded.begin() as connection:
        connection.execute(update(ClaimSampleLabel).values(label_rule_version="v0"))

    exit_code = main([])

    assert exit_code == 1
    assert "rule version v0, the code is at v2" in capsys.readouterr().err
    after = (model_dir / MODEL_FILE_NAME).read_bytes(), (model_dir / FACTS_FILE_NAME).read_bytes()
    assert after == before


def test_a_train_split_with_one_proxy_value_exits_1_and_writes_nothing(
    loaded: Engine, model_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only the duplicate claims with a false proxy stay in train: one class, nothing to learn.
    with loaded.begin() as connection:
        connection.execute(
            delete(ClaimSample).where(
                ClaimSample.source_claim_id.in_(
                    ClaimSampleLabel.__table__.select()
                    .with_only_columns(ClaimSampleLabel.source_claim_id)
                    .where(ClaimSampleLabel.split == DatasetSplit.TRAIN)
                    .where(ClaimSampleLabel.appeal_success_proxy.is_(True))
                )
            )
        )

    exit_code = main([])

    assert exit_code == 1
    assert not model_dir.exists()
    assert "do not include both a true and a false proxy" in capsys.readouterr().err


@pytest.mark.parametrize("seed", ["-1", str(MAX_TRAINING_SEED + 1), "abc", "1.5"])
def test_a_bad_seed_exits_2(seed: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--seed", seed])

    assert error.value.code == 2
    assert "--seed" in capsys.readouterr().err


def test_the_largest_seed_is_accepted(loaded: Engine, model_dir: Path) -> None:
    assert main(["--seed", str(MAX_TRAINING_SEED)]) == 0
    assert read_model_facts(model_dir).training_seed == MAX_TRAINING_SEED


def test_the_default_seed_is_42() -> None:
    assert DEFAULT_SEED == 42
