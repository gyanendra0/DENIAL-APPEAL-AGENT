"""`GET /audit`, and the events sign-up and upload write. Everything here is made up."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.dependencies import NOT_ALLOWED
from src.api.passwords import hash_password
from src.db.models import User, UserRole
from tests.api.conftest import VERIFIED_AT, signup_and_verify

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
LETTER_TEXT = b"Made-up denial letter for Zebulon Quillfeather, claim 999.\n"


def _signup(client: TestClient, name: str, email: str) -> dict[str, int]:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    body: dict[str, int] = signed_up
    return body


def _upload(client: TestClient) -> int:
    response = client.post(
        "/uploads", files={"file": ("letter.txt", LETTER_TEXT, "application/octet-stream")}
    )
    assert response.status_code == 202
    job_id: int = response.json()["job_id"]
    return job_id


def test_sign_up_and_upload_are_audited_newest_first(api_client: TestClient) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    job_id = _upload(api_client)
    response = api_client.get("/audit")
    assert response.status_code == 200
    body = response.json()
    assert [(item["action"], item["job_id"]) for item in body["items"]] == [
        ("upload_created", job_id),
        ("email_verified", None),
        ("account_created", None),
    ]
    assert {item["user_id"] for item in body["items"]} == {admin["user_id"]}
    assert {item["user_email"] for item in body["items"]} == {"admin@a.org"}
    assert body["next_before"] is None
    assert "Zebulon" not in response.text


def test_the_audit_log_pages(api_client: TestClient) -> None:
    _signup(api_client, "A", "admin@a.org")
    _upload(api_client)
    first = api_client.get("/audit", params={"limit": 1}).json()
    second = api_client.get("/audit", params={"limit": 1, "before": first["next_before"]}).json()
    third = api_client.get("/audit", params={"limit": 1, "before": second["next_before"]}).json()
    assert [item["action"] for item in first["items"] + second["items"] + third["items"]] == [
        "upload_created",
        "email_verified",
        "account_created",
    ]


def test_never_shows_another_accounts_events(api_client: TestClient) -> None:
    _signup(api_client, "B", "admin@b.org")
    _upload(api_client)
    api_client.cookies.clear()
    _signup(api_client, "A", "admin@a.org")
    items = api_client.get("/audit").json()["items"]
    assert [item["action"] for item in items] == ["email_verified", "account_created"]


def test_only_an_admin_reads_the_audit_log(api_client: TestClient, db_session: Session) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    db_session.add(
        User(
            account_id=admin["account_id"],
            email="reviewer@a.org",
            password_hash=hash_password(PASSWORD),
            role=UserRole.REVIEWER,
            email_verified_at=VERIFIED_AT,
        )
    )
    db_session.commit()
    api_client.cookies.clear()
    api_client.post("/auth/login", json={"email": "reviewer@a.org", "password": PASSWORD})
    response = api_client.get("/audit")
    assert (response.status_code, response.json()) == (403, {"detail": NOT_ALLOWED})
