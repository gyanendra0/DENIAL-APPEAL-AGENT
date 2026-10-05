from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

BUSINESS_TABLES = {"accounts", "users", "claims", "denials"}
REFERENCE_TABLES = {"issuer_denial_stats"}
TABLES = BUSINESS_TABLES | REFERENCE_TABLES


def test_upgrade_creates_core_tables_and_vector_extension(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= TABLES
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def test_business_tables_have_indexed_account_id(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in BUSINESS_TABLES - {"accounts"}:
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        indexed = {col for ix in inspector.get_indexes(table) for col in ix["column_names"]}
        assert columns["account_id"]["nullable"] is False, table
        assert "account_id" in indexed, table


def test_reference_tables_have_timestamps_and_no_account_id(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in REFERENCE_TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "account_id" not in columns, table
        assert {"created_at", "updated_at"} <= columns, table


def test_downgrade_to_0001_removes_issuer_stats_table_and_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0001")
    assert "issuer_denial_stats" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_type WHERE typname = 'exchange_type'")) is None

    command.upgrade(alembic_config, "head")
    assert "issuer_denial_stats" in inspect(engine).get_table_names()


def test_downgrade_then_upgrade_round_trips(engine: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    assert not TABLES & set(inspect(engine).get_table_names())

    command.upgrade(alembic_config, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES
