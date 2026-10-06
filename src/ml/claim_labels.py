"""Build the label rows of a batch of claims and store them in `claim_sample_labels`.

One row per claim: the proxy label from `src.ml.labels` and the split from `src.ml.splits`.
`claim_sample_labels` is a public reference table (no `account_id`). Labels are derived
data: they can always be rebuilt from the claims, the rule version and the seed.
"""

import logging
from collections.abc import Iterable
from typing import Protocol

from pydantic import Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import ClaimSampleLabel, DatasetSplit
from src.ingest.marketplace_denials import upsert_rows
from src.ml.labels import ClaimLabel, LabelLine, label_claim
from src.ml.splits import MAX_SPLIT_SEED, assign_split

logger = logging.getLogger(__name__)

UPSERT_CONSTRAINT = "uq_claim_sample_labels_source_claim_id"
UPSERT_KEY_COLUMNS = ("source_claim_id",)


class ClaimLine(LabelLine, Protocol):
    """A service line that also says which claim it belongs to."""

    @property
    def source_claim_id(self) -> str: ...


class ClaimLabelRow(ClaimLabel):
    """One claim's proxy label and split, shaped like the `claim_sample_labels` table."""

    split: DatasetSplit
    split_seed: int = Field(ge=0, le=MAX_SPLIT_SEED)


def build_claim_label_rows(
    claim_ids: Iterable[str], lines: Iterable[ClaimLine], split_seed: int
) -> list[ClaimLabelRow]:
    """Label and split every claim in `claim_ids`, using its lines from `lines`.

    Raises `ValueError` if a claim has no lines or a line belongs to a claim that is not in
    `claim_ids`.
    """
    lines_by_claim: dict[str, list[ClaimLine]] = {claim_id: [] for claim_id in claim_ids}
    for line in lines:
        if line.source_claim_id not in lines_by_claim:
            raise ValueError(f"a line belongs to claim {line.source_claim_id}, which is not given")
        lines_by_claim[line.source_claim_id].append(line)
    return [
        ClaimLabelRow(
            **label_claim(claim_id, claim_lines).model_dump(),
            split=assign_split(claim_id, split_seed),
            split_seed=split_seed,
        )
        for claim_id, claim_lines in lines_by_claim.items()
    ]


def upsert_claim_label_rows(session: Session, rows: list[ClaimLabelRow]) -> int:
    """Insert `rows` into `claim_sample_labels`, updating any claim that already has a label.

    The claims must already be in `claim_samples`. Storing the same rows twice changes nothing
    except `updated_at`. Returns the number of rows written. Does not commit: the caller owns
    the transaction.
    """
    written = upsert_rows(session, ClaimSampleLabel, rows, UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
    logger.info("upserted %d claim labels", written)
    return written


def replace_claim_label_rows(session: Session, rows: list[ClaimLabelRow]) -> int:
    """Make `claim_sample_labels` hold exactly `rows`: remove every label, then store `rows`.

    The table then always comes from one run, with one rule version and one split seed. With
    an upsert alone, a run on fewer claims or with another seed would leave the other claims
    on their old split. Returns the number of rows written. Does not commit: the caller owns
    the transaction, so a failure brings the old labels back.
    """
    session.execute(delete(ClaimSampleLabel))
    return upsert_claim_label_rows(session, rows)
