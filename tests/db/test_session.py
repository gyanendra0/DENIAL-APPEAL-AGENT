import pytest
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import OperationalError

from src.db.models import Account
from src.db.session import (
    DB_CONNECT_TIMEOUT_SECONDS,
    check_database,
    create_db_engine,
    create_session_factory,
    session_scope,
)


def test_session_scope_commits_on_success(engine: Engine) -> None:
    factory = create_session_factory(engine)

    with session_scope(factory) as session:
        session.add(Account(name="scope-commit"))

    with session_scope(factory) as session:
        account = session.scalars(select(Account).where(Account.name == "scope-commit")).one()
        session.delete(account)


def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    factory = create_session_factory(engine)

    with pytest.raises(RuntimeError), session_scope(factory) as session:
        session.add(Account(name="scope-rollback"))
        session.flush()
        raise RuntimeError("boom")

    with session_scope(factory) as session:
        count = session.scalar(
            select(func.count()).select_from(Account).where(Account.name == "scope-rollback")
        )
    assert count == 0


def test_a_database_error_does_not_show_the_query_values() -> None:
    engine = create_db_engine("postgresql+psycopg://made:up@localhost:5433/none")
    assert engine.hide_parameters is True


def test_opening_a_connection_gives_up_after_the_connect_timeout() -> None:
    engine = create_db_engine("postgresql+psycopg://made:up@localhost:5433/none")
    seen: dict[str, object] = {}

    @event.listens_for(engine, "do_connect")
    def record(
        dialect: object, connection_record: object, cargs: object, cparams: dict[str, object]
    ) -> None:
        # Keep the arguments psycopg would get, then stop before it connects.
        seen.update(cparams)
        raise _StopConnectError

    with pytest.raises(_StopConnectError):
        engine.connect()
    assert DB_CONNECT_TIMEOUT_SECONDS == 5
    assert seen["connect_timeout"] == DB_CONNECT_TIMEOUT_SECONDS


class _StopConnectError(Exception):
    pass


def test_check_database_passes_on_a_new_connection_and_leaves_the_pool_alone(
    engine: Engine,
) -> None:
    factory = create_session_factory(engine)
    pool = engine.pool
    before = pool.status()
    check_database(factory, statement_timeout_ms=2000)
    assert pool.status() == before


def test_check_database_sends_its_timeout_with_the_connection(test_database_url: str) -> None:
    seen: dict[str, object] = {}

    def record(dialect: object, record_: object, cargs: object, cparams: dict[str, object]) -> None:
        seen.update(cparams)

    event.listen(Engine, "do_connect", record)
    try:
        check_database(
            create_session_factory(create_db_engine(test_database_url)),
            statement_timeout_ms=1234,
        )
    finally:
        event.remove(Engine, "do_connect", record)
    assert seen["connect_timeout"] == DB_CONNECT_TIMEOUT_SECONDS
    assert seen["options"] == "-c statement_timeout=1234"


def test_check_database_raises_when_nothing_listens() -> None:
    factory = create_session_factory(create_db_engine("postgresql+psycopg://made@127.0.0.1:1/none"))
    with pytest.raises(OperationalError):
        check_database(factory, statement_timeout_ms=2000)
