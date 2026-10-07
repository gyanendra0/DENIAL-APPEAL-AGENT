"""Build the generated documents of the stored claims and store them in `generated_documents`.

Every claim that is labelled denied gets a denial letter (`src.synth.denial_letter`) and a
clinical note (`src.synth.clinical_note`); a claim that `needs_prior_auth` also gets a
prior-authorisation record (`src.synth.prior_auth`). Each is written from the claim, its lines
and its label as they are stored, and then damaged by the noise step (`src.synth.noise`): the
stored text is the noisy text, the answer key stays the truth of the clean document.
`generated_documents` is a public reference table (no `account_id`), like the three tables read
here. Documents are derived data: they can always be rebuilt from the claims, the labels, the
seed, the generator versions and the noise version.
"""

import logging
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DocumentType,
    GeneratedDocument,
)
from src.ingest.marketplace_denials import BatchRejectedError, upsert_rows
from src.ml.labels import LABEL_RULE_VERSION, ClaimId, ClaimLabel, label_claim
from src.synth import clinical_note, denial_letter, prior_auth
from src.synth.clinical_note import ClinicalNote, generate_clinical_note
from src.synth.denial_letter import DenialLetter, generate_denial_letter
from src.synth.identity import MAX_SEED
from src.synth.noise import NOISE_VERSION, NoiseLevel, apply_noise
from src.synth.prior_auth import PriorAuthRecord, generate_prior_auth, needs_prior_auth

logger = logging.getLogger(__name__)

UPSERT_CONSTRAINT = "uq_generated_documents_claim_type"
UPSERT_KEY_COLUMNS = ("source_claim_id", "document_type")

# The generator version of each document type, in the order a claim's documents are built.
GENERATOR_VERSIONS = {
    DocumentType.DENIAL_LETTER: denial_letter.GENERATOR_VERSION,
    DocumentType.CLINICAL_NOTE: clinical_note.GENERATOR_VERSION,
    DocumentType.PRIOR_AUTH: prior_auth.GENERATOR_VERSION,
}


class GeneratedDocumentRow(BaseModel):
    """One generated document, shaped like the `generated_documents` table.

    `text` is the text after noise. `answer_key` is the answer key of the clean document,
    already turned into plain JSON values (dates and amounts as text), because that is what the
    JSON column stores. `noise_record` is the record of what the noise step did, as plain JSON
    values too.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    template_id: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=MAX_SEED)
    generator_version: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1)
    answer_key: dict[str, Any] = Field(min_length=1)
    noise_version: str = Field(min_length=1, max_length=20)
    noise_record: dict[str, Any] = Field(min_length=1)


def build_document_rows(session: Session, seed: int) -> list[GeneratedDocumentRow]:
    """Write the documents of every stored claim that is labelled denied.

    Each denied claim gets a denial letter and a clinical note, and a prior-authorisation
    record too if it `needs_prior_auth`. Every document then goes through the noise step.
    Reads `claim_sample_labels`, `claim_samples` and `claim_sample_lines`; writes nothing. The
    rows come back in claim id order, and for one claim in the order letter, note, record.

    Raises `BatchRejectedError` if no claim is labelled denied, if a label was made by a
    label rule other than `LABEL_RULE_VERSION` (the documents would then disagree with the
    rule in the code), or if a label no longer fits the claim's stored lines (the lines were
    loaded again after the labels): the label rule, applied to the stored lines, must give
    exactly the stored label.

    With the default load of 50,000 claims this takes about 25 seconds: the noise step makes
    one hash per character of every damaged document.
    """
    stored_labels = session.scalars(
        select(ClaimSampleLabel)
        .where(ClaimSampleLabel.is_denied)
        .order_by(ClaimSampleLabel.source_claim_id)
    ).all()
    if not stored_labels:
        raise BatchRejectedError(
            ["no claim is labelled denied: run pipelines.run_data_pipeline first"]
        )
    other_versions = sorted(
        {label.label_rule_version for label in stored_labels} - {LABEL_RULE_VERSION}
    )
    if other_versions:
        raise BatchRejectedError(
            [
                f"labels were made with rule version {', '.join(other_versions)}, the code is at"
                f" {LABEL_RULE_VERSION}: run pipelines.run_data_pipeline again"
            ]
        )
    labels = [
        ClaimLabel(
            source_claim_id=label.source_claim_id,
            is_denied=label.is_denied,
            denial_reason_category=label.denial_reason_category,
            appeal_success_proxy=label.appeal_success_proxy,
            label_rule_version=label.label_rule_version,
        )
        for label in stored_labels
    ]

    is_denied_claim = ClaimSampleLabel.is_denied
    claims = {
        claim.source_claim_id: claim
        for claim in session.scalars(
            select(ClaimSample)
            .join(ClaimSampleLabel, ClaimSampleLabel.source_claim_id == ClaimSample.source_claim_id)
            .where(is_denied_claim)
        )
    }
    lines_by_claim: dict[str, list[ClaimSampleLine]] = {claim_id: [] for claim_id in claims}
    for line in session.scalars(
        select(ClaimSampleLine)
        .join(ClaimSampleLabel, ClaimSampleLabel.source_claim_id == ClaimSampleLine.source_claim_id)
        .where(is_denied_claim)
    ):
        lines_by_claim[line.source_claim_id].append(line)

    stale: list[str] = []
    for label in labels:
        claim_id = label.source_claim_id
        lines = lines_by_claim[claim_id]
        if not lines or label_claim(claim_id, lines) != label:
            stale.append(f"claim {claim_id}: the stored label no longer fits its stored lines")
    if stale:
        raise BatchRejectedError(
            [f"{problem}: run pipelines.run_data_pipeline again" for problem in stale]
        )

    rows: list[GeneratedDocumentRow] = []
    for label in labels:
        claim = claims[label.source_claim_id]
        lines = lines_by_claim[label.source_claim_id]
        rows.append(_row(generate_denial_letter(claim, lines, label, seed)))
        rows.append(_row(generate_clinical_note(claim, lines, label, seed)))
        if needs_prior_auth(label):
            rows.append(_row(generate_prior_auth(claim, lines, label, seed)))
    logger.info(
        "generated %d documents (%s)",
        len(rows),
        ", ".join(f"{kind.value}: {count}" for kind, count in count_by_type(rows).items()),
    )
    return rows


def count_by_type(rows: Iterable[GeneratedDocumentRow]) -> dict[DocumentType, int]:
    """How many of `rows` each document type has. Every type is listed, a type with no
    document as 0, in the order of `GENERATOR_VERSIONS`."""
    counts = dict.fromkeys(GENERATOR_VERSIONS, 0)
    for row in rows:
        counts[row.document_type] += 1
    return counts


def count_by_noise_level(rows: Iterable[GeneratedDocumentRow]) -> dict[NoiseLevel, int]:
    """How many of `rows` each noise level has. Every level is listed, a level with no
    document as 0."""
    counts = dict.fromkeys(NoiseLevel, 0)
    for row in rows:
        counts[NoiseLevel(row.noise_record["level"])] += 1
    return counts


def count_missing_fields(rows: Iterable[GeneratedDocumentRow]) -> int:
    """How many of `rows` had one field blanked by the noise step."""
    return sum(row.noise_record["missing_field"] is not None for row in rows)


def replace_generated_document_rows(session: Session, rows: list[GeneratedDocumentRow]) -> int:
    """Make `generated_documents` hold exactly `rows`: remove every document, then store `rows`.

    Every document type is removed, so `rows` must hold every type of the run (as
    `build_document_rows` returns them). The table then always comes from one run, with one
    seed and one generator version per type. With an upsert alone, a claim that is no longer
    denied would keep its old documents. The claims must already be in `claim_samples`.
    Returns the number of rows written. Does not commit: the caller owns the transaction, so
    a failure brings the old documents back.
    """
    session.execute(delete(GeneratedDocument))
    written = upsert_rows(session, GeneratedDocument, rows, UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
    logger.info("stored %d generated documents", written)
    return written


def count_denied_claims_without_document(session: Session) -> int:
    """How many stored claims are labelled denied and have no row in `generated_documents`.

    0 means every denied claim has at least one document. It reads the stored rows, so it
    checks what was written, not how the rows were built. Call it after
    `replace_generated_document_rows`, in the same transaction.
    """
    has_document = (
        select(GeneratedDocument.source_claim_id)
        .where(GeneratedDocument.source_claim_id == ClaimSampleLabel.source_claim_id)
        .exists()
    )
    count = session.scalar(
        select(func.count())
        .select_from(ClaimSampleLabel)
        .where(ClaimSampleLabel.is_denied, ~has_document)
    )
    return count or 0


def _row(document: DenialLetter | ClinicalNote | PriorAuthRecord) -> GeneratedDocumentRow:
    noisy = apply_noise(
        document.text,
        document.answer_key,
        document.document_type,
        document.source_claim_id,
        document.seed,
    )
    return GeneratedDocumentRow(
        **document.model_dump(exclude={"answer_key", "text"}),
        text=noisy.text,
        answer_key=document.answer_key.model_dump(mode="json"),
        noise_version=NOISE_VERSION,
        noise_record=noisy.record.model_dump(mode="json"),
    )
