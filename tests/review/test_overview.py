from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from src.db.models import DraftRejectReason, DraftReviewStatus, EndState, JobStatus
from src.jobs.store import create_upload_job
from src.review.actions import reject_draft
from src.review.overview import read_overview
from tests.jobs.helpers import add_member, new_upload
from tests.review.helpers import (
    add_job_with_draft,
    finish_job,
    made_up_trace,
    move_job_back,
    review_setup,
)


def test_an_account_without_jobs_has_zero_everywhere(session: Session) -> None:
    member = add_member(session, "Made Up Empty Clinic")

    overview = read_overview(session, member.account_id)

    assert set(overview.jobs_by_status.values()) == {0}
    assert set(overview.jobs_by_end_state.values()) == {0}
    assert set(overview.drafts_by_review_status.values()) == {0}
    assert (overview.cost_usd_this_month, overview.cost_usd_total) == (Decimal(0), Decimal(0))
    assert overview.average_seconds is None


def test_counts_jobs_drafts_costs_and_times_of_the_account_only(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    account = member.account_id
    finish_job(session, account, setup.job_id, seconds=20, cost_usd="0.001200")
    old = add_job_with_draft(session, member)
    finish_job(
        session,
        account,
        old,
        trace=made_up_trace(EndState.STOPPED_BY_RULES),
        seconds=40,
        cost_usd="0.000300",
    )
    move_job_back(session, account, old, days=40)
    create_upload_job(session, account, new_upload(member))
    reject_draft(session, account, member.user_id, old, DraftRejectReason.OTHER)
    # The other account's job costs money too, and must not be counted.
    finish_job(session, setup.other_member.account_id, setup.other_job_id, cost_usd="9.000000")

    overview = read_overview(session, account)

    assert overview.jobs_by_status == {
        JobStatus.QUEUED: 1,
        JobStatus.RUNNING: 0,
        JobStatus.DONE: 2,
        JobStatus.FAILED: 0,
    }
    assert overview.jobs_by_end_state[EndState.NEEDS_REVIEW] == 1
    assert overview.jobs_by_end_state[EndState.STOPPED_BY_RULES] == 1
    assert overview.drafts_by_review_status == {
        DraftReviewStatus.PENDING: 1,
        DraftReviewStatus.APPROVED: 0,
        DraftReviewStatus.REJECTED: 1,
    }
    assert overview.cost_usd_total == Decimal("0.001500")
    assert overview.cost_usd_this_month == Decimal("0.001200")
    # The moved job ends 40 days and 40 seconds after its new upload time.
    assert overview.average_seconds == pytest.approx((20 + 40 * 86_400 + 40) / 2)
