"""The worklist, the denial detail, and edit / approve / reject through the API.

Every letter, chunk and person here is made up; no model is called.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies import NOT_ALLOWED
from src.api.passwords import hash_password
from src.api.routes.jobs import JOB_NOT_FOUND
from src.api.routes.review import (
    DRAFT_NOT_FOUND,
    DRAFT_NOT_PENDING,
    LETTER_REFUSED,
    STALE_VERSION,
    _review_error,
)
from src.db.models import AuditAction, AuditEvent, DraftReviewStatus, UploadDraft, User, UserRole
from src.drafting.schemas import AppealLetter
from src.jobs.store import create_upload_job
from tests.drafting.letter_helpers import (
    CHUNK_TEXT,
    CLOSING_TEXT,
    OTHER_CHUNK_TEXT,
    citation,
    good_letter,
    letter,
    other,
    policy,
)
from tests.jobs.helpers import Member, new_upload
from tests.review.helpers import add_chunk, add_job_with_draft, finish_job

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _signup(client: TestClient, name: str, email: str) -> Member:
    response = client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Member(account_id=body["account_id"], user_id=body["user_id"])


def _add_user(session: Session, account_id: int, email: str, role: UserRole) -> None:
    session.add(
        User(account_id=account_id, email=email, password_hash=hash_password(PASSWORD), role=role)
    )
    session.commit()


def _login(client: TestClient, email: str) -> None:
    client.cookies.clear()
    assert (
        client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    )


def _drafted_job(session: Session, member: Member) -> int:
    job_id = add_job_with_draft(session, member)
    finish_job(session, member.account_id, job_id)
    session.commit()
    return job_id


@pytest.fixture
def chunks(db_session: Session) -> None:
    """The two made-up chunks `good_letter` cites."""
    add_chunk(db_session, "901", CHUNK_TEXT)
    add_chunk(db_session, "902", OTHER_CHUNK_TEXT)
    db_session.commit()


@pytest.fixture
def admin(api_client: TestClient, chunks: None) -> Member:
    """Account A's admin, logged in."""
    return _signup(api_client, "A", "admin@a.org")


@pytest.fixture
def job_id(db_session: Session, admin: Member) -> int:
    """A done job of account A with a pending draft."""
    return _drafted_job(db_session, admin)


@pytest.fixture
def other_job_id(api_client: TestClient, db_session: Session, admin: Member) -> int:
    """A drafted job of account B; account A's admin is logged in again afterwards."""
    other = _signup(api_client, "B", "admin@b.org")
    job = _drafted_job(db_session, other)
    _login(api_client, "admin@a.org")
    return job


def _as(client: TestClient, session: Session, admin: Member, role: UserRole) -> None:
    email = f"{role.value}@a.org"
    _add_user(session, admin.account_id, email, role)
    _login(client, email)


def _edited() -> AppealLetter:
    return letter(other("A made-up new opening."), policy(citation()), other(CLOSING_TEXT))


def _put(client: TestClient, job: int, edited: AppealLetter, based_on: int = 0) -> Any:
    return client.put(
        f"/jobs/{job}/draft",
        json={"letter": edited.model_dump(mode="json"), "based_on_version": based_on},
    )


def _approve(client: TestClient, job: int, version: int = 0) -> Any:
    return client.post(f"/jobs/{job}/draft/approve", json={"version": version})


def _reject(client: TestClient, job: int, reason: str = "poor_wording") -> Any:
    return client.post(f"/jobs/{job}/draft/reject", json={"reason": reason})


def _draft(session: Session, job: int) -> UploadDraft:
    session.expire_all()
    return session.scalars(select(UploadDraft).where(UploadDraft.job_id == job)).one()


# --- worklist ---


def test_the_worklist_lists_the_accounts_jobs(
    api_client: TestClient, job_id: int, other_job_id: int
) -> None:
    response = api_client.get("/worklist")
    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body["items"]] == [job_id]
    [item] = body["items"]
    assert (item["status"], item["end_state"], item["review_status"]) == (
        "done",
        "needs_review",
        "pending",
    )
    assert (item["denial_reason"], item["uploaded_by"]) == ("noncovered", "admin@a.org")
    assert body["next_before"] is None


def test_the_worklist_pages_and_filters(
    api_client: TestClient, db_session: Session, admin: Member, job_id: int
) -> None:
    queued = create_upload_job(db_session, admin.account_id, new_upload(admin))
    db_session.commit()
    first = api_client.get("/worklist", params={"limit": 1}).json()
    second = api_client.get("/worklist", params={"limit": 1, "before": first["next_before"]})
    done = api_client.get("/worklist", params={"status": "done"}).json()
    pending = api_client.get("/worklist", params={"review_status": "pending"}).json()
    assert [item["job_id"] for item in first["items"]] == [queued]
    assert [item["job_id"] for item in second.json()["items"]] == [job_id]
    assert [item["job_id"] for item in done["items"]] == [job_id]
    assert [item["job_id"] for item in pending["items"]] == [job_id]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"status": "lost"}])
def test_the_worklist_refuses_a_bad_query(
    api_client: TestClient, admin: Member, params: dict[str, int | str]
) -> None:
    assert api_client.get("/worklist", params=params).status_code == 422


def test_a_viewer_reads_the_worklist_and_the_detail(
    api_client: TestClient, db_session: Session, admin: Member, job_id: int
) -> None:
    _as(api_client, db_session, admin, UserRole.VIEWER)
    assert api_client.get("/worklist").status_code == 200
    assert api_client.get(f"/jobs/{job_id}/detail").status_code == 200


@pytest.mark.parametrize("path", ["/worklist", "/jobs/1/detail", "/overview", "/audit"])
def test_reading_needs_a_login(api_client: TestClient, path: str) -> None:
    assert api_client.get(path).status_code == 401


# --- detail ---


def test_the_detail_has_the_text_fields_trace_and_draft(
    api_client: TestClient, job_id: int
) -> None:
    response = api_client.get(f"/jobs/{job_id}/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["text"].startswith("Made-up denial letter")
    assert body["extraction"]["denial_reason_category"] == {
        "value": "noncovered",
        "confidence": 0.9,
    }
    assert body["trace"]["end_state"] == "needs_review"
    assert body["trace"]["scoring"]["score"] == 0.62
    draft = body["draft"]
    assert AppealLetter.model_validate(draft["original"]) == good_letter()
    assert (draft["latest_version"], draft["review_status"]) == (0, "pending")


def test_the_detail_of_another_accounts_job_is_404_like_a_missing_one(
    api_client: TestClient, other_job_id: int
) -> None:
    other = api_client.get(f"/jobs/{other_job_id}/detail")
    missing = api_client.get(f"/jobs/{other_job_id + 1000}/detail")
    assert (other.status_code, other.json()) == (404, {"detail": JOB_NOT_FOUND})
    assert (missing.status_code, missing.json()) == (404, {"detail": JOB_NOT_FOUND})


# --- edit ---


def test_an_edit_stores_version_1_and_the_detail_shows_it(
    api_client: TestClient, db_session: Session, admin: Member, job_id: int
) -> None:
    response = _put(api_client, job_id, _edited())
    assert (response.status_code, response.json()) == (200, {"job_id": job_id, "version": 1})
    draft = api_client.get(f"/jobs/{job_id}/detail").json()["draft"]
    assert AppealLetter.model_validate(draft["latest"]) == _edited()
    assert AppealLetter.model_validate(draft["original"]) == good_letter()
    assert draft["latest_version"] == 1
    events = db_session.scalars(select(AuditEvent.action).where(AuditEvent.job_id == job_id)).all()
    assert AuditAction.DRAFT_EDITED in events


def test_an_edit_on_a_stale_version_is_409(api_client: TestClient, job_id: int) -> None:
    assert _put(api_client, job_id, _edited()).status_code == 200
    response = _put(api_client, job_id, _edited(), based_on=0)
    assert (response.status_code, response.json()) == (409, {"detail": STALE_VERSION})


def test_a_refused_edit_is_422_with_kinds_and_positions_only(
    api_client: TestClient, job_id: int
) -> None:
    no_citation = letter(other(), policy(), other(CLOSING_TEXT))
    response = _put(api_client, job_id, no_citation)
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "message": LETTER_REFUSED,
            "problems": [
                {"kind": "policy_without_citation", "paragraph_index": 1, "citation_index": None}
            ],
        }
    }


def test_a_bad_edit_body_is_422_without_echoing_the_text(
    api_client: TestClient, job_id: int
) -> None:
    body = {"letter": {"paragraphs": [{"kind": "other", "text": "Zebulon", "x": 1}]}}
    response = api_client.put(f"/jobs/{job_id}/draft", json=body)
    assert response.status_code == 422
    assert "Zebulon" not in json.dumps(response.json())


def test_a_job_without_a_draft_is_404_draft_not_found(
    api_client: TestClient, db_session: Session, admin: Member
) -> None:
    queued = create_upload_job(db_session, admin.account_id, new_upload(admin))
    db_session.commit()
    response = _put(api_client, queued, _edited())
    assert (response.status_code, response.json()) == (404, {"detail": DRAFT_NOT_FOUND})


# --- roles ---


@pytest.mark.parametrize("action", [_put, _approve, _reject])
def test_a_viewer_may_not_act_on_a_draft(
    api_client: TestClient,
    db_session: Session,
    admin: Member,
    job_id: int,
    action: Any,
) -> None:
    _as(api_client, db_session, admin, UserRole.VIEWER)
    response = (
        action(api_client, job_id, _edited()) if action is _put else action(api_client, job_id)
    )
    assert (response.status_code, response.json()) == (403, {"detail": NOT_ALLOWED})
    assert _draft(db_session, job_id).review_status is DraftReviewStatus.PENDING


def test_a_viewer_gets_404_not_403_for_another_accounts_job(
    api_client: TestClient, db_session: Session, admin: Member, other_job_id: int
) -> None:
    _as(api_client, db_session, admin, UserRole.VIEWER)
    response = _approve(api_client, other_job_id)
    assert (response.status_code, response.json()) == (404, {"detail": JOB_NOT_FOUND})


def test_a_reviewer_edits_and_approves(
    api_client: TestClient, db_session: Session, admin: Member, job_id: int
) -> None:
    _as(api_client, db_session, admin, UserRole.REVIEWER)
    assert _put(api_client, job_id, _edited()).json()["version"] == 1
    response = _approve(api_client, job_id, version=1)
    assert (response.status_code, response.json()) == (
        200,
        {"job_id": job_id, "review_status": "approved"},
    )
    draft = api_client.get(f"/jobs/{job_id}/detail").json()["draft"]
    assert (draft["review_status"], draft["approved_version"]) == ("approved", 1)
    assert draft["reviewed_by"] == "reviewer@a.org"


# --- approve and reject ---


@pytest.mark.parametrize("action", [_approve, _reject])
def test_another_accounts_draft_is_404_and_unchanged(
    api_client: TestClient, db_session: Session, other_job_id: int, action: Any
) -> None:
    response = action(api_client, other_job_id)
    assert (response.status_code, response.json()) == (404, {"detail": JOB_NOT_FOUND})
    assert _draft(db_session, other_job_id).review_status is DraftReviewStatus.PENDING


def test_approving_an_old_version_is_409(api_client: TestClient, job_id: int) -> None:
    _put(api_client, job_id, _edited())
    response = _approve(api_client, job_id, version=0)
    assert (response.status_code, response.json()) == (409, {"detail": STALE_VERSION})


def test_a_decided_draft_cannot_be_decided_or_edited_again(
    api_client: TestClient, job_id: int
) -> None:
    assert _approve(api_client, job_id).status_code == 200
    for response in (_approve(api_client, job_id), _reject(api_client, job_id)):
        assert (response.status_code, response.json()) == (409, {"detail": DRAFT_NOT_PENDING})
    assert _put(api_client, job_id, _edited()).status_code == 409


def test_approving_a_letter_that_no_longer_passes_is_422(
    api_client: TestClient, db_session: Session, admin: Member, chunks: None
) -> None:
    unchecked = letter(other(), policy(citation(document_id="903")), other(CLOSING_TEXT))
    job = add_job_with_draft(db_session, admin, unchecked)
    db_session.commit()
    response = _approve(api_client, job)
    assert response.status_code == 422
    assert response.json()["detail"]["problems"][0]["kind"] == "chunk_not_found"
    assert _draft(db_session, job).review_status is DraftReviewStatus.PENDING


def test_a_rejection_stores_the_reason(
    api_client: TestClient, db_session: Session, job_id: int
) -> None:
    response = _reject(api_client, job_id, "wrong_fact")
    assert (response.status_code, response.json()) == (
        200,
        {"job_id": job_id, "review_status": "rejected"},
    )
    assert _draft(db_session, job_id).reject_reason == "wrong_fact"


def test_a_rejection_needs_a_rubric_reason(api_client: TestClient, job_id: int) -> None:
    assert _reject(api_client, job_id, "too long").status_code == 422


def test_an_unknown_error_is_not_turned_into_an_answer() -> None:
    with pytest.raises(TypeError, match="not a review error"):
        _review_error(ValueError("x"))
