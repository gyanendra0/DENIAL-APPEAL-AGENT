"""`POST /uploads` and `GET /jobs/{job_id}`. Every letter here is made up; no model is called."""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.api.passwords import hash_password
from src.api.routes.jobs import BAD_FILE_NAME, JOB_NOT_FOUND
from src.api.upload_file import NOT_UTF8, PDF_UNREADABLE
from src.api.upload_limit import FILE_TOO_LARGE
from src.config.settings import ApiSettings
from src.db.models import Job, JobStatus, Upload, UploadFileType, User, UserRole
from src.extraction.prompts import ExtractionPrompt
from src.jobs.run import JobContext, run_upload_job
from src.llm.gateway import LlmRequest
from tests.agent.helpers import fixed_model
from tests.api.conftest import (
    API_SECRET,
    BASE_URL,
    PUBLIC_URL,
    VERIFIED_AT,
    RecordingSender,
    signup_and_verify,
)
from tests.drafting.helpers import PROMPT, drafting_gateway
from tests.extraction.helpers import LETTER, PROMPT_VERSION, StubProvider, fields_json, system_text
from tests.ingest.helpers import write_text_pdf
from tests.rag.helpers import MODEL

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
# The made-up extracted letter may be appealed until 2008-09-16: the rules stop it then.
AFTER_THE_DEADLINE = date(2009, 1, 1)
LETTER_TEXT = b"Made-up denial letter for Zebulon Quillfeather, claim 999.\n"


def _signup(client: TestClient, name: str, email: str) -> dict[str, int]:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    body: dict[str, int] = signed_up
    return body


def _add_user(session: Session, account_id: int, email: str, role: UserRole) -> None:
    session.add(
        User(
            account_id=account_id,
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            email_verified_at=VERIFIED_AT,
        )
    )
    session.commit()


def _login(client: TestClient, email: str) -> None:
    client.cookies.clear()
    assert (
        client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    )


def _upload(
    client: TestClient, data: bytes = LETTER_TEXT, name: str = "letter.txt"
) -> tuple[int, dict[str, object]]:
    response = client.post("/uploads", files={"file": (name, data, "application/octet-stream")})
    body: dict[str, object] = response.json()
    return response.status_code, body


def test_an_upload_queues_a_job_and_hands_it_to_the_runner(
    api_client: TestClient, db_session: Session, job_calls: list[tuple[int, int]]
) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    code, body = _upload(api_client)
    assert code == 202
    assert body["status"] == "queued"
    job = db_session.scalars(select(Job).where(Job.id == body["job_id"])).one()
    upload = db_session.scalars(select(Upload).where(Upload.id == job.upload_id)).one()
    assert (job.account_id, job.status) == (admin["account_id"], JobStatus.QUEUED)
    assert (upload.user_id, upload.file_name, upload.file_type) == (
        admin["user_id"],
        "letter.txt",
        UploadFileType.TEXT,
    )
    assert (upload.text, upload.byte_size) == (LETTER_TEXT.decode(), len(LETTER_TEXT))
    assert job_calls == [(admin["account_id"], job.id)]


def test_a_pdf_upload_is_stored_as_its_text(
    api_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _signup(api_client, "A", "admin@a.org")
    data = write_text_pdf(tmp_path / "letter.pdf", [["Made-up denial letter"]]).read_bytes()
    code, body = _upload(api_client, data, "letter.pdf")
    assert code == 202
    upload = db_session.scalars(
        select(Upload).join(Job, Job.upload_id == Upload.id).where(Job.id == body["job_id"])
    ).one()
    assert (upload.file_type, upload.text) == (UploadFileType.PDF, "Made-up denial letter ")


def test_a_reviewer_may_upload(api_client: TestClient, db_session: Session) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    _add_user(db_session, admin["account_id"], "reviewer@a.org", UserRole.REVIEWER)
    _login(api_client, "reviewer@a.org")
    assert _upload(api_client)[0] == 202


def test_a_viewer_may_not_upload(
    api_client: TestClient, db_session: Session, job_calls: list[tuple[int, int]]
) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    _add_user(db_session, admin["account_id"], "viewer@a.org", UserRole.VIEWER)
    _login(api_client, "viewer@a.org")
    assert _upload(api_client)[0] == 403
    assert db_session.scalars(select(Job)).all() == []
    assert job_calls == []


def test_an_upload_needs_a_login(api_client: TestClient) -> None:
    assert _upload(api_client)[0] == 401


def test_an_upload_without_a_file_is_422(api_client: TestClient) -> None:
    _signup(api_client, "A", "admin@a.org")
    response = api_client.post("/uploads", data={"other": "x"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]


def test_a_file_over_the_limit_is_413(api_connection: Connection) -> None:
    settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(API_SECRET),
        app_public_url=PUBLIC_URL,
        upload_max_bytes=10,
        _env_file=None,
    )
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    calls: list[tuple[int, int]] = []
    app = create_app(
        settings,
        factory,
        lambda account_id, job_id: calls.append((account_id, job_id)),
        RecordingSender(),
    )
    with TestClient(app, base_url=BASE_URL) as client:
        _signup(client, "A", "admin@a.org")
        assert _upload(client, b"x" * 10)[0] == 202
        assert _upload(client, b"x" * 11) == (413, {"detail": FILE_TOO_LARGE})
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("data", "detail"),
    [(b"\xff\xfe made-up", NOT_UTF8), (b"%PDF-1.4 damaged", PDF_UNREADABLE)],
)
def test_a_file_that_is_not_a_letter_is_422(
    api_client: TestClient, job_calls: list[tuple[int, int]], data: bytes, detail: str
) -> None:
    _signup(api_client, "A", "admin@a.org")
    assert _upload(api_client, data) == (422, {"detail": detail})
    assert job_calls == []


@pytest.mark.parametrize("name", [" ", "x" * 256])
def test_a_bad_file_name_is_422(api_client: TestClient, name: str) -> None:
    _signup(api_client, "A", "admin@a.org")
    assert _upload(api_client, name=name) == (422, {"detail": BAD_FILE_NAME})


def test_a_refusal_never_echoes_the_file_name_or_text(api_client: TestClient) -> None:
    _signup(api_client, "A", "admin@a.org")
    code, body = _upload(api_client, b"Zebulon \xff", "Zebulon_Quillfeather.txt")
    assert code == 422
    assert "Zebulon" not in json.dumps(body)


def test_reads_a_job_of_the_own_account(api_client: TestClient) -> None:
    _signup(api_client, "A", "admin@a.org")
    job_id = _upload(api_client)[1]["job_id"]
    response = api_client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert (body["job_id"], body["status"]) == (job_id, "queued")
    assert (body["failure"], body["end_state"], body["started_at"], body["finished_at"]) == (
        None,
        None,
        None,
        None,
    )
    assert body["created_at"]


def test_a_viewer_reads_a_job_of_the_own_account(
    api_client: TestClient, db_session: Session
) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    job_id = _upload(api_client)[1]["job_id"]
    _add_user(db_session, admin["account_id"], "viewer@a.org", UserRole.VIEWER)
    _login(api_client, "viewer@a.org")
    assert api_client.get(f"/jobs/{job_id}").status_code == 200


def test_a_job_of_another_account_is_404_like_a_missing_one(api_client: TestClient) -> None:
    _signup(api_client, "B", "admin@b.org")
    other_job = _upload(api_client)[1]["job_id"]
    assert isinstance(other_job, int)
    api_client.cookies.clear()
    _signup(api_client, "A", "admin@a.org")
    other = api_client.get(f"/jobs/{other_job}")
    missing = api_client.get(f"/jobs/{other_job + 1000}")
    assert (other.status_code, other.json()) == (404, {"detail": JOB_NOT_FOUND})
    assert (missing.status_code, missing.json()) == (404, {"detail": JOB_NOT_FOUND})


def test_reading_a_job_needs_a_login(api_client: TestClient) -> None:
    assert api_client.get("/jobs/1").status_code == 401


@pytest.fixture
def running_client(api_connection: Connection) -> Iterator[TestClient]:
    """An app whose runner runs the real job, with stub providers, after the letter's deadline."""
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    extraction_prompt = ExtractionPrompt(
        version=PROMPT_VERSION, document_type=LETTER, system=system_text(LETTER)
    )

    def answer(request: LlmRequest) -> str:
        return json.dumps(fields_json(LETTER))

    context = JobContext(
        gateway=drafting_gateway(factory, StubProvider(answer)),
        extraction_prompt=extraction_prompt,
        drafting_prompt=PROMPT,
        model=fixed_model(),
        amount_floor=Decimal("25"),
        win_threshold=0.30,
        embedding_model=MODEL,
    )

    def run(account_id: int, job_id: int) -> None:
        run_upload_job(
            factory, context, account_id=account_id, job_id=job_id, today=AFTER_THE_DEADLINE
        )

    settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(API_SECRET), app_public_url=PUBLIC_URL, _env_file=None
    )
    with TestClient(
        create_app(settings, factory, run, RecordingSender()), base_url=BASE_URL
    ) as client:
        yield client


def test_an_uploaded_letter_runs_to_done_in_the_background(running_client: TestClient) -> None:
    _signup(running_client, "A", "admin@a.org")
    code, body = _upload(running_client)
    assert (code, body["status"]) == (202, "queued")
    # The test client returns once the background task has finished.
    job = running_client.get(f"/jobs/{body['job_id']}").json()
    assert (job["status"], job["end_state"], job["failure"]) == ("done", "stopped_by_rules", None)
    assert job["started_at"] and job["finished_at"]
