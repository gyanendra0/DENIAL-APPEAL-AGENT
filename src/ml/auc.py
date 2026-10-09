"""The AUC of a score against the appeal-success proxy.

One function scores both the label rule's own chance (the best possible AUC in the quality
report) and the win-probability model, so the two numbers can be compared.
"""

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction
from typing import TypeVar

# The Stage 3 done condition for the win-probability model (docs/05-build-plan.md).
TARGET_AUC = Decimal("0.70")

# The rule's chances are exact decimals, a model's scores are floats; one call never mixes them.
Score = TypeVar("Score", Decimal, float)


def pairwise_auc(true_scores: Sequence[Score], false_scores: Sequence[Score]) -> float | None:
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
