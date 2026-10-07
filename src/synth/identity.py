"""The made-up identity shared by every generated document of one claim.

A claim's denial letter, clinical note and prior-authorisation record must describe the same
patient, member id, provider and payer. Each generator therefore asks `claim_identity` instead
of drawing its own. Every value is made up from the small word lists below. No real patient,
provider or insurer appears in a document.

The identity was first drawn inside the denial letter generator, version `v1`. To keep every
existing letter byte-identical, the identity is still drawn from the letter's text
`doc:v1:<seed>:denial_letter:<claim id>:<choice>`, whichever document asks. A change to a word
list, to an identity choice or to that text changes every document type, so each generator
then needs a new version.

The helpers every generator uses for its own choices (`document_draw`, `pick`, `made_up_code`,
`check_seed`) are here too.
"""

from collections.abc import Sequence
from fractions import Fraction
from string import ascii_uppercase
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType
from src.ml.draws import repeatable_draw

T = TypeVar("T")

MAX_SEED = 2**31 - 1  # the seed is stored in an integer column

# The identity draw text is frozen at the denial letter's v1. These two never follow a
# generator's own version or document type.
IDENTITY_KEY_VERSION = "v1"
IDENTITY_KEY_DOCUMENT_TYPE = DocumentType.DENIAL_LETTER

MEMBER_ID_LETTERS = 3
MEMBER_ID_DIGITS = 8

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


class ClaimIdentity(BaseModel):
    """The four made-up values that every document of one claim shares."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patient_name: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    payer_name: str = Field(min_length=1)


def claim_identity(claim_id: str, seed: int) -> ClaimIdentity:
    """Return the made-up identity of one claim. The same claim and seed always give the same
    identity, whichever document type asks."""
    check_seed(seed)

    def draw(choice: str) -> Fraction:
        return document_draw(
            IDENTITY_KEY_VERSION, seed, IDENTITY_KEY_DOCUMENT_TYPE, claim_id, choice
        )

    return ClaimIdentity(
        patient_name=(
            f"{pick(FIRST_NAMES, draw('first_name'))} {pick(LAST_NAMES, draw('last_name'))}"
        ),
        member_id=made_up_code(draw("member_id"), MEMBER_ID_LETTERS, MEMBER_ID_DIGITS),
        provider_name=pick(PROVIDER_NAMES, draw("provider_name")),
        payer_name=pick(PAYER_NAMES, draw("payer_name")),
    )


def check_seed(seed: int) -> None:
    """Raise `ValueError` unless the seed fits the stored integer column."""
    if not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be from 0 to {MAX_SEED}, got {seed}")


def document_draw(
    generator_version: str, seed: int, document_type: DocumentType, claim_id: str, choice: str
) -> Fraction:
    """The repeatable draw of one named choice of one document: a number from 0 up to, but
    not including, 1."""
    return repeatable_draw(
        f"doc:{generator_version}:{seed}:{document_type.value}:{claim_id}:{choice}"
    )


def pick(options: Sequence[T], draw: Fraction) -> T:
    """Turn one draw into one of the options, each equally likely."""
    return options[int(draw * len(options))]


def made_up_code(draw: Fraction, letters: int, digits: int) -> str:
    """Turn one draw into `letters` capital letters followed by `digits` digits."""
    number = int(draw * len(ascii_uppercase) ** letters * 10**digits)
    number, digit_part = divmod(number, 10**digits)
    chars: list[str] = []
    for _ in range(letters):
        number, index = divmod(number, len(ascii_uppercase))
        chars.append(ascii_uppercase[index])
    return "".join(chars) + f"{digit_part:0{digits}d}"
