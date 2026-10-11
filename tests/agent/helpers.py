"""Made-up claim lines, a stand-in model and stored traces for the agent run tests.

`agent_data` is `drafting_data` (made-up claims with a letter extraction each, and one
evidence document) plus one service line per claim, so the model inputs can be built from the
claim rows. The rows are really committed and removed afterwards (the lines and the traces go
with their claim). No stored document and no real model is used.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import AGENT_VERSION
from src.db.models import AgentRun, ClaimSampleLabel, ClaimSampleLine, DocumentExtraction
from src.db.session import session_scope
from src.ml.labels import label_claim
from src.ml.training import WinModel
from tests.drafting.helpers import drafting_data
from tests.extraction.helpers import LETTER, SPLITS, fields_json


class _FixedClassifier:
    """A stand-in for a fitted model: every claim gets the same chance."""

    def __init__(self, chance: float) -> None:
        self._chance = chance

    def predict_proba(self, matrix: Any) -> list[list[float]]:
        return [[1 - self._chance, self._chance] for _ in matrix]


def fixed_model(chance: float = 0.75) -> WinModel:
    return WinModel(classifier=_FixedClassifier(chance), training_seed=7)


def add_claim_line(
    session: Session,
    claim_id: str,
    line_number: int = 1,
    *,
    indicator: str = "C",
    payment: str = "0.00",
    allowed: str = "130.10",
) -> None:
    """Store one service line of `claim_id`. The default is a denied, noncovered line."""
    session.add(
        ClaimSampleLine(
            source_claim_id=claim_id,
            line_number=line_number,
            hcpcs_code="99213",
            line_diagnosis_code=None,
            processing_indicator=indicator,
            payment_amount=Decimal(payment),
            deductible_amount=Decimal("0.00"),
            primary_payer_paid_amount=Decimal("0.00"),
            coinsurance_amount=Decimal("0.00"),
            allowed_charge_amount=Decimal(allowed),
        )
    )


def fit_label(session: Session, claim_id: str) -> None:
    """Make the stored label of `claim_id` the one the label rule gives its stored lines."""
    session.flush()
    lines = session.scalars(
        select(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == claim_id)
    ).all()
    label = label_claim(claim_id, lines)
    session.execute(
        update(ClaimSampleLabel)
        .where(ClaimSampleLabel.source_claim_id == claim_id)
        .values(
            denial_reason_category=label.denial_reason_category,
            appeal_success_proxy=label.appeal_success_proxy,
        )
    )


@contextmanager
def agent_data(engine: Engine, *, embedded: bool = True) -> Iterator[sessionmaker[Session]]:
    """Commit the drafting tests' made-up rows and one denied line per claim."""
    with drafting_data(engine, embedded=embedded) as factory:
        with session_scope(factory) as session:
            for claim_id in SPLITS:
                add_claim_line(session, claim_id)
                fit_label(session, claim_id)
        yield factory


def change_letter(factory: sessionmaker[Session], claim_id: str, **changed: Any) -> None:
    """Replace values of the stored letter extraction of `claim_id`."""
    with session_scope(factory) as session:
        session.execute(
            update(DocumentExtraction)
            .where(
                DocumentExtraction.source_claim_id == claim_id,
                DocumentExtraction.document_type == LETTER,
            )
            .values(fields=fields_json(LETTER, **{"claim_number": claim_id, **changed}))
        )


def stored_runs(factory: sessionmaker[Session]) -> dict[str, AgentRun]:
    """The stored traces of the code's agent version for the made-up claims, by claim id."""
    with session_scope(factory) as session:
        rows = session.scalars(
            select(AgentRun).where(
                AgentRun.agent_version == AGENT_VERSION, AgentRun.source_claim_id.in_(SPLITS)
            )
        ).all()
        session.expunge_all()
        return {row.source_claim_id: row for row in rows}
