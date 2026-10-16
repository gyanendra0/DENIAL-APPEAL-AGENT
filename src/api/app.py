"""The app factory. Run with `uvicorn src.api.app:create_app --factory`."""

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from src.api.routes import auth, health, me, users
from src.config.settings import ApiSettings, load_api_settings
from src.db.session import create_db_engine, create_session_factory


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
    for router in (health.router, auth.router, me.router, users.router):
        app.include_router(router)
    return app
