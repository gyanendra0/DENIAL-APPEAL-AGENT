"""Engine and session creation."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.config.settings import get_settings

# How long opening a connection may take. Without it psycopg waits for ever on a database that
# accepts the connection but never answers, and the request (even `/health`) hangs.
DB_CONNECT_TIMEOUT_SECONDS = 5


def create_db_engine(url: str | None = None) -> Engine:
    """Create an engine for `url`, or for the configured database.

    `hide_parameters` keeps the values of a query (an email, a password hash) out of the text of
    a database error, so a logged traceback never carries them. A connection that is not open
    within `DB_CONNECT_TIMEOUT_SECONDS` fails instead of waiting.
    """
    return create_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def check_database(factory: sessionmaker[Session], *, statement_timeout_ms: int) -> None:
    """Run `SELECT 1` on a new connection, outside the pool of `factory`.

    Raises a `SQLAlchemyError` if the database does not answer. A pooled connection cannot be
    used here: its ping has no time limit on our side, so on a frozen database it waits for ever,
    and a server-side timeout cannot fire there. A new connection fails within
    `DB_CONNECT_TIMEOUT_SECONDS`, so this can take that long. The timeout is sent with the
    connection itself, so setting it needs no answer from a database that froze just after.
    """
    engine = create_engine(
        factory.kw["bind"].engine.url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on any error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
