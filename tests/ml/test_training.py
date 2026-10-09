import hashlib
import json
from pathlib import Path

import pytest
import sklearn
from pydantic import ValidationError

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.features import FEATURE_VERSION, ClaimFeatures
from src.ml.inference import predict_win_probabilities, predict_win_probability
from src.ml.labels import LABEL_RULE_VERSION
from src.ml.training import (
    CATEGORICAL_FEATURES,
    CATEGORY_CODE,
    FACTS_FILE_NAME,
    LEARNING_RATE,
    MAX_DEPTH,
    MAX_TREES,
    MIN_CLAIMS_PER_LEAF,
    MODEL_FILE_NAME,
    MODEL_VERSION,
    SplitFacts,
    TrainingRow,
    feature_matrix,
    feature_numbers,
    save_win_model,
    train_win_model,
)
from tests.ml.made_up_rows import features, training_rows

SPLITS = {
    DatasetSplit.TRAIN: SplitFacts(rows=288, proxy_true=120, auc=0.9),
    DatasetSplit.VALIDATION: SplitFacts(rows=0, proxy_true=0, auc=None),
}


def _every_feature_row() -> list[ClaimFeatures]:
    return [row.features for row in training_rows()]


def test_the_model_version_is_v1() -> None:
    assert MODEL_VERSION == "v1"


def test_the_settings_are_the_ones_the_design_measured() -> None:
    assert (MAX_TREES, MAX_DEPTH, LEARNING_RATE, MIN_CLAIMS_PER_LEAF) == (50, 3, 0.05, 50)


def test_a_category_code_follows_the_order_of_the_constrained_type() -> None:
    assert CATEGORY_CODE == {
        DenialReasonCategory.NONCOVERED: 0,
        DenialReasonCategory.MEDICAL_NECESSITY: 1,
        DenialReasonCategory.DUPLICATE: 2,
        DenialReasonCategory.BENEFITS_EXHAUSTED: 3,
        DenialReasonCategory.COORDINATION_OF_BENEFITS: 4,
        DenialReasonCategory.OTHER: 5,
    }


def test_a_feature_row_becomes_three_numbers() -> None:
    row = features(DenialReasonCategory.DUPLICATE, "130.10", fully_denied=True)

    assert feature_numbers(row) == [2.0, 130.1, 1.0]
    assert feature_numbers(features(DenialReasonCategory.NONCOVERED, "0.00")) == [0.0, 0.0, 0.0]


def test_many_feature_rows_become_one_line_of_numbers_each() -> None:
    rows = [
        features(DenialReasonCategory.DUPLICATE, "130.10", fully_denied=True),
        features(DenialReasonCategory.OTHER, "0.00"),
    ]

    matrix = feature_matrix(rows)

    assert matrix.tolist() == [[2.0, 130.1, 1.0], [5.0, 0.0, 0.0]]
    assert feature_matrix([]).shape == (0, 3)


def test_only_the_category_is_marked_as_a_code() -> None:
    numbers = feature_numbers(features(DenialReasonCategory.OTHER))

    assert len(CATEGORICAL_FEATURES) == len(numbers)
    assert CATEGORICAL_FEATURES == [True, False, False]


def test_a_training_row_holds_only_the_features_and_the_proxy() -> None:
    assert set(TrainingRow.model_fields) == {"features", "appeal_success_proxy"}


def test_a_training_row_refuses_an_extra_field() -> None:
    with pytest.raises(ValidationError):
        TrainingRow.model_validate(
            {
                "features": features(DenialReasonCategory.OTHER),
                "appeal_success_proxy": True,
                "source_claim_id": "1",
            }
        )


def test_the_model_is_fitted_with_the_fixed_settings() -> None:
    model = train_win_model(training_rows(), seed=42)

    settings = model.classifier.get_params()
    assert settings["max_iter"] == MAX_TREES
    assert settings["max_depth"] == MAX_DEPTH
    assert settings["learning_rate"] == LEARNING_RATE
    assert settings["min_samples_leaf"] == MIN_CLAIMS_PER_LEAF
    assert settings["early_stopping"] is False
    assert settings["random_state"] == 42
    assert model.classifier.n_iter_ == MAX_TREES
    assert model.training_seed == 42


def test_the_model_learns_the_made_up_rule() -> None:
    model = train_win_model(training_rows(), seed=42)

    likely = predict_win_probability(model, features(DenialReasonCategory.MEDICAL_NECESSITY))
    unlikely = predict_win_probability(model, features(DenialReasonCategory.DUPLICATE))
    assert likely > 0.5 > unlikely


def test_the_same_seed_gives_the_same_predictions() -> None:
    rows = _every_feature_row()

    first = predict_win_probabilities(train_win_model(training_rows(), seed=7), rows)
    second = predict_win_probabilities(train_win_model(training_rows(), seed=7), rows)

    assert first == second


def test_a_category_missing_from_the_training_rows_can_still_be_scored() -> None:
    missing = DenialReasonCategory.BENEFITS_EXHAUSTED
    model = train_win_model(training_rows(without=missing), seed=42)

    assert 0.0 <= predict_win_probability(model, features(missing)) <= 1.0


def test_training_refuses_no_rows() -> None:
    with pytest.raises(ValueError, match="no rows"):
        train_win_model([], seed=42)


@pytest.mark.parametrize("proxy", [True, False])
def test_training_refuses_rows_that_all_have_the_same_proxy(proxy: bool) -> None:
    rows = [
        TrainingRow(features=row.features, appeal_success_proxy=proxy) for row in training_rows()
    ]

    with pytest.raises(ValueError, match="same proxy"):
        train_win_model(rows, seed=42)


def test_saving_writes_the_model_file_and_the_facts_file(tmp_path: Path) -> None:
    folder = tmp_path / "models" / "win"
    model = train_win_model(training_rows(), seed=7)

    facts = save_win_model(model, folder, split_seed=42, splits=SPLITS)

    assert sorted(path.name for path in folder.iterdir()) == [MODEL_FILE_NAME, FACTS_FILE_NAME]
    assert facts.model_sha256 == hashlib.sha256((folder / MODEL_FILE_NAME).read_bytes()).hexdigest()


def test_the_facts_file_holds_the_versions_the_seeds_and_the_splits(tmp_path: Path) -> None:
    model = train_win_model(training_rows(), seed=7)

    facts = save_win_model(model, tmp_path, split_seed=42, splits=SPLITS)

    stored = json.loads((tmp_path / FACTS_FILE_NAME).read_text(encoding="utf-8"))
    assert stored == facts.model_dump(mode="json")
    assert stored["feature_version"] == FEATURE_VERSION
    assert stored["model_version"] == MODEL_VERSION
    assert stored["label_rule_version"] == LABEL_RULE_VERSION
    assert stored["scikit_learn_version"] == sklearn.__version__
    assert stored["split_seed"] == 42
    assert stored["training_seed"] == 7
    assert stored["splits"] == {
        "train": {"rows": 288, "proxy_true": 120, "auc": 0.9},
        "validation": {"rows": 0, "proxy_true": 0, "auc": None},
    }


def test_saving_again_replaces_the_earlier_files(tmp_path: Path) -> None:
    first = save_win_model(
        train_win_model(training_rows(), seed=7), tmp_path, split_seed=42, splits=SPLITS
    )
    other_rows = training_rows(without=DenialReasonCategory.OTHER)

    second = save_win_model(
        train_win_model(other_rows, seed=7), tmp_path, split_seed=42, splits=SPLITS
    )

    assert second.model_sha256 != first.model_sha256
    assert (
        second.model_sha256 == hashlib.sha256((tmp_path / MODEL_FILE_NAME).read_bytes()).hexdigest()
    )
