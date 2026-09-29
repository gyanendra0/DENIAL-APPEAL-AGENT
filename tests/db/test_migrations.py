from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

TABLES = {"accounts", "users", "claims", "denials"}


def test_upgrade_creates_core_tables_and_vector_extension(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= TABLES
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def test_business_tables_have_indexed_account_id(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in TABLES - {"accounts"}:
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        indexed = {col for ix in inspector.get_indexes(table) for col in ix["column_names"]}
        assert columns["account_id"]["nullable"] is False, table
        assert "account_id" in indexed, table


def test_downgrade_then_upgrade_round_trips(engine: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    assert not TABLES & set(inspect(engine).get_table_names())

    command.upgrade(alembic_config, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES
