"""The quality report of one labelling run, and the class-balance gate.

The appeal-success label is a proxy, not an observed outcome; the report says so.
"""

from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction

from pydantic import BaseModel, ConfigDict

from src.db.models import DatasetSplit
from src.ml.claim_labels import ClaimLabelRow

# The model's target must not be too one-sided (docs/02-data.md 2.6).
MIN_PROXY_TRUE_SHARE = Decimal("0.20")
MAX_PROXY_TRUE_SHARE = Decimal("0.80")
NO_SHARE = "n/a"


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
        verdict = "fails" if self.problems() else "passes"
        lines.append(
            f"class balance gate (proxy true among denied claims, {MIN_PROXY_TRUE_SHARE:.0%} to"
            f" {MAX_PROXY_TRUE_SHARE:.0%}): {_share(total.proxy_true, total.denied)}, {verdict}"
        )
        return "\n".join(lines)


def build_label_quality_report(rows: Sequence[ClaimLabelRow]) -> LabelQualityReport:
    """Count `rows` per split. They must come from one run: one rule version and one seed."""
    versions = {row.label_rule_version for row in rows}
    seeds = {row.split_seed for row in rows}
    if len(versions) != 1 or len(seeds) != 1:
        raise ValueError("a report needs rows from exactly one rule version and one split seed")
    splits = {}
    for split in DatasetSplit:
        in_split = [row for row in rows if row.split is split]
        splits[split] = LabelCounts(
            claims=len(in_split),
            denied=sum(row.is_denied for row in in_split),
            proxy_true=sum(row.appeal_success_proxy is True for row in in_split),
        )
    return LabelQualityReport(
        label_rule_version=versions.pop(), split_seed=seeds.pop(), splits=splits
    )


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.2%}" if whole else NO_SHARE
