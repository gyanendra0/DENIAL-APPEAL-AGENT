"""One training run of the win-probability model on the stored denied claims.

`load_training_data` reads the labels and lines that `pipelines.run_data_pipeline` stored and
turns every denied claim into a feature row. `train_and_score` fits the model on the train
split only and scores every split with the same AUC function as the quality report.

The label is a proxy made by label rule v2 (`src/ml/labels.py`), so the AUC says how well the
model learned that rule and nothing about real appeals.
"""

import logging
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ClaimSampleLabel, ClaimSampleLine, DatasetSplit
from src.ingest.marketplace_denials import BatchRejectedError
from src.ml.auc import TARGET_AUC, pairwise_auc
from src.ml.features import FEATURE_VERSION, ClaimFeatures, build_claim_features
from src.ml.inference import predict_win_probabilities
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel, claim_appeal_success_chance, label_claim
from src.ml.training import MODEL_VERSION, SplitFacts, TrainingRow, WinModel, train_win_model

logger = logging.getLogger(__name__)

# The library takes a seed from 0 to this number.
MAX_TRAINING_SEED = 2**32 - 1
NO_VALUE = "n/a"
RUN_DATA_PIPELINE_FIRST = "run pipelines.run_data_pipeline first"
RUN_DATA_PIPELINE_AGAIN = "run pipelines.run_data_pipeline again"


class DeniedClaim(BaseModel):
    """One denied claim of a training run. It has no claim id, so the id cannot reach the model.

    `rule_chance` is the label rule's own chance. It is the score of the best possible AUC
    and is never a model input.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: DatasetSplit
    features: ClaimFeatures
    appeal_success_proxy: bool
    rule_chance: Decimal = Field(ge=0, le=1)


class TrainingData(BaseModel):
    """Every stored denied claim, in claim id order, and the seed that split them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split_seed: int
    claims: tuple[DeniedClaim, ...]


class SplitScore(BaseModel):
    """One split of a training run: its size and how the model and the rule scored on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(ge=0)
    proxy_true: int = Field(ge=0)
    # None where the split has no claim with a true proxy, or none with a false one.
    auc: float | None
    best_possible_auc: float | None


class WinModelReport(BaseModel):
    """What one training run measured, per split."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split_seed: int
    training_seed: int
    splits: dict[DatasetSplit, SplitScore]

    def split_facts(self) -> dict[DatasetSplit, SplitFacts]:
        """The part of the report that `save_win_model` writes into the facts file."""
        return {
            split: SplitFacts(rows=score.rows, proxy_true=score.proxy_true, auc=score.auc)
            for split, score in self.splits.items()
        }

    def as_text(self) -> str:
        """The report as plain lines, ready to print."""
        lines = [
            f"win-probability model {MODEL_VERSION} (features {FEATURE_VERSION}, label rule"
            f" {LABEL_RULE_VERSION}; the appeal-success label is a proxy, not an observed outcome)",
            f"training seed: {self.training_seed} | split seed: {self.split_seed}",
            "split: denied claims | proxy true (of denied) | model AUC | best possible AUC",
        ]
        for split in DatasetSplit:
            score = self.splits[split]
            lines.append(
                f"{split.value}: {score.rows:,} | {score.proxy_true:,}"
                f" ({_share(score.proxy_true, score.rows)})"
                f" | {_auc(score.auc)} | {_auc(score.best_possible_auc)}"
            )
        test_auc = self.splits[DatasetSplit.TEST].auc
        if test_auc is None:
            verdict = "no AUC, the test split needs a true and a false proxy"
        elif Decimal(str(test_auc)) >= TARGET_AUC:
            verdict = f"{_auc(test_auc)}, meets the target"
        else:
            verdict = f"{_auc(test_auc)}, below the target"
        lines.append(f"test AUC against the target {TARGET_AUC} (reported, not a gate): {verdict}")
        return "\n".join(lines)


def load_training_data(session: Session) -> TrainingData:
    """Read every stored claim that is labelled denied and build its feature row.

    Reads `claim_sample_labels` and `claim_sample_lines`; writes nothing.

    Raises `BatchRejectedError` if no claim is labelled denied, if a label was made by a label
    rule other than `LABEL_RULE_VERSION`, if the labels hold more than one split seed, or if a
    label no longer fits the claim's stored lines (the lines were loaded again after the
    labels): the label rule, applied to the stored lines, must give exactly the stored label.
    """
    stored_labels = session.scalars(
        select(ClaimSampleLabel)
        .where(ClaimSampleLabel.is_denied)
        .order_by(ClaimSampleLabel.source_claim_id)
    ).all()
    if not stored_labels:
        raise BatchRejectedError([f"no claim is labelled denied: {RUN_DATA_PIPELINE_FIRST}"])
    other_versions = sorted(
        {label.label_rule_version for label in stored_labels} - {LABEL_RULE_VERSION}
    )
    if other_versions:
        raise BatchRejectedError(
            [
                f"labels were made with rule version {', '.join(other_versions)}, the code is at"
                f" {LABEL_RULE_VERSION}: {RUN_DATA_PIPELINE_AGAIN}"
            ]
        )
    split_seeds = sorted({label.split_seed for label in stored_labels})
    if len(split_seeds) > 1:
        raise BatchRejectedError(
            [
                "labels were split with more than one seed"
                f" ({', '.join(str(seed) for seed in split_seeds)}): {RUN_DATA_PIPELINE_AGAIN}"
            ]
        )

    lines_by_claim: dict[str, list[ClaimSampleLine]] = {
        label.source_claim_id: [] for label in stored_labels
    }
    for line in session.scalars(
        select(ClaimSampleLine)
        .join(ClaimSampleLabel, ClaimSampleLabel.source_claim_id == ClaimSampleLine.source_claim_id)
        .where(ClaimSampleLabel.is_denied)
    ):
        lines_by_claim[line.source_claim_id].append(line)

    claims: list[DeniedClaim] = []
    stale: list[str] = []
    for stored in stored_labels:
        claim_id = stored.source_claim_id
        lines = lines_by_claim[claim_id]
        label = ClaimLabel(
            source_claim_id=claim_id,
            is_denied=stored.is_denied,
            denial_reason_category=stored.denial_reason_category,
            appeal_success_proxy=stored.appeal_success_proxy,
            label_rule_version=stored.label_rule_version,
        )
        chance = claim_appeal_success_chance(lines) if lines else None
        if (
            chance is None
            or label.denial_reason_category is None
            or label.appeal_success_proxy is None
            or label_claim(claim_id, lines) != label
        ):
            stale.append(
                f"claim {claim_id}: the stored label no longer fits its stored lines:"
                f" {RUN_DATA_PIPELINE_AGAIN}"
            )
            continue
        claims.append(
            DeniedClaim(
                split=stored.split,
                features=build_claim_features(label.denial_reason_category, lines),
                appeal_success_proxy=label.appeal_success_proxy,
                rule_chance=chance,
            )
        )
    if stale:
        raise BatchRejectedError(stale)
    logger.info("read %d denied claims for training", len(claims))
    return TrainingData(split_seed=split_seeds[0], claims=tuple(claims))


def train_and_score(data: TrainingData, seed: int) -> tuple[WinModel, WinModelReport]:
    """Fit the model on the train split of `data` and score every split.

    The validation and test splits are only scored: they never reach the fit. The same data
    and seed give the same model. Raises `BatchRejectedError` when the train split has no
    claim with a true proxy or none with a false one, because the model needs both.

    With the default load of 50,000 claims (5,325 denied) this takes about a second.
    """
    train = [claim for claim in data.claims if claim.split is DatasetSplit.TRAIN]
    if len({claim.appeal_success_proxy for claim in train}) < 2:
        raise BatchRejectedError(
            [
                f"the train split has {len(train)} denied claims and they do not include both a"
                " true and a false proxy: the model needs both, load more claims"
            ]
        )
    model = train_win_model(
        [
            TrainingRow(features=claim.features, appeal_success_proxy=claim.appeal_success_proxy)
            for claim in train
        ],
        seed,
    )
    scores = predict_win_probabilities(model, [claim.features for claim in data.claims])
    splits = {}
    for split in DatasetSplit:
        in_split = [
            (claim, score)
            for claim, score in zip(data.claims, scores, strict=True)
            if claim.split is split
        ]
        true_claims = [pair for pair in in_split if pair[0].appeal_success_proxy]
        false_claims = [pair for pair in in_split if not pair[0].appeal_success_proxy]
        splits[split] = SplitScore(
            rows=len(in_split),
            proxy_true=len(true_claims),
            auc=pairwise_auc(
                [score for _, score in true_claims], [score for _, score in false_claims]
            ),
            best_possible_auc=pairwise_auc(
                [claim.rule_chance for claim, _ in true_claims],
                [claim.rule_chance for claim, _ in false_claims],
            ),
        )
    report = WinModelReport(split_seed=data.split_seed, training_seed=seed, splits=splits)
    logger.info(
        "trained the win-probability model on %d claims (%s)",
        len(train),
        ", ".join(f"{split.value} AUC {_auc(score.auc)}" for split, score in splits.items()),
    )
    return model, report


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.2%}" if whole else NO_VALUE


def _auc(value: float | None) -> str:
    return NO_VALUE if value is None else f"{value:.4f}"
