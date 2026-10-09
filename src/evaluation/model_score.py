"""Measures the saved win-probability model's AUC on one split.

This is the model half of the Stage 3 done condition (docs/05-build-plan.md): an AUC of at
least `TARGET_AUC` (`src/ml/auc.py`) on the test split. Nothing is trained here: the model is
the one `pipelines.train_win_model` saved. The label is a proxy made by the label rule, so
the AUC says how well the model learned that rule and nothing about real appeals.
"""

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DatasetSplit
from src.ml.auc import pairwise_auc
from src.ml.inference import predict_win_probabilities
from src.ml.training import WinModel
from src.ml.win_model_run import TrainingData


class ModelScore(BaseModel):
    """How the saved model scored on the denied claims of one split.

    `best_possible_auc` is the AUC of the label rule's own chance: no model can be expected
    to score above it, so a model AUC well above it would point to a leak. Both are None when
    the split has no claim with a true proxy or none with a false one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: DatasetSplit
    rows: int = Field(ge=0)
    proxy_true: int = Field(ge=0)
    auc: float | None = Field(ge=0, le=1)
    best_possible_auc: float | None = Field(ge=0, le=1)


def score_saved_model(model: WinModel, data: TrainingData, split: DatasetSplit) -> ModelScore:
    """Score the denied claims of `split` in `data` with `model` and return the AUC.

    `model` comes from `load_win_model` and `data` from `load_training_data`. Only the claims
    of `split` are scored; the model is not fitted or changed.
    """
    claims = [claim for claim in data.claims if claim.split is split]
    scores = predict_win_probabilities(model, [claim.features for claim in claims])
    scored = list(zip(claims, scores, strict=True))
    true_claims = [pair for pair in scored if pair[0].appeal_success_proxy]
    false_claims = [pair for pair in scored if not pair[0].appeal_success_proxy]
    return ModelScore(
        split=split,
        rows=len(claims),
        proxy_true=len(true_claims),
        auc=pairwise_auc([score for _, score in true_claims], [score for _, score in false_claims]),
        best_possible_auc=pairwise_auc(
            [claim.rule_chance for claim, _ in true_claims],
            [claim.rule_chance for claim, _ in false_claims],
        ),
    )
