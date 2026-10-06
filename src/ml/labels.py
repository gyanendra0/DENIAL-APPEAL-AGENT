"""Label rule v1: turn one claim and its service lines into a proxy label.

Nothing here is an observed outcome. The claims source has no appeals, so
`appeal_success_proxy` is made by the documented rule below, and every report must call it a
proxy. The base chances and nudges are assumptions, not measurements.

The rule:

- A line is denied when its processing indicator is not `A` and its payment is 0.
- A claim is denied when any of its lines is denied.
- The reason category comes from the indicator of the first denied line.
- The proxy is a repeatable draw: a chance from the category and the claim's total allowed
  charge, compared with a number made from a hash of the rule version and the claim id.

Any change to a constant or to the hash input needs a new `LABEL_RULE_VERSION`.
"""

from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.db.models import DenialReasonCategory
from src.ml.draws import repeatable_draw

LABEL_RULE_VERSION = "v1"

ALLOWED_INDICATOR = "A"
# Secondary payer and the "MSP cost avoided" codes: another payer is primary.
COORDINATION_OF_BENEFITS_INDICATORS = frozenset("SQTUVXY!@#$*()+<>%&")
CATEGORY_BY_INDICATOR = {
    "C": DenialReasonCategory.NONCOVERED,
    "N": DenialReasonCategory.MEDICAL_NECESSITY,
    "M": DenialReasonCategory.DUPLICATE,
    "B": DenialReasonCategory.BENEFITS_EXHAUSTED,
} | dict.fromkeys(
    COORDINATION_OF_BENEFITS_INDICATORS, DenialReasonCategory.COORDINATION_OF_BENEFITS
)

BASE_CHANCE = {
    DenialReasonCategory.MEDICAL_NECESSITY: Decimal("0.60"),
    DenialReasonCategory.OTHER: Decimal("0.45"),
    DenialReasonCategory.NONCOVERED: Decimal("0.25"),
    DenialReasonCategory.COORDINATION_OF_BENEFITS: Decimal("0.20"),
    DenialReasonCategory.DUPLICATE: Decimal("0.10"),
    DenialReasonCategory.BENEFITS_EXHAUSTED: Decimal("0.10"),
}
# Bands of the claim's total allowed charge: zero, low (below 100), mid (below 250), high.
MID_BAND_FROM = Decimal("100")
HIGH_BAND_FROM = Decimal("250")
ZERO_BAND_NUDGE = Decimal("-0.10")
LOW_BAND_NUDGE = Decimal("0.00")
MID_BAND_NUDGE = Decimal("0.05")
HIGH_BAND_NUDGE = Decimal("0.10")

ClaimId = Annotated[str, Field(pattern=r"^[0-9]{15}$")]


class LabelLine(Protocol):
    """The four fields of a service line that the rule reads."""

    @property
    def line_number(self) -> int: ...

    @property
    def processing_indicator(self) -> str: ...

    @property
    def payment_amount(self) -> Decimal: ...

    @property
    def allowed_charge_amount(self) -> Decimal: ...


class ClaimLabel(BaseModel):
    """The proxy label of one claim.

    A denied claim has a reason category and a proxy value; a claim that is not denied has
    neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    is_denied: bool
    denial_reason_category: DenialReasonCategory | None
    appeal_success_proxy: bool | None
    label_rule_version: str = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _denied_fields_match(self) -> Self:
        has_category = self.denial_reason_category is not None
        has_proxy = self.appeal_success_proxy is not None
        if has_category != self.is_denied or has_proxy != self.is_denied:
            raise ValueError(
                "a denied claim needs a reason category and a proxy; other claims have neither"
            )
        return self


def label_claim(source_claim_id: str, lines: Sequence[LabelLine]) -> ClaimLabel:
    """Apply label rule v1 to one claim. `lines` are all of the claim's lines, in any order."""
    if not lines:
        raise ValueError(f"claim {source_claim_id} has no lines to label")
    denied = [line for line in lines if is_denied_line(line)]
    if not denied:
        return ClaimLabel(
            source_claim_id=source_claim_id,
            is_denied=False,
            denial_reason_category=None,
            appeal_success_proxy=None,
            label_rule_version=LABEL_RULE_VERSION,
        )
    first = min(denied, key=lambda line: line.line_number)
    category = CATEGORY_BY_INDICATOR.get(first.processing_indicator, DenialReasonCategory.OTHER)
    total_allowed = sum((line.allowed_charge_amount for line in lines), Decimal(0))
    chance = appeal_success_chance(category, total_allowed)
    draw = repeatable_draw(f"{LABEL_RULE_VERSION}:{source_claim_id}")
    return ClaimLabel(
        source_claim_id=source_claim_id,
        is_denied=True,
        denial_reason_category=category,
        appeal_success_proxy=draw < Fraction(chance),
        label_rule_version=LABEL_RULE_VERSION,
    )


def appeal_success_chance(category: DenialReasonCategory, total_allowed_charge: Decimal) -> Decimal:
    """The assumed chance that an appeal succeeds: the category's base chance plus a nudge
    for the band of the claim's total allowed charge."""
    if total_allowed_charge < 0:
        raise ValueError("total allowed charge cannot be negative")
    if total_allowed_charge == 0:
        nudge = ZERO_BAND_NUDGE
    elif total_allowed_charge < MID_BAND_FROM:
        nudge = LOW_BAND_NUDGE
    elif total_allowed_charge < HIGH_BAND_FROM:
        nudge = MID_BAND_NUDGE
    else:
        nudge = HIGH_BAND_NUDGE
    return BASE_CHANCE[category] + nudge


def is_denied_line(line: LabelLine) -> bool:
    """Whether the rule counts this line as denied: indicator not `A` and nothing paid."""
    return line.processing_indicator != ALLOWED_INDICATOR and line.payment_amount == 0
