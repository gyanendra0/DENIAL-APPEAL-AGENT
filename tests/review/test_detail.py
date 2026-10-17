from decimal import Decimal

from sqlalchemy.orm import Session

from src.db.models import DraftRejectReason, DraftReviewStatus, EndState, JobStatus
from src.drafting.schemas import AppealLetter
from src.jobs.store import create_upload_job
from src.review.actions import approve_draft, edit_draft, reject_draft
from src.review.detail import read_job_detail
from tests.drafting.letter_helpers import CLOSING_TEXT, citation, good_letter, letter, other, policy
from tests.jobs.helpers import UPLOAD_TEXT, new_upload
from tests.review.helpers import finish_job, made_up_trace, review_setup


def _edited() -> AppealLetter:
    return letter(other("A made-up new opening."), policy(citation()), other(CLOSING_TEXT))


def test_a_drafted_job_has_its_text_fields_trace_and_draft(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    finish_job(session, member.account_id, setup.job_id, cost_usd="0.002500")

    detail = read_job_detail(session, member.account_id, setup.job_id)

    assert detail is not None
    assert (detail.job_id, detail.status, detail.end_state) == (
        setup.job_id,
        JobStatus.DONE,
        EndState.NEEDS_REVIEW,
    )
    assert (detail.text, detail.cost_usd) == (UPLOAD_TEXT, Decimal("0.002500"))
    assert detail.uploaded_by == f"admin-{member.account_id}@example.com"
    assert detail.extraction is not None
    assert detail.extraction.denial_reason_category.confidence == 0.9
    assert detail.trace == made_up_trace()
    assert detail.draft is not None
    assert detail.draft.original == detail.draft.latest == good_letter()
    assert detail.draft.latest_version == 0
    assert detail.draft.review_status is DraftReviewStatus.PENDING
    assert (detail.draft.reviewed_by, detail.draft.reviewed_at) == (None, None)


def test_the_latest_version_is_the_edit_and_the_original_stays(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    edit_draft(session, member.account_id, member.user_id, setup.job_id, _edited(), 0)

    detail = read_job_detail(session, member.account_id, setup.job_id)

    assert detail is not None and detail.draft is not None
    assert (detail.draft.original, detail.draft.latest) == (good_letter(), _edited())
    assert detail.draft.latest_version == 1


def test_an_approved_draft_names_the_reviewer_and_the_version(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    approve_draft(session, member.account_id, member.user_id, setup.job_id, 0)

    detail = read_job_detail(session, member.account_id, setup.job_id)

    assert detail is not None and detail.draft is not None
    assert detail.draft.review_status is DraftReviewStatus.APPROVED
    assert detail.draft.reviewed_by == f"admin-{member.account_id}@example.com"
    assert detail.draft.reviewed_at is not None
    assert (detail.draft.approved_version, detail.draft.reject_reason) == (0, None)


def test_a_rejected_draft_has_its_reason(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    reject_draft(
        session, member.account_id, member.user_id, setup.job_id, DraftRejectReason.WRONG_FACT
    )

    detail = read_job_detail(session, member.account_id, setup.job_id)

    assert detail is not None and detail.draft is not None
    assert detail.draft.reject_reason is DraftRejectReason.WRONG_FACT


def test_a_queued_job_has_only_its_text(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member
    queued = create_upload_job(session, member.account_id, new_upload(member))

    detail = read_job_detail(session, member.account_id, queued)

    assert detail is not None
    assert detail.text == UPLOAD_TEXT
    assert (detail.extraction, detail.trace, detail.draft) == (None, None, None)


def test_a_job_of_another_account_is_none_like_a_missing_one(session: Session) -> None:
    setup = review_setup(session)
    account_id = setup.member.account_id

    assert read_job_detail(session, account_id, setup.other_job_id) is None
    assert read_job_detail(session, account_id, setup.other_job_id + 1000) is None


def test_the_repr_holds_no_letter_text(session: Session) -> None:
    setup = review_setup(session)

    detail = read_job_detail(session, setup.member.account_id, setup.job_id)

    assert "Zebulon" not in repr(detail)
    assert UPLOAD_TEXT not in repr(detail)
