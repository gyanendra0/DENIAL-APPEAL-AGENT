"""`GET /overview`. Everything here is made up; no model is called."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.passwords import hash_password
from src.db.models import User, UserRole
from tests.jobs.helpers import Member
from tests.review.helpers import add_job_with_draft, finish_job

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _signup(client: TestClient, name: str, email: str) -> Member:
    response = client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Member(account_id=body["account_id"], user_id=body["user_id"])


def test_the_overview_counts_the_accounts_jobs_and_costs(
    api_client: TestClient, db_session: Session
) -> None:
    other = _signup(api_client, "B", "admin@b.org")
    finish_job(
        db_session,
        other.account_id,
        add_job_with_draft(db_session, other),
        cost_usd="9.000000",
    )
    api_client.cookies.clear()
    admin = _signup(api_client, "A", "admin@a.org")
    job_id = add_job_with_draft(db_session, admin)
    finish_job(db_session, admin.account_id, job_id, seconds=30, cost_usd="0.001100")
    db_session.commit()

    response = api_client.get("/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["jobs_by_status"] == {"queued": 0, "running": 0, "done": 1, "failed": 0}
    assert body["jobs_by_end_state"]["needs_review"] == 1
    assert body["drafts_by_review_status"] == {"pending": 1, "approved": 0, "rejected": 0}
    assert (body["cost_usd_this_month"], body["cost_usd_total"]) == ("0.001100", "0.001100")
    assert body["average_seconds"] == 30


def test_a_viewer_reads_the_overview(api_client: TestClient, db_session: Session) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    db_session.add(
        User(
            account_id=admin.account_id,
            email="viewer@a.org",
            password_hash=hash_password(PASSWORD),
            role=UserRole.VIEWER,
        )
    )
    db_session.commit()
    api_client.cookies.clear()
    api_client.post("/auth/login", json={"email": "viewer@a.org", "password": PASSWORD})
    assert api_client.get("/overview").status_code == 200
