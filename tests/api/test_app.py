"""The app factory: settings, database and job runner from the environment, a bad secret or a
missing LLM key fails at startup, unfinished jobs are failed before the first request."""

import asyncio
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker

import src.api.app as app_module
from src.api.app import answer_validation_error, create_app
from src.config.settings import ApiSettings, get_settings
from src.db.models import DatasetSplit, Job, JobFailure, JobStatus
from src.jobs.run import JobContext
from src.jobs.store import create_upload_job, start_job
from src.ml.training import SplitFacts, save_win_model, train_win_model
from tests.api.conftest import BASE_URL
from tests.jobs.helpers import add_member, new_upload
from tests.ml.made_up_rows import training_rows

URL = "postgresql+psycopg://made:up@localhost:5433/none"
SECRET = "a-made-up-factory-secret-of-forty-chars!"  # noqa: S105  (made up)
PROMPT_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"
LLM_ENV = {
    "LLM_MONTHLY_BUDGET_USD": "3.00",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_PRIMARY_BASE_URL": "https://primary.example.test/v1",
    "LLM_PRIMARY_MODEL": "example-small-model",
    "LLM_PRIMARY_API_KEY": "made-up-key",
    "LLM_PRIMARY_INPUT_USD_PER_MTOK": "0.15",
    "LLM_PRIMARY_OUTPUT_USD_PER_MTOK": "0.60",
    "LLM_EMBEDDING_MODEL": "example-embedding-model",
    "LLM_EMBEDDING_USD_PER_MTOK": "0.02",
}


def _no_job(account_id: int, job_id: int) -> None:
    raise AssertionError("no job is expected")


@pytest.fixture
def no_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run where no `.env` exists, so only the variables set by the test count."""
    monkeypatch.chdir(tmp_path)
    for key in ("AUTH_TOKEN_SECRET", "AUTH_TOKEN_TTL_MINUTES", "AUTH_COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)
    for key in [name for name in os.environ if name.startswith("LLM_")]:
        monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def full_env(no_env_file: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every key the app needs, a made-up model and the repository's own prompts."""
    model_dir = tmp_path / "model"
    save_win_model(
        train_win_model(training_rows(), seed=7),
        model_dir,
        split_seed=42,
        splits={DatasetSplit.TRAIN: SplitFacts(rows=288, proxy_true=120, auc=0.9)},
    )
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.setenv("PROMPT_DIR", str(PROMPT_DIR))


@pytest.mark.usefixtures("full_env")
def test_builds_from_the_environment() -> None:
    app = create_app()
    settings: ApiSettings = app.state.api_settings
    assert settings.auth_token_secret.get_secret_value() == SECRET
    assert settings.upload_max_bytes == 1_000_000
    assert app.state.session_factory.kw["bind"].url.database == "none"
    assert callable(app.state.job_runner)


@pytest.mark.usefixtures("full_env")
def test_the_built_runner_runs_the_job_with_todays_date(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, date, JobContext]] = []

    def record(
        factory: object, context: JobContext, *, account_id: int, job_id: int, today: date
    ) -> None:
        calls.append((account_id, job_id, today, context))

    monkeypatch.setattr(app_module, "run_upload_job", record)
    create_app().state.job_runner(3, 9)
    [(account_id, job_id, today, context)] = calls
    assert (account_id, job_id, today) == (3, 9, date.today())
    assert context.embedding_model == "example-embedding-model"


@pytest.mark.usefixtures("full_env")
def test_a_missing_llm_key_fails_at_startup_naming_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PRIMARY_API_KEY")
    with pytest.raises(ValidationError, match="llm_primary_api_key"):
        create_app()


@pytest.mark.usefixtures("no_env_file")
def test_missing_secret_fails_at_startup_naming_the_key() -> None:
    with pytest.raises(ValidationError, match="auth_token_secret"):
        create_app()


@pytest.mark.usefixtures("no_env_file")
def test_short_secret_fails_at_startup_without_showing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "short-made-up-value")
    with pytest.raises(ValidationError, match="auth_token_secret") as caught:
        create_app()
    assert "short-made-up-value" not in str(caught.value)


def test_routes_are_registered() -> None:
    settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET), _env_file=None
    )
    app = create_app(settings, sessionmaker(), _no_job)
    # Included routers are nested in `app.routes`; the published schema lists every path.
    assert set(app.openapi()["paths"]) == {
        "/health",
        "/auth/signup",
        "/auth/login",
        "/auth/logout",
        "/me",
        "/me/password",
        "/users/{user_id}",
        "/uploads",
        "/jobs/{job_id}",
        "/worklist",
        "/jobs/{job_id}/detail",
        "/jobs/{job_id}/draft",
        "/jobs/{job_id}/draft/approve",
        "/jobs/{job_id}/draft/reject",
        "/overview",
        "/audit",
    }


def test_the_validation_answer_handles_another_error_as_empty() -> None:
    response = asyncio.run(answer_validation_error(Request({"type": "http"}), ValueError("x")))
    assert (response.status_code, response.body) == (422, b'{"detail":[]}')


def test_startup_fails_the_jobs_a_stopped_process_left_unfinished(
    api_connection: Connection, db_session: Session
) -> None:
    member = add_member(db_session, "Made Up Startup Clinic")
    queued = create_upload_job(db_session, member.account_id, new_upload(member))
    running = create_upload_job(db_session, member.account_id, new_upload(member))
    assert start_job(db_session, member.account_id, running)
    db_session.commit()
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET), _env_file=None
    )
    with TestClient(create_app(settings, factory, _no_job), base_url=BASE_URL):
        pass
    db_session.expire_all()
    for job_id in (queued, running):
        job = db_session.scalars(select(Job).where(Job.id == job_id)).one()
        assert (job.status, job.failure) == (JobStatus.FAILED, JobFailure.INTERRUPTED)
