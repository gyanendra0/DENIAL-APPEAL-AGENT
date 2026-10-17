import pytest
from sqlalchemy.orm import Session

from src.db.models import AuditAction, DraftRejectReason
from src.review.actions import reject_draft
from src.review.audit import AuditDetail, record_audit_event
from src.review.audit_log import list_audit_events
from src.review.paging import MAX_PAGE_SIZE
from tests.review.helpers import review_setup


def test_lists_the_accounts_events_newest_first_with_the_user(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    record_audit_event(
        session, member.account_id, member.user_id, AuditAction.UPLOAD_CREATED, setup.job_id
    )
    reject_draft(session, member.account_id, member.user_id, setup.job_id, DraftRejectReason.OTHER)
    other = setup.other_member
    record_audit_event(session, other.account_id, other.user_id, AuditAction.ACCOUNT_CREATED, None)

    page = list_audit_events(session, member.account_id)

    assert [entry.action for entry in page.items] == [
        AuditAction.DRAFT_REJECTED,
        AuditAction.UPLOAD_CREATED,
    ]
    newest = page.items[0]
    assert (newest.user_id, newest.user_email, newest.job_id) == (
        member.user_id,
        f"admin-{member.account_id}@example.com",
        setup.job_id,
    )
    assert newest.detail == AuditDetail(reject_reason=DraftRejectReason.OTHER)
    assert page.next_before is None


def test_pages_by_event_id(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    for _ in range(3):
        record_audit_event(
            session, member.account_id, member.user_id, AuditAction.UPLOAD_CREATED, setup.job_id
        )

    first = list_audit_events(session, member.account_id, limit=2)
    second = list_audit_events(session, member.account_id, limit=2, before=first.next_before)

    assert len(first.items) == 2 and first.next_before == first.items[-1].event_id
    assert len(second.items) == 1 and second.next_before is None
    assert second.items[0].event_id < first.items[-1].event_id


def test_paging_does_not_skip_or_repeat_when_an_event_is_added(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member

    def record() -> None:
        record_audit_event(
            session, member.account_id, member.user_id, AuditAction.UPLOAD_CREATED, setup.job_id
        )

    for _ in range(3):
        record()
    older = [entry.event_id for entry in list_audit_events(session, member.account_id).items]

    first = list_audit_events(session, member.account_id, limit=2)
    record()
    second = list_audit_events(session, member.account_id, limit=2, before=first.next_before)

    seen = [entry.event_id for entry in [*first.items, *second.items]]
    assert seen == older


@pytest.mark.parametrize("limit", [0, MAX_PAGE_SIZE + 1])
def test_refuses_a_page_size_out_of_range(session: Session, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between 1"):
        list_audit_events(session, 1, limit=limit)
