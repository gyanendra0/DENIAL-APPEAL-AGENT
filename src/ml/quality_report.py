"""The quality report of one labelling run, and the class-balance gate.

The appeal-success label is a proxy, not an observed outcome; the report says so.

The report also gives the best possible AUC: the AUC of the rule's own chance used as the
score. The label is that chance plus a hash draw, and no model can learn the draw, so no model
can be expected to score higher. It is reported beside the Stage 3 target and is not a gate:
a small load has few test claims, so the number is noisy.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction

from pydantic import BaseModel, ConfigDict

from src.db.models import DatasetSplit
from src.ml.claim_labels import ClaimLabelRow

# The model's target must not be too one-sided (docs/02-data.md 2.6).
MIN_PROXY_TRUE_SHARE = Decimal("0.20")
MAX_PROXY_TRUE_SHARE = Decimal("0.80")
NO_SHARE = "n/a"
# The Stage 3 done condition for the win-probability model (docs/05-build-plan.md).
TARGET_AUC = Decimal("0.70")


class LabelCounts(BaseModel):
    """How many claims are in a group, how many are denied, and how many have a true proxy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: int = 0
    denied: int = 0
    proxy_true: int = 0


class LabelQualityReport(BaseModel):
    """Counts of one labelling run, per split and in total."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label_rule_version: str
    split_seed: int
    splits: dict[DatasetSplit, LabelCounts]
    # None where a group has no denied claim with a true proxy, or none with a false one.
    best_auc_by_split: dict[DatasetSplit, float | None]
    best_auc_total: float | None

    @property
    def total(self) -> LabelCounts:
        counts = self.splits.values()
        return LabelCounts(
            claims=sum(c.claims for c in counts),
            denied=sum(c.denied for c in counts),
            proxy_true=sum(c.proxy_true for c in counts),
        )

    def problems(self) -> list[str]:
        """Reasons to reject the run; empty when the class-balance gate passes."""
        total = self.total
        if total.denied == 0:
            return ["no claim is denied, so there is no appeal-success proxy to balance"]
        share = Fraction(total.proxy_true, total.denied)
        if Fraction(MIN_PROXY_TRUE_SHARE) <= share <= Fraction(MAX_PROXY_TRUE_SHARE):
            return []
        return [
            "class balance: the appeal-success proxy is true for"
            f" {_share(total.proxy_true, total.denied)} of denied claims,"
            f" outside {MIN_PROXY_TRUE_SHARE:.0%} to {MAX_PROXY_TRUE_SHARE:.0%}"
        ]

    def as_text(self) -> str:
        """The report as plain lines, ready to print."""
        total = self.total
        lines = [
            f"label rule version: {self.label_rule_version}"
            " (the appeal-success label is a proxy, not an observed outcome)",
            f"split seed: {self.split_seed}",
            f"claims labelled: {total.claims:,}",
            "group: claims (share) | denied (of group) | proxy true (of denied)",
        ]
        groups = [(split.value, self.splits[split]) for split in DatasetSplit]
        for name, counts in [*groups, ("all", total)]:
            lines.append(
                f"{name}: {counts.claims:,} ({_share(counts.claims, total.claims)})"
                f" | {counts.denied:,} ({_share(counts.denied, counts.claims)})"
                f" | {counts.proxy_true:,} ({_share(counts.proxy_true, counts.denied)})"
            )
        best_aucs = [
            *((split.value, self.best_auc_by_split[split]) for split in DatasetSplit),
            ("all", self.best_auc_total),
        ]
        lines.append(
            f"best possible AUC (score = the rule's own chance; target {TARGET_AUC};"
            " reported, not a gate): "
            + " | ".join(f"{name} {_auc(value)}" for name, value in best_aucs)
        )
        verdict = "fails" if self.problems() else "passes"
        lines.append(
            f"class balance gate (proxy true among denied claims, {MIN_PROXY_TRUE_SHARE:.0%} to"
            f" {MAX_PROXY_TRUE_SHARE:.0%}): {_share(total.proxy_true, total.denied)}, {verdict}"
        )
        return "\n".join(lines)


def best_possible_auc(
    true_scores: Sequence[Decimal], false_scores: Sequence[Decimal]
) -> float | None:
    """The AUC of a score: how often a claim with a true proxy scores above one with a false one.

    It is the share of all (true, false) pairs in which the true claim has the higher score;
    a pair with equal scores counts as half. None when either group is empty.
    """
    if not true_scores or not false_scores:
        return None
    false_counts = Counter(false_scores)
    wins = Fraction(0)
    for score, true_count in Counter(true_scores).items():
        below = sum(count for other, count in false_counts.items() if other < score)
        wins += true_count * (below + Fraction(false_counts[score], 2))
    return float(wins / (len(true_scores) * len(false_scores)))


def build_label_quality_report(
    rows: Sequence[ClaimLabelRow], chances: Mapping[str, Decimal]
) -> LabelQualityReport:
    """Count `rows` per split. They must come from one run: one rule version and one seed.

    `chances` gives the rule's chance of every denied claim, by claim id (see
    `build_appeal_success_chances`); it is the score of the best possible AUC. Raises
    `ValueError` if a denied claim has no chance.
    """
    versions = {row.label_rule_version for row in rows}
    seeds = {row.split_seed for row in rows}
    if len(versions) != 1 or len(seeds) != 1:
        raise ValueError("a report needs rows from exactly one rule version and one split seed")
    for row in rows:
        if row.is_denied and row.source_claim_id not in chances:
            raise ValueError(f"denied claim {row.source_claim_id} has no appeal-success chance")
    splits = {}
    best_auc_by_split = {}
    for split in DatasetSplit:
        in_split = [row for row in rows if row.split is split]
        best_auc_by_split[split] = _best_auc(in_split, chances)
        splits[split] = LabelCounts(
            claims=len(in_split),
            denied=sum(row.is_denied for row in in_split),
            proxy_true=sum(row.appeal_success_proxy is True for row in in_split),
        )
    return LabelQualityReport(
        label_rule_version=versions.pop(),
        split_seed=seeds.pop(),
        splits=splits,
        best_auc_by_split=best_auc_by_split,
        best_auc_total=_best_auc(rows, chances),
    )


def _best_auc(rows: Sequence[ClaimLabelRow], chances: Mapping[str, Decimal]) -> float | None:
    """The best possible AUC over the denied claims in `rows`."""
    return best_possible_auc(
        [chances[row.source_claim_id] for row in rows if row.appeal_success_proxy is True],
        [chances[row.source_claim_id] for row in rows if row.appeal_success_proxy is False],
    )


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.2%}" if whole else NO_SHARE


def _auc(value: float | None) -> str:
    return NO_SHARE if value is None else f"{value:.4f}"
