"""Shared fixtures. Database tests need TEST_DATABASE_URL and are skipped without it."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from src.db.session import create_db_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point at a database whose name ends in _test")
    return url


@pytest.fixture(scope="session")
def alembic_config(test_database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    return cfg


@pytest.fixture(scope="session")
def engine(test_database_url: str, alembic_config: Config) -> Iterator[Engine]:
    """Migrate the test database up for the session, and fully down afterwards."""
    command.upgrade(alembic_config, "head")
    eng = create_db_engine(test_database_url)
    yield eng
    eng.dispose()
    command.downgrade(alembic_config, "base")


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after each test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        yield db_session
        db_session.close()
        transaction.rollback()
