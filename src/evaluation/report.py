"""The combined Stage 3 evaluation: the three measurements, the printed text, the decision.

The Stage 3 done condition (docs/05-build-plan.md) has two halves: field extraction accuracy
of at least 85% and a model AUC of at least 0.70 on the test split. The report checks three
numbers against them: the field accuracy over all documents, the denial letters' headline
reason, and the saved model's AUC. The rules agreement, the noise levels and the confidence
bands are reported and are never part of the decision.

Everything here is generated data: made-up documents of the synthetic claims sample, and a
label that is a proxy made by the label rule. The numbers say nothing about real appeals.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.db.models import DatasetSplit
from src.evaluation.extraction_accuracy import (
    TARGET_FIELD_ACCURACY,
    ExtractionAccuracyReport,
    MatchCount,
    build_extraction_accuracy_report,
)
from src.evaluation.model_score import ModelScore
from src.evaluation.rules_agreement import RulesAgreementReport, build_rules_agreement_report
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument
from src.ml.auc import TARGET_AUC
from src.ml.training import MODEL_VERSION
from src.rules.verdict import RULES_VERSION

NO_VALUE = "n/a"
# A share is printed as a percentage with two decimals, an AUC with four.
_SHARE_STEP = Decimal("0.0001")
_AUC_STEP = Decimal("0.0001")


class GateStatus(StrEnum):
    """How one measured number stands against its target."""

    MET = "meets the target"
    BELOW = "below the target"
    NOT_MEASURED = "cannot be measured"


class EvaluationReport(BaseModel):
    """What one evaluation run measured on one split.

    `model` is None when the saved model could not be scored (no model file, stored labels
    that cannot be used); `model_problem` then says why, and is None otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: DatasetSplit
    prompt_version: str = Field(min_length=1)
    rules_today: date
    amount_floor: Decimal
    extraction: ExtractionAccuracyReport
    rules: RulesAgreementReport
    model: ModelScore | None
    model_problem: str | None = Field(min_length=1)

    @model_validator(mode="after")
    def _a_score_or_a_problem(self) -> "EvaluationReport":
        if (self.model is None) == (self.model_problem is None):
            raise ValueError("exactly one of model and model_problem must be given")
        return self

    @property
    def model_auc(self) -> Decimal | None:
        """The model's AUC as an exact decimal, or None when there is none."""
        if self.model is None or self.model.auc is None:
            return None
        return Decimal(str(self.model.auc))

    @property
    def field_accuracy_status(self) -> GateStatus:
        return _status(self.extraction.all_documents.share, TARGET_FIELD_ACCURACY)

    @property
    def headline_reason_status(self) -> GateStatus:
        return _status(self.extraction.headline_reason.share, TARGET_FIELD_ACCURACY)

    @property
    def model_auc_status(self) -> GateStatus:
        return _status(self.model_auc, TARGET_AUC)

    @property
    def targets_met(self) -> bool:
        """True only when all three numbers were measured and meet their targets."""
        return all(
            status is GateStatus.MET
            for status in (
                self.field_accuracy_status,
                self.headline_reason_status,
                self.model_auc_status,
            )
        )

    def as_text(self) -> str:
        """The report as printed lines. It holds counts and names only, no document value."""
        extraction = self.extraction
        lines = [
            f"Stage 3 evaluation on the {self.split.value} split (generated data: made-up"
            " documents of the synthetic claims sample; the appeal-success label is a proxy,"
            " not an observed outcome)",
            f"extraction prompt {self.prompt_version} | win-probability model {MODEL_VERSION}"
            f" | rules {RULES_VERSION}, today {self.rules_today.isoformat()}, amount floor"
            f" ${self.amount_floor}",
            f"documents: {extraction.documents:,} | with a current result"
            f" {extraction.with_result:,} | no result {extraction.no_result:,} | stale result"
            f" {extraction.stale:,}",
            "field accuracy (exact match with the answer keys):",
            "  no result counted as wrong (the number checked against the target):"
            f" {_count(extraction.all_documents)}",
            f"  no result left out: {_count(extraction.with_result_only)}",
            "  per document type (no result counted as wrong), then per field (documents with"
            " a result):",
        ]
        for kind, accuracy in extraction.by_type.items():
            lines.append(
                f"    {kind.value} ({accuracy.documents:,} documents, {accuracy.with_result:,}"
                f" with a result): {_count(accuracy.all_documents)}"
            )
            lines.extend(_named(accuracy.fields, indent=6))
        lines.append("  per noise level (documents with a result):")
        lines.extend(_named({k.value: v for k, v in extraction.by_noise_level.items()}, indent=4))
        lines.append("  per confidence score of the model (documents with a result):")
        lines.extend(_named({k.value: v for k, v in extraction.by_confidence.items()}, indent=4))
        lines.append(
            "headline denial reason (denial letters, no result counted as wrong):"
            f" {_count(extraction.headline_reason)}"
        )
        lines.append(self._model_line())
        lines.extend(self._rules_lines())
        accuracy_shown = _share(extraction.all_documents.share, TARGET_FIELD_ACCURACY)
        headline_shown = _share(extraction.headline_reason.share, TARGET_FIELD_ACCURACY)
        auc_shown = _auc(self.model_auc, TARGET_AUC)
        lines.extend(
            [
                "targets:",
                f"  field accuracy, at least {TARGET_FIELD_ACCURACY:.0%}:"
                f" {_verdict(accuracy_shown, self.field_accuracy_status)}",
                f"  headline denial reason, at least {TARGET_FIELD_ACCURACY:.0%}:"
                f" {_verdict(headline_shown, self.headline_reason_status)}",
                f"  model AUC, at least {TARGET_AUC}: {_verdict(auc_shown, self.model_auc_status)}",
                f"Stage 3 targets met: {'yes' if self.targets_met else 'no'}",
            ]
        )
        return "\n".join(lines)

    def _model_line(self) -> str:
        if self.model is None:
            return f"win-probability model: {GateStatus.NOT_MEASURED.value} ({self.model_problem})"
        model = self.model
        proxy_share = _share(MatchCount(matched=model.proxy_true, compared=model.rows).share)
        best = None if model.best_possible_auc is None else Decimal(str(model.best_possible_auc))
        return (
            f"win-probability model (saved model, not trained here): {model.rows:,} denied"
            f" claims | proxy true {model.proxy_true:,} ({proxy_share}) | model AUC"
            f" {_auc(self.model_auc, TARGET_AUC)} | best possible AUC {_auc(best)}"
        )

    def _rules_lines(self) -> list[str]:
        rules = self.rules
        outcomes = " | ".join(
            f"{outcome.value} {letters:,}" for outcome, letters in rules.expected_outcomes.items()
        )
        lines = [
            "rules agreement (verdict from the extracted values against the verdict from the"
            " answer key; reported, not a gate):",
            f"  denial letters: {rules.letters:,} | with a verdict from extraction"
            f" {rules.with_verdict:,} | no verdict {rules.no_verdict:,}",
            f"  answer-key outcomes: {outcomes}",
            f"  same outcome: {_count(rules.same_outcome)}",
        ]
        lines.extend(
            f"  {move.expected.value} to {move.extracted.value}: {move.letters:,}"
            for move in rules.moves
        )
        return lines


def build_evaluation_report(
    documents: Iterable[StoredDocument],
    rows: Mapping[DocumentKey, DocumentExtractionRow],
    *,
    split: DatasetSplit,
    prompt_version: str,
    rules_today: date,
    amount_floor: Decimal,
    model: ModelScore | None,
    model_problem: str | None,
) -> EvaluationReport:
    """Measure `rows` against `documents` and join the result with the model's score.

    `documents` are those of `split` and `rows` the stored results of `prompt_version`.
    `rules_today` and `amount_floor` are passed on to the rules as they are: nothing here
    reads the clock or the settings. Give `model`, or `model_problem` when it has no score.
    """
    documents = list(documents)
    return EvaluationReport(
        split=split,
        prompt_version=prompt_version,
        rules_today=rules_today,
        amount_floor=amount_floor,
        extraction=build_extraction_accuracy_report(documents, rows),
        rules=build_rules_agreement_report(
            documents, rows, today=rules_today, amount_floor=amount_floor
        ),
        model=model,
        model_problem=model_problem,
    )


def _status(value: Decimal | None, target: Decimal) -> GateStatus:
    if value is None:
        return GateStatus.NOT_MEASURED
    return GateStatus.MET if value >= target else GateStatus.BELOW


def _rounded(value: Decimal, step: Decimal, target: Decimal | None) -> Decimal:
    """Round `value` to `step`; down when rounding would lift it to a target it misses."""
    rounded = value.quantize(step, rounding=ROUND_HALF_EVEN)
    if target is not None and value < target <= rounded:
        return value.quantize(step, rounding=ROUND_DOWN)
    return rounded


def _share(value: Decimal | None, target: Decimal | None = None) -> str:
    return NO_VALUE if value is None else f"{_rounded(value, _SHARE_STEP, target):.2%}"


def _auc(value: Decimal | None, target: Decimal | None = None) -> str:
    return NO_VALUE if value is None else f"{_rounded(value, _AUC_STEP, target):.4f}"


def _count(count: MatchCount) -> str:
    return f"{count.matched:,} of {count.compared:,} ({_share(count.share)})"


def _named(counts: Mapping[str, MatchCount], *, indent: int) -> list[str]:
    return [f"{' ' * indent}{name}: {_count(count)}" for name, count in counts.items()]


def _verdict(shown: str, status: GateStatus) -> str:
    if status is GateStatus.NOT_MEASURED:
        return status.value
    return f"{shown}, {status.value}"
