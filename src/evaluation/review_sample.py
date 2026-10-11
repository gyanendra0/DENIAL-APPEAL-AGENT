"""Picks which drafted appeal letters a reviewer scores.

This is the sampling half of the Stage 4 done condition (docs/05-build-plan.md): a reviewer
accepts at least 60% of drafts with only light edits. The share is measured on a sample, and
the sample must be repeatable, so the draw depends only on the claim ids and the seed. Nothing
here reads the database, calls a model or looks at the clock; the caller finds the claim ids
that can be sampled.
"""

import random
from collections.abc import Iterable

from src.ml.labels import ClaimId

REVIEW_SAMPLE_SIZE = 30
REVIEW_SAMPLE_SEED = 42


def select_review_sample(claim_ids: Iterable[ClaimId], *, size: int, seed: int) -> list[ClaimId]:
    """Draw `size` of `claim_ids` with `seed` and return them sorted.

    The same claim ids and the same seed always give the same sample, whatever order the ids
    arrive in. Raises `ValueError` when `size` is below 1, when a claim id is given twice, or
    when `size` is above the number of claim ids: a smaller sample is never returned quietly.
    """
    if size < 1:
        raise ValueError(f"the sample size must be at least 1, got {size}")
    ordered = sorted(claim_ids)
    repeated = len(ordered) - len(set(ordered))
    if repeated:
        raise ValueError(f"{repeated} claim id(s) were given more than once")
    if size > len(ordered):
        raise ValueError(f"{size} drafts asked for, only {len(ordered)} can be sampled")
    drawn = random.Random(seed).sample(ordered, size)  # noqa: S311  (a repeatable draw, no secret)
    return sorted(drawn)
