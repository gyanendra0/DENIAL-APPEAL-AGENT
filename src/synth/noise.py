"""Seeded noise for the generated documents: what a poor scan does to a clean page.

A generator writes the clean text of a document. `apply_noise` turns it into the text that is
stored, in this order:

1. one document in ten loses one field: its value is blanked wherever it is printed, and the
   label stays, like a form field left empty;
2. a run of spaces becomes one space, which removes column alignment and indents;
3. lines longer than a drawn page width are broken at a space;
4. single characters are dropped, or swapped for a character that looks alike (`0` and `O`).

Steps 2 to 4 depend on the document's drawn noise level: `none` skips them, `light` and `heavy`
apply them at the rates below. Step 1 is its own draw, at any level.

Noise adds no name, code, amount or date, and the answer key is not changed: it stays the truth
of the clean document. What was done to the page is returned as a separate `NoiseRecord`.

Every choice is a repeatable draw of the text
`doc:<NOISE_VERSION>:<seed>:<document type>:<claim id>:noise_<choice>`, so the noise of a
document depends only on its claim, its type, the seed and the noise version. Noise never reads
the appeal-success proxy, the amount band, the dataset split or the denial reason: a model could
otherwise read its target from how damaged a page is.

A change to a rate, a share, the look-alike table, the list of fields that can go missing or a
draw text changes the stored text for the same seed, so it needs a new `NOISE_VERSION`. The
pinned hash in the tests catches it.
"""

import re
from collections.abc import Callable
from datetime import date
from enum import StrEnum
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType
from src.synth.clinical_note import ClinicalNoteAnswerKey
from src.synth.denial_letter import DenialLetterAnswerKey
from src.synth.formats import iso_date, long_date, us_date
from src.synth.identity import check_seed, document_draw, pick
from src.synth.prior_auth import PriorAuthAnswerKey

NOISE_VERSION = "v1"


class NoiseLevel(StrEnum):
    """How much scan damage one document gets."""

    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"


# The share of documents at each level, in the order the level draw is read.
LEVEL_SHARES = (
    (NoiseLevel.NONE, Fraction(20, 100)),
    (NoiseLevel.LIGHT, Fraction(50, 100)),
    (NoiseLevel.HEAVY, Fraction(30, 100)),
)
# The chance that one look-alike character is swapped for its partner.
SWAP_RATES = {NoiseLevel.LIGHT: Fraction(1, 100), NoiseLevel.HEAVY: Fraction(3, 100)}
# The chance that one character that is not a space or a line break disappears.
DROP_RATES = {NoiseLevel.LIGHT: Fraction(1, 1000), NoiseLevel.HEAVY: Fraction(5, 1000)}
LOOK_ALIKES = {
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
NEVER_DROPPED = " \n"

MIN_PAGE_WIDTH = 60
MAX_PAGE_WIDTH = 100

MISSING_FIELD_SHARE = Fraction(10, 100)
# Answer-key fields that can go missing. Each is printed in every document of its type and
# never equals or sits inside another printed value of the same document.
MISSING_FIELDS = {
    DocumentType.DENIAL_LETTER: ("member_id", "reference_number", "letter_date", "appeal_deadline"),
    DocumentType.CLINICAL_NOTE: ("member_id",),
    DocumentType.PRIOR_AUTH: ("member_id", "authorization_number", "request_date", "decision_date"),
}
# Every way a template prints a date.
DATE_STYLES = (long_date, us_date, iso_date)

AnswerKey = DenialLetterAnswerKey | ClinicalNoteAnswerKey | PriorAuthAnswerKey


class NoiseRecord(BaseModel):
    """What the noise step did to one document.

    `missing_field` is the name of the blanked answer-key field, or None. `page_width` is the
    drawn width the lines were re-wrapped to, or None at level `none`. The record does not say
    which values a swap or a drop hit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: NoiseLevel
    swaps: int = Field(ge=0)
    drops: int = Field(ge=0)
    missing_field: str | None
    page_width: int | None = Field(ge=MIN_PAGE_WIDTH, le=MAX_PAGE_WIDTH)


class NoisyText(BaseModel):
    """The text of one document after noise, and the record of what was done to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    record: NoiseRecord


def apply_noise(
    text: str, answer_key: AnswerKey, document_type: DocumentType, claim_id: str, seed: int
) -> NoisyText:
    """Turn the clean text of one document into its noisy text.

    `answer_key` is the document's own answer key; it is only read to find the printed value
    of a field that goes missing. The same text, claim, type, seed and `NOISE_VERSION` always
    give the same result.

    Raises `ValueError` if the seed is outside the stored range, if `answer_key` does not
    belong to `document_type`, or if the value of the field to blank is not printed in `text`.
    """
    check_seed(seed)
    fields = MISSING_FIELDS[document_type]
    unknown = [name for name in fields if name not in type(answer_key).model_fields]
    if unknown:
        raise ValueError(
            f"the answer key of a {document_type.value} must have {', '.join(unknown)}:"
            f" got {type(answer_key).__name__}"
        )

    def draw(choice: str) -> Fraction:
        return document_draw(NOISE_VERSION, seed, document_type, claim_id, f"noise_{choice}")

    missing_field = None
    if draw("missing") < MISSING_FIELD_SHARE:
        missing_field = pick(fields, draw("missing_field"))
        text = _blank_field(text, missing_field, getattr(answer_key, missing_field))

    level = _level(draw("level"))
    if level is NoiseLevel.NONE:
        return NoisyText(
            text=text,
            record=NoiseRecord(
                level=level, swaps=0, drops=0, missing_field=missing_field, page_width=None
            ),
        )

    page_width = MIN_PAGE_WIDTH + int(draw("page_width") * (MAX_PAGE_WIDTH - MIN_PAGE_WIDTH + 1))
    text = _wrap_lines(_collapse_spaces(text), page_width)
    text, swaps, drops = damage_characters(text, SWAP_RATES[level], DROP_RATES[level], draw)
    return NoisyText(
        text=text,
        record=NoiseRecord(
            level=level,
            swaps=swaps,
            drops=drops,
            missing_field=missing_field,
            page_width=page_width,
        ),
    )


def damage_characters(
    text: str, swap_rate: Fraction, drop_rate: Fraction, draw: Callable[[str], Fraction]
) -> tuple[str, int, int]:
    """Drop and swap single characters of `text`. Returns the new text, the number of swaps
    and the number of drops.

    Each character that is not a space or a line break is dropped with the chance `drop_rate`.
    Each look-alike character that stays is swapped for its partner with the chance
    `swap_rate`. `draw` is asked once per test, with the choice `drop_<position>` or
    `swap_<position>`, where the position counts from 0 in `text`. With both rates at 0 the
    text comes back unchanged.

    One hash per test: about 3 microseconds each, so a few milliseconds for one document.
    """
    kept: list[str] = []
    swaps = 0
    drops = 0
    for position, char in enumerate(text):
        if char not in NEVER_DROPPED and draw(f"drop_{position}") < drop_rate:
            drops += 1
            continue
        partner = LOOK_ALIKES.get(char)
        if partner is not None and draw(f"swap_{position}") < swap_rate:
            swaps += 1
            char = partner
        kept.append(char)
    return "".join(kept), swaps, drops


def _level(draw: Fraction) -> NoiseLevel:
    reached = Fraction(0)
    for level, share in LEVEL_SHARES:
        reached += share
        if draw < reached:
            return level
    raise AssertionError("the level shares must add up to 1")


def _blank_field(text: str, field: str, value: date | str) -> str:
    """Remove every printed copy of one answer-key value. A date is searched in each style a
    template can print it in; nothing is guessed."""
    printed = [show(value) for show in DATE_STYLES] if isinstance(value, date) else [value]
    found = [form for form in printed if form in text]
    if not found:
        raise ValueError(f"the value of {field} is not printed in the text")
    for form in found:
        text = text.replace(form, "")
    return text


def _collapse_spaces(text: str) -> str:
    return re.sub(" {2,}", " ", text)


def _wrap_lines(text: str, width: int) -> str:
    """Break each line longer than `width` at spaces. Every word is kept; a single word longer
    than `width` stays whole on its own line."""
    wrapped: list[str] = []
    for line in text.split("\n"):
        if len(line) <= width:
            wrapped.append(line)
            continue
        current = ""
        for index, word in enumerate(line.split(" ")):
            if index == 0:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current = f"{current} {word}"
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
    return "\n".join(wrapped)
