"""The app factory. Run with `uvicorn src.api.app:create_app --factory`."""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from src.api.routes import auth, health, me, users
from src.config.settings import ApiSettings, load_api_settings
from src.db.session import create_db_engine, create_session_factory


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


def create_app(
    api_settings: ApiSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Build the API app.

    With no arguments the settings and the database come from the environment, so a missing or
    short `AUTH_TOKEN_SECRET` or a missing `DATABASE_URL` fails here, at startup.
    """
    settings = api_settings if api_settings is not None else load_api_settings()
    factory = (
        session_factory
        if session_factory is not None
        else create_session_factory(create_db_engine())
    )
    app = FastAPI(title="denial-appeal-agent")
    app.state.api_settings = settings
    app.state.session_factory = factory
    app.add_exception_handler(RequestValidationError, answer_validation_error)
    for router in (health.router, auth.router, me.router, users.router):
        app.include_router(router)
    return app
