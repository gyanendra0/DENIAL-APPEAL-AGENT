"""Denial letter generator v1: turn one denied claim into a fabricated letter.

Every letter is made up. The claim numbers, dates, codes and amounts come from the synthetic
claims sample; every name, id and letter date on top is drawn from the small word lists below.
No real patient, provider or insurer appears in a letter.

How one letter is made:

- Every choice is a repeatable draw of the text
  `doc:<generator version>:<seed>:<document type>:<claim id>:<name of the choice>`, so a letter
  depends only on its claim, the seed and the generator version.
- The answer key (every value the letter prints) is built first. A template then writes the
  text from the answer key alone, so the text cannot hold a value the key lacks.
- The letter never shows the appeal-success proxy, its chance, the amount band or the split.

Any change to a word list, a template, a date rule or the draw text needs a new
`GENERATOR_VERSION`, because the same seed would no longer give the same text. One exception
was made under v1: the "not provided" wording for a claim without diagnosis codes was added
later, because no v1 letter had such a claim, so no existing text changed.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from string import ascii_uppercase
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import CATEGORY_BY_INDICATOR, ClaimId, ClaimLabel, LabelLine, is_denied_line

T = TypeVar("T")

GENERATOR_VERSION = "v1"
DOCUMENT_TYPE = DocumentType.DENIAL_LETTER
MAX_SEED = 2**31 - 1  # the seed is stored in an integer column

TEMPLATE_IDS = ("formal_letter", "benefits_table", "short_notice", "two_section")

# The letter is dated this many days after the last service date (both ends included).
MIN_LETTER_DELAY_DAYS = 7
MAX_LETTER_DELAY_DAYS = 45
# Made up, like the payer: the appeal deadline is the letter date plus this many days.
APPEAL_WINDOW_DAYS = 180

MEMBER_ID_LETTERS = 3
MEMBER_ID_DIGITS = 8
REFERENCE_PREFIX = "DL-"
REFERENCE_DIGITS = 8
CODE_NOT_PROVIDED = "not provided"
CENT = Decimal("0.01")

FIRST_NAMES = (
    "Avery",
    "Casey",
    "Dana",
    "Elliot",
    "Harper",
    "Jamie",
    "Jordan",
    "Kendall",
    "Logan",
    "Marlow",
    "Morgan",
    "Noel",
    "Quinn",
    "Reese",
    "Riley",
    "Robin",
    "Rowan",
    "Sage",
    "Sidney",
    "Taylor",
)
LAST_NAMES = (
    "Ashdown",
    "Birchall",
    "Calloway",
    "Dunmore",
    "Ellery",
    "Farrow",
    "Garrick",
    "Holloway",
    "Ingram",
    "Kestrel",
    "Lockwood",
    "Merriman",
    "Norwood",
    "Orrin",
    "Pembroke",
    "Quimby",
    "Radley",
    "Stanhope",
    "Thackeray",
    "Whitlock",
)
# Made-up practices and insurers. Never add the name of a real one.
PROVIDER_NAMES = (
    "Marrowby Family Practice",
    "Thistledown Medical Group",
    "Pellwick Orthopedic Associates",
    "Greywether Internal Medicine",
    "Sablebrook Clinic",
    "Hartleap Cardiology Partners",
    "Ondry Lane Physicians",
    "Wrenfold Health Center",
)
PAYER_NAMES = (
    "Larkspur Vale Mutual Health Plan",
    "Cinderfield Health Assurance",
    "Quillhaven Benefit Trust",
    "Tamarack Hollow Health Cooperative",
    "Owlmoor Health Plan",
    "Fennick and Dray Health Insurance",
)

# Generic wording only. No policy text is quoted: that would be an invented policy statement.
REASON_WORDING = {
    DenialReasonCategory.NONCOVERED: "the service is not covered",
    DenialReasonCategory.MEDICAL_NECESSITY: "the service was not found to be medically necessary",
    DenialReasonCategory.DUPLICATE: "the service duplicates one that was already processed",
    DenialReasonCategory.BENEFITS_EXHAUSTED: "the benefit limit for the service has been reached",
    DenialReasonCategory.COORDINATION_OF_BENEFITS: "another payer is primary for the service",
    DenialReasonCategory.OTHER: "the service could not be paid as submitted",
}
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class LetterClaim(Protocol):
    """The four fields of a claim that the letter prints."""

    @property
    def source_claim_id(self) -> str: ...

    @property
    def claim_from_date(self) -> date: ...

    @property
    def claim_thru_date(self) -> date: ...

    @property
    def diagnosis_codes(self) -> Sequence[str]: ...


class LetterLine(LabelLine, Protocol):
    """A service line: what the label rule reads, plus the procedure code."""

    @property
    def hcpcs_code(self) -> str | None: ...


class DeniedLineKey(BaseModel):
    """One denied service line as the letter prints it. `hcpcs_code` is None when the source
    has no procedure code; the letter then says "not provided"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_number: int = Field(ge=1)
    hcpcs_code: str | None
    allowed_charge_amount: Decimal
    payment_amount: Decimal
    reason_category: DenialReasonCategory


class DenialLetterAnswerKey(BaseModel):
    """Every value printed in one denial letter, real or fabricated.

    The totals are over all of the claim's lines, paid ones included. The headline
    `denial_reason_category` is the label's, so the letter and the label always agree. When
    `diagnosis_codes` is empty, the letter says "not provided".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_number: ClaimId
    service_from_date: date
    service_thru_date: date
    diagnosis_codes: tuple[str, ...]
    denied_lines: tuple[DeniedLineKey, ...] = Field(min_length=1)
    total_allowed_charge_amount: Decimal
    total_payment_amount: Decimal
    denial_reason_category: DenialReasonCategory
    patient_name: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    payer_name: str = Field(min_length=1)
    reference_number: str = Field(min_length=1)
    letter_date: date
    appeal_deadline: date


class DenialLetter(BaseModel):
    """One generated denial letter: the fields of a `generated_documents` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    template_id: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=MAX_SEED)
    generator_version: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1)
    answer_key: DenialLetterAnswerKey


def generate_denial_letter(
    claim: LetterClaim, lines: Sequence[LetterLine], label: ClaimLabel, seed: int
) -> DenialLetter:
    """Write the denial letter of one denied claim.

    `lines` are all of the claim's lines, in any order, and `label` is that claim's label. The
    same claim, seed and `GENERATOR_VERSION` always give the same letter.
    """
    claim_id = claim.source_claim_id
    if not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be from 0 to {MAX_SEED}, got {seed}")
    if label.source_claim_id != claim_id:
        raise ValueError(f"the label is for claim {label.source_claim_id}, not claim {claim_id}")
    category = label.denial_reason_category
    if not label.is_denied or category is None:
        raise ValueError(f"claim {claim_id} is not denied, so it gets no denial letter")
    denied = sorted(filter(is_denied_line, lines), key=lambda line: line.line_number)
    if not denied:
        raise ValueError(f"claim {claim_id} is labelled denied but has no denied line")

    def draw(choice: str) -> Fraction:
        return repeatable_draw(
            f"doc:{GENERATOR_VERSION}:{seed}:{DOCUMENT_TYPE.value}:{claim_id}:{choice}"
        )

    delay_days = MIN_LETTER_DELAY_DAYS + int(
        draw("letter_delay_days") * (MAX_LETTER_DELAY_DAYS - MIN_LETTER_DELAY_DAYS + 1)
    )
    letter_date = claim.claim_thru_date + timedelta(days=delay_days)
    key = DenialLetterAnswerKey(
        claim_number=claim_id,
        service_from_date=claim.claim_from_date,
        service_thru_date=claim.claim_thru_date,
        diagnosis_codes=tuple(claim.diagnosis_codes),
        denied_lines=tuple(_denied_line_key(line) for line in denied),
        total_allowed_charge_amount=_total(line.allowed_charge_amount for line in lines),
        total_payment_amount=_total(line.payment_amount for line in lines),
        denial_reason_category=category,
        patient_name=(
            f"{_pick(FIRST_NAMES, draw('first_name'))} {_pick(LAST_NAMES, draw('last_name'))}"
        ),
        member_id=_made_up_code(draw("member_id"), MEMBER_ID_LETTERS, MEMBER_ID_DIGITS),
        provider_name=_pick(PROVIDER_NAMES, draw("provider_name")),
        payer_name=_pick(PAYER_NAMES, draw("payer_name")),
        reference_number=REFERENCE_PREFIX
        + _made_up_code(draw("reference_number"), 0, REFERENCE_DIGITS),
        letter_date=letter_date,
        appeal_deadline=letter_date + timedelta(days=APPEAL_WINDOW_DAYS),
    )
    template_id = _pick(TEMPLATE_IDS, draw("template"))
    return DenialLetter(
        source_claim_id=claim_id,
        document_type=DOCUMENT_TYPE,
        template_id=template_id,
        seed=seed,
        generator_version=GENERATOR_VERSION,
        text=_RENDERERS[template_id](key),
        answer_key=key,
    )


def _pick(options: Sequence[T], draw: Fraction) -> T:
    return options[int(draw * len(options))]


def _made_up_code(draw: Fraction, letters: int, digits: int) -> str:
    """Turn one draw into `letters` capital letters followed by `digits` digits."""
    number = int(draw * len(ascii_uppercase) ** letters * 10**digits)
    number, digit_part = divmod(number, 10**digits)
    chars: list[str] = []
    for _ in range(letters):
        number, index = divmod(number, len(ascii_uppercase))
        chars.append(ascii_uppercase[index])
    return "".join(chars) + f"{digit_part:0{digits}d}"


def _total(amounts: Iterable[Decimal]) -> Decimal:
    return sum(amounts, Decimal(0)).quantize(CENT)


def _denied_line_key(line: LetterLine) -> DeniedLineKey:
    return DeniedLineKey(
        line_number=line.line_number,
        hcpcs_code=line.hcpcs_code,
        allowed_charge_amount=line.allowed_charge_amount.quantize(CENT),
        payment_amount=line.payment_amount.quantize(CENT),
        reason_category=CATEGORY_BY_INDICATOR.get(
            line.processing_indicator, DenialReasonCategory.OTHER
        ),
    )


def _long_date(day: date) -> str:
    return f"{MONTH_NAMES[day.month - 1]} {day.day}, {day.year}"


def _us_date(day: date) -> str:
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


def _iso_date(day: date) -> str:
    return day.isoformat()


def _service_dates(key: DenialLetterAnswerKey, show: Callable[[date], str]) -> str:
    if key.service_from_date == key.service_thru_date:
        return show(key.service_from_date)
    return f"{show(key.service_from_date)} to {show(key.service_thru_date)}"


def _money(amount: Decimal) -> str:
    return f"${amount:.2f}"


def _code(line: DeniedLineKey) -> str:
    return line.hcpcs_code or CODE_NOT_PROVIDED


def _diagnoses(key: DenialLetterAnswerKey, separator: str) -> str:
    return separator.join(key.diagnosis_codes) or CODE_NOT_PROVIDED


def _formal_letter(key: DenialLetterAnswerKey) -> str:
    denied = [
        f"- Line {line.line_number}, procedure code {_code(line)}: allowed amount "
        f"{_money(line.allowed_charge_amount)}, paid amount {_money(line.payment_amount)}. "
        f"Reason: {REASON_WORDING[line.reason_category]}."
        for line in key.denied_lines
    ]
    return "\n".join(
        [
            key.payer_name,
            "Claims Review Department",
            "",
            _long_date(key.letter_date),
            "",
            f"Reference: {key.reference_number}",
            "",
            key.patient_name,
            f"Member ID: {key.member_id}",
            "",
            f"Dear {key.patient_name},",
            "",
            f"We have reviewed claim {key.claim_number}, submitted by {key.provider_name} for "
            f"services provided on {_service_dates(key, _long_date)}. The diagnosis codes on "
            f"the claim are {_diagnoses(key, ', ')}.",
            "",
            "We are unable to approve payment for this claim because "
            f"{REASON_WORDING[key.denial_reason_category]}.",
            "",
            "The following service lines were denied:",
            "",
            *denied,
            "",
            "For the claim as a whole, the total allowed amount is "
            f"{_money(key.total_allowed_charge_amount)} and the total paid amount is "
            f"{_money(key.total_payment_amount)}.",
            "",
            "You have the right to appeal this decision. We must receive your written appeal "
            f"by {_long_date(key.appeal_deadline)}. Please quote reference "
            f"{key.reference_number} when you write to us.",
            "",
            "Sincerely,",
            "Claims Review Department",
            key.payer_name,
        ]
    )


def _benefits_table(key: DenialLetterAnswerKey) -> str:
    rows = [
        f"{line.line_number:<6}{_code(line):<14}{_money(line.allowed_charge_amount):<14}"
        f"{_money(line.payment_amount):<14}{REASON_WORDING[line.reason_category]}"
        for line in key.denied_lines
    ]
    return "\n".join(
        [
            "EXPLANATION OF BENEFITS - CLAIM DENIED",
            key.payer_name,
            "",
            f"Statement date:    {_us_date(key.letter_date)}",
            f"Reference no.:     {key.reference_number}",
            f"Member:            {key.patient_name}",
            f"Member ID:         {key.member_id}",
            f"Provider:          {key.provider_name}",
            f"Claim number:      {key.claim_number}",
            f"Service date(s):   {_service_dates(key, _us_date)}",
            f"Diagnosis codes:   {_diagnoses(key, ' ')}",
            f"Denial reason:     {REASON_WORDING[key.denial_reason_category]}",
            "",
            f"{'Line':<6}{'Procedure':<14}{'Allowed':<14}{'Paid':<14}Remark",
            *rows,
            "",
            f"Total allowed:     {_money(key.total_allowed_charge_amount)}",
            f"Total paid:        {_money(key.total_payment_amount)}",
            "",
            f"Appeal deadline:   {_us_date(key.appeal_deadline)}",
            "To appeal, write to us by this date and quote the reference number.",
        ]
    )


def _short_notice(key: DenialLetterAnswerKey) -> str:
    denied = [
        f"  #{line.line_number} code {_code(line)} - allowed "
        f"{_money(line.allowed_charge_amount)}, paid {_money(line.payment_amount)} - "
        f"{REASON_WORDING[line.reason_category]}"
        for line in key.denied_lines
    ]
    return "\n".join(
        [
            "NOTICE OF DENIAL",
            "",
            f"{key.payer_name} | Ref {key.reference_number} | {_iso_date(key.letter_date)}",
            "",
            f"To: {key.patient_name} (member {key.member_id})",
            f"Claim {key.claim_number} from {key.provider_name}, service date "
            f"{_service_dates(key, _iso_date)}, was denied: "
            f"{REASON_WORDING[key.denial_reason_category]}.",
            f"Diagnosis: {_diagnoses(key, ', ')}",
            "Denied lines:",
            *denied,
            f"Claim totals: allowed {_money(key.total_allowed_charge_amount)}, paid "
            f"{_money(key.total_payment_amount)}.",
            f"Appeal by: {_iso_date(key.appeal_deadline)}",
        ]
    )


def _two_section(key: DenialLetterAnswerKey) -> str:
    denied = [
        f"  {position}. Procedure {_code(line)} (line {line.line_number}): amount allowed "
        f"{_money(line.allowed_charge_amount)}, amount paid {_money(line.payment_amount)}. "
        f"Reason: {REASON_WORDING[line.reason_category]}."
        for position, line in enumerate(key.denied_lines, start=1)
    ]
    return "\n".join(
        [
            key.payer_name,
            f"Letter date: {_long_date(key.letter_date)}",
            f"Our reference: {key.reference_number}",
            "",
            f"Re: Claim {key.claim_number} for {key.patient_name}, member ID {key.member_id}",
            "",
            "SECTION 1 - OUR DECISION",
            "",
            f"Claim {key.claim_number} from {key.provider_name}, for services on "
            f"{_service_dates(key, _long_date)}, has been denied. Reason for the decision: "
            f"{REASON_WORDING[key.denial_reason_category]}.",
            f"Diagnosis codes reported: {_diagnoses(key, ', ')}",
            "",
            "Denied services:",
            *denied,
            "",
            f"Total amount allowed on the claim: {_money(key.total_allowed_charge_amount)}",
            f"Total amount paid on the claim: {_money(key.total_payment_amount)}",
            "",
            "SECTION 2 - YOUR APPEAL RIGHTS",
            "",
            "You may ask us to review this decision. Send your appeal in writing so that it "
            f"reaches us no later than {_long_date(key.appeal_deadline)}. Include our "
            f"reference {key.reference_number} and the claim number.",
        ]
    )


_RENDERERS: dict[str, Callable[[DenialLetterAnswerKey], str]] = {
    "formal_letter": _formal_letter,
    "benefits_table": _benefits_table,
    "short_notice": _short_notice,
    "two_section": _two_section,
}
