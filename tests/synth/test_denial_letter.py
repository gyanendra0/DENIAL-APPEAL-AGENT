import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.denial_letter import (
    APPEAL_WINDOW_DAYS,
    CODE_NOT_PROVIDED,
    GENERATOR_VERSION,
    MAX_LETTER_DELAY_DAYS,
    MAX_SEED,
    MIN_LETTER_DELAY_DAYS,
    PAYER_NAMES,
    PROVIDER_NAMES,
    REASON_WORDING,
    TEMPLATE_IDS,
    DenialLetter,
    generate_denial_letter,
)

CLAIM_ID = "800000000000001"
SEED = 42
SEEDS_TO_SEARCH = 200
# SHA-256 of the full text of `_letter()`: claim 800000000000001, seed 42, generator v1.
PINNED_TEXT_SHA256 = "d8ff1af3529a899368a4abeb782d4609ade04bd71b6f31f3330b6d014cc77419"


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields the letter prints."""

    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    """A made-up service line with the fields the letter reads."""

    line_number: int
    hcpcs_code: str | None
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


def _line(number: int, code: str | None, indicator: str, payment: str, allowed: str) -> Line:
    return Line(number, code, indicator, Decimal(payment), Decimal(allowed))


def _label(
    claim_id: str = CLAIM_ID,
    category: DenialReasonCategory = DenialReasonCategory.NONCOVERED,
    proxy: bool = True,
) -> ClaimLabel:
    return ClaimLabel(
        source_claim_id=claim_id,
        is_denied=True,
        denial_reason_category=category,
        appeal_success_proxy=proxy,
        label_rule_version=LABEL_RULE_VERSION,
    )


CLAIM = Claim(CLAIM_ID, date(2009, 3, 3), date(2009, 3, 3), ("4019", "25000"))
DENIED_NONCOVERED = _line(1, "99213", "C", "0.00", "81.25")
PAID = _line(2, "71020", "A", "40.00", "55.50")
DENIED_NO_CODE = _line(3, None, "N", "0.00", "17.30")
LINES = [DENIED_NO_CODE, PAID, DENIED_NONCOVERED]  # not in line order on purpose

LONG_MONTHS = (
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


def _long(day: date) -> str:
    return f"{LONG_MONTHS[day.month - 1]} {day.day}, {day.year}"


DATE_STYLE: dict[str, Callable[[date], str]] = {
    "formal_letter": _long,
    "benefits_table": lambda day: day.strftime("%m/%d/%Y"),
    "short_notice": lambda day: day.isoformat(),
    "two_section": _long,
}
SERVICE_DATE_TEXT = {
    "formal_letter": "March 3, 2009",
    "benefits_table": "03/03/2009",
    "short_notice": "2009-03-03",
    "two_section": "March 3, 2009",
}


def _letter(claim: Claim = CLAIM, seed: int = SEED) -> DenialLetter:
    return generate_denial_letter(claim, LINES, _label(claim.source_claim_id), seed)


def _letter_with_template(template_id: str, claim: Claim = CLAIM) -> DenialLetter:
    """The first seed whose draw picks `template_id` for this claim."""
    for seed in range(SEEDS_TO_SEARCH):
        letter = _letter(claim, seed)
        if letter.template_id == template_id:
            return letter
    raise AssertionError(f"no seed below {SEEDS_TO_SEARCH} picks {template_id}")


def test_same_claim_and_seed_give_the_identical_letter() -> None:
    assert _letter() == _letter()
    assert _letter().text == _letter().text


def test_the_text_of_one_letter_is_pinned_to_the_generator_version() -> None:
    # If this fails, a word list, a template or a rule changed: bump GENERATOR_VERSION, then
    # update the hash.
    assert _letter().generator_version == "v1"
    assert hashlib.sha256(_letter().text.encode()).hexdigest() == PINNED_TEXT_SHA256


def test_a_different_seed_gives_different_text() -> None:
    assert _letter(seed=1).text != _letter(seed=2).text


def test_a_different_claim_gives_different_fabricated_values() -> None:
    other = replace(CLAIM, source_claim_id="800000000000002")

    assert _letter(other).answer_key.reference_number != _letter().answer_key.reference_number


def test_the_order_of_the_lines_does_not_change_the_letter() -> None:
    reordered = generate_denial_letter(
        CLAIM, sorted(LINES, key=lambda x: x.line_number), _label(), SEED
    )

    assert reordered == _letter()


def test_letter_carries_the_claim_the_seed_the_type_and_the_generator_version() -> None:
    letter = _letter()

    assert letter.source_claim_id == CLAIM_ID
    assert letter.seed == SEED
    assert letter.document_type is DocumentType.DENIAL_LETTER
    assert letter.generator_version == GENERATOR_VERSION == "v1"


def test_each_choice_is_a_draw_of_the_documented_text() -> None:
    draw = repeatable_draw(f"doc:v1:{SEED}:denial_letter:{CLAIM_ID}:template")

    assert _letter().template_id == TEMPLATE_IDS[int(draw * len(TEMPLATE_IDS))]


def test_every_template_is_used_across_many_claims() -> None:
    claims = [replace(CLAIM, source_claim_id=str(800000000000000 + n)) for n in range(60)]

    assert {_letter(claim).template_id for claim in claims} == set(TEMPLATE_IDS)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_prints_the_claims_codes_and_amounts(template_id: str) -> None:
    text = _letter_with_template(template_id).text

    assert CLAIM_ID in text
    assert "4019" in text
    assert "25000" in text
    assert "99213" in text
    assert "$81.25" in text
    assert "$17.30" in text
    assert "$0.00" in text  # what the denied lines were paid
    assert "$154.05" in text  # total allowed over all three lines
    assert "$40.00" in text  # total paid over all three lines
    assert SERVICE_DATE_TEXT[template_id] in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_prints_every_fabricated_value_of_the_answer_key(template_id: str) -> None:
    letter = _letter_with_template(template_id)
    key = letter.answer_key
    show = DATE_STYLE[template_id]

    for value in (
        key.patient_name,
        key.member_id,
        key.provider_name,
        key.payer_name,
        key.reference_number,
        show(key.letter_date),
        show(key.appeal_deadline),
    ):
        assert value in letter.text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_words_the_headline_and_each_denied_lines_reason(template_id: str) -> None:
    text = _letter_with_template(template_id).text

    assert text.count(REASON_WORDING[DenialReasonCategory.NONCOVERED]) == 2  # headline + line 1
    assert text.count(REASON_WORDING[DenialReasonCategory.MEDICAL_NECESSITY]) == 1  # line 3


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_paid_line_is_not_listed_but_counts_in_the_totals(template_id: str) -> None:
    letter = _letter_with_template(template_id)

    assert "71020" not in letter.text
    assert "55.50" not in letter.text
    assert [line.line_number for line in letter.answer_key.denied_lines] == [1, 3]
    assert letter.answer_key.total_allowed_charge_amount == Decimal("154.05")
    assert letter.answer_key.total_payment_amount == Decimal("40.00")


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_missing_procedure_code_is_printed_as_not_provided(template_id: str) -> None:
    letter = _letter_with_template(template_id)

    assert letter.text.count(CODE_NOT_PROVIDED) == 1
    assert letter.answer_key.denied_lines[1].hcpcs_code is None


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_claim_without_diagnosis_codes_prints_not_provided(template_id: str) -> None:
    letter = _letter_with_template(template_id, replace(CLAIM, diagnosis_codes=()))

    assert letter.answer_key.diagnosis_codes == ()
    # Once for the diagnosis codes, once for line 3's missing procedure code.
    assert letter.text.count(CODE_NOT_PROVIDED) == 2


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_amount_in_the_text_is_an_amount_of_the_answer_key(template_id: str) -> None:
    letter = _letter_with_template(template_id)
    key = letter.answer_key
    known = {key.total_allowed_charge_amount, key.total_payment_amount}
    for line in key.denied_lines:
        known |= {line.allowed_charge_amount, line.payment_amount}

    printed = re.findall(r"\$(\d+\.\d\d)(?!\d)", letter.text)

    assert len(printed) == letter.text.count("$")  # every amount has the dollars.cents shape
    assert {Decimal(amount) for amount in printed} <= known
    assert "billed" not in letter.text.lower()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_both_service_dates_are_printed_when_they_differ(template_id: str) -> None:
    claim = replace(CLAIM, claim_thru_date=date(2009, 3, 5))
    show = DATE_STYLE[template_id]

    text = _letter_with_template(template_id, claim).text

    assert f"{show(date(2009, 3, 3))} to {show(date(2009, 3, 5))}" in text


def test_a_zero_total_is_printed_as_zero() -> None:
    lines = [_line(1, "99213", "C", "0", "0")]

    letter = generate_denial_letter(CLAIM, lines, _label(), SEED)

    assert letter.answer_key.total_allowed_charge_amount == Decimal("0.00")
    assert str(letter.answer_key.total_allowed_charge_amount) == "0.00"
    assert "$0.00" in letter.text
    assert "$0 " not in letter.text


def test_answer_key_carries_the_real_claim_fields() -> None:
    key = _letter().answer_key

    assert key.claim_number == CLAIM_ID
    assert key.service_from_date == key.service_thru_date == date(2009, 3, 3)
    assert key.diagnosis_codes == ("4019", "25000")
    first, second = key.denied_lines
    assert (first.hcpcs_code, first.allowed_charge_amount, first.payment_amount) == (
        "99213",
        Decimal("81.25"),
        Decimal("0.00"),
    )
    assert first.reason_category is DenialReasonCategory.NONCOVERED
    assert second.reason_category is DenialReasonCategory.MEDICAL_NECESSITY


def test_headline_reason_is_the_labels_category_not_the_first_lines() -> None:
    label = _label(category=DenialReasonCategory.DUPLICATE)

    letter = generate_denial_letter(CLAIM, LINES, label, SEED)

    assert letter.answer_key.denial_reason_category is DenialReasonCategory.DUPLICATE
    assert REASON_WORDING[DenialReasonCategory.DUPLICATE] in letter.text


def test_an_indicator_outside_the_rules_table_is_worded_as_other() -> None:
    lines = [_line(1, "99213", "Z", "0.00", "10.00")]

    letter = generate_denial_letter(CLAIM, lines, _label(), SEED)

    assert letter.answer_key.denied_lines[0].reason_category is DenialReasonCategory.OTHER


def test_letter_date_and_deadline_follow_the_date_rules_for_every_seed() -> None:
    delays = set()
    for seed in range(SEEDS_TO_SEARCH):
        key = _letter(seed=seed).answer_key
        delays.add((key.letter_date - CLAIM.claim_thru_date).days)
        assert key.appeal_deadline == key.letter_date + timedelta(days=APPEAL_WINDOW_DAYS)

    assert min(delays) >= MIN_LETTER_DELAY_DAYS
    assert max(delays) <= MAX_LETTER_DELAY_DAYS
    assert len(delays) > 20  # the delay really varies


def test_fabricated_values_come_from_the_word_lists_and_fixed_patterns() -> None:
    for seed in range(SEEDS_TO_SEARCH):
        key = _letter(seed=seed).answer_key
        assert key.payer_name in PAYER_NAMES
        assert key.provider_name in PROVIDER_NAMES
        assert len(key.patient_name.split()) == 2
        assert len(key.member_id) == 11
        assert key.member_id[:3].isalpha() and key.member_id[:3].isupper()
        assert key.member_id[3:].isdigit()
        assert key.reference_number.startswith("DL-")
        assert len(key.reference_number) == 11 and key.reference_number[3:].isdigit()


def test_letter_is_the_same_whatever_the_proxy_value() -> None:
    won = generate_denial_letter(CLAIM, LINES, _label(proxy=True), SEED)
    lost = generate_denial_letter(CLAIM, LINES, _label(proxy=False), SEED)

    assert won == lost


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_letter_never_mentions_the_proxy_the_chance_the_band_or_the_split(
    template_id: str,
) -> None:
    letter = _letter_with_template(template_id)
    text = letter.text.lower()
    # The whole saved answer key: every field name and every value, nested ones included.
    saved_key = letter.answer_key.model_dump_json().lower()

    for word in ("proxy", "chance", "band", "split", "train", "validation", "test"):
        assert word not in text
        assert word not in saved_key


def test_answer_key_can_be_saved_as_json() -> None:
    saved = _letter().answer_key.model_dump(mode="json")

    assert saved["claim_number"] == CLAIM_ID
    assert saved["total_allowed_charge_amount"] == "154.05"
    assert saved["service_from_date"] == "2009-03-03"
    assert saved["denied_lines"][1]["hcpcs_code"] is None


def test_rejects_a_claim_that_is_not_denied() -> None:
    label = ClaimLabel(
        source_claim_id=CLAIM_ID,
        is_denied=False,
        denial_reason_category=None,
        appeal_success_proxy=None,
        label_rule_version=LABEL_RULE_VERSION,
    )

    with pytest.raises(ValueError, match="is not denied"):
        generate_denial_letter(CLAIM, LINES, label, SEED)


def test_rejects_a_label_that_belongs_to_another_claim() -> None:
    with pytest.raises(ValueError, match="the label is for claim 800000000000002"):
        generate_denial_letter(CLAIM, LINES, _label("800000000000002"), SEED)


@pytest.mark.parametrize("lines", [[], [PAID]], ids=["no lines", "only a paid line"])
def test_rejects_a_denied_label_without_a_denied_line(lines: list[Line]) -> None:
    with pytest.raises(ValueError, match="has no denied line"):
        generate_denial_letter(CLAIM, lines, _label(), SEED)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_outside_the_stored_range(seed: int) -> None:
    with pytest.raises(ValueError, match="seed must be from 0 to"):
        generate_denial_letter(CLAIM, LINES, _label(), seed)


@pytest.mark.parametrize("seed", [0, MAX_SEED])
def test_accepts_the_lowest_and_highest_seed(seed: int) -> None:
    assert _letter(seed=seed).seed == seed
