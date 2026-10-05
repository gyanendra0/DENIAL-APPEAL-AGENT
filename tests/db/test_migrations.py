from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from src.db.migrations.versions import rev_0002_issuer_denial_stats as migration_0002
from src.db.migrations.versions import rev_0003_plan_denial_stats as migration_0003
from src.db.migrations.versions import rev_0004_claim_samples as migration_0004
from src.db.models import (
    CLAIM_LINE_AMOUNT_COLUMNS,
    CLAIM_LINE_MAX_NUMBER,
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    PLAN_COUNT_COLUMNS,
    ExchangeType,
    MetalLevel,
    PlanType,
)

BUSINESS_TABLES = {"accounts", "users", "claims", "denials"}
REFERENCE_TABLES = {
    "issuer_denial_stats",
    "plan_denial_stats",
    "claim_samples",
    "claim_sample_lines",
}
TABLES = BUSINESS_TABLES | REFERENCE_TABLES
STATS_TABLE = "issuer_denial_stats"
PLAN_TABLE = "plan_denial_stats"
CLAIM_TABLE = "claim_samples"
LINE_TABLE = "claim_sample_lines"


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


def test_plan_stats_has_its_key_link_index_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(PLAN_TABLE)}
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(PLAN_TABLE)
    }
    indexes = {
        i["name"]: i["column_names"]
        for i in inspector.get_indexes(PLAN_TABLE)
        if not i.get("duplicates_constraint")  # skip the index that backs the unique key
    }
    checks = {c["name"] for c in inspector.get_check_constraints(PLAN_TABLE)}

    assert unique == {"uq_plan_denial_stats_plan_year": ["plan_id", "plan_year"]}
    assert foreign == {
        "fk_plan_denial_stats_issuer_year": (["issuer_id", "plan_year"], STATS_TABLE, "CASCADE")
    }
    assert indexes == {"ix_plan_denial_stats_issuer_year": ["issuer_id", "plan_year"]}
    assert checks == {
        "ck_plan_denial_stats_plan_id_format",
        "ck_plan_denial_stats_plan_id_matches_issuer",
        "ck_plan_denial_stats_counts_non_negative",
        "ck_plan_denial_stats_unreported_has_no_counts",
    }


def test_migration_0003_lists_the_same_columns_and_values_as_the_model() -> None:
    assert migration_0003.COUNT_COLUMNS == PLAN_COUNT_COLUMNS
    assert tuple(member.value for member in PlanType) == migration_0003.PLAN_TYPE
    assert tuple(member.value for member in MetalLevel) == migration_0003.METAL_LEVEL


def test_claim_samples_has_its_unique_key_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(CLAIM_TABLE)}
    checks = {c["name"] for c in inspector.get_check_constraints(CLAIM_TABLE)}

    assert unique == {"uq_claim_samples_source_claim_id": ["source_claim_id"]}
    assert checks == {
        "ck_claim_samples_source_claim_id_format",
        "ck_claim_samples_from_not_after_thru",
    }


def test_claim_sample_lines_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(LINE_TABLE)}
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(LINE_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(LINE_TABLE)}

    assert unique == {"uq_claim_sample_lines_claim_line": ["source_claim_id", "line_number"]}
    assert foreign == {"fk_claim_sample_lines_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")}
    assert checks == {
        "ck_claim_sample_lines_line_number_in_range",
        "ck_claim_sample_lines_processing_indicator_one_char",
        "ck_claim_sample_lines_amounts_non_negative",
    }


def test_migration_0004_lists_the_same_columns_and_limit_as_the_model() -> None:
    assert migration_0004.AMOUNT_COLUMNS == CLAIM_LINE_AMOUNT_COLUMNS
    assert migration_0004.MAX_LINE_NUMBER == CLAIM_LINE_MAX_NUMBER


def test_downgrade_to_0003_removes_both_claim_sample_tables(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0003")
    try:
        tables = inspect(engine).get_table_names()
        assert CLAIM_TABLE not in tables
        assert LINE_TABLE not in tables
        assert PLAN_TABLE in tables
    finally:
        command.upgrade(alembic_config, "head")

    assert {CLAIM_TABLE, LINE_TABLE} <= set(inspect(engine).get_table_names())


def test_downgrade_to_0002_removes_plan_stats_table_and_enums(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0002")
    try:
        tables = inspect(engine).get_table_names()
        assert PLAN_TABLE not in tables
        assert STATS_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname IN ('plan_type', 'metal_level')")
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert PLAN_TABLE in inspect(engine).get_table_names()


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
