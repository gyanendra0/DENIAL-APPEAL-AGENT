import json
from pathlib import Path

import joblib
import pytest

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.inference import (
    ModelFileError,
    load_win_model,
    predict_win_probabilities,
    predict_win_probability,
    read_model_facts,
)
from src.ml.training import (
    FACTS_FILE_NAME,
    MODEL_FILE_NAME,
    SplitFacts,
    WinModel,
    save_win_model,
    train_win_model,
)
from tests.ml.made_up_rows import AMOUNTS, features, training_rows

SPLITS = {DatasetSplit.TRAIN: SplitFacts(rows=288, proxy_true=120, auc=0.9)}


@pytest.fixture(scope="module")
def model() -> WinModel:
    return train_win_model(training_rows(), seed=7)


@pytest.fixture
def saved_folder(tmp_path: Path, model: WinModel) -> Path:
    save_win_model(model, tmp_path, split_seed=42, splits=SPLITS)
    return tmp_path


def _change_fact(folder: Path, key: str, value: object) -> None:
    path = folder / FACTS_FILE_NAME
    facts = json.loads(path.read_text(encoding="utf-8"))
    facts[key] = value
    path.write_text(json.dumps(facts), encoding="utf-8")


def _refuse_to_unpickle(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("the model file was read before the checks passed")

    monkeypatch.setattr(joblib, "load", fail)


@pytest.mark.parametrize("category", list(DenialReasonCategory))
@pytest.mark.parametrize("amount", [*AMOUNTS, "3440.00", "99999999.99"])
@pytest.mark.parametrize("fully_denied", [False, True])
def test_the_win_probability_is_between_0_and_1(
    model: WinModel, category: DenialReasonCategory, amount: str, fully_denied: bool
) -> None:
    chance = predict_win_probability(model, features(category, amount, fully_denied=fully_denied))

    assert isinstance(chance, float)
    assert 0.0 <= chance <= 1.0


def test_many_claims_are_scored_in_the_order_given(model: WinModel) -> None:
    rows = [
        features(DenialReasonCategory.DUPLICATE),
        features(DenialReasonCategory.MEDICAL_NECESSITY),
        features(DenialReasonCategory.OTHER, "800.00"),
    ]

    chances = predict_win_probabilities(model, rows)

    assert chances == [predict_win_probability(model, row) for row in rows]
    assert chances[0] < chances[1]


def test_scoring_no_claims_gives_no_scores(model: WinModel) -> None:
    assert predict_win_probabilities(model, []) == []


def test_a_loaded_model_gives_the_same_predictions_as_the_saved_one(
    saved_folder: Path, model: WinModel
) -> None:
    rows = [row.features for row in training_rows()]

    loaded = load_win_model(saved_folder)

    assert predict_win_probabilities(loaded, rows) == predict_win_probabilities(model, rows)
    assert loaded.training_seed == 7


def test_two_runs_with_the_same_seed_save_models_that_predict_the_same(tmp_path: Path) -> None:
    rows = [row.features for row in training_rows()]
    for name in ("first", "second"):
        save_win_model(
            train_win_model(training_rows(), seed=7), tmp_path / name, split_seed=42, splits=SPLITS
        )

    first = predict_win_probabilities(load_win_model(tmp_path / "first"), rows)
    second = predict_win_probabilities(load_win_model(tmp_path / "second"), rows)

    assert first == second


def test_the_facts_can_be_read_back(saved_folder: Path) -> None:
    facts = read_model_facts(saved_folder)

    assert facts.split_seed == 42
    assert facts.splits == SPLITS


def test_a_changed_model_file_is_refused_before_it_is_read(
    saved_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (saved_folder / MODEL_FILE_NAME).open("ab") as file:
        file.write(b"x")
    _refuse_to_unpickle(monkeypatch)

    with pytest.raises(ModelFileError, match="SHA-256"):
        load_win_model(saved_folder)


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("feature_version", "feature version"),
        ("model_version", "model version"),
        ("label_rule_version", "label rule version"),
        ("scikit_learn_version", "scikit-learn version"),
    ],
)
def test_a_model_made_by_another_version_is_refused_before_it_is_read(
    saved_folder: Path, monkeypatch: pytest.MonkeyPatch, key: str, name: str
) -> None:
    _change_fact(saved_folder, key, "v0")
    _refuse_to_unpickle(monkeypatch)

    with pytest.raises(ModelFileError, match=f"{name} v0.*train again"):
        load_win_model(saved_folder)


def test_a_missing_facts_file_is_refused(saved_folder: Path) -> None:
    (saved_folder / FACTS_FILE_NAME).unlink()

    with pytest.raises(ModelFileError, match="facts file .* is missing"):
        load_win_model(saved_folder)


def test_a_missing_model_file_is_refused(saved_folder: Path) -> None:
    (saved_folder / MODEL_FILE_NAME).unlink()

    with pytest.raises(ModelFileError, match="model file .* is missing"):
        load_win_model(saved_folder)


@pytest.mark.parametrize("text", ["not json", "{}", '{"model_sha256": "abc"}'])
def test_a_malformed_facts_file_is_refused(saved_folder: Path, text: str) -> None:
    (saved_folder / FACTS_FILE_NAME).write_text(text, encoding="utf-8")

    with pytest.raises(ModelFileError, match="malformed"):
        load_win_model(saved_folder)


def test_a_facts_file_with_an_unknown_key_is_refused(saved_folder: Path) -> None:
    _change_fact(saved_folder, "source_claim_id", "1")

    with pytest.raises(ModelFileError, match="malformed"):
        load_win_model(saved_folder)
