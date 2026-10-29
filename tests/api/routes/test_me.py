"""`GET /me`, `POST /me/password` and `POST /me/delete-account`: the logged-in user's own
record, password and personal account, and 401 without a valid login."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.routes import me as me_module
from src.api.routes.me import (
    LETTER_STILL_RUNNING,
    LOGIN_AGAIN,
    NO_PASSWORD_YET,
    ONLY_PERSONAL_ACCOUNTS,
    WRONG_CURRENT_PASSWORD,
    WRONG_PASSWORD,
)
from src.api.schemas import MAX_LOGIN_PASSWORD_CHARACTERS
from src.db.base import Base
from src.db.models import (
    Account,
    AuditAction,
    AuditEvent,
    DraftRevision,
    IdentityProvider,
    Job,
    JobFailure,
    LlmCall,
    LlmProvider,
    LoginFailure,
    LoginKeyKind,
    User,
    UserIdentity,
)
from src.db.query import for_account
from src.jobs.store import finish_job_failed, start_job
from tests.api.conftest import (
    INDIVIDUAL_PASSWORD,
    RecordingSender,
    signup_and_verify,
    signup_individual,
)
from tests.drafting.letter_helpers import good_letter
from tests.jobs.helpers import Member
from tests.review.helpers import add_job_with_draft, finish_job

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NEW_PASSWORD = "a-new-made-up-password"  # noqa: S105  (made up)
WRONG = "a-made-up-wrong-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _signup(client: TestClient, name: str, email: str) -> dict[str, object]:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    body: dict[str, object] = signed_up
    return body


def _failure_ids(session: Session) -> list[int]:
    session.expire_all()
    return list(session.scalars(select(LoginFailure.id).order_by(LoginFailure.id)))


def test_me_returns_the_user_and_the_account(api_client: TestClient) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    response = api_client.get("/me")
    assert response.status_code == 200
    assert response.json() == signed_up
    assert set(signed_up) == {
        "user_id",
        "email",
        "role",
        "account_id",
        "account_name",
        "account_kind",
    }
    assert signed_up["account_kind"] == "shared"


def test_user_of_account_a_sees_only_account_a(api_client: TestClient) -> None:
    b = _signup(api_client, "B", "b@x.org")
    api_client.cookies.clear()
    a = _signup(api_client, "A", "a@x.org")
    me = api_client.get("/me").json()
    assert (me["account_id"], me["account_name"]) == (a["account_id"], "A")
    assert me["account_id"] != b["account_id"]
    assert "b@x.org" not in str(me)


def test_no_cookie_is_401(api_client: TestClient) -> None:
    assert api_client.get("/me").status_code == 401


def test_damaged_cookie_is_401(api_client: TestClient) -> None:
    api_client.cookies.set(AUTH_COOKIE_NAME, "not-a-token")
    assert api_client.get("/me").status_code == 401


def test_deleted_user_is_401(api_client: TestClient, db_session: Session) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    # Sign-up audited the user, and an audited user cannot be deleted alone.
    db_session.execute(delete(AuditEvent).where(AuditEvent.user_id == signed_up["user_id"]))
    db_session.execute(delete(User).where(User.id == signed_up["user_id"]))
    db_session.commit()
    assert api_client.get("/me").status_code == 401


def test_user_deleted_after_the_login_check_is_401(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")
    monkeypatch.setattr(me_module, "find_user_response", lambda *_, **__: None)
    assert api_client.get("/me").status_code == 401


# --- POST /me/password ---


class _Clock:
    """The time the app sees; a test moves it on instead of waiting."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(api_client: TestClient) -> Iterator[_Clock]:
    fake = _Clock()
    api_client.app.dependency_overrides[get_now] = fake  # type: ignore[attr-defined]
    yield fake
    api_client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _change(client: TestClient, current: str = PASSWORD, new: str = NEW_PASSWORD) -> int:
    response = client.post("/me/password", json={"current_password": current, "new_password": new})
    return response.status_code


def _login(client: TestClient, password: str, email: str = "a@x.org") -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def _hash(session: Session, user_id: object) -> str:
    session.expire_all()
    found = session.scalar(select(User.password_hash).where(User.id == user_id))
    assert found is not None
    return found


def _password_events(session: Session) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PASSWORD_CHANGED)
        ).all()
    )


def test_change_password_makes_the_new_password_the_only_one(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.content) == (204, b"")
    api_client.cookies.clear()
    assert _login(api_client, PASSWORD) == 401
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_wrong_current_password_is_400_and_changes_nothing(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    response = api_client.post(
        "/me/password", json={"current_password": WRONG, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (400, {"detail": WRONG_CURRENT_PASSWORD})
    assert WRONG not in response.text and NEW_PASSWORD not in response.text
    assert _hash(db_session, signed_up["user_id"]) == before
    assert _password_events(db_session) == []
    assert api_client.get("/me").status_code == 200


def test_a_user_with_no_password_is_400_told_so_and_nothing_counts(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    db_session.execute(
        update(User).where(User.id == signed_up["user_id"]).values(password_hash=None)
    )
    db_session.commit()
    counted = _failure_ids(db_session)
    response = api_client.post(
        "/me/password", json={"current_password": WRONG, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (400, {"detail": NO_PASSWORD_YET})
    db_session.expire_all()
    stored = db_session.scalar(select(User.password_hash).where(User.id == signed_up["user_id"]))
    assert stored is None
    assert _password_events(db_session) == []
    # Nothing was guessed, so neither the email nor the IP count grows.
    assert _failure_ids(db_session) == counted


@pytest.mark.parametrize("length", [11, 129])
def test_a_new_password_outside_12_to_128_is_422_and_changes_nothing(
    api_client: TestClient, db_session: Session, clock: _Clock, length: int
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    counted = _failure_ids(db_session)
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": "n" * length}
    )
    assert response.status_code == 422
    assert "n" * length not in response.text and PASSWORD not in response.text
    assert _hash(db_session, signed_up["user_id"]) == before
    # Refused before the route runs, so it is not counted toward the login limit.
    assert _failure_ids(db_session) == counted


def test_change_password_without_a_login_is_401(api_client: TestClient, clock: _Clock) -> None:
    assert _change(api_client) == 401


def test_the_user_cannot_be_named_in_the_body(api_client: TestClient, clock: _Clock) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    response = api_client.post(
        "/me/password",
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "user_id": signed_up["user_id"],
        },
    )
    assert response.status_code == 422


def test_change_password_changes_only_the_logged_in_user(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    other = _signup(api_client, "B", "b@x.org")
    other_before = _hash(db_session, other["user_id"])
    api_client.cookies.clear()
    _signup(api_client, "A", "a@x.org")
    assert _change(api_client) == 204
    assert _hash(db_session, other["user_id"]) == other_before


def test_older_tokens_stop_working_and_this_session_stays(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    old_token = api_client.cookies[AUTH_COOKIE_NAME]
    assert _change(api_client) == 204
    new_token = api_client.cookies[AUTH_COOKIE_NAME]
    assert new_token != old_token
    assert api_client.get("/me").status_code == 200
    # Another browser still holding a token from before the change (and only that token).
    api_client.cookies.clear()
    api_client.cookies.set(AUTH_COOKIE_NAME, old_token)
    assert api_client.get("/me").status_code == 401


def test_change_password_writes_one_audit_event_without_a_job_or_secret(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    assert _change(api_client) == 204
    events = _password_events(db_session)
    assert len(events) == 1
    event = events[0]
    assert (event.account_id, event.user_id) == (signed_up["account_id"], signed_up["user_id"])
    assert (event.job_id, event.detail) == (None, {})
    assert api_client.get("/audit").json()["items"][0]["action"] == "password_changed"


def test_five_wrong_current_passwords_then_even_the_right_one_is_429(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    for _ in range(5):
        assert _change(api_client, current=WRONG) == 400
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert response.headers["retry-after"] == str(14 * 60)
    assert _hash(db_session, signed_up["user_id"]) == before


def test_wrong_current_passwords_count_toward_the_login_limit(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    for _ in range(5):
        assert _change(api_client, current=WRONG) == 400
    api_client.cookies.clear()
    assert _login(api_client, PASSWORD) == 429


def test_failed_logins_count_toward_the_change_limit(api_client: TestClient, clock: _Clock) -> None:
    _signup(api_client, "One", "a@x.org")
    cookie = api_client.cookies[AUTH_COOKIE_NAME]
    api_client.cookies.clear()
    for _ in range(5):
        assert _login(api_client, WRONG) == 401
    api_client.cookies.set(AUTH_COOKIE_NAME, cookie)
    assert _change(api_client) == 429


def test_a_change_clears_the_email_count_but_not_the_ip_count(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    for _ in range(4):
        assert _change(api_client, current=WRONG) == 400
    assert _change(api_client) == 204
    kinds = db_session.scalars(select(LoginFailure.key_kind)).all()
    # The sign-up's own IP try stays too.
    assert list(kinds) == [LoginKeyKind.IP] * 5


def test_a_current_password_over_the_cap_is_400_without_hashing(
    api_client: TestClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")
    checked: list[str] = []
    monkeypatch.setattr(me_module, "verify_password", lambda _hash, pw: checked.append(pw))
    assert _change(api_client, current="p" * (MAX_LOGIN_PASSWORD_CHARACTERS + 1)) == 400
    assert checked == []


def test_user_deleted_before_the_change_is_401(
    api_client: TestClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")

    # The row lookup inside the route finds nothing, as if the user was deleted meanwhile.
    monkeypatch.setattr(me_module, "for_account", lambda model, _account_id: for_account(model, -1))
    assert _change(api_client) == 401


def test_change_password_never_logs_or_answers_a_password(
    api_client: TestClient, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(api_client, "One", "a@x.org")
    caplog.set_level(logging.INFO)
    responses = [
        api_client.post(
            "/me/password", json={"current_password": current, "new_password": NEW_PASSWORD}
        )
        for current in (WRONG, PASSWORD)
    ]
    assert [response.status_code for response in responses] == [400, 204]
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    assert "password changed: account" in caplog.text
    assert "password change failed" in caplog.text
    for secret in ("a@x.org", WRONG, PASSWORD, NEW_PASSWORD, "testclient"):
        assert secret not in shown


# --- POST /me/delete-account ---


def _delete(client: TestClient, password: str | None = INDIVIDUAL_PASSWORD) -> int:
    body = {} if password is None else {"password": password}
    return client.post("/me/delete-account", json=body).status_code


def _rows_of(session: Session, account_id: object) -> dict[str, int]:
    """How many rows every business table holds for `account_id`, by table name."""
    session.expire_all()
    counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        if "account_id" in table.c:
            query = select(func.count()).select_from(table).where(table.c.account_id == account_id)
            counts[table.name] = session.scalar(query) or 0
    return counts


def _account_exists(session: Session, account_id: object) -> bool:
    session.expire_all()
    return session.scalar(select(Account.id).where(Account.id == account_id)) is not None


def _fill_account(session: Session, user: dict[str, Any]) -> None:
    """A finished letter with a draft, a revision and a Google link, all in the user's account."""
    member = Member(account_id=user["account_id"], user_id=user["user_id"])
    job_id = add_job_with_draft(session, member)
    finish_job(session, member.account_id, job_id)
    session.add(
        DraftRevision(
            account_id=member.account_id,
            job_id=job_id,
            version=1,
            edited_by=member.user_id,
            letter=good_letter().model_dump(mode="json"),
            text_sha256="b" * 64,
        )
    )
    session.add(
        UserIdentity(
            account_id=member.account_id,
            user_id=member.user_id,
            provider=IdentityProvider.GOOGLE,
            subject="made-up-google-subject",
            email_at_link=user["email"],
        )
    )
    session.commit()


def _google_only(session: Session, user: dict[str, Any]) -> None:
    session.execute(update(User).where(User.id == user["user_id"]).values(password_hash=None))
    session.commit()


def test_an_individual_deletes_their_account_and_everything_in_it(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, clock: _Clock
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    _fill_account(db_session, user)
    db_session.add(
        LlmCall(
            provider=LlmProvider.PRIMARY,
            model_name="example-model",
            prompt_version="v1",
            purpose="extraction",
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("0.000100"),
        )
    )
    db_session.commit()
    before = _rows_of(db_session, user["account_id"])
    for table in ("users", "uploads", "jobs", "upload_drafts", "draft_revisions", "audit_events"):
        assert before[table] >= 1, table
    assert before["user_identities"] == 1

    response = api_client.post("/me/delete-account", json={"password": INDIVIDUAL_PASSWORD})

    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert not _account_exists(db_session, user["account_id"])
    assert set(_rows_of(db_session, user["account_id"]).values()) == {0}
    # The cost record holds no account and no content, so it stays.
    assert db_session.scalar(select(func.count()).select_from(LlmCall)) == 1


def test_the_old_login_stops_working_after_the_delete(
    api_client: TestClient, sent_mail: RecordingSender, clock: _Clock
) -> None:
    signup_individual(api_client, sent_mail, "solo@individual.test")
    cookie = api_client.cookies[AUTH_COOKIE_NAME]
    assert _delete(api_client) == 204
    api_client.cookies.set(AUTH_COOKIE_NAME, cookie)
    assert api_client.get("/me").status_code == 401
    assert _login(api_client, INDIVIDUAL_PASSWORD, email="solo@individual.test") == 401


def test_another_individuals_account_is_left_alone(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, clock: _Clock
) -> None:
    other = signup_individual(api_client, sent_mail, "other@individual.test")
    _fill_account(db_session, other)
    before = _rows_of(db_session, other["account_id"])
    signup_individual(api_client, sent_mail, "solo@individual.test")
    assert _delete(api_client) == 204
    assert _account_exists(db_session, other["account_id"])
    assert _rows_of(db_session, other["account_id"]) == before


@pytest.mark.parametrize("password", [WRONG, None, "p" * (MAX_LOGIN_PASSWORD_CHARACTERS + 1)])
def test_a_wrong_or_missing_password_is_400_counts_and_deletes_nothing(
    api_client: TestClient,
    db_session: Session,
    sent_mail: RecordingSender,
    clock: _Clock,
    password: str | None,
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    failures = len(_failure_ids(db_session))
    response = api_client.post(
        "/me/delete-account", json={} if password is None else {"password": password}
    )
    assert (response.status_code, response.json()) == (400, {"detail": WRONG_PASSWORD})
    assert _account_exists(db_session, user["account_id"])
    # One row for the email and one for the IP.
    assert len(_failure_ids(db_session)) == failures + 2


def test_five_wrong_passwords_then_even_the_right_one_is_429(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, clock: _Clock
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    for _ in range(5):
        assert _delete(api_client, WRONG) == 400
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post("/me/delete-account", json={"password": INDIVIDUAL_PASSWORD})
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert response.headers["retry-after"] == str(14 * 60)
    assert _account_exists(db_session, user["account_id"])


def test_a_shared_account_user_is_403_and_nothing_is_deleted(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    failures = _failure_ids(db_session)
    response = api_client.post("/me/delete-account", json={"password": PASSWORD})
    assert (response.status_code, response.json()) == (403, {"detail": ONLY_PERSONAL_ACCOUNTS})
    assert _account_exists(db_session, signed_up["account_id"])
    # Refused before the password is looked at, so nothing counts.
    assert _failure_ids(db_session) == failures


@pytest.mark.parametrize(("minutes", "status"), [(9, 204), (11, 403)])
def test_a_google_only_user_must_have_logged_in_within_ten_minutes(
    api_client: TestClient,
    db_session: Session,
    sent_mail: RecordingSender,
    clock: _Clock,
    minutes: int,
    status: int,
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    _google_only(db_session, user)
    failures = _failure_ids(db_session)
    # The login cookie was issued at the real time; the app's clock moves on from there.
    clock.now = datetime.now(UTC) + timedelta(minutes=minutes)
    response = api_client.post("/me/delete-account", json={})
    assert response.status_code == status
    assert _account_exists(db_session, user["account_id"]) is (status == 403)
    if status == 403:
        assert response.json() == {"detail": LOGIN_AGAIN}
        # Nothing was guessed, so nothing new counts (older rows may be cleared).
        assert set(_failure_ids(db_session)) <= set(failures)


def test_an_unfinished_letter_is_409_and_nothing_is_deleted(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, clock: _Clock
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    account_id = user["account_id"]
    uploaded = api_client.post(
        "/uploads",
        files={"file": ("letter.txt", b"Made-up denial letter, claim 999.\n", "text/plain")},
        data={"no_personal_data": "true"},
    )
    assert uploaded.status_code == 202, uploaded.text
    job_id = db_session.scalars(select(Job.id).where(Job.account_id == account_id)).one()
    response = api_client.post("/me/delete-account", json={"password": INDIVIDUAL_PASSWORD})
    assert (response.status_code, response.json()) == (409, {"detail": LETTER_STILL_RUNNING})
    assert start_job(db_session, account_id, job_id)
    db_session.commit()
    assert _delete(api_client) == 409
    assert _account_exists(db_session, account_id)
    assert finish_job_failed(
        db_session, account_id, job_id, JobFailure.INTERNAL_ERROR, Decimal("0")
    )
    db_session.commit()
    assert _delete(api_client) == 204


def test_delete_without_a_login_is_401(api_client: TestClient, clock: _Clock) -> None:
    assert _delete(api_client) == 401


def test_the_account_cannot_be_named_in_the_body(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, clock: _Clock
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    response = api_client.post(
        "/me/delete-account",
        json={"password": INDIVIDUAL_PASSWORD, "account_id": user["account_id"] + 1},
    )
    assert response.status_code == 422
    assert _account_exists(db_session, user["account_id"])


def test_user_deleted_before_the_delete_is_401(
    api_client: TestClient,
    sent_mail: RecordingSender,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signup_individual(api_client, sent_mail, "solo@individual.test")
    # The row lookup inside the route finds nothing, as if the user was deleted meanwhile.
    monkeypatch.setattr(me_module, "for_account", lambda model, _account_id: for_account(model, -1))
    assert _delete(api_client) == 401


def test_delete_logs_the_account_id_only(
    api_client: TestClient,
    sent_mail: RecordingSender,
    clock: _Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = signup_individual(api_client, sent_mail, "solo@individual.test")
    caplog.set_level(logging.INFO)
    responses = [
        api_client.post("/me/delete-account", json={"password": password})
        for password in (WRONG, INDIVIDUAL_PASSWORD)
    ]
    assert [response.status_code for response in responses] == [400, 204]
    assert f"account deleted: account {user['account_id']}" in caplog.text
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    for secret in ("solo@individual.test", WRONG, INDIVIDUAL_PASSWORD, "testclient"):
        assert secret not in shown
