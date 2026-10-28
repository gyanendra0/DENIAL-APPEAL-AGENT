import pytest
from sqlalchemy.orm import Session

from src.db.models import (
    DenialReasonCategory,
    DraftRejectReason,
    DraftReviewStatus,
    EndState,
    JobStatus,
    UserRole,
)
from src.jobs.store import create_upload_job
from src.review.actions import reject_draft
from src.review.paging import MAX_PAGE_SIZE
from src.review.worklist import list_worklist
from tests.jobs.helpers import account_scope, add_user, new_upload, owner_scope
from tests.review.helpers import add_job_with_draft, finish_job, review_setup


def test_a_job_with_a_draft_shows_its_reason_score_and_review_status(session: Session) -> None:
    setup = review_setup(session)
    finish_job(session, setup.member.account_id, setup.job_id)

    page = list_worklist(session, account_scope(setup.member.account_id))

    [item] = page.items
    assert item.job_id == setup.job_id
    assert (item.file_name, item.uploaded_by) == (
        "letter.txt",
        f"admin-{setup.member.account_id}@example.com",
    )
    assert (item.status, item.failure, item.end_state) == (
        JobStatus.DONE,
        None,
        EndState.NEEDS_REVIEW,
    )
    assert item.review_status is DraftReviewStatus.PENDING
    assert item.denial_reason is DenialReasonCategory.NONCOVERED
    assert item.win_probability == pytest.approx(0.62)
    assert page.next_before is None


def test_a_queued_job_lists_without_extraction_trace_or_draft(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    queued = create_upload_job(session, member.account_id, new_upload(member))

    [newest, _] = list_worklist(session, account_scope(member.account_id)).items

    assert newest.job_id == queued
    assert (newest.status, newest.end_state, newest.review_status) == (
        JobStatus.QUEUED,
        None,
        None,
    )
    assert (newest.denial_reason, newest.win_probability) == (None, None)


def test_never_lists_a_job_of_another_account(session: Session) -> None:
    setup = review_setup(session)

    ids = [
        item.job_id for item in list_worklist(session, account_scope(setup.member.account_id)).items
    ]

    assert ids == [setup.job_id]
    assert setup.other_job_id not in ids


def test_pages_newest_first_by_job_id(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    later = [add_job_with_draft(session, member) for _ in range(2)]

    first = list_worklist(session, account_scope(member.account_id), limit=2)
    second = list_worklist(
        session, account_scope(member.account_id), limit=2, before=first.next_before
    )

    assert [item.job_id for item in first.items] == [later[1], later[0]]
    assert first.next_before == later[0]
    assert [item.job_id for item in second.items] == [setup.job_id]
    assert second.next_before is None


def test_paging_does_not_skip_or_repeat_when_a_job_is_added(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    older = [setup.job_id, *(add_job_with_draft(session, member) for _ in range(3))]

    first = list_worklist(session, account_scope(member.account_id), limit=2)
    added = add_job_with_draft(session, member)
    second = list_worklist(
        session, account_scope(member.account_id), limit=2, before=first.next_before
    )

    seen = [item.job_id for item in [*first.items, *second.items]]
    assert [item.job_id for item in second.items] == [older[1], older[0]]
    assert sorted(seen) == sorted(older)
    assert added not in seen


def test_filters_on_job_status(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    queued = add_job_with_draft(session, member)
    finish_job(session, member.account_id, setup.job_id)

    done = list_worklist(session, account_scope(member.account_id), status=JobStatus.DONE)
    waiting = list_worklist(session, account_scope(member.account_id), status=JobStatus.QUEUED)

    assert [item.job_id for item in done.items] == [setup.job_id]
    assert [item.job_id for item in waiting.items] == [queued]


def test_filters_on_review_status_and_leaves_out_jobs_without_a_draft(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    rejected = add_job_with_draft(session, member)
    create_upload_job(session, member.account_id, new_upload(member))
    reject_draft(
        session, member.account_id, member.user_id, rejected, DraftRejectReason.POOR_WORDING
    )

    pending = list_worklist(
        session, account_scope(member.account_id), review_status=DraftReviewStatus.PENDING
    )
    done = list_worklist(
        session, account_scope(member.account_id), review_status=DraftReviewStatus.REJECTED
    )

    assert [item.job_id for item in pending.items] == [setup.job_id]
    assert [item.job_id for item in done.items] == [rejected]


@pytest.mark.parametrize("limit", [0, MAX_PAGE_SIZE + 1])
def test_refuses_a_page_size_out_of_range(session: Session, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between 1"):
        list_worklist(session, account_scope(1), limit=limit)


def test_an_owner_scope_lists_only_the_users_own_jobs(session: Session) -> None:
    setup = review_setup(session)
    reviewer = add_user(session, setup.member, UserRole.REVIEWER)
    own = add_job_with_draft(session, reviewer)

    mine = list_worklist(session, owner_scope(reviewer))
    whole = list_worklist(session, account_scope(setup.member.account_id))

    assert [item.job_id for item in mine.items] == [own]
    assert [item.job_id for item in whole.items] == [own, setup.job_id]
