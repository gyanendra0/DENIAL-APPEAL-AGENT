from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.draws import repeatable_draw
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.denial_letter import generate_denial_letter
from src.synth.identity import (
    FIRST_NAMES,
    LAST_NAMES,
    MAX_SEED,
    PAYER_NAMES,
    PROVIDER_NAMES,
    ClaimIdentity,
    check_seed,
    claim_identity,
    document_draw,
    made_up_code,
    pick,
)

CLAIM_ID = "800000000000001"
OTHER_CLAIM_ID = "800000000000002"
SEED = 42
SEEDS_TO_SEARCH = 200
# Just below 1: the highest value a draw can come close to.
HIGHEST_DRAW = Fraction(2**256 - 1, 2**256)


@dataclass(frozen=True)
class Claim:
    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    line_number: int
    hcpcs_code: str | None
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


def _letter_draw(choice: str, claim_id: str = CLAIM_ID, seed: int = SEED) -> Fraction:
    return repeatable_draw(f"doc:v1:{seed}:denial_letter:{claim_id}:{choice}")


def test_same_claim_and_seed_give_the_same_identity() -> None:
    assert claim_identity(CLAIM_ID, SEED) == claim_identity(CLAIM_ID, SEED)


def test_a_different_claim_gives_a_different_identity() -> None:
    assert claim_identity(CLAIM_ID, SEED) != claim_identity(OTHER_CLAIM_ID, SEED)


def test_a_different_seed_gives_a_different_identity() -> None:
    assert claim_identity(CLAIM_ID, SEED) != claim_identity(CLAIM_ID, SEED + 1)


def test_each_identity_choice_is_a_draw_of_the_denial_letters_v1_text() -> None:
    identity = claim_identity(CLAIM_ID, SEED)

    first_name = FIRST_NAMES[int(_letter_draw("first_name") * len(FIRST_NAMES))]
    last_name = LAST_NAMES[int(_letter_draw("last_name") * len(LAST_NAMES))]
    assert identity.patient_name == f"{first_name} {last_name}"
    assert identity.member_id == made_up_code(_letter_draw("member_id"), 3, 8)
    assert (
        identity.provider_name
        == PROVIDER_NAMES[int(_letter_draw("provider_name") * len(PROVIDER_NAMES))]
    )
    assert identity.payer_name == PAYER_NAMES[int(_letter_draw("payer_name") * len(PAYER_NAMES))]


def test_the_denial_letter_carries_the_shared_identity() -> None:
    claim = Claim(CLAIM_ID, date(2009, 3, 3), date(2009, 3, 3), ("4019",))
    lines = [Line(1, "99213", "C", Decimal("0.00"), Decimal("81.25"))]
    label = ClaimLabel(
        source_claim_id=CLAIM_ID,
        is_denied=True,
        denial_reason_category=DenialReasonCategory.NONCOVERED,
        appeal_success_proxy=False,
        label_rule_version=LABEL_RULE_VERSION,
    )

    key = generate_denial_letter(claim, lines, label, SEED).answer_key

    assert claim_identity(CLAIM_ID, SEED) == ClaimIdentity(
        patient_name=key.patient_name,
        member_id=key.member_id,
        provider_name=key.provider_name,
        payer_name=key.payer_name,
    )


def test_identity_values_come_from_the_word_lists_and_the_member_id_pattern() -> None:
    for seed in range(SEEDS_TO_SEARCH):
        identity = claim_identity(CLAIM_ID, seed)
        first_name, last_name = identity.patient_name.split()
        assert first_name in FIRST_NAMES
        assert last_name in LAST_NAMES
        assert identity.provider_name in PROVIDER_NAMES
        assert identity.payer_name in PAYER_NAMES
        assert len(identity.member_id) == 11
        assert identity.member_id[:3].isalpha() and identity.member_id[:3].isupper()
        assert identity.member_id[3:].isdigit()


def test_document_draw_is_a_draw_of_the_documented_text() -> None:
    draw = document_draw("v7", SEED, DocumentType.CLINICAL_NOTE, CLAIM_ID, "template")

    assert draw == repeatable_draw(f"doc:v7:{SEED}:clinical_note:{CLAIM_ID}:template")


def test_pick_covers_the_first_and_the_last_option() -> None:
    options = ("a", "b", "c")

    assert pick(options, Fraction(0)) == "a"
    assert pick(options, HIGHEST_DRAW) == "c"


def test_made_up_code_is_letters_then_digits_at_both_ends_of_the_draw() -> None:
    assert made_up_code(Fraction(0), 3, 8) == "AAA00000000"
    assert made_up_code(HIGHEST_DRAW, 3, 8) == "ZZZ99999999"


def test_made_up_code_without_letters_is_digits_only() -> None:
    assert made_up_code(Fraction(0), 0, 8) == "00000000"
    assert made_up_code(HIGHEST_DRAW, 0, 8) == "99999999"


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_outside_the_stored_range(seed: int) -> None:
    with pytest.raises(ValueError, match="seed must be from 0 to"):
        check_seed(seed)
    with pytest.raises(ValueError, match="seed must be from 0 to"):
        claim_identity(CLAIM_ID, seed)


@pytest.mark.parametrize("seed", [0, MAX_SEED])
def test_accepts_the_lowest_and_highest_seed(seed: int) -> None:
    check_seed(seed)

    assert claim_identity(CLAIM_ID, seed).patient_name
