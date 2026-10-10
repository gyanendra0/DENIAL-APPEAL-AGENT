from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine, inspect, text

from src.db.base import Base
from src.db.migrations.versions import rev_0002_issuer_denial_stats as migration_0002
from src.db.migrations.versions import rev_0003_plan_denial_stats as migration_0003
from src.db.migrations.versions import rev_0004_claim_samples as migration_0004
from src.db.migrations.versions import rev_0005_claim_sample_labels as migration_0005
from src.db.migrations.versions import rev_0006_generated_documents as migration_0006
from src.db.migrations.versions import rev_0007_document_noise as migration_0007
from src.db.migrations.versions import rev_0008_llm_calls as migration_0008
from src.db.migrations.versions import rev_0009_document_extractions as migration_0009
from src.db.migrations.versions import rev_0010_evidence_corpus as migration_0010
from src.db.models import (
    CLAIM_LINE_AMOUNT_COLUMNS,
    CLAIM_LINE_MAX_NUMBER,
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    PLAN_COUNT_COLUMNS,
    DatasetSplit,
    DenialReasonCategory,
    DocumentType,
    EvidenceSource,
    ExchangeType,
    LlmProvider,
    MetalLevel,
    PlanType,
)

BUSINESS_TABLES = {"accounts", "users", "claims", "denials"}
REFERENCE_TABLES = {
    "issuer_denial_stats",
    "plan_denial_stats",
    "claim_samples",
    "claim_sample_lines",
    "claim_sample_labels",
    "generated_documents",
    "document_extractions",
    "evidence_documents",
    "evidence_chunks",
}
# Neither customer data nor public data: counts and cost of the project's own model calls.
OPERATIONAL_TABLES = {"llm_calls"}
TABLES = BUSINESS_TABLES | REFERENCE_TABLES | OPERATIONAL_TABLES
STATS_TABLE = "issuer_denial_stats"
PLAN_TABLE = "plan_denial_stats"
CLAIM_TABLE = "claim_samples"
LINE_TABLE = "claim_sample_lines"
LABEL_TABLE = "claim_sample_labels"
DOCUMENT_TABLE = "generated_documents"
LLM_CALL_TABLE = "llm_calls"
EXTRACTION_TABLE = "document_extractions"
EVIDENCE_DOCUMENT_TABLE = "evidence_documents"
EVIDENCE_CHUNK_TABLE = "evidence_chunks"


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


def test_operational_tables_have_timestamps_and_no_account_id(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in OPERATIONAL_TABLES:
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


def test_claim_sample_labels_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(LABEL_TABLE)}
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(LABEL_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(LABEL_TABLE)}

    assert unique == {"uq_claim_sample_labels_source_claim_id": ["source_claim_id"]}
    assert foreign == {
        "fk_claim_sample_labels_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")
    }
    assert checks == {
        "ck_claim_sample_labels_denied_fields_match",
        "ck_claim_sample_labels_rule_version_not_empty",
    }


def test_migration_0005_lists_the_same_values_as_the_model() -> None:
    assert (
        tuple(member.value for member in DenialReasonCategory)
        == migration_0005.DENIAL_REASON_CATEGORY
    )
    assert tuple(member.value for member in DatasetSplit) == migration_0005.DATASET_SPLIT


def test_migration_0005_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[LABEL_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_claim_sample_labels_denied_fields_match": migration_0005.DENIED_FIELDS_MATCH,
        "ck_claim_sample_labels_rule_version_not_empty": migration_0005.RULE_VERSION_NOT_EMPTY,
    }


def test_generated_documents_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints(DOCUMENT_TABLE)
    }
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(DOCUMENT_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(DOCUMENT_TABLE)}

    assert unique == {"uq_generated_documents_claim_type": ["source_claim_id", "document_type"]}
    assert foreign == {
        "fk_generated_documents_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")
    }
    assert checks == {
        "ck_generated_documents_template_id_not_empty",
        "ck_generated_documents_seed_not_negative",
        "ck_generated_documents_generator_version_not_empty",
        "ck_generated_documents_text_not_empty",
        "ck_generated_documents_noise_version_not_empty",
    }


def test_migration_0006_lists_the_same_values_as_the_model() -> None:
    assert tuple(member.value for member in DocumentType) == migration_0006.DOCUMENT_TYPE


def test_migrations_0006_and_0007_have_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[DOCUMENT_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_generated_documents_template_id_not_empty": migration_0006.TEMPLATE_ID_NOT_EMPTY,
        "ck_generated_documents_seed_not_negative": migration_0006.SEED_NOT_NEGATIVE,
        "ck_generated_documents_generator_version_not_empty": (
            migration_0006.GENERATOR_VERSION_NOT_EMPTY
        ),
        "ck_generated_documents_text_not_empty": migration_0006.TEXT_NOT_EMPTY,
        migration_0007.NOISE_VERSION_CHECK: migration_0007.NOISE_VERSION_NOT_EMPTY,
    }


def test_llm_calls_has_its_index_check_constraints_and_six_place_cost(engine: Engine) -> None:
    inspector = inspect(engine)
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes(LLM_CALL_TABLE)}
    checks = {c["name"] for c in inspector.get_check_constraints(LLM_CALL_TABLE)}
    cost = next(c for c in inspector.get_columns(LLM_CALL_TABLE) if c["name"] == "cost_usd")

    assert indexes == {"ix_llm_calls_created_at": ["created_at"]}
    assert checks == {
        "ck_llm_calls_model_name_not_empty",
        "ck_llm_calls_prompt_version_not_empty",
        "ck_llm_calls_purpose_not_empty",
        "ck_llm_calls_input_tokens_not_negative",
        "ck_llm_calls_output_tokens_not_negative",
        "ck_llm_calls_cost_usd_not_negative",
    }
    assert (cost["type"].precision, cost["type"].scale) == (12, 6)  # type: ignore[attr-defined]
    assert inspector.get_foreign_keys(LLM_CALL_TABLE) == []


def test_migration_0008_lists_the_same_values_as_the_model() -> None:
    assert tuple(member.value for member in LlmProvider) == migration_0008.LLM_PROVIDER


def test_migration_0008_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[LLM_CALL_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_llm_calls_model_name_not_empty": migration_0008.MODEL_NAME_NOT_EMPTY,
        "ck_llm_calls_prompt_version_not_empty": migration_0008.PROMPT_VERSION_NOT_EMPTY,
        "ck_llm_calls_purpose_not_empty": migration_0008.PURPOSE_NOT_EMPTY,
        "ck_llm_calls_input_tokens_not_negative": migration_0008.INPUT_TOKENS_NOT_NEGATIVE,
        "ck_llm_calls_output_tokens_not_negative": migration_0008.OUTPUT_TOKENS_NOT_NEGATIVE,
        "ck_llm_calls_cost_usd_not_negative": migration_0008.COST_USD_NOT_NEGATIVE,
    }


def test_document_extractions_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints(EXTRACTION_TABLE)
    }
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(EXTRACTION_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(EXTRACTION_TABLE)}

    assert unique == {
        "uq_document_extractions_claim_type_prompt": [
            "source_claim_id",
            "document_type",
            "prompt_version",
        ]
    }
    # Linked to the claim only: a new document run must not remove the results.
    assert foreign == {
        "fk_document_extractions_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")
    }
    assert checks == {
        "ck_document_extractions_prompt_version_not_empty",
        "ck_document_extractions_model_name_not_empty",
        "ck_document_extractions_text_sha256_format",
    }


def test_migration_0009_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[EXTRACTION_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_document_extractions_prompt_version_not_empty": (
            migration_0009.PROMPT_VERSION_NOT_EMPTY
        ),
        "ck_document_extractions_model_name_not_empty": migration_0009.MODEL_NAME_NOT_EMPTY,
        "ck_document_extractions_text_sha256_format": migration_0009.TEXT_SHA256_FORMAT,
    }


def test_evidence_documents_has_its_two_unique_keys_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"]
        for u in inspector.get_unique_constraints(EVIDENCE_DOCUMENT_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(EVIDENCE_DOCUMENT_TABLE)}

    assert unique == {
        "uq_evidence_documents_source_document": ["source", "source_document_id"],
        "uq_evidence_documents_source_section": ["source", "section_number"],
    }
    assert checks == {
        "ck_evidence_documents_source_document_id_not_empty",
        "ck_evidence_documents_section_number_not_empty",
        "ck_evidence_documents_title_not_empty",
        "ck_evidence_documents_version_number_positive",
    }
    assert inspector.get_foreign_keys(EVIDENCE_DOCUMENT_TABLE) == []


def test_evidence_chunks_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints(EVIDENCE_CHUNK_TABLE)
    }
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(EVIDENCE_CHUNK_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(EVIDENCE_CHUNK_TABLE)}
    columns = {c["name"]: c for c in inspector.get_columns(EVIDENCE_CHUNK_TABLE)}

    assert unique == {"uq_evidence_chunks_document_index": ["evidence_document_id", "chunk_index"]}
    assert foreign == {
        "fk_evidence_chunks_document": (
            ["evidence_document_id"],
            EVIDENCE_DOCUMENT_TABLE,
            "CASCADE",
        )
    }
    assert checks == {
        "ck_evidence_chunks_chunk_index_not_negative",
        "ck_evidence_chunks_section_title_not_empty",
        "ck_evidence_chunks_text_not_empty",
        "ck_evidence_chunks_text_sha256_format",
        "ck_evidence_chunks_chunker_version_not_empty",
    }
    assert columns["section_title"]["nullable"] is True
    # The vector column comes with the retrieval branch, once the embedding model is chosen.
    assert "embedding" not in columns


def test_migration_0010_lists_the_same_values_as_the_model() -> None:
    assert tuple(member.value for member in EvidenceSource) == migration_0010.EVIDENCE_SOURCE


def test_migration_0010_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for table in (EVIDENCE_DOCUMENT_TABLE, EVIDENCE_CHUNK_TABLE)
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_evidence_documents_source_document_id_not_empty": (
            migration_0010.SOURCE_DOCUMENT_ID_NOT_EMPTY
        ),
        "ck_evidence_documents_section_number_not_empty": (migration_0010.SECTION_NUMBER_NOT_EMPTY),
        "ck_evidence_documents_title_not_empty": migration_0010.TITLE_NOT_EMPTY,
        "ck_evidence_documents_version_number_positive": migration_0010.VERSION_NUMBER_POSITIVE,
        "ck_evidence_chunks_chunk_index_not_negative": migration_0010.CHUNK_INDEX_NOT_NEGATIVE,
        "ck_evidence_chunks_section_title_not_empty": migration_0010.SECTION_TITLE_NOT_EMPTY,
        "ck_evidence_chunks_text_not_empty": migration_0010.TEXT_NOT_EMPTY,
        "ck_evidence_chunks_text_sha256_format": migration_0010.TEXT_SHA256_FORMAT,
        "ck_evidence_chunks_chunker_version_not_empty": migration_0010.CHUNKER_VERSION_NOT_EMPTY,
    }


def test_migrated_database_has_the_same_columns_keys_and_links_as_the_models(
    engine: Engine,
) -> None:
    # Alembic compares tables, columns, types, nullability, unique keys, links and indexes.
    # It does not compare CHECK rules or enum values; the per-migration tests above do.
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_downgrade_to_0009_removes_both_evidence_tables_and_the_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0009")
    try:
        tables = set(inspect(engine).get_table_names())
        assert not {EVIDENCE_DOCUMENT_TABLE, EVIDENCE_CHUNK_TABLE} & tables
        assert EXTRACTION_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'evidence_source'")
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert {EVIDENCE_DOCUMENT_TABLE, EVIDENCE_CHUNK_TABLE} <= set(inspect(engine).get_table_names())


def test_downgrade_to_0008_removes_document_extractions_and_keeps_both_enums(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0008")
    try:
        tables = inspect(engine).get_table_names()
        assert EXTRACTION_TABLE not in tables
        assert {DOCUMENT_TABLE, LLM_CALL_TABLE} <= set(tables)
        with engine.connect() as conn:
            kept = conn.scalar(
                text(
                    "SELECT count(*) FROM pg_type"
                    " WHERE typname IN ('document_type', 'llm_provider')"
                )
            )
        assert kept == 2
    finally:
        command.upgrade(alembic_config, "head")

    assert EXTRACTION_TABLE in inspect(engine).get_table_names()


def test_downgrade_to_0007_removes_llm_calls_table_and_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0007")
    try:
        tables = inspect(engine).get_table_names()
        assert LLM_CALL_TABLE not in tables
        assert DOCUMENT_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'llm_provider'")
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert LLM_CALL_TABLE in inspect(engine).get_table_names()


NOISE_COLUMNS = {"noise_version", "noise_record"}
MIGRATION_CLAIM_ID = "800000000000077"
INSERT_CLAIM = (
    "INSERT INTO claim_samples (source_claim_id, claim_from_date, claim_thru_date,"
    " diagnosis_codes) VALUES (:claim_id, '2009-03-01', '2009-03-02', ARRAY['4019'])"
)
INSERT_DOCUMENT_0006 = (
    "INSERT INTO generated_documents (source_claim_id, document_type, template_id, seed,"
    " generator_version, text, answer_key) VALUES (:claim_id, 'denial_letter',"
    " 'formal_letter', 42, 'v1', 'Made-up text.', '{}')"
)
INSERT_DOCUMENT_0007 = (
    "INSERT INTO generated_documents (source_claim_id, document_type, template_id, seed,"
    " generator_version, text, answer_key, noise_version, noise_record) VALUES (:claim_id,"
    " 'denial_letter', 'formal_letter', 42, 'v1', 'Made-up text.', '{}', 'v1', '{}')"
)
DELETE_CLAIM = "DELETE FROM claim_samples WHERE source_claim_id = :claim_id"
COUNT_DOCUMENTS = "SELECT count(*) FROM generated_documents"


def _document_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(DOCUMENT_TABLE)}


def test_downgrade_to_0006_removes_the_noise_columns_and_keeps_the_table(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0006")
    try:
        assert not NOISE_COLUMNS & _document_columns(engine)
        checks = {c["name"] for c in inspect(engine).get_check_constraints(DOCUMENT_TABLE)}
        assert migration_0007.NOISE_VERSION_CHECK not in checks
    finally:
        command.upgrade(alembic_config, "head")

    assert _document_columns(engine) >= NOISE_COLUMNS


def test_upgrade_to_0007_removes_documents_written_before_the_noise_step(
    engine: Engine, alembic_config: Config
) -> None:
    claim = {"claim_id": MIGRATION_CLAIM_ID}
    command.downgrade(alembic_config, "0006")
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_CLAIM), claim)
            conn.execute(text(INSERT_DOCUMENT_0006), claim)
        command.upgrade(alembic_config, "head")
        with engine.connect() as conn:
            assert conn.scalar(text(COUNT_DOCUMENTS)) == 0
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(text(DELETE_CLAIM), claim)


def test_downgrade_to_0006_removes_the_noisy_documents(
    engine: Engine, alembic_config: Config
) -> None:
    claim = {"claim_id": MIGRATION_CLAIM_ID}
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_CLAIM), claim)
            conn.execute(text(INSERT_DOCUMENT_0007), claim)
        command.downgrade(alembic_config, "0006")
        with engine.connect() as conn:
            assert conn.scalar(text(COUNT_DOCUMENTS)) == 0
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(text(DELETE_CLAIM), claim)


def test_downgrade_to_0005_removes_documents_table_and_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0005")
    try:
        tables = inspect(engine).get_table_names()
        assert DOCUMENT_TABLE not in tables
        assert LABEL_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'document_type'")
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert DOCUMENT_TABLE in inspect(engine).get_table_names()


def test_downgrade_to_0004_removes_labels_table_and_enums(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0004")
    try:
        tables = inspect(engine).get_table_names()
        assert LABEL_TABLE not in tables
        assert CLAIM_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text(
                    "SELECT count(*) FROM pg_type"
                    " WHERE typname IN ('denial_reason_category', 'dataset_split')"
                )
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert LABEL_TABLE in inspect(engine).get_table_names()


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
