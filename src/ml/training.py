"""Train the win-probability model and save it with a file of facts.

The model predicts `appeal_success_proxy` from the three features in `src/ml/features.py`.
That label is a proxy made by label rule v2 (`src/ml/labels.py`), so a model that scores well
has learned that rule and nothing about real appeals.

The settings are fixed constants and are not tuned on the validation split: 180 settings were
measured and the validation split could not tell them apart. Any change to a setting, or to
how a feature becomes a number, needs a new `MODEL_VERSION`.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import numpy.typing as npt
import sklearn
from pydantic import BaseModel, ConfigDict, Field
from sklearn.ensemble import HistGradientBoostingClassifier

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.features import FEATURE_VERSION, ClaimFeatures
from src.ml.labels import LABEL_RULE_VERSION

MODEL_VERSION = "v1"

MAX_TREES = 50
MAX_DEPTH = 3
LEARNING_RATE = 0.05
MIN_CLAIMS_PER_LEAF = 50

MODEL_FILE_NAME = "win_model.joblib"
FACTS_FILE_NAME = "win_model.json"

# The code of a category is its place in the constrained type, not the order seen in the data,
# so a category that is missing from the training rows still has a code.
CATEGORY_CODE = {category: code for code, category in enumerate(DenialReasonCategory)}
# One entry per number in `feature_numbers`: only the category is a code and not a quantity.
CATEGORICAL_FEATURES = [True, False, False]


class TrainingRow(BaseModel):
    """One denied claim for training: its features and the proxy the model learns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    features: ClaimFeatures
    appeal_success_proxy: bool


@dataclass(frozen=True)
class WinModel:
    """A fitted model and the seed it was fitted with.

    Score a claim with `predict_win_probability` in `src/ml/inference.py`; nothing else should
    reach into `classifier`.
    """

    classifier: Any
    training_seed: int


class SplitFacts(BaseModel):
    """What one split looked like when the model was trained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(ge=0)
    proxy_true: int = Field(ge=0)
    auc: float | None = Field(ge=0, le=1)


class ModelFacts(BaseModel):
    """The facts file saved beside a model file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_version: str
    model_version: str
    label_rule_version: str
    scikit_learn_version: str
    split_seed: int
    training_seed: int
    splits: dict[DatasetSplit, SplitFacts]
    model_sha256: str = Field(pattern="^[0-9a-f]{64}$")


def feature_numbers(features: ClaimFeatures) -> list[float]:
    """Turn a feature row into the three numbers the model reads.

    The amount becomes a float here because the library computes with floats; it is a model
    input, not money that is stored or shown.
    """
    return [
        float(CATEGORY_CODE[features.denial_reason_category]),
        float(features.total_allowed_charge),
        1.0 if features.fully_denied else 0.0,
    ]


def feature_matrix(rows: Sequence[ClaimFeatures]) -> npt.NDArray[np.float64]:
    """One line of `feature_numbers` per claim, as the array the library needs."""
    return np.array([feature_numbers(row) for row in rows], dtype=np.float64).reshape(
        len(rows), len(CATEGORICAL_FEATURES)
    )


def train_win_model(rows: Sequence[TrainingRow], seed: int) -> WinModel:
    """Fit the model on `rows`, which must be the train split only.

    The same rows and seed give the same model. Raises `ValueError` when there are no rows or
    every row has the same proxy, because nothing can be learned from one class.
    """
    if not rows:
        raise ValueError("there are no rows to train on")
    targets = [row.appeal_success_proxy for row in rows]
    if len(set(targets)) < 2:
        raise ValueError("every training row has the same proxy; the model needs both")
    classifier = HistGradientBoostingClassifier(
        max_iter=MAX_TREES,
        max_depth=MAX_DEPTH,
        learning_rate=LEARNING_RATE,
        min_samples_leaf=MIN_CLAIMS_PER_LEAF,
        early_stopping=False,
        categorical_features=CATEGORICAL_FEATURES,
        random_state=seed,
    )
    classifier.fit(feature_matrix([row.features for row in rows]), targets)
    return WinModel(classifier=classifier, training_seed=seed)


def save_win_model(
    model: WinModel,
    folder: Path,
    *,
    split_seed: int,
    splits: Mapping[DatasetSplit, SplitFacts],
) -> ModelFacts:
    """Write the model file and its facts file into `folder`, replacing earlier ones.

    The facts hold the versions in use right now and the SHA-256 of the model file, which
    `load_win_model` checks before it reads the model. The folder is created when missing.
    """
    folder.mkdir(parents=True, exist_ok=True)
    model_path = folder / MODEL_FILE_NAME
    joblib.dump(model.classifier, model_path)
    facts = ModelFacts(
        feature_version=FEATURE_VERSION,
        model_version=MODEL_VERSION,
        label_rule_version=LABEL_RULE_VERSION,
        scikit_learn_version=str(sklearn.__version__),
        split_seed=split_seed,
        training_seed=model.training_seed,
        splits=dict(splits),
        model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
    )
    (folder / FACTS_FILE_NAME).write_text(facts.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return facts
