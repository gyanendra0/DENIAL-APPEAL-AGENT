import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import AuditAction, DraftRejectReason
from src.review.audit import AuditDetail, record_audit_event
from tests.review.helpers import audit_events, review_setup


def test_records_an_event_with_its_detail(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member

    record_audit_event(
        session,
        member.account_id,
        member.user_id,
        AuditAction.DRAFT_REJECTED,
        setup.job_id,
        AuditDetail(reject_reason=DraftRejectReason.WRONG_FACT),
    )

    [event] = audit_events(session, member.account_id)
    assert (event.user_id, event.action, event.job_id) == (
        member.user_id,
        AuditAction.DRAFT_REJECTED,
        setup.job_id,
    )
    assert event.detail == {"reject_reason": "wrong_fact"}
    assert event.created_at is not None


def test_an_event_without_detail_stores_an_empty_object(session: Session) -> None:
    member = review_setup(session).member

    record_audit_event(
        session, member.account_id, member.user_id, AuditAction.ACCOUNT_CREATED, None
    )

    [event] = audit_events(session, member.account_id)
    assert event.detail == {}


def test_the_detail_has_fixed_keys_only() -> None:
    with pytest.raises(ValidationError):
        AuditDetail.model_validate({"text": "Made-up letter text."})


def test_the_table_refuses_a_job_event_without_a_job(session: Session) -> None:
    member = review_setup(session).member

    with pytest.raises(IntegrityError):
        record_audit_event(
            session, member.account_id, member.user_id, AuditAction.DRAFT_EDITED, None
        )


def test_the_table_refuses_another_accounts_job(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member

    with pytest.raises(IntegrityError):
        record_audit_event(
            session,
            member.account_id,
            member.user_id,
            AuditAction.DRAFT_EDITED,
            setup.other_job_id,
            AuditDetail(version=1),
        )
