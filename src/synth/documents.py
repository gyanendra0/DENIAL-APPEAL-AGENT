"""Build the generated documents of the stored claims and store them in `generated_documents`.

One denial letter per claim that is labelled denied, written by `src.synth.denial_letter` from
the claim, its lines and its label as they are stored. `generated_documents` is a public
reference table (no `account_id`), like the three tables read here. Documents are derived
data: they can always be rebuilt from the claims, the labels, the seed and the generator
version.
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DocumentType,
    GeneratedDocument,
)
from src.ingest.marketplace_denials import BatchRejectedError, upsert_rows
from src.ml.labels import LABEL_RULE_VERSION, ClaimId, ClaimLabel, is_denied_line
from src.synth.denial_letter import MAX_SEED, generate_denial_letter

logger = logging.getLogger(__name__)

UPSERT_CONSTRAINT = "uq_generated_documents_claim_type"
UPSERT_KEY_COLUMNS = ("source_claim_id", "document_type")


class GeneratedDocumentRow(BaseModel):
    """One generated document, shaped like the `generated_documents` table.

    `answer_key` is the document's answer key already turned into plain JSON values (dates
    and amounts as text), because that is what the JSON column stores.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    template_id: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=MAX_SEED)
    generator_version: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1)
    answer_key: dict[str, Any] = Field(min_length=1)


def build_denial_letter_rows(session: Session, seed: int) -> list[GeneratedDocumentRow]:
    """Write the denial letter of every stored claim that is labelled denied.

    Reads `claim_sample_labels`, `claim_samples` and `claim_sample_lines`; writes nothing.
    The rows come back in claim id order. Raises `BatchRejectedError` if no claim is labelled
    denied, if a label was made by a label rule other than `LABEL_RULE_VERSION` (the
    letters would then disagree with the rule in the code), or if a claim is labelled denied
    but its stored lines hold no denied line (the lines were loaded again after the labels).
    """
    labels = session.scalars(
        select(ClaimSampleLabel)
        .where(ClaimSampleLabel.is_denied)
        .order_by(ClaimSampleLabel.source_claim_id)
    ).all()
    if not labels:
        raise BatchRejectedError(
            ["no claim is labelled denied: run pipelines.run_data_pipeline first"]
        )
    other_versions = sorted({label.label_rule_version for label in labels} - {LABEL_RULE_VERSION})
    if other_versions:
        raise BatchRejectedError(
            [
                f"labels were made with rule version {', '.join(other_versions)}, the code is at"
                f" {LABEL_RULE_VERSION}: run pipelines.run_data_pipeline again"
            ]
        )

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

    stale = [
        label.source_claim_id
        for label in labels
        if not any(is_denied_line(line) for line in lines_by_claim[label.source_claim_id])
    ]
    if stale:
        raise BatchRejectedError(
            [
                f"claim {claim_id} is labelled denied but has no denied line: run"
                " pipelines.run_data_pipeline again"
                for claim_id in stale
            ]
        )

    rows: list[GeneratedDocumentRow] = []
    for label in labels:
        claim_id = label.source_claim_id
        letter = generate_denial_letter(
            claims[claim_id],
            lines_by_claim[claim_id],
            ClaimLabel(
                source_claim_id=claim_id,
                is_denied=label.is_denied,
                denial_reason_category=label.denial_reason_category,
                appeal_success_proxy=label.appeal_success_proxy,
                label_rule_version=label.label_rule_version,
            ),
            seed,
        )
        rows.append(
            GeneratedDocumentRow(
                **letter.model_dump(exclude={"answer_key"}),
                answer_key=letter.answer_key.model_dump(mode="json"),
            )
        )
    logger.info("generated %d denial letters", len(rows))
    return rows


def replace_generated_document_rows(session: Session, rows: list[GeneratedDocumentRow]) -> int:
    """Make `generated_documents` hold exactly `rows`: remove every document, then store `rows`.

    The table then always comes from one run, with one seed and one generator version. With an
    upsert alone, a claim that is no longer denied would keep its old letter. The claims must
    already be in `claim_samples`. Returns the number of rows written. Does not commit: the
    caller owns the transaction, so a failure brings the old documents back.
    """
    session.execute(delete(GeneratedDocument))
    written = upsert_rows(session, GeneratedDocument, rows, UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
    logger.info("stored %d generated documents", written)
    return written
