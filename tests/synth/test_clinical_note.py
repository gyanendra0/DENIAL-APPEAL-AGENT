import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.clinical_note import (
    GENERATOR_VERSION,
    TEMPLATE_IDS,
    ClinicalNote,
    generate_clinical_note,
)
from src.synth.denial_letter import REASON_WORDING, generate_denial_letter
from src.synth.formats import CODE_NOT_PROVIDED
from src.synth.identity import MAX_SEED, PAYER_NAMES, claim_identity

CLAIM_ID = "800000000000001"
SEED = 42
SEEDS_TO_SEARCH = 200
# SHA-256 of the full text of `_note_with_template(template_id)`, generator v1.
PINNED_TEXT_SHA256 = {
    "visit_note": "05b113709408811b78bbd45fc33874846f2e023f5a0cad8aa8f6b6543fbd3026",
    "encounter_summary": "4ce9af5372eee39ad1a4b095a94d1041fe5fa99e60ccbd8f239075690b764c2a",
    "chart_entry": "4ff7bf7fd19a17d8cb9f7ebc460adf02031f40c687594bd898d2fb21ddf30562",
}


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields the note reads."""

    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    """A made-up service line. The note reads the first two fields; the rest let the same
    line go to the denial letter generator."""

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
DENIED_REPEATED_CODE = _line(4, "99213", "C", "0.00", "12.00")
# Not in line order on purpose.
LINES = [DENIED_REPEATED_CODE, DENIED_NO_CODE, PAID, DENIED_NONCOVERED]

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
    "visit_note": _long,
    "encounter_summary": lambda day: day.strftime("%m/%d/%Y"),
    "chart_entry": lambda day: day.isoformat(),
}
CODE_SEPARATOR = {"visit_note": ", ", "encounter_summary": " ", "chart_entry": ", "}


def _note(
    claim: Claim = CLAIM, seed: int = SEED, lines: tuple[Line, ...] = tuple(LINES)
) -> ClinicalNote:
    return generate_clinical_note(claim, lines, _label(claim.source_claim_id), seed)


def _note_with_template(
    template_id: str, claim: Claim = CLAIM, lines: tuple[Line, ...] = tuple(LINES)
) -> ClinicalNote:
    """The first seed whose draw picks `template_id` for this claim."""
    for seed in range(SEEDS_TO_SEARCH):
        note = _note(claim, seed, lines)
        if note.template_id == template_id:
            return note
    raise AssertionError(f"no seed below {SEEDS_TO_SEARCH} picks {template_id}")


def test_same_claim_and_seed_give_the_identical_note() -> None:
    assert _note() == _note()
    assert _note().text == _note().text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_the_text_of_every_template_is_pinned_to_the_generator_version(template_id: str) -> None:
    # If this fails, a word list, a template or a rule changed: bump GENERATOR_VERSION, then
    # update the hashes.
    note = _note_with_template(template_id)

    assert note.generator_version == "v1"
    assert hashlib.sha256(note.text.encode()).hexdigest() == PINNED_TEXT_SHA256[template_id]


def test_a_different_seed_gives_different_text() -> None:
    assert _note(seed=1).text != _note(seed=2).text


def test_the_order_of_the_lines_does_not_change_the_note() -> None:
    in_order = tuple(sorted(LINES, key=lambda line: line.line_number))

    assert _note(lines=in_order) == _note()


def test_note_carries_the_claim_the_seed_the_type_and_the_generator_version() -> None:
    note = _note()

    assert note.source_claim_id == CLAIM_ID
    assert note.seed == SEED
    assert note.document_type is DocumentType.CLINICAL_NOTE
    assert note.generator_version == GENERATOR_VERSION == "v1"


def test_the_template_is_a_draw_of_the_documented_text() -> None:
    draw = repeatable_draw(f"doc:v1:{SEED}:clinical_note:{CLAIM_ID}:template")

    assert _note().template_id == TEMPLATE_IDS[int(draw * len(TEMPLATE_IDS))]


def test_every_template_is_used_across_many_claims() -> None:
    claims = [replace(CLAIM, source_claim_id=str(800000000000000 + n)) for n in range(60)]

    assert {_note(claim).template_id for claim in claims} == set(TEMPLATE_IDS)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_prints_the_claims_codes_in_order(template_id: str) -> None:
    text = _note_with_template(template_id).text
    separator = CODE_SEPARATOR[template_id]

    assert separator.join(("4019", "25000")) in text
    # The paid line's code is in the note too: the note knows nothing of the denial.
    assert separator.join(("99213", "71020")) in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_prints_every_value_of_the_answer_key(template_id: str) -> None:
    note = _note_with_template(template_id)
    key = note.answer_key
    show = DATE_STYLE[template_id]

    for value in (
        key.patient_name,
        key.member_id,
        key.provider_name,
        show(key.service_from_date),
        show(key.note_date),
        *key.diagnosis_codes,
        *key.procedure_codes,
    ):
        assert value in note.text


def test_answer_key_carries_the_real_claim_fields() -> None:
    key = _note().answer_key

    assert key.service_from_date == key.service_thru_date == date(2009, 3, 3)
    assert key.diagnosis_codes == ("4019", "25000")


def test_procedure_codes_cover_all_lines_in_line_order_each_code_once() -> None:
    note = _note()

    # Lines 1 and 4 share 99213, line 2 is paid, line 3 has no code.
    assert note.answer_key.procedure_codes == ("99213", "71020")
    assert note.text.count("99213") == 1


@pytest.mark.parametrize("claim_thru_date", [date(2009, 3, 3), date(2009, 3, 5)])
def test_note_date_is_the_last_service_date(claim_thru_date: date) -> None:
    claim = replace(CLAIM, claim_thru_date=claim_thru_date)

    assert _note(claim).answer_key.note_date == claim_thru_date


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_both_service_dates_are_printed_when_they_differ(template_id: str) -> None:
    claim = replace(CLAIM, claim_thru_date=date(2009, 3, 5))
    show = DATE_STYLE[template_id]

    text = _note_with_template(template_id, claim).text

    assert f"{show(date(2009, 3, 3))} to {show(date(2009, 3, 5))}" in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_one_service_date_is_printed_when_both_are_equal(template_id: str) -> None:
    text = _note_with_template(template_id).text

    assert f"{DATE_STYLE[template_id](date(2009, 3, 3))} to " not in text


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
@pytest.mark.parametrize("lines", [(), (DENIED_NO_CODE,)], ids=["no lines", "no code on a line"])
def test_a_claim_without_procedure_codes_prints_not_provided(
    template_id: str, lines: tuple[Line, ...]
) -> None:
    note = _note_with_template(template_id, lines=lines)

    assert note.answer_key.procedure_codes == ()
    assert note.text.count(CODE_NOT_PROVIDED) == 1


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_a_claim_without_diagnosis_codes_prints_not_provided(template_id: str) -> None:
    note = _note_with_template(template_id, replace(CLAIM, diagnosis_codes=()))

    assert note.answer_key.diagnosis_codes == ()
    assert note.text.count(CODE_NOT_PROVIDED) == 1


def test_identity_matches_the_denial_letter_of_the_same_claim_and_seed() -> None:
    for seed in range(20):
        note_key = _note(seed=seed).answer_key
        letter_key = generate_denial_letter(CLAIM, LINES, _label(), seed).answer_key

        assert note_key.patient_name == letter_key.patient_name
        assert note_key.member_id == letter_key.member_id
        assert note_key.provider_name == letter_key.provider_name


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_note_shows_no_money_no_payer_no_claim_number_and_no_denial(template_id: str) -> None:
    note = _note_with_template(template_id)
    saved_key = note.answer_key.model_dump_json()

    for text in (note.text, saved_key):
        assert "$" not in text
        assert CLAIM_ID not in text
        assert claim_identity(CLAIM_ID, note.seed).payer_name not in text
        assert not any(payer in text for payer in PAYER_NAMES)
        assert not any(wording in text for wording in REASON_WORDING.values())
        for word in ("denied", "denial", "appeal", "noncovered", "81.25", "40.00", "55.50"):
            assert word not in text.lower()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_note_never_mentions_the_proxy_the_chance_the_band_or_the_split(
    template_id: str,
) -> None:
    note = _note_with_template(template_id)
    text = note.text.lower()
    # The whole saved answer key: every field name and every value.
    saved_key = note.answer_key.model_dump_json().lower()

    for word in ("proxy", "chance", "band", "split", "train", "validation", "test"):
        assert word not in text
        assert word not in saved_key


def test_note_is_the_same_whatever_the_proxy_value_and_the_reason() -> None:
    notes = {
        generate_clinical_note(CLAIM, LINES, _label(category=category, proxy=proxy), SEED)
        for category in DenialReasonCategory
        for proxy in (True, False)
    }

    assert len(notes) == 1


def test_answer_key_can_be_saved_as_json_with_exactly_its_fields() -> None:
    saved = _note().answer_key.model_dump(mode="json")

    assert set(saved) == {
        "service_from_date",
        "service_thru_date",
        "note_date",
        "diagnosis_codes",
        "procedure_codes",
        "patient_name",
        "member_id",
        "provider_name",
    }
    assert saved["note_date"] == "2009-03-03"
    assert saved["procedure_codes"] == ["99213", "71020"]


def test_rejects_a_claim_that_is_not_denied() -> None:
    label = ClaimLabel(
        source_claim_id=CLAIM_ID,
        is_denied=False,
        denial_reason_category=None,
        appeal_success_proxy=None,
        label_rule_version=LABEL_RULE_VERSION,
    )

    with pytest.raises(ValueError, match="is not denied"):
        generate_clinical_note(CLAIM, LINES, label, SEED)


def test_rejects_a_label_that_belongs_to_another_claim() -> None:
    with pytest.raises(ValueError, match="the label is for claim 800000000000002"):
        generate_clinical_note(CLAIM, LINES, _label("800000000000002"), SEED)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_outside_the_stored_range(seed: int) -> None:
    with pytest.raises(ValueError, match="seed must be from 0 to"):
        generate_clinical_note(CLAIM, LINES, _label(), seed)


@pytest.mark.parametrize("seed", [0, MAX_SEED])
def test_accepts_the_lowest_and_highest_seed(seed: int) -> None:
    assert _note(seed=seed).seed == seed
