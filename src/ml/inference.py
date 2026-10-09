"""Load a saved win-probability model and score denied claims with it.

The score is the model's estimate of `appeal_success_proxy`, a made-up label (label rule v2).
It says nothing about real appeals.
"""

import hashlib
import io
from collections.abc import Sequence
from pathlib import Path

import joblib
import sklearn
from pydantic import ValidationError

from src.ml.features import FEATURE_VERSION, ClaimFeatures
from src.ml.labels import LABEL_RULE_VERSION
from src.ml.training import (
    FACTS_FILE_NAME,
    MODEL_FILE_NAME,
    MODEL_VERSION,
    ModelFacts,
    WinModel,
    feature_matrix,
)


class ModelFileError(Exception):
    """The saved model is missing, damaged, or was made by other versions than the code's."""


def load_win_model(folder: Path) -> WinModel:
    """Load the model that `save_win_model` wrote into `folder`.

    The model file is a pickle (joblib), and loading a pickle runs the code inside it. So only
    load files this project wrote: never point `folder` at a download or an upload. The file is
    read only after its SHA-256 equals the one in the facts file, and only when the facts name
    the feature, model, label rule and scikit-learn versions in use now. That check catches a
    damaged or out-of-date file; it does not make a file from someone else safe, because they
    could write a matching facts file.

    Raises `ModelFileError` when a file is missing or any check fails. The fix is to train again.
    """
    facts = read_model_facts(folder)
    _check_versions(facts)
    model_path = folder / MODEL_FILE_NAME
    try:
        content = model_path.read_bytes()
    except FileNotFoundError:
        raise ModelFileError(f"the model file {MODEL_FILE_NAME} is missing") from None
    if hashlib.sha256(content).hexdigest() != facts.model_sha256:
        raise ModelFileError(
            f"the model file {MODEL_FILE_NAME} does not match the SHA-256 in {FACTS_FILE_NAME}"
        )
    return WinModel(classifier=joblib.load(io.BytesIO(content)), training_seed=facts.training_seed)


def read_model_facts(folder: Path) -> ModelFacts:
    """Read the facts file in `folder`. Raises `ModelFileError` when it is missing or malformed."""
    try:
        text = (folder / FACTS_FILE_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ModelFileError(f"the facts file {FACTS_FILE_NAME} is missing") from None
    try:
        return ModelFacts.model_validate_json(text)
    except ValidationError as error:
        raise ModelFileError(
            f"the facts file {FACTS_FILE_NAME} is malformed ({error.error_count()} problems)"
        ) from None


def predict_win_probability(model: WinModel, features: ClaimFeatures) -> float:
    """The model's chance, between 0 and 1, that the claim's proxy is true."""
    return predict_win_probabilities(model, [features])[0]


def predict_win_probabilities(model: WinModel, features: Sequence[ClaimFeatures]) -> list[float]:
    """Score many claims in one call; the result has the order of `features`."""
    if not features:
        return []
    chances = model.classifier.predict_proba(feature_matrix(features))
    # The classes are sorted (False, True), so the second column is the chance of a true proxy.
    return [float(chance[1]) for chance in chances]


def _check_versions(facts: ModelFacts) -> None:
    pairs = (
        ("feature version", facts.feature_version, FEATURE_VERSION),
        ("model version", facts.model_version, MODEL_VERSION),
        ("label rule version", facts.label_rule_version, LABEL_RULE_VERSION),
        ("scikit-learn version", facts.scikit_learn_version, str(sklearn.__version__)),
    )
    for name, saved, current in pairs:
        if saved != current:
            raise ModelFileError(
                f"the saved model has {name} {saved}, the code uses {current}; train again"
            )
