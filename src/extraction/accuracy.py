"""Counts how many extracted values equal the answer key exactly.

A small count for comparing two prompt versions. It is not the accuracy gate: that, the
accuracy per noise level and the confidence against correctness belong to the evaluation
harness.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType
from src.extraction.schemas import ExtractionSchema, schema_for
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument, text_sha256
from src.synth.clinical_note import ClinicalNoteAnswerKey
from src.synth.denial_letter import DenialLetterAnswerKey
from src.synth.prior_auth import PriorAuthAnswerKey

AnswerKey = DenialLetterAnswerKey | ClinicalNoteAnswerKey | PriorAuthAnswerKey

_ANSWER_KEYS: Mapping[DocumentType, type[AnswerKey]] = {
    DocumentType.DENIAL_LETTER: DenialLetterAnswerKey,
    DocumentType.CLINICAL_NOTE: ClinicalNoteAnswerKey,
    DocumentType.PRIOR_AUTH: PriorAuthAnswerKey,
}


class FieldMatchCount(BaseModel):
    """How many documents were compared on one field, and how many matched."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matched: int = Field(ge=0)
    compared: int = Field(ge=0)


class FieldMatchReport(BaseModel):
    """The exact-match count per document type and field.

    `documents` is how many documents of each type were compared; every type is listed, a
    type with no compared document as 0 and with no fields. `left_out` is how many of the
    documents given had no result for the text they have now: they are in no count, so two
    reports with different `left_out` numbers do not compare the same documents.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: dict[DocumentType, int]
    fields: dict[DocumentType, dict[str, FieldMatchCount]]
    left_out: int = Field(ge=0)

    def as_text(self) -> str:
        """The report as printed lines. It holds counts and field names only, no value."""
        lines = [
            f"exact match with the answer keys ({sum(self.documents.values())} documents"
            " with a result):",
            f"  {self.left_out} selected documents have no current result and are left out",
        ]
        for document_type, documents in self.documents.items():
            counts = self.fields[document_type]
            matched = sum(count.matched for count in counts.values())
            compared = sum(count.compared for count in counts.values())
            lines.append(
                f"  {document_type.value} ({documents} documents): "
                f"{matched} of {compared} values"
            )
            lines.extend(
                f"    {name}: {count.matched} of {count.compared}" for name, count in counts.items()
            )
        return "\n".join(lines)


def match_fields(document: StoredDocument, extraction: ExtractionSchema) -> dict[str, bool]:
    """Say for each field of `document` whether `extraction` has exactly the right value.

    The right value is the answer key's, or null for the field the noise step blanked.
    Amounts are compared as numbers, so 130.1 equals 130.10; text must be the same
    character for character; a list must have the same entries in the same order.
    """
    answer_key = _ANSWER_KEYS[document.document_type].model_validate(document.answer_key)
    matches: dict[str, bool] = {}
    for name in type(answer_key).model_fields:
        expected = None if name == document.missing_field else getattr(answer_key, name)
        matches[name] = _plain(getattr(extraction, name).value) == _plain(expected)
    return matches


def count_field_matches(
    documents: Iterable[StoredDocument], rows: Mapping[DocumentKey, DocumentExtractionRow]
) -> FieldMatchReport:
    """Count the exact matches of `rows` against the answer keys of `documents`.

    A document with no row, or with a row made from another text than the one it has now,
    is left out and counted in `left_out`.
    """
    compared = dict.fromkeys(DocumentType, 0)
    matched: dict[DocumentType, dict[str, int]] = {kind: {} for kind in DocumentType}
    left_out = 0
    for document in documents:
        row = rows.get(document.key)
        if row is None or row.text_sha256 != text_sha256(document.text):
            left_out += 1
            continue
        kind = document.document_type
        extraction = schema_for(kind).model_validate(row.fields)
        compared[kind] += 1
        for name, is_match in match_fields(document, extraction).items():
            matched[kind][name] = matched[kind].get(name, 0) + is_match
    return FieldMatchReport(
        documents=compared,
        fields={
            kind: {
                name: FieldMatchCount(matched=count, compared=compared[kind])
                for name, count in matched[kind].items()
            }
            for kind in DocumentType
        },
        left_out=left_out,
    )


def _plain(value: Any) -> Any:
    """Turn models into dicts, so an extracted denied line can equal the answer key's."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, tuple):
        return tuple(_plain(entry) for entry in value)
    return value
