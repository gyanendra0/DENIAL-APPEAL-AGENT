"""Features of one denied claim for the win-probability model.

The model predicts `appeal_success_proxy`, which label rule v2 makes from three inputs and a
hash draw (`src/ml/labels.py`). The features are those three inputs and nothing else. The
proxy, the claim id, the split and the rule's own chance are never features: the function
below takes no label and no claim id, so none of them can reach a feature row.

Any change to a field or to how it is computed needs a new `FEATURE_VERSION`.
"""

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DenialReasonCategory
from src.ml.labels import LabelLine, is_denied_line

FEATURE_VERSION = "v1"


class ClaimFeatures(BaseModel):
    """The three model inputs of one denied claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    denial_reason_category: DenialReasonCategory
    total_allowed_charge: Decimal = Field(ge=0)
    fully_denied: bool


def build_claim_features(
    category: DenialReasonCategory, lines: Sequence[LabelLine]
) -> ClaimFeatures:
    """Build the feature row of one denied claim.

    `category` is the claim's stored reason category. `lines` are all of the claim's lines, in
    any order. Raises `ValueError` when the claim has no lines or no denied line: a claim that
    is not denied has no category and no proxy, so it has no feature row.
    """
    if not lines:
        raise ValueError("a claim has no lines to build features from")
    denied_count = sum(1 for line in lines if is_denied_line(line))
    if denied_count == 0:
        raise ValueError("a claim with no denied line has no features")
    return ClaimFeatures(
        denial_reason_category=category,
        total_allowed_charge=sum((line.allowed_charge_amount for line in lines), Decimal(0)),
        fully_denied=denied_count == len(lines),
    )
