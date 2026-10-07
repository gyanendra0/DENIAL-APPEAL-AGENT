import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.denial_letter import REASON_WORDING, generate_denial_letter
from src.synth.formats import CODE_NOT_PROVIDED, iso_date, long_date, us_date
from src.synth.identity import MAX_SEED
from src.synth.prior_auth import (
    GENERATOR_VERSION,
    PRIOR_AUTH_CATEGORIES,
    TEMPLATE_IDS,
    PriorAuthRecord,
    PriorAuthStatus,
    generate_prior_auth,
    needs_prior_auth,
)

CLAIM_ID = "800000000000001"
SEED = 42
SEEDS_TO_SEARCH = 200
# SHA-256 of the full text of `_record_with_template(template_id)`, generator v1.
PINNED_TEXT_SHA256 = {
    "authorization_notice": "2cefdeb2c19db2c4ef4fcdeab36e50766cb18ff13e41e3c511601c311be9c77b",
    "request_summary": "1b2994c5d9347465c794da69f2814d7cb0e5fd12256a0931b1384247266b62bb",
    "status_record": "ac7eb0497c0be47d1a957b899f46bf22674a1d0d0eb003dc5d20dc2ca745d74e",
}


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields the record reads."""

    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    """A made-up service line."""

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


NOT_DENIED_LABEL = ClaimLabel(
    source_claim_id=CLAIM_ID,
    is_denied=False,
    denial_reason_category=None,
    appeal_success_proxy=None,
    label_rule_version=LABEL_RULE_VERSION,
)

CLAIM = Claim(CLAIM_ID, date(2009, 3, 3), date(2009, 3, 3), ("4019", "25000"))
DENIED_NONCOVERED = _line(1, "99213", "C", "0.00", "81.25")
PAID = _line(2, "71020", "A", "40.00", "55.50")
DENIED_NECESSITY_NO_CODE = _line(3, None, "N", "0.00", "17.30")
DENIED_DUPLICATE = _line(4, "A0425", "M", "0.00", "12.00")
DENIED_REPEATED_CODE = _line(5, "99213", "C", "0.00", "5.75")
# A noncovered indicator with a payment: the label rule does not count it as denied.
NONCOVERED_BUT_PAID = _line(6, "36415", "C", "3.00", "3.00")
# Not in line order on purpose.
LINES = (
    DENIED_REPEATED_CODE,
    NONCOVERED_BUT_PAID,
    DENIED_DUPLICATE,
    DENIED_NECESSITY_NO_CODE,
    PAID,
    DENIED_NONCOVERED,
)

DATE_STYLE: dict[str, Callable[[date], str]] = {
    "authorization_notice": long_date,
    "request_summary": us_date,
    "status_record": iso_date,
}


def _record(
    claim: Claim = CLAIM, seed: int = SEED, lines: tuple[Line, ...] = LINES
) -> PriorAuthRecord:
    return generate_prior_auth(claim, lines, _label(claim.source_claim_id), seed)


def _record_with_template(
    template_id: str, claim: Claim = CLAIM, lines: tuple[Line, ...] = LINES
) -> PriorAuthRecord:
    """The first seed whose draw picks `template_id` for this claim."""
    for seed in range(SEEDS_TO_SEARCH):
        record = _record(claim, seed, lines)
        if record.template_id == template_id:
            return record
    raise AssertionError(f"no seed below {SEEDS_TO_SEARCH} picks {template_id}")


def _many_claims(count: int) -> list[Claim]:
    return [replace(CLAIM, source_claim_id=str(800000000000000 + n)) for n in range(count)]


# --- the "where relevant" rule ---


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_only_medical_necessity_and_noncovered_claims_need_a_record(
    category: DenialReasonCategory,
) -> None:
    expected = category in {
        DenialReasonCategory.MEDICAL_NECESSITY,
        DenialReasonCategory.NONCOVERED,
    }

    assert needs_prior_auth(_label(category=category)) is expected


def test_a_claim_that_is_not_denied_needs_no_record() -> None:
    assert needs_prior_auth(NOT_DENIED_LABEL) is False


@pytest.mark.parametrize("proxy", [True, False])
def test_the_rule_does_not_read_the_proxy(proxy: bool) -> None:
    assert needs_prior_auth(_label(proxy=proxy)) is True


# --- repeatable ---


def test_same_claim_and_seed_give_the_identical_record() -> None:
    assert _record() == _record()
    assert _record().text == _record().text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_the_text_of_every_template_is_pinned_to_the_generator_version(template_id: str) -> None:
    # If this fails, a word list, a template or a rule changed: bump GENERATOR_VERSION, then
    # update the hashes.
    record = _record_with_template(template_id)

    assert record.generator_version == "v1"
    assert hashlib.sha256(record.text.encode()).hexdigest() == PINNED_TEXT_SHA256[template_id]


def test_a_different_seed_gives_different_text() -> None:
    assert _record(seed=1).text != _record(seed=2).text


def test_the_order_of_the_lines_does_not_change_the_record() -> None:
    in_order = tuple(sorted(LINES, key=lambda line: line.line_number))

    assert _record(lines=in_order) == _record()


def test_record_carries_the_claim_the_seed_the_type_and_the_generator_version() -> None:
    record = _record()

    assert record.source_claim_id == CLAIM_ID
    assert record.seed == SEED
    assert record.document_type is DocumentType.PRIOR_AUTH
    assert record.generator_version == GENERATOR_VERSION == "v1"


def test_the_template_is_a_draw_of_the_documented_text() -> None:
    draw = repeatable_draw(f"doc:v1:{SEED}:prior_auth:{CLAIM_ID}:template")

    assert _record().template_id == TEMPLATE_IDS[int(draw * len(TEMPLATE_IDS))]


def test_every_template_is_used_across_many_claims() -> None:
    assert {_record(claim).template_id for claim in _many_claims(60)} == set(TEMPLATE_IDS)


# --- what the record prints ---


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_prints_every_value_of_the_answer_key(template_id: str) -> None:
    record = _record_with_template(template_id)
    key = record.answer_key
    show = DATE_STYLE[template_id]

    for value in (
        key.patient_name,
        key.member_id,
        key.provider_name,
        key.payer_name,
        key.authorization_number,
        show(key.service_from_date),
        show(key.request_date),
        show(key.decision_date),
        *key.diagnosis_codes,
        *(code or CODE_NOT_PROVIDED for code in key.requested_procedure_codes),
    ):
        assert value in record.text
    assert key.status.value in record.text.lower()


def test_answer_key_carries_the_real_claim_fields() -> None:
    key = _record().answer_key

    assert key.service_from_date == key.service_thru_date == date(2009, 3, 3)
    assert key.diagnosis_codes == ("4019", "25000")


def test_requested_codes_are_the_qualifying_denied_lines_in_line_order() -> None:
    # Lines 1, 3 and 5 qualify. Line 2 is paid, line 4 is a duplicate, line 6 has the
    # noncovered indicator but was paid. One entry per line, so 99213 is listed twice.
    assert _record().answer_key.requested_procedure_codes == ("99213", None, "99213")


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_codes_of_lines_that_do_not_qualify_are_not_printed(template_id: str) -> None:
    text = _record_with_template(template_id).text

    assert text.count("99213") == 2
    for code in ("71020", "A0425", "36415"):
        assert code not in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_requested_line_without_a_code_prints_not_provided(template_id: str) -> None:
    assert _record_with_template(template_id).text.count(CODE_NOT_PROVIDED) == 1


def test_an_empty_code_is_saved_as_missing() -> None:
    lines = (replace(DENIED_NONCOVERED, hcpcs_code=""),)

    assert _record(lines=lines).answer_key.requested_procedure_codes == (None,)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_claim_without_diagnosis_codes_prints_not_provided(template_id: str) -> None:
    record = _record_with_template(
        template_id, replace(CLAIM, diagnosis_codes=()), (DENIED_NONCOVERED,)
    )

    assert record.answer_key.diagnosis_codes == ()
    assert record.text.count(CODE_NOT_PROVIDED) == 1


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_both_service_dates_are_printed_when_they_differ(template_id: str) -> None:
    claim = replace(CLAIM, claim_thru_date=date(2009, 3, 5))
    show = DATE_STYLE[template_id]

    text = _record_with_template(template_id, claim).text

    assert f"{show(date(2009, 3, 3))} to {show(date(2009, 3, 5))}" in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_one_service_date_is_printed_when_both_are_equal(template_id: str) -> None:
    text = _record_with_template(template_id).text

    assert f"{DATE_STYLE[template_id](date(2009, 3, 3))} to " not in text


# --- the made-up number, dates and status ---


def test_authorization_number_is_the_prefix_and_eight_digits() -> None:
    for seed in range(50):
        number = _record(seed=seed).answer_key.authorization_number

        assert number.startswith("PA-")
        assert len(number) == 11
        assert number[3:].isdigit()


def test_authorization_number_differs_from_the_letters_reference() -> None:
    letter = generate_denial_letter(CLAIM, LINES, _label(), SEED)

    assert _record().answer_key.authorization_number[3:] != letter.answer_key.reference_number[3:]


def test_request_is_7_to_30_days_before_the_first_service_date() -> None:
    leads = {
        (CLAIM.claim_from_date - _record(seed=seed).answer_key.request_date).days
        for seed in range(500)
    }

    assert min(leads) == 7
    assert max(leads) == 30


def test_decision_is_1_to_5_days_after_the_request() -> None:
    delays = set()
    for seed in range(500):
        key = _record(seed=seed).answer_key
        delays.add((key.decision_date - key.request_date).days)

    assert delays == {1, 2, 3, 4, 5}


def test_decision_is_always_before_the_first_service_date() -> None:
    claim = replace(CLAIM, claim_thru_date=date(2009, 3, 9))

    for seed in range(500):
        key = _record(claim, seed).answer_key

        assert key.request_date < key.decision_date < claim.claim_from_date


def test_the_dates_are_draws_of_the_documented_text() -> None:
    lead = repeatable_draw(f"doc:v1:{SEED}:prior_auth:{CLAIM_ID}:request_lead_days")
    delay = repeatable_draw(f"doc:v1:{SEED}:prior_auth:{CLAIM_ID}:decision_delay_days")
    key = _record().answer_key

    assert (CLAIM.claim_from_date - key.request_date).days == 7 + int(lead * 24)
    assert (key.decision_date - key.request_date).days == 1 + int(delay * 5)


def test_the_status_is_a_draw_of_the_documented_text() -> None:
    for claim in _many_claims(40):
        draw = repeatable_draw(f"doc:v1:{SEED}:prior_auth:{claim.source_claim_id}:status")
        expected = PriorAuthStatus.APPROVED if draw < Fraction(1, 2) else PriorAuthStatus.DENIED

        assert _record(claim).answer_key.status is expected


def test_about_half_of_the_records_are_approved() -> None:
    statuses = [_record(claim).answer_key.status for claim in _many_claims(400)]

    assert set(statuses) == set(PriorAuthStatus)
    assert 160 <= statuses.count(PriorAuthStatus.APPROVED) <= 240


def test_record_is_the_same_whatever_the_proxy_value_and_the_qualifying_reason() -> None:
    records = {
        generate_prior_auth(CLAIM, LINES, _label(category=category, proxy=proxy), SEED)
        for category in PRIOR_AUTH_CATEGORIES
        for proxy in (True, False)
    }

    assert len(records) == 1


# --- shared identity, and what must never appear ---


def test_identity_matches_the_denial_letter_of_the_same_claim_and_seed() -> None:
    for seed in range(20):
        record_key = _record(seed=seed).answer_key
        letter_key = generate_denial_letter(CLAIM, LINES, _label(), seed).answer_key

        assert record_key.patient_name == letter_key.patient_name
        assert record_key.member_id == letter_key.member_id
        assert record_key.provider_name == letter_key.provider_name
        assert record_key.payer_name == letter_key.payer_name


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_record_shows_no_money_no_claim_number_and_no_reason_wording(template_id: str) -> None:
    record = _record_with_template(template_id)
    saved_key = record.answer_key.model_dump_json()

    for text in (record.text, saved_key):
        assert "$" not in text
        assert CLAIM_ID not in text
        assert not any(wording in text for wording in REASON_WORDING.values())
        for word in ("appeal", "noncovered", "81.25", "40.00", "17.30", "5.75"):
            assert word not in text.lower()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_record_never_mentions_the_proxy_the_chance_the_band_or_the_split(
    template_id: str,
) -> None:
    record = _record_with_template(template_id)
    text = record.text.lower()
    # The whole saved answer key: every field name and every value.
    saved_key = record.answer_key.model_dump_json().lower()

    for word in ("proxy", "chance", "band", "split", "train", "validation", "test"):
        assert word not in text
        assert word not in saved_key


def test_answer_key_can_be_saved_as_json_with_exactly_its_fields() -> None:
    saved = _record().answer_key.model_dump(mode="json")

    assert set(saved) == {
        "service_from_date",
        "service_thru_date",
        "diagnosis_codes",
        "requested_procedure_codes",
        "patient_name",
        "member_id",
        "provider_name",
        "payer_name",
        "authorization_number",
        "request_date",
        "decision_date",
        "status",
    }
    assert saved["requested_procedure_codes"] == ["99213", None, "99213"]
    assert saved["status"] in {"approved", "denied"}
    assert date.fromisoformat(saved["request_date"]) < date(2009, 3, 3)


# --- rejected input ---


def test_rejects_a_claim_that_is_not_denied() -> None:
    with pytest.raises(ValueError, match="gets no prior-authorisation record"):
        generate_prior_auth(CLAIM, LINES, NOT_DENIED_LABEL, SEED)


@pytest.mark.parametrize(
    "category", sorted(set(DenialReasonCategory) - PRIOR_AUTH_CATEGORIES, key=str)
)
def test_rejects_a_denied_claim_with_another_headline_reason(
    category: DenialReasonCategory,
) -> None:
    with pytest.raises(ValueError, match="gets no prior-authorisation record"):
        generate_prior_auth(CLAIM, LINES, _label(category=category), SEED)


@pytest.mark.parametrize(
    "lines",
    [(), (PAID,), (DENIED_DUPLICATE, NONCOVERED_BUT_PAID)],
    ids=["no lines", "paid only", "no qualifying denied line"],
)
def test_rejects_a_qualifying_label_with_no_qualifying_line(lines: tuple[Line, ...]) -> None:
    with pytest.raises(ValueError, match="has no line that qualifies"):
        generate_prior_auth(CLAIM, lines, _label(), SEED)


def test_rejects_a_label_that_belongs_to_another_claim() -> None:
    with pytest.raises(ValueError, match="the label is for claim 800000000000002"):
        generate_prior_auth(CLAIM, LINES, _label("800000000000002"), SEED)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_outside_the_stored_range(seed: int) -> None:
    with pytest.raises(ValueError, match="seed must be from 0 to"):
        generate_prior_auth(CLAIM, LINES, _label(), seed)


@pytest.mark.parametrize("seed", [0, MAX_SEED])
def test_accepts_the_lowest_and_highest_seed(seed: int) -> None:
    assert _record(seed=seed).seed == seed
