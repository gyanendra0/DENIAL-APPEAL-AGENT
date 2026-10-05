from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from src.db.migrations.versions import rev_0002_issuer_denial_stats as migration_0002
from src.db.models import ISSUER_COUNT_COLUMNS, ISSUER_PERCENT_COLUMNS, ExchangeType

BUSINESS_TABLES = {"accounts", "users", "claims", "denials"}
REFERENCE_TABLES = {"issuer_denial_stats"}
TABLES = BUSINESS_TABLES | REFERENCE_TABLES
STATS_TABLE = "issuer_denial_stats"


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


def test_issuer_stats_has_the_unique_key_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(STATS_TABLE)}
    checks = {c["name"] for c in inspector.get_check_constraints(STATS_TABLE)}

    assert unique == {"uq_issuer_denial_stats_issuer_year": ["issuer_id", "plan_year"]}
    assert checks == {
        "ck_issuer_denial_stats_issuer_id_format",
        "ck_issuer_denial_stats_counts_non_negative",
        "ck_issuer_denial_stats_percents_in_range",
    }


def test_migration_0002_lists_the_same_columns_and_values_as_the_model() -> None:
    # Both files build the CHECK rules and the enum from their own copy of these lists.
    assert migration_0002.COUNT_COLUMNS == ISSUER_COUNT_COLUMNS
    assert migration_0002.PERCENT_COLUMNS == ISSUER_PERCENT_COLUMNS
    assert tuple(member.value for member in ExchangeType) == migration_0002.EXCHANGE_TYPE


def test_downgrade_to_0001_removes_issuer_stats_table_and_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0001")
    try:
        assert "issuer_denial_stats" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT 1 FROM pg_type WHERE typname = 'exchange_type'")) is None
            )
    finally:
        command.upgrade(alembic_config, "head")

    assert "issuer_denial_stats" in inspect(engine).get_table_names()


def test_downgrade_then_upgrade_round_trips(engine: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    try:
        assert not TABLES & set(inspect(engine).get_table_names())
    finally:
        command.upgrade(alembic_config, "head")

    assert set(inspect(engine).get_table_names()) >= TABLES
