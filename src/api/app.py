"""The app factory. Run with `uvicorn src.api.app:create_app --factory`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import httpx
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import JobRunner
from src.api.google_oidc import GoogleOidc
from src.api.mail import MailSender, build_mail_sender
from src.api.routes import (
    audit,
    auth,
    google,
    health,
    jobs,
    me,
    overview,
    password_reset,
    review,
    users,
)
from src.api.upload_limit import UploadSizeLimit
from src.config.settings import (
    ApiSettings,
    get_settings,
    load_api_settings,
    load_llm_gateway_settings,
)
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.jobs.context import build_job_context
from src.jobs.run import run_upload_job
from src.jobs.store import mark_interrupted_jobs

logger = logging.getLogger(__name__)

# How long one call to Google (the token swap, the public keys) may take.
GOOGLE_TIMEOUT_SECONDS = 10.0


async def answer_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """Answer 422 naming each bad field, without the value that was sent.

    FastAPI's own answer repeats the value (`input`), which for a password is the password.
    """
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [{"loc": error["loc"], "type": error["type"], "msg": error["msg"]} for error in errors]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(detail)},
    )


def build_job_runner(session_factory: sessionmaker[Session]) -> JobRunner:
    """Build the runner of upload jobs from the environment.

    Loads the gateway, the prompts and the win model once, so a missing LLM key, prompt file or
    model file fails here, at startup. Each job uses the date it runs on as today.
    """
    context = build_job_context(get_settings(), load_llm_gateway_settings(), session_factory)

    def run(account_id: int, job_id: int) -> None:
        run_upload_job(
            session_factory, context, account_id=account_id, job_id=job_id, today=date.today()
        )

    return run


@asynccontextmanager
async def fail_interrupted_jobs(app: FastAPI) -> AsyncIterator[None]:
    """Before the first request, fail the jobs a stopped process left `queued` or `running`."""
    with session_scope(app.state.session_factory) as session:
        interrupted = mark_interrupted_jobs(session)
    if interrupted:
        logger.warning("startup: %d unfinished jobs marked interrupted", interrupted)
    yield


def create_app(
    api_settings: ApiSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    job_runner: JobRunner | None = None,
    mail_sender: MailSender | None = None,
    google_http: httpx.Client | None = None,
) -> FastAPI:
    """Build the API app.

    With no arguments the settings, the database, the job runner and the mail sender come from
    the environment, so a missing or short `AUTH_TOKEN_SECRET`, a missing `DATABASE_URL`, a
    missing LLM key, a missing model file or a mail setup that cannot send fails here, at
    startup. A missing `APP_PUBLIC_URL` fails here too, whatever is passed in. When Google
    sign-in is configured, `google_http` makes the calls to Google (by default a new client).
    """
    settings = api_settings if api_settings is not None else load_api_settings()
    if settings.app_public_url is None:
        raise ValueError("APP_PUBLIC_URL is not set; password reset links are built from it")
    sender = mail_sender if mail_sender is not None else build_mail_sender(settings)
    factory = (
        session_factory
        if session_factory is not None
        else create_session_factory(create_db_engine())
    )
    runner = job_runner if job_runner is not None else build_job_runner(factory)
    google_oidc = None
    if settings.google_enabled:
        # Never built from the request's `Host` header: it must match what Google has on file.
        google_oidc = GoogleOidc(
            settings,
            (
                google_http
                if google_http is not None
                else httpx.Client(timeout=GOOGLE_TIMEOUT_SECONDS)
            ),
            redirect_uri=settings.app_public_url + google.CALLBACK_PATH,
        )
    app = FastAPI(title="denial-appeal-agent", lifespan=fail_interrupted_jobs)
    app.state.api_settings = settings
    app.state.session_factory = factory
    app.state.job_runner = runner
    app.state.mail_sender = sender
    app.state.public_url = settings.app_public_url
    app.state.google = google_oidc
    app.add_exception_handler(RequestValidationError, answer_validation_error)
    app.add_middleware(UploadSizeLimit)
    for router in (
        health.router,
        auth.router,
        google.router,
        password_reset.router,
        me.router,
        users.router,
        jobs.router,
        review.router,
        overview.router,
        audit.router,
    ):
        app.include_router(router)
    return app
