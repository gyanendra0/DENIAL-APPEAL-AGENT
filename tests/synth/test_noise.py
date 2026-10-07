import hashlib
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.clinical_note import ClinicalNote, generate_clinical_note
from src.synth.denial_letter import DenialLetter, generate_denial_letter
from src.synth.formats import iso_date, long_date, us_date
from src.synth.identity import MAX_SEED
from src.synth.noise import (
    LOOK_ALIKES,
    MAX_PAGE_WIDTH,
    MIN_PAGE_WIDTH,
    MISSING_FIELDS,
    NOISE_VERSION,
    NoiseLevel,
    NoisyText,
    apply_noise,
    damage_characters,
)
from src.synth.prior_auth import PriorAuthRecord, generate_prior_auth

CLAIM_ID = "800000000000001"
FIRST_CLAIM_NUMBER = 800000000000000
SEED = 42
CLAIMS_TO_SEARCH = 2000
# SHA-256 of the noisy text of `_document(DENIAL_LETTER, PINNED_CLAIM_ID)`, noise v1: a `heavy`
# letter that also lost its reference number.
PINNED_NOISY_TEXT_SHA256 = "f493f7f922e85c7bca6a8f98358c914638c9a846e1394dd5d0b1d5e051c044db"

Document = DenialLetter | ClinicalNote | PriorAuthRecord


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields a document reads."""

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


CLAIM = Claim(CLAIM_ID, date(2009, 3, 3), date(2009, 3, 5), ("4019", "25000"))
LINES = (
    Line(1, "99213", "C", Decimal("0.00"), Decimal("81.25")),
    Line(2, "71020", "A", Decimal("40.00"), Decimal("55.50")),
    Line(3, None, "N", Decimal("0.00"), Decimal("17.30")),
)


def _label(claim_id: str, proxy: bool = True) -> ClaimLabel:
    return ClaimLabel(
        source_claim_id=claim_id,
        is_denied=True,
        denial_reason_category=DenialReasonCategory.NONCOVERED,
        appeal_success_proxy=proxy,
        label_rule_version=LABEL_RULE_VERSION,
    )


def _document(document_type: DocumentType, claim_id: str = CLAIM_ID) -> Document:
    """The clean document of one made-up claim, written by the real generator."""
    claim = replace(CLAIM, source_claim_id=claim_id)
    if document_type is DocumentType.DENIAL_LETTER:
        return generate_denial_letter(claim, LINES, _label(claim_id), SEED)
    if document_type is DocumentType.CLINICAL_NOTE:
        return generate_clinical_note(claim, LINES, _label(claim_id), SEED)
    return generate_prior_auth(claim, LINES, _label(claim_id), SEED)


def _noisy(document: Document, seed: int = SEED) -> NoisyText:
    return apply_noise(
        document.text,
        document.answer_key,
        document.document_type,
        document.source_claim_id,
        seed,
    )


def _noise_draw(document_type: DocumentType, claim_id: str, choice: str) -> Fraction:
    """The documented draw of one noise choice, written out by hand."""
    return repeatable_draw(
        f"doc:{NOISE_VERSION}:{SEED}:{document_type.value}:{claim_id}:noise_{choice}"
    )


def _documented_level(document_type: DocumentType, claim_id: str) -> NoiseLevel:
    draw = _noise_draw(document_type, claim_id, "level")
    if draw < Fraction(20, 100):
        return NoiseLevel.NONE
    if draw < Fraction(70, 100):
        return NoiseLevel.LIGHT
    return NoiseLevel.HEAVY


def _documented_missing_field(document_type: DocumentType, claim_id: str) -> str | None:
    if _noise_draw(document_type, claim_id, "missing") >= Fraction(10, 100):
        return None
    fields = MISSING_FIELDS[document_type]
    return fields[int(_noise_draw(document_type, claim_id, "missing_field") * len(fields))]


def _claim_ids() -> list[str]:
    return [str(FIRST_CLAIM_NUMBER + n) for n in range(CLAIMS_TO_SEARCH)]


def _claim_id_with(
    document_type: DocumentType, level: NoiseLevel, missing_field: str | None = None
) -> str:
    """The first made-up claim whose documented draws give this level and missing field."""
    for claim_id in _claim_ids():
        if (
            _documented_level(document_type, claim_id) is level
            and _documented_missing_field(document_type, claim_id) == missing_field
        ):
            return claim_id
    raise AssertionError(f"no claim gives {level.value} with missing field {missing_field}")


PINNED_CLAIM_ID = _claim_id_with(DocumentType.DENIAL_LETTER, NoiseLevel.HEAVY, "reference_number")
FIELD_AND_TYPE_PAIRS = [
    (document_type, field) for document_type, fields in MISSING_FIELDS.items() for field in fields
]


def _printed_forms(value: date | str) -> list[str]:
    if isinstance(value, date):
        return [long_date(value), us_date(value), iso_date(value)]
    return [value]


# --- repeatable ---


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_same_document_and_seed_give_the_identical_noise(document_type: DocumentType) -> None:
    claim_id = _claim_id_with(document_type, NoiseLevel.HEAVY)

    assert _noisy(_document(document_type, claim_id)) == _noisy(_document(document_type, claim_id))


def test_a_different_seed_gives_different_noise_on_the_same_clean_text() -> None:
    documents = [_document(DocumentType.DENIAL_LETTER, claim_id) for claim_id in _claim_ids()[:30]]

    first = [_noisy(document, SEED) for document in documents]
    second = [_noisy(document, SEED + 1) for document in documents]

    assert [noisy.record.level for noisy in first] != [noisy.record.level for noisy in second]
    assert [noisy.text for noisy in first] != [noisy.text for noisy in second]


def test_the_documents_of_one_claim_get_different_noise() -> None:
    levels = {
        document_type: [
            _noisy(_document(document_type, claim_id)).record.level
            for claim_id in _claim_ids()[:30]
        ]
        for document_type in (DocumentType.DENIAL_LETTER, DocumentType.CLINICAL_NOTE)
    }

    assert levels[DocumentType.DENIAL_LETTER] != levels[DocumentType.CLINICAL_NOTE]


def test_a_full_noisy_text_is_pinned_to_the_noise_version() -> None:
    noisy = _noisy(_document(DocumentType.DENIAL_LETTER, PINNED_CLAIM_ID))

    assert NOISE_VERSION == "v1"
    assert noisy.record.level is NoiseLevel.HEAVY
    assert noisy.record.missing_field == "reference_number"
    assert noisy.record.swaps > 0
    assert noisy.record.drops > 0
    assert hashlib.sha256(noisy.text.encode()).hexdigest() == PINNED_NOISY_TEXT_SHA256


# --- the level ---


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_the_level_is_a_draw_of_the_documented_text(document_type: DocumentType) -> None:
    for claim_id in _claim_ids()[:20]:
        noisy = _noisy(_document(document_type, claim_id))

        assert noisy.record.level is _documented_level(document_type, claim_id)


def test_levels_and_missing_fields_come_in_the_documented_shares() -> None:
    key = _document(DocumentType.CLINICAL_NOTE).answer_key
    text = f"Member ID: {key.member_id}"

    records = [
        apply_noise(text, key, DocumentType.CLINICAL_NOTE, claim_id, SEED).record
        for claim_id in _claim_ids()
    ]

    def share(level: NoiseLevel) -> float:
        return sum(record.level is level for record in records) / len(records)

    assert share(NoiseLevel.NONE) == pytest.approx(0.20, abs=0.03)
    assert share(NoiseLevel.LIGHT) == pytest.approx(0.50, abs=0.03)
    assert share(NoiseLevel.HEAVY) == pytest.approx(0.30, abs=0.03)
    missing = sum(record.missing_field is not None for record in records) / len(records)
    assert missing == pytest.approx(0.10, abs=0.02)


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_a_none_document_that_loses_no_field_keeps_its_clean_text(
    document_type: DocumentType,
) -> None:
    document = _document(document_type, _claim_id_with(document_type, NoiseLevel.NONE))

    noisy = _noisy(document)

    assert noisy.text == document.text
    assert noisy.record.model_dump() == {
        "level": NoiseLevel.NONE,
        "swaps": 0,
        "drops": 0,
        "missing_field": None,
        "page_width": None,
    }


# --- missing fields ---


@pytest.mark.parametrize(("document_type", "field"), FIELD_AND_TYPE_PAIRS)
def test_a_missing_field_is_blanked_wherever_it_is_printed_and_nothing_else_changes(
    document_type: DocumentType, field: str
) -> None:
    document = _document(document_type, _claim_id_with(document_type, NoiseLevel.NONE, field))
    expected = document.text
    for form in _printed_forms(getattr(document.answer_key, field)):
        expected = expected.replace(form, "")

    noisy = _noisy(document)

    assert noisy.record.missing_field == field
    assert noisy.record.level is NoiseLevel.NONE
    assert noisy.text == expected
    assert len(noisy.text) < len(document.text)
    assert noisy.text.count("\n") == document.text.count("\n")


@pytest.mark.parametrize(("document_type", "field"), FIELD_AND_TYPE_PAIRS)
def test_a_missing_field_is_blanked_before_the_character_errors(
    document_type: DocumentType, field: str
) -> None:
    document = _document(document_type, _claim_id_with(document_type, NoiseLevel.HEAVY, field))

    noisy = _noisy(document)

    assert noisy.record.missing_field == field
    for form in _printed_forms(getattr(document.answer_key, field)):
        assert form not in noisy.text


def test_the_missing_field_is_a_draw_of_the_documented_text() -> None:
    for claim_id in _claim_ids()[:60]:
        noisy = _noisy(_document(DocumentType.PRIOR_AUTH, claim_id))

        assert noisy.record.missing_field == _documented_missing_field(
            DocumentType.PRIOR_AUTH, claim_id
        )


def test_noise_does_not_change_the_answer_key() -> None:
    document = _document(DocumentType.DENIAL_LETTER, PINNED_CLAIM_ID)
    saved_before = document.answer_key.model_dump_json()

    _noisy(document)

    assert document.answer_key.model_dump_json() == saved_before


def test_rejects_a_text_that_does_not_print_the_field_to_blank() -> None:
    claim_id = _claim_id_with(DocumentType.CLINICAL_NOTE, NoiseLevel.NONE, "member_id")
    key = _document(DocumentType.CLINICAL_NOTE, claim_id).answer_key

    with pytest.raises(ValueError, match="member_id is not printed"):
        apply_noise("A note without the id.", key, DocumentType.CLINICAL_NOTE, claim_id, SEED)


def test_rejects_an_answer_key_of_another_document_type() -> None:
    note = _document(DocumentType.CLINICAL_NOTE)

    with pytest.raises(ValueError, match="reference_number"):
        apply_noise(note.text, note.answer_key, DocumentType.DENIAL_LETTER, CLAIM_ID, SEED)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_outside_the_stored_range(seed: int) -> None:
    with pytest.raises(ValueError, match="seed must be from 0"):
        _noisy(_document(DocumentType.CLINICAL_NOTE), seed)


@pytest.mark.parametrize("seed", [0, MAX_SEED])
def test_accepts_the_lowest_and_highest_seed(seed: int) -> None:
    assert _noisy(_document(DocumentType.CLINICAL_NOTE), seed).text


# --- layout ---


def _light_note_with_broken_lines_and_no_character_error() -> tuple[ClinicalNote, NoisyText]:
    for claim_id in _claim_ids():
        if _documented_level(DocumentType.CLINICAL_NOTE, claim_id) is not NoiseLevel.LIGHT:
            continue
        note = generate_clinical_note(
            replace(CLAIM, source_claim_id=claim_id), LINES, _label(claim_id), SEED
        )
        noisy = _noisy(note)
        if (
            noisy.record.swaps == 0
            and noisy.record.drops == 0
            and noisy.record.missing_field is None
            and noisy.text.count("\n") > note.text.count("\n")
        ):
            return note, noisy
    raise AssertionError("no light note with a broken line and no character error")


def test_layout_damage_keeps_every_word_in_order() -> None:
    note, noisy = _light_note_with_broken_lines_and_no_character_error()

    assert noisy.text != note.text
    assert noisy.text.split() == note.text.split()


def test_lines_are_broken_at_the_drawn_page_width() -> None:
    note, noisy = _light_note_with_broken_lines_and_no_character_error()
    width = noisy.record.page_width

    assert width is not None
    assert max(len(line) for line in note.text.split("\n")) > width
    assert max(len(line) for line in noisy.text.split("\n")) <= width


def test_the_page_width_is_a_draw_of_the_documented_text() -> None:
    claim_id = _claim_id_with(DocumentType.DENIAL_LETTER, NoiseLevel.LIGHT)
    draw = _noise_draw(DocumentType.DENIAL_LETTER, claim_id, "page_width")

    noisy = _noisy(_document(DocumentType.DENIAL_LETTER, claim_id))

    assert noisy.record.page_width == 60 + int(draw * 41)


def test_every_page_width_is_from_60_to_100() -> None:
    widths = {
        _noisy(_document(DocumentType.CLINICAL_NOTE, claim_id)).record.page_width
        for claim_id in _claim_ids()[:300]
    } - {None}

    assert (MIN_PAGE_WIDTH, MAX_PAGE_WIDTH) == (60, 100)
    assert min(width for width in widths if width is not None) >= 60
    assert max(width for width in widths if width is not None) <= 100
    assert len(widths) > 30


@pytest.mark.parametrize("level", [NoiseLevel.LIGHT, NoiseLevel.HEAVY])
@pytest.mark.parametrize("document_type", list(DocumentType))
def test_a_damaged_document_has_no_run_of_spaces(
    document_type: DocumentType, level: NoiseLevel
) -> None:
    table_style = 0
    for claim_id in [c for c in _claim_ids() if _documented_level(document_type, c) is level][:8]:
        document = _document(document_type, claim_id)
        table_style += "  " in document.text

        assert "  " not in _noisy(document).text

    assert table_style > 0


def test_a_word_longer_than_the_page_stays_whole() -> None:
    claim_id = _claim_id_with(DocumentType.CLINICAL_NOTE, NoiseLevel.LIGHT, "member_id")
    key = _document(DocumentType.CLINICAL_NOTE, claim_id).answer_key
    long_word = "x" * 150
    text = f"ID {key.member_id}\n{long_word} end"

    noisy = apply_noise(text, key, DocumentType.CLINICAL_NOTE, claim_id, SEED)

    # `x` is not a look-alike, and a drop would shorten the word, so allow for drops only.
    assert noisy.text.split("\n")[1].strip("x") == ""
    assert len(noisy.text.split("\n")[1]) + noisy.record.drops >= 150
    assert noisy.text.split("\n")[2] == "end"


# --- character errors ---


def _never(choice: str) -> Fraction:
    return Fraction(0)


def test_rate_zero_returns_the_text_unchanged() -> None:
    text = _document(DocumentType.DENIAL_LETTER).text

    assert damage_characters(text, Fraction(0), Fraction(0), _never) == (text, 0, 0)


def test_a_swap_turns_a_look_alike_into_its_partner_and_touches_nothing_else() -> None:
    text = "0O 1l I 5S 8B 2Z 6G\nabc, xyz."

    noisy, swaps, drops = damage_characters(text, Fraction(1), Fraction(0), _never)

    assert noisy == "O0 l1 l S5 B8 Z2 G6\nabc, xyz."
    assert (swaps, drops) == (13, 0)


def test_the_look_alike_table_is_the_documented_one() -> None:
    assert LOOK_ALIKES == {
        "0": "O",
        "O": "0",
        "1": "l",
        "l": "1",
        "I": "l",
        "5": "S",
        "S": "5",
        "8": "B",
        "B": "8",
        "2": "Z",
        "Z": "2",
        "6": "G",
        "G": "6",
    }


def test_a_drop_never_removes_a_space_or_a_line_break() -> None:
    noisy, swaps, drops = damage_characters("ab c0\n d", Fraction(1), Fraction(1), _never)

    assert noisy == " \n "
    assert (swaps, drops) == (0, 5)


def test_each_character_is_tested_by_its_own_named_draw() -> None:
    asked: list[str] = []

    def draw(choice: str) -> Fraction:
        asked.append(choice)
        return Fraction(1, 2)

    damage_characters("a0 \nS", Fraction(0), Fraction(0), draw)

    assert asked == ["drop_0", "drop_1", "swap_1", "drop_4", "swap_4"]


def test_a_character_is_changed_only_when_its_draw_is_below_the_rate() -> None:
    def draw(choice: str) -> Fraction:
        return {"drop_1": Fraction(1, 1000), "swap_2": Fraction(1, 100)}.get(choice, Fraction(1, 2))

    below = damage_characters("a000", Fraction(2, 100), Fraction(2, 1000), draw)
    at_the_rate = damage_characters("a000", Fraction(1, 100), Fraction(1, 1000), draw)

    assert below == ("aO0", 1, 1)
    assert at_the_rate == ("a000", 0, 0)


@pytest.mark.parametrize("level", [NoiseLevel.LIGHT, NoiseLevel.HEAVY])
def test_character_errors_change_the_length_by_exactly_the_drops(level: NoiseLevel) -> None:
    claim_id = _claim_id_with(DocumentType.CLINICAL_NOTE, level)
    key = _document(DocumentType.CLINICAL_NOTE, claim_id).answer_key
    text = "\n".join(["O0O0 lIl1 S5S5 B8B8 Z2Z2 G6G6 plain words"] * 300)

    noisy = apply_noise(text, key, DocumentType.CLINICAL_NOTE, claim_id, SEED)

    assert noisy.record.swaps > 0
    assert noisy.record.drops > 0
    assert len(noisy.text) == len(text) - noisy.record.drops
    assert noisy.text.count("\n") == text.count("\n")


def test_heavy_noise_makes_more_errors_than_light_noise() -> None:
    key = _document(DocumentType.CLINICAL_NOTE).answer_key
    text = "\n".join(["O0O0 lIl1 S5S5 B8B8 Z2Z2 G6G6 plain words"] * 300)
    errors = {}
    for level in (NoiseLevel.LIGHT, NoiseLevel.HEAVY):
        claim_id = _claim_id_with(DocumentType.CLINICAL_NOTE, level)
        record = apply_noise(text, key, DocumentType.CLINICAL_NOTE, claim_id, SEED).record
        errors[level] = record.swaps + record.drops

    assert errors[NoiseLevel.HEAVY] > errors[NoiseLevel.LIGHT]


# --- nothing of the label leaks ---


def test_the_record_can_be_saved_as_json_with_exactly_its_fields() -> None:
    saved = _noisy(_document(DocumentType.DENIAL_LETTER, PINNED_CLAIM_ID)).record.model_dump(
        mode="json"
    )

    assert set(saved) == {"level", "swaps", "drops", "missing_field", "page_width"}
    assert saved["level"] == "heavy"
    assert saved["missing_field"] == "reference_number"
    assert 60 <= saved["page_width"] <= 100


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_the_record_never_mentions_the_proxy_the_chance_the_band_the_split_or_the_reason(
    document_type: DocumentType,
) -> None:
    for claim_id in _claim_ids()[:40]:
        saved = _noisy(_document(document_type, claim_id)).record.model_dump_json().lower()

        for word in ("proxy", "chance", "band", "split", "train", "validation", "test", "reason"):
            assert word not in saved
        for category in DenialReasonCategory:
            assert category.value not in saved


def test_noise_is_the_same_whatever_the_denial_reason_in_the_answer_key() -> None:
    letter = _document(DocumentType.DENIAL_LETTER, PINNED_CLAIM_ID)
    assert isinstance(letter, DenialLetter)
    other_reason = letter.answer_key.model_copy(
        update={"denial_reason_category": DenialReasonCategory.DUPLICATE}
    )

    noisy = apply_noise(
        letter.text, other_reason, DocumentType.DENIAL_LETTER, PINNED_CLAIM_ID, SEED
    )

    assert noisy == _noisy(letter)
