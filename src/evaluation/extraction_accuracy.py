"""Measures how many extracted values equal the answer key exactly.

This is the extraction half of the Stage 3 done condition (docs/05-build-plan.md): field
extraction accuracy of at least 85%. The truth is the answer key, which holds the values
from before the noise step, so a value the noise damaged and the model copied faithfully
counts as wrong. Everything here is generated data: made-up documents of the synthetic
claims sample.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.db.models import DocumentType
from src.extraction.accuracy import match_fields
from src.extraction.schemas import schema_for
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument, text_sha256
from src.synth.noise import NoiseLevel

# The Stage 3 done condition for extraction (docs/05-build-plan.md).
TARGET_FIELD_ACCURACY = Decimal("0.85")

# The denial letter's field that names why the claim was denied; reported on its own line.
HEADLINE_REASON_FIELD = "denial_reason_category"


class ConfidenceBand(StrEnum):
    """The range a model's own confidence score for one value falls in."""

    BELOW_080 = "below 0.80"
    FROM_080 = "0.80 to 0.89"
    FROM_090 = "0.90 to 0.94"
    FROM_095 = "0.95 to 0.99"
    EXACTLY_1 = "1.00"


# The lowest score of each band, highest band first.
_BAND_FLOORS: tuple[tuple[float, ConfidenceBand], ...] = (
    (1.0, ConfidenceBand.EXACTLY_1),
    (0.95, ConfidenceBand.FROM_095),
    (0.90, ConfidenceBand.FROM_090),
    (0.80, ConfidenceBand.FROM_080),
)


class MatchCount(BaseModel):
    """How many values were compared with the answer key, and how many were exactly right."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matched: int = Field(ge=0)
    compared: int = Field(ge=0)

    @model_validator(mode="after")
    def _matched_fits_compared(self) -> "MatchCount":
        if self.matched > self.compared:
            raise ValueError("matched is more than compared")
        return self

    @property
    def share(self) -> Decimal | None:
        """The matched share as an exact decimal, or None when nothing was compared."""
        if self.compared == 0:
            return None
        return Decimal(self.matched) / Decimal(self.compared)


class TypeAccuracy(BaseModel):
    """The exact-match counts of one document type.

    `with_result_only` and `fields` count the documents with a current result.
    `all_documents` also has the values of the documents without one, as wrong.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: int = Field(ge=0)
    with_result: int = Field(ge=0)
    with_result_only: MatchCount
    all_documents: MatchCount
    fields: dict[str, MatchCount]


class ExtractionAccuracyReport(BaseModel):
    """Exact match of the stored extraction results against the answer keys.

    A document has a current result, no result, or a stale one (made from another text
    than the one the document has now, so it says nothing about that text). `all_documents`
    is the number the 85% target is checked against: every value of a document with no
    current result is in it as wrong. `with_result_only` leaves those documents out, so
    it rises when more documents fail; it is reported, never the gate.

    `headline_reason` is the denial letters' `denial_reason_category` over all letters, a
    letter with no current result as wrong. `by_noise_level` and `by_confidence` count
    the values of the documents with a current result; every level and band is listed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: int = Field(ge=0)
    with_result: int = Field(ge=0)
    no_result: int = Field(ge=0)
    stale: int = Field(ge=0)
    with_result_only: MatchCount
    all_documents: MatchCount
    by_type: dict[DocumentType, TypeAccuracy]
    by_noise_level: dict[NoiseLevel, MatchCount]
    headline_reason: MatchCount
    by_confidence: dict[ConfidenceBand, MatchCount]


class _Tally:
    """A matched and a compared count that grow while the documents are read."""

    def __init__(self) -> None:
        self.matched = 0
        self.compared = 0

    def add(self, matched: int, compared: int) -> None:
        self.matched += matched
        self.compared += compared

    def count(self) -> MatchCount:
        return MatchCount(matched=self.matched, compared=self.compared)


def confidence_band(confidence: float) -> ConfidenceBand:
    """Return the band the score `confidence` (0 to 1) falls in."""
    for floor, band in _BAND_FLOORS:
        if confidence >= floor:
            return band
    return ConfidenceBand.BELOW_080


def build_extraction_accuracy_report(
    documents: Iterable[StoredDocument], rows: Mapping[DocumentKey, DocumentExtractionRow]
) -> ExtractionAccuracyReport:
    """Count the exact matches of `rows` against the answer keys of `documents`.

    A value is right when it equals the answer key's, or is null for the field the noise
    step blanked (the rule of `match_fields`). A document with no row, or with a row whose
    `text_sha256` is not that of its text, has every field counted as wrong in
    `all_documents`. A row for a document that is not in `documents` is ignored.
    """
    total = with_result = no_result = stale = 0
    type_documents = dict.fromkeys(DocumentType, 0)
    type_with_result = dict.fromkeys(DocumentType, 0)
    type_scored = {kind: _Tally() for kind in DocumentType}
    type_all = {kind: _Tally() for kind in DocumentType}
    fields: dict[DocumentType, dict[str, _Tally]] = {kind: {} for kind in DocumentType}
    levels = {level: _Tally() for level in NoiseLevel}
    bands = {band: _Tally() for band in ConfidenceBand}
    headline = _Tally()

    for document in documents:
        kind = document.document_type
        total += 1
        type_documents[kind] += 1
        row = rows.get(document.key)
        if row is None or row.text_sha256 != text_sha256(document.text):
            if row is None:
                no_result += 1
            else:
                stale += 1
            type_all[kind].add(0, len(schema_for(kind).model_fields))
            if kind is DocumentType.DENIAL_LETTER:
                headline.add(0, 1)
            continue

        extraction = schema_for(kind).model_validate(row.fields)
        matches = match_fields(document, extraction)
        right = sum(matches.values())
        with_result += 1
        type_with_result[kind] += 1
        type_scored[kind].add(right, len(matches))
        type_all[kind].add(right, len(matches))
        levels[document.noise_level].add(right, len(matches))
        for name, is_match in matches.items():
            fields[kind].setdefault(name, _Tally()).add(is_match, 1)
            bands[confidence_band(getattr(extraction, name).confidence)].add(is_match, 1)
        if kind is DocumentType.DENIAL_LETTER:
            headline.add(matches[HEADLINE_REASON_FIELD], 1)

    return ExtractionAccuracyReport(
        documents=total,
        with_result=with_result,
        no_result=no_result,
        stale=stale,
        with_result_only=_total(type_scored.values()),
        all_documents=_total(type_all.values()),
        by_type={
            kind: TypeAccuracy(
                documents=type_documents[kind],
                with_result=type_with_result[kind],
                with_result_only=type_scored[kind].count(),
                all_documents=type_all[kind].count(),
                fields={name: tally.count() for name, tally in fields[kind].items()},
            )
            for kind in DocumentType
        },
        by_noise_level={level: tally.count() for level, tally in levels.items()},
        headline_reason=headline.count(),
        by_confidence={band: tally.count() for band, tally in bands.items()},
    )


def _total(tallies: Iterable[_Tally]) -> MatchCount:
    counts = [tally.count() for tally in tallies]
    return MatchCount(
        matched=sum(count.matched for count in counts),
        compared=sum(count.compared for count in counts),
    )
