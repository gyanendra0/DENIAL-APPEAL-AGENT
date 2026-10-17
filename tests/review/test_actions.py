from collections.abc import Callable

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.db.models import (
    AuditAction,
    DraftRejectReason,
    DraftReviewStatus,
    DraftRevision,
    EvidenceChunk,
)
from src.drafting.letter_check import LetterProblem, LetterProblemKind
from src.drafting.schemas import AppealLetter
from src.review.actions import (
    MODEL_DRAFT_VERSION,
    DraftNotFoundError,
    DraftNotPendingError,
    LetterCheckError,
    StaleDraftVersionError,
    approve_draft,
    edit_draft,
    read_latest_draft,
    reject_draft,
)
from tests.drafting.letter_helpers import (
    CHUNK_TEXT,
    CLOSING_TEXT,
    OTHER_CHUNK_TEXT,
    OTHER_QUOTE,
    citation,
    good_letter,
    letter,
    other,
    policy,
    sha256,
)
from tests.review.helpers import ReviewSetup, audit_events, read_draft, review_setup

Action = Callable[[Session, ReviewSetup], object]


def _edited() -> AppealLetter:
    """`good_letter` with its opening text changed and its second citation removed."""
    return letter(
        other("A made-up new opening, written by the reviewer."),
        policy(citation()),
        other(CLOSING_TEXT),
    )


def _edit(session: Session, setup: ReviewSetup, edited: AppealLetter, based_on: int = 0) -> int:
    member = setup.member
    return edit_draft(session, member.account_id, member.user_id, setup.job_id, edited, based_on)


def _approve(session: Session, setup: ReviewSetup, version: int = 0) -> None:
    member = setup.member
    approve_draft(session, member.account_id, member.user_id, setup.job_id, version)


def _reject(session: Session, setup: ReviewSetup) -> None:
    member = setup.member
    reject_draft(
        session,
        member.account_id,
        member.user_id,
        setup.job_id,
        DraftRejectReason.CITATION_DOES_NOT_FIT,
    )


def _revisions(session: Session, job_id: int) -> list[DraftRevision]:
    return list(
        session.scalars(
            select(DraftRevision)
            .where(DraftRevision.job_id == job_id)
            .order_by(DraftRevision.version)
        )
    )


def _change_chunk(session: Session, old_text: str) -> None:
    new_text = old_text + " Made-up new sentence."
    session.execute(
        update(EvidenceChunk)
        .where(EvidenceChunk.text == old_text)
        .values(text=new_text, text_sha256=sha256(new_text))
    )


# --- edit ---


def test_an_edit_stores_version_1_and_its_audit_event(session: Session) -> None:
    setup = review_setup(session)

    version = _edit(session, setup, _edited())

    assert version == 1
    [revision] = _revisions(session, setup.job_id)
    assert AppealLetter.model_validate(revision.letter) == _edited()
    assert revision.text_sha256 == sha256(_edited().model_dump_json())
    assert (revision.account_id, revision.edited_by) == (
        setup.member.account_id,
        setup.member.user_id,
    )
    [event] = audit_events(session, setup.member.account_id)
    assert (event.action, event.job_id, event.detail) == (
        AuditAction.DRAFT_EDITED,
        setup.job_id,
        {"version": 1},
    )


def test_an_edit_leaves_the_models_draft_unchanged_and_pending(session: Session) -> None:
    setup = review_setup(session)

    _edit(session, setup, _edited())

    draft = read_draft(session, setup.job_id)
    assert AppealLetter.model_validate(draft.letter) == good_letter()
    assert draft.review_status is DraftReviewStatus.PENDING


def test_a_second_edit_based_on_the_first_stores_version_2(session: Session) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())

    version = _edit(session, setup, good_letter(), based_on=1)

    assert version == 2
    assert [revision.version for revision in _revisions(session, setup.job_id)] == [1, 2]


@pytest.mark.parametrize("based_on", [0, 2])
def test_refuses_an_edit_based_on_a_version_that_is_not_the_latest(
    session: Session, based_on: int
) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())

    with pytest.raises(StaleDraftVersionError):
        _edit(session, setup, good_letter(), based_on=based_on)

    assert len(_revisions(session, setup.job_id)) == 1


def test_refuses_an_edit_that_adds_a_citation(session: Session) -> None:
    setup = review_setup(session)
    added = citation(document_id="902", quote=OTHER_QUOTE, chunk_text=OTHER_CHUNK_TEXT)
    edited = letter(other(), policy(citation(), added, added), other(CLOSING_TEXT))

    with pytest.raises(LetterCheckError) as refused:
        _edit(session, setup, edited)

    assert refused.value.problems == (
        LetterProblem(kind=LetterProblemKind.EDIT_NOT_ALLOWED, paragraph_index=1),
    )


def test_refuses_an_edit_with_a_wrong_amount_or_a_placeholder(session: Session) -> None:
    setup = review_setup(session)
    edited = letter(
        other("I appeal the made-up amount of $999.99."),
        policy(citation()),
        other("Sincerely, [Your Name]"),
    )

    with pytest.raises(LetterCheckError) as refused:
        _edit(session, setup, edited)

    assert [problem.kind for problem in refused.value.problems] == [
        LetterProblemKind.AMOUNT_NOT_A_DENIED_LINE,
        LetterProblemKind.PLACEHOLDER_TEXT,
    ]


def test_refuses_an_edit_whose_cited_chunk_changed(session: Session) -> None:
    setup = review_setup(session)
    _change_chunk(session, CHUNK_TEXT)

    with pytest.raises(LetterCheckError) as refused:
        _edit(session, setup, _edited())

    assert refused.value.problems == (
        LetterProblem(kind=LetterProblemKind.CHUNK_NOT_FOUND, paragraph_index=1, citation_index=0),
    )


def test_a_refused_edit_stores_no_revision_and_no_audit_event(session: Session) -> None:
    setup = review_setup(session)

    with pytest.raises(LetterCheckError):
        _edit(session, setup, letter(other("Sincerely, [Your Name]")))

    assert _revisions(session, setup.job_id) == []
    assert audit_events(session, setup.member.account_id) == []


def test_a_letter_check_error_names_the_job_and_no_text(session: Session) -> None:
    setup = review_setup(session)

    with pytest.raises(LetterCheckError) as refused:
        _edit(session, setup, letter(other("Sincerely, [Your Name]")))

    assert str(refused.value) == f"job {setup.job_id}: the letter has 1 problem(s)"


# --- approve ---


def test_approving_the_models_draft_stores_version_0(session: Session) -> None:
    setup = review_setup(session)

    _approve(session, setup, MODEL_DRAFT_VERSION)

    draft = read_draft(session, setup.job_id)
    assert draft.review_status is DraftReviewStatus.APPROVED
    assert (draft.reviewed_by, draft.approved_version) == (setup.member.user_id, 0)
    assert draft.reviewed_at is not None
    assert draft.reject_reason is None
    [event] = audit_events(session, setup.member.account_id)
    assert (event.action, event.detail) == (AuditAction.DRAFT_APPROVED, {"version": 0})


def test_approving_an_edited_draft_stores_its_version(session: Session) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())

    _approve(session, setup, 1)

    assert read_draft(session, setup.job_id).approved_version == 1
    assert [event.action for event in audit_events(session, setup.member.account_id)] == [
        AuditAction.DRAFT_EDITED,
        AuditAction.DRAFT_APPROVED,
    ]


def test_refuses_to_approve_a_version_that_is_not_the_latest(session: Session) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())

    with pytest.raises(StaleDraftVersionError):
        _approve(session, setup, 0)

    assert read_draft(session, setup.job_id).review_status is DraftReviewStatus.PENDING


def test_refuses_to_approve_when_a_cited_chunk_changed_since_the_edit(session: Session) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())
    _change_chunk(session, CHUNK_TEXT)

    with pytest.raises(LetterCheckError):
        _approve(session, setup, 1)

    assert read_draft(session, setup.job_id).review_status is DraftReviewStatus.PENDING
    assert [event.action for event in audit_events(session, setup.member.account_id)] == [
        AuditAction.DRAFT_EDITED
    ]


def test_a_chunk_the_edit_removed_does_not_block_the_approval(session: Session) -> None:
    setup = review_setup(session)
    _edit(session, setup, _edited())
    # `_edited` no longer cites the second chunk.
    _change_chunk(session, OTHER_CHUNK_TEXT)

    _approve(session, setup, 1)

    assert read_draft(session, setup.job_id).review_status is DraftReviewStatus.APPROVED


# --- reject ---


def test_a_rejection_stores_the_reason_and_its_audit_event(session: Session) -> None:
    setup = review_setup(session)

    _reject(session, setup)

    draft = read_draft(session, setup.job_id)
    assert draft.review_status is DraftReviewStatus.REJECTED
    assert draft.reject_reason is DraftRejectReason.CITATION_DOES_NOT_FIT
    assert draft.reviewed_by == setup.member.user_id
    assert draft.approved_version is None
    [event] = audit_events(session, setup.member.account_id)
    assert (event.action, event.detail) == (
        AuditAction.DRAFT_REJECTED,
        {"reject_reason": "citation_does_not_fit"},
    )


# --- final states and other accounts ---


@pytest.mark.parametrize("decide", [_approve, _reject])
@pytest.mark.parametrize(
    "act",
    [
        pytest.param(lambda session, setup: _edit(session, setup, _edited()), id="edit"),
        pytest.param(_approve, id="approve"),
        pytest.param(_reject, id="reject"),
    ],
)
def test_a_decided_draft_cannot_be_edited_or_decided_again(
    session: Session, decide: Action, act: Action
) -> None:
    setup = review_setup(session)
    decide(session, setup)
    events_before = len(audit_events(session, setup.member.account_id))

    with pytest.raises(DraftNotPendingError):
        act(session, setup)

    assert len(audit_events(session, setup.member.account_id)) == events_before


def test_another_accounts_job_is_not_found_for_every_action(session: Session) -> None:
    setup = review_setup(session)
    member, job_id = setup.member, setup.other_job_id

    with pytest.raises(DraftNotFoundError):
        edit_draft(session, member.account_id, member.user_id, job_id, _edited(), 0)
    with pytest.raises(DraftNotFoundError):
        approve_draft(session, member.account_id, member.user_id, job_id, 0)
    with pytest.raises(DraftNotFoundError):
        reject_draft(session, member.account_id, member.user_id, job_id, DraftRejectReason.OTHER)
    with pytest.raises(DraftNotFoundError):
        read_latest_draft(session, member.account_id, job_id)

    other_draft = read_draft(session, job_id)
    assert other_draft.review_status is DraftReviewStatus.PENDING
    assert audit_events(session, setup.other_member.account_id) == []


def test_a_job_without_a_draft_is_not_found(session: Session) -> None:
    setup = review_setup(session)
    member = setup.member

    with pytest.raises(DraftNotFoundError) as refused:
        approve_draft(session, member.account_id, member.user_id, 999_999_999, 0)

    assert str(refused.value) == "job 999999999: no draft"


# --- reading ---


def test_the_latest_draft_is_the_models_until_an_edit(session: Session) -> None:
    setup = review_setup(session)
    account_id = setup.member.account_id

    first = read_latest_draft(session, account_id, setup.job_id)
    _edit(session, setup, _edited())
    second = read_latest_draft(session, account_id, setup.job_id)

    assert (first.version, first.letter) == (0, good_letter())
    assert (second.version, second.letter) == (1, _edited())
