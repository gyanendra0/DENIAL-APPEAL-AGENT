import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
from src.db.migrations.versions import rev_0011_evidence_chunk_embeddings as migration_0011
from src.db.migrations.versions import rev_0012_appeal_drafts as migration_0012
from src.db.migrations.versions import rev_0013_agent_runs as migration_0013
from src.db.migrations.versions import rev_0014_evidence_source_cms_manual as migration_0014
from src.db.migrations.versions import rev_0015_users_password_and_global_email as migration_0015
from src.db.migrations.versions import rev_0016_uploads_and_jobs as migration_0016
from src.db.migrations.versions import rev_0017_draft_review_and_audit as migration_0017
from src.db.migrations.versions import rev_0018_login_failures as migration_0018
from src.db.migrations.versions import (
    rev_0019_password_changed_and_token_version as migration_0019,
)
from src.db.migrations.versions import rev_0020_password_reset_tokens as migration_0020
from src.db.models import (
    CLAIM_LINE_AMOUNT_COLUMNS,
    CLAIM_LINE_MAX_NUMBER,
    EMBEDDING_DIMENSIONS,
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    PLAN_COUNT_COLUMNS,
    AuditAction,
    DatasetSplit,
    DenialReasonCategory,
    DocumentType,
    DraftRejectReason,
    DraftReviewStatus,
    EndState,
    EvidenceSource,
    ExchangeType,
    JobFailure,
    JobStatus,
    LlmProvider,
    LoginKeyKind,
    MetalLevel,
    PlanType,
    UploadFileType,
)

# The upload tables, children before parents (the order a downgrade drops them).
UPLOAD_TABLES = ("upload_drafts", "upload_agent_runs", "upload_extractions", "jobs", "uploads")
# Added by 0017, children before parents.
REVIEW_TABLES = ("audit_events", "draft_revisions")
RESET_TABLE = "password_reset_tokens"
BUSINESS_TABLES = {
    "accounts",
    "users",
    "claims",
    "denials",
    *UPLOAD_TABLES,
    *REVIEW_TABLES,
    RESET_TABLE,
}
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
    "evidence_chunk_embeddings",
    "appeal_drafts",
    "agent_runs",
}
# Neither customer data nor public data: counts and cost of the project's own model calls,
# and keyed hashes of failed logins.
OPERATIONAL_TABLES = {"llm_calls", "login_failures"}
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
EMBEDDING_TABLE = "evidence_chunk_embeddings"
DRAFT_TABLE = "appeal_drafts"
AGENT_RUN_TABLE = "agent_runs"
LOGIN_FAILURE_TABLE = "login_failures"


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
    # The vectors have their own table, so a chunk row never holds one.
    assert "embedding" not in columns


def test_migrations_0010_and_0014_list_the_same_values_as_the_model() -> None:
    migrated = (*migration_0010.EVIDENCE_SOURCE, migration_0014.NEW_EVIDENCE_SOURCE)

    assert tuple(member.value for member in EvidenceSource) == migrated


def test_migration_0014_downgrade_builds_the_values_of_migration_0010() -> None:
    assert migration_0014.EVIDENCE_SOURCE_BEFORE == migration_0010.EVIDENCE_SOURCE


def _evidence_source_values(engine: Engine) -> list[str]:
    query = text(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
        " WHERE t.typname = 'evidence_source' ORDER BY e.enumsortorder"
    )
    with engine.connect() as conn:
        return list(conn.scalars(query))


def test_upgrade_gives_the_evidence_source_type_both_values(engine: Engine) -> None:
    assert _evidence_source_values(engine) == ["cms_ncd", "cms_manual"]


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


def test_evidence_chunk_embeddings_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints(EMBEDDING_TABLE)
    }
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(EMBEDDING_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(EMBEDDING_TABLE)}

    assert unique == {
        "uq_evidence_chunk_embeddings_chunk_model_version": [
            "evidence_chunk_id",
            "model_name",
            "input_version",
        ]
    }
    assert foreign == {
        "fk_evidence_chunk_embeddings_chunk": (
            ["evidence_chunk_id"],
            EVIDENCE_CHUNK_TABLE,
            "CASCADE",
        )
    }
    assert checks == {
        "ck_evidence_chunk_embeddings_model_name_not_empty",
        "ck_evidence_chunk_embeddings_input_version_not_empty",
    }
    # No index on the vectors: every row is compared, so the answer is exact. The only index
    # is the one Postgres keeps for the unique key.
    assert [i["name"] for i in inspector.get_indexes(EMBEDDING_TABLE)] == [
        "uq_evidence_chunk_embeddings_chunk_model_version"
    ]


def test_evidence_chunk_embeddings_stores_a_vector_of_the_fixed_size(engine: Engine) -> None:
    with engine.connect() as conn:
        column_type, nullable = conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod), NOT attnotnull FROM pg_attribute"
                " WHERE attrelid = 'evidence_chunk_embeddings'::regclass"
                " AND attname = 'embedding'"
            )
        ).one()

    assert column_type == f"vector({EMBEDDING_DIMENSIONS})"
    assert nullable is False


def test_migration_0011_has_the_same_vector_size_and_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[EMBEDDING_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert migration_0011.EMBEDDING_DIMENSIONS == EMBEDDING_DIMENSIONS
    assert model_checks == {
        "ck_evidence_chunk_embeddings_model_name_not_empty": migration_0011.MODEL_NAME_NOT_EMPTY,
        "ck_evidence_chunk_embeddings_input_version_not_empty": (
            migration_0011.INPUT_VERSION_NOT_EMPTY
        ),
    }


def test_appeal_drafts_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(DRAFT_TABLE)}
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(DRAFT_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(DRAFT_TABLE)}

    assert unique == {"uq_appeal_drafts_claim_prompt": ["source_claim_id", "prompt_version"]}
    # Linked to the claim only: a new load of the evidence corpus must not remove a draft.
    assert foreign == {"fk_appeal_drafts_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")}
    assert checks == {
        "ck_appeal_drafts_prompt_version_not_empty",
        "ck_appeal_drafts_search_question_version_not_empty",
        "ck_appeal_drafts_model_name_not_empty",
        "ck_appeal_drafts_text_sha256_format",
    }


def test_migration_0012_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[DRAFT_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_appeal_drafts_prompt_version_not_empty": migration_0012.PROMPT_VERSION_NOT_EMPTY,
        "ck_appeal_drafts_search_question_version_not_empty": (
            migration_0012.SEARCH_QUESTION_VERSION_NOT_EMPTY
        ),
        "ck_appeal_drafts_model_name_not_empty": migration_0012.MODEL_NAME_NOT_EMPTY,
        "ck_appeal_drafts_text_sha256_format": migration_0012.TEXT_SHA256_FORMAT,
    }


def test_agent_runs_has_its_key_link_and_check_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    unique = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints(AGENT_RUN_TABLE)
    }
    foreign = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["options"].get("ondelete"))
        for f in inspector.get_foreign_keys(AGENT_RUN_TABLE)
    }
    checks = {c["name"] for c in inspector.get_check_constraints(AGENT_RUN_TABLE)}

    assert unique == {"uq_agent_runs_claim_agent_version": ["source_claim_id", "agent_version"]}
    # Linked to the claim only: not to the stored draft and not to the evidence tables.
    assert foreign == {"fk_agent_runs_claim": (["source_claim_id"], CLAIM_TABLE, "CASCADE")}
    assert checks == {
        "ck_agent_runs_agent_version_not_empty",
        "ck_agent_runs_text_sha256_format",
        "ck_agent_runs_trace_end_state_matches",
        "ck_agent_runs_trace_agent_version_matches",
    }


def test_migration_0013_lists_the_same_values_as_the_model() -> None:
    assert tuple(member.value for member in EndState) == migration_0013.AGENT_END_STATE


def test_migration_0013_has_the_same_check_rules_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[AGENT_RUN_TABLE].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_agent_runs_agent_version_not_empty": migration_0013.AGENT_VERSION_NOT_EMPTY,
        "ck_agent_runs_text_sha256_format": migration_0013.TEXT_SHA256_FORMAT,
        "ck_agent_runs_trace_end_state_matches": migration_0013.TRACE_END_STATE_MATCHES,
        "ck_agent_runs_trace_agent_version_matches": migration_0013.TRACE_AGENT_VERSION_MATCHES,
    }


def test_migration_0015_has_the_same_check_rule_as_the_model() -> None:
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables["users"].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert model_checks == {
        "ck_users_email_normalized": migration_0015.EMAIL_NORMALIZED,
        "ck_users_token_version_not_negative": migration_0019.TOKEN_VERSION_NOT_NEGATIVE,
    }


def test_users_has_the_global_email_key_and_the_normalized_check(engine: Engine) -> None:
    inspector = inspect(engine)

    assert {key["name"] for key in inspector.get_unique_constraints("users")} == {
        "uq_users_email",
        "uq_users_account_id",
    }
    assert {check["name"] for check in inspector.get_check_constraints("users")} == {
        "ck_users_email_normalized",
        "ck_users_token_version_not_negative",
    }


# Names are `str | None` to the inspector; every constraint read here has a name.
KeysLinksChecks = tuple[
    dict[str | None, list[str]],
    dict[str | None, tuple[list[str], str, list[str], str | None]],
    set[str | None],
]


def _keys_links_and_checks(engine: Engine, table: str) -> KeysLinksChecks:
    inspector = inspect(engine)
    unique = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints(table)}
    # Named links only: the unnamed `account_id` link to `accounts` is checked for every
    # business table by the `account_id` test above.
    foreign = {
        f["name"]: (
            f["constrained_columns"],
            f["referred_table"],
            f["referred_columns"],
            f["options"].get("ondelete"),
        )
        for f in inspector.get_foreign_keys(table)
        if (f["name"] or "").startswith("fk_")
    }
    checks = {c["name"] for c in inspector.get_check_constraints(table)}
    return unique, foreign, checks


def test_uploads_has_its_keys_the_user_link_without_cascade_and_its_checks(
    engine: Engine,
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, "uploads")

    assert unique == {"uq_uploads_account_id": ["account_id", "id"]}
    assert foreign == {
        "fk_uploads_user": (["account_id", "user_id"], "users", ["account_id", "id"], None)
    }
    assert checks == {
        "ck_uploads_file_name_not_empty",
        "ck_uploads_byte_size_positive",
        "ck_uploads_text_not_empty",
        "ck_uploads_text_sha256_format",
    }


def test_jobs_has_its_keys_the_upload_link_carrying_the_account_and_its_checks(
    engine: Engine,
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, "jobs")

    assert unique == {
        "uq_jobs_account_id": ["account_id", "id"],
        "uq_jobs_upload_id": ["upload_id"],
    }
    assert foreign == {
        "fk_jobs_upload": (
            ["account_id", "upload_id"],
            "uploads",
            ["account_id", "id"],
            "CASCADE",
        )
    }
    assert checks == {
        "ck_jobs_failure_only_when_failed",
        "ck_jobs_end_state_only_when_done",
        "ck_jobs_times_match_status",
        "ck_jobs_finished_not_before_started",
        "ck_jobs_cost_usd_not_negative",
    }


@pytest.mark.parametrize(
    ("table", "expected_checks"),
    [
        (
            "upload_extractions",
            {
                "ck_upload_extractions_prompt_version_not_empty",
                "ck_upload_extractions_model_name_not_empty",
                "ck_upload_extractions_text_sha256_format",
            },
        ),
        (
            "upload_agent_runs",
            {
                "ck_upload_agent_runs_agent_version_not_empty",
                "ck_upload_agent_runs_text_sha256_format",
                "ck_upload_agent_runs_trace_end_state_matches",
                "ck_upload_agent_runs_trace_agent_version_matches",
            },
        ),
    ],
)
def test_job_result_tables_have_one_row_per_job_a_link_carrying_the_account_and_checks(
    engine: Engine, table: str, expected_checks: set[str]
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, table)

    assert unique == {f"uq_{table}_job_id": ["job_id"]}
    assert foreign == {
        f"fk_{table}_job": (["account_id", "job_id"], "jobs", ["account_id", "id"], "CASCADE")
    }
    assert checks == expected_checks


def test_migration_0016_lists_the_same_values_as_the_models() -> None:
    assert tuple(member.value for member in UploadFileType) == migration_0016.UPLOAD_FILE_TYPE
    assert tuple(member.value for member in JobStatus) == migration_0016.JOB_STATUS
    assert tuple(member.value for member in JobFailure) == migration_0016.JOB_FAILURE


def _model_checks(tables: tuple[str, ...]) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for table in tables
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, CheckConstraint)
    }


# The checks 0017 adds, two of them on tables that 0016 made.
MIGRATION_0017_CHECKS = {
    "ck_jobs_cost_usd_not_negative": migration_0017.COST_USD_NOT_NEGATIVE,
    "ck_upload_drafts_review_matches_status": migration_0017.REVIEW_MATCHES_STATUS,
    "ck_upload_drafts_approved_version_not_negative": (
        migration_0017.APPROVED_VERSION_NOT_NEGATIVE
    ),
    "ck_draft_revisions_version_positive": migration_0017.VERSION_POSITIVE,
    "ck_draft_revisions_text_sha256_format": migration_0017.TEXT_SHA256_FORMAT,
    "ck_audit_events_job_matches_action": migration_0017.JOB_MATCHES_ACTION,
    "ck_audit_events_detail_is_object": migration_0017.DETAIL_IS_OBJECT,
}


def test_migration_0016_has_the_same_check_rules_as_the_models() -> None:
    model_checks = {
        name: rule
        for name, rule in _model_checks(UPLOAD_TABLES).items()
        if name not in MIGRATION_0017_CHECKS
    }

    assert model_checks == {
        "ck_uploads_file_name_not_empty": migration_0016.FILE_NAME_NOT_EMPTY,
        "ck_uploads_byte_size_positive": migration_0016.BYTE_SIZE_POSITIVE,
        "ck_uploads_text_not_empty": migration_0016.TEXT_NOT_EMPTY,
        "ck_uploads_text_sha256_format": migration_0016.TEXT_SHA256_FORMAT,
        "ck_jobs_failure_only_when_failed": migration_0016.FAILURE_ONLY_WHEN_FAILED,
        "ck_jobs_end_state_only_when_done": migration_0016.END_STATE_ONLY_WHEN_DONE,
        "ck_jobs_times_match_status": migration_0016.TIMES_MATCH_STATUS,
        "ck_jobs_finished_not_before_started": migration_0016.FINISHED_NOT_BEFORE_STARTED,
        "ck_upload_extractions_prompt_version_not_empty": migration_0016.PROMPT_VERSION_NOT_EMPTY,
        "ck_upload_extractions_model_name_not_empty": migration_0016.MODEL_NAME_NOT_EMPTY,
        "ck_upload_extractions_text_sha256_format": migration_0016.TEXT_SHA256_FORMAT,
        "ck_upload_agent_runs_agent_version_not_empty": migration_0016.AGENT_VERSION_NOT_EMPTY,
        "ck_upload_agent_runs_text_sha256_format": migration_0016.TEXT_SHA256_FORMAT,
        "ck_upload_agent_runs_trace_end_state_matches": migration_0016.TRACE_END_STATE_MATCHES,
        "ck_upload_agent_runs_trace_agent_version_matches": (
            migration_0016.TRACE_AGENT_VERSION_MATCHES
        ),
        "ck_upload_drafts_prompt_version_not_empty": migration_0016.PROMPT_VERSION_NOT_EMPTY,
        "ck_upload_drafts_search_question_version_not_empty": (
            migration_0016.SEARCH_QUESTION_VERSION_NOT_EMPTY
        ),
        "ck_upload_drafts_model_name_not_empty": migration_0016.MODEL_NAME_NOT_EMPTY,
        "ck_upload_drafts_text_sha256_format": migration_0016.TEXT_SHA256_FORMAT,
    }


def test_upload_drafts_has_its_keys_the_job_and_reviewer_links_and_its_checks(
    engine: Engine,
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, "upload_drafts")

    assert unique == {
        "uq_upload_drafts_job_id": ["job_id"],
        "uq_upload_drafts_account_job_id": ["account_id", "job_id"],
    }
    assert foreign == {
        "fk_upload_drafts_job": (["account_id", "job_id"], "jobs", ["account_id", "id"], "CASCADE"),
        "fk_upload_drafts_reviewer": (
            ["account_id", "reviewed_by"],
            "users",
            ["account_id", "id"],
            None,
        ),
    }
    assert checks == {
        "ck_upload_drafts_prompt_version_not_empty",
        "ck_upload_drafts_search_question_version_not_empty",
        "ck_upload_drafts_model_name_not_empty",
        "ck_upload_drafts_text_sha256_format",
        "ck_upload_drafts_review_matches_status",
        "ck_upload_drafts_approved_version_not_negative",
    }


def test_draft_revisions_has_one_version_per_number_a_cascading_draft_link_and_its_checks(
    engine: Engine,
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, "draft_revisions")

    assert unique == {"uq_draft_revisions_job_version": ["job_id", "version"]}
    assert foreign == {
        "fk_draft_revisions_draft": (
            ["account_id", "job_id"],
            "upload_drafts",
            ["account_id", "job_id"],
            "CASCADE",
        ),
        "fk_draft_revisions_editor": (
            ["account_id", "edited_by"],
            "users",
            ["account_id", "id"],
            None,
        ),
    }
    assert checks == {
        "ck_draft_revisions_version_positive",
        "ck_draft_revisions_text_sha256_format",
    }


def test_audit_events_has_user_and_job_links_without_cascade_and_its_checks(
    engine: Engine,
) -> None:
    unique, foreign, checks = _keys_links_and_checks(engine, "audit_events")

    assert unique == {}
    assert foreign == {
        "fk_audit_events_user": (["account_id", "user_id"], "users", ["account_id", "id"], None),
        "fk_audit_events_job": (["account_id", "job_id"], "jobs", ["account_id", "id"], None),
    }
    assert checks == {"ck_audit_events_job_matches_action", "ck_audit_events_detail_is_object"}


def test_migration_0017_lists_the_same_values_as_the_models() -> None:
    assert tuple(member.value for member in DraftReviewStatus) == (
        migration_0017.DRAFT_REVIEW_STATUS
    )
    assert tuple(member.value for member in DraftRejectReason) == (
        migration_0017.DRAFT_REJECT_REASON
    )
    # 0019 added one audit action; the rest are the ones 0017 made.
    assert migration_0017.AUDIT_ACTION == migration_0019.AUDIT_ACTION_BEFORE


def test_migration_0017_has_the_same_check_rules_as_the_models() -> None:
    model_checks = _model_checks((*UPLOAD_TABLES, *REVIEW_TABLES))
    # 0019 replaced one rule of 0017; the test of 0019 compares the new one.
    kept = {
        name: rule
        for name, rule in MIGRATION_0017_CHECKS.items()
        if name != migration_0019.JOB_CHECK_NAME
    }

    assert {name: rule for name, rule in model_checks.items() if name in kept} == kept
    assert migration_0017.JOB_MATCHES_ACTION == migration_0019.JOB_MATCHES_ACTION_BEFORE
    assert set(_model_checks(REVIEW_TABLES)) <= set(MIGRATION_0017_CHECKS)


def test_login_failures_has_its_two_indexes_and_hash_check_and_no_links(engine: Engine) -> None:
    inspector = inspect(engine)
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes(LOGIN_FAILURE_TABLE)}
    unique, foreign, checks = _keys_links_and_checks(engine, LOGIN_FAILURE_TABLE)

    assert indexes == {
        "ix_login_failures_key_kind_key_hash_created_at": ["key_kind", "key_hash", "created_at"],
        "ix_login_failures_created_at": ["created_at"],
    }
    assert unique == {}
    assert inspector.get_foreign_keys(LOGIN_FAILURE_TABLE) == []
    assert foreign == {}
    assert checks == {"ck_login_failures_key_hash_format"}


def test_migration_0018_lists_the_same_values_and_check_rule_as_the_model() -> None:
    assert tuple(member.value for member in LoginKeyKind) == migration_0018.LOGIN_KEY_KIND
    assert _model_checks((LOGIN_FAILURE_TABLE,)) == {
        "ck_login_failures_key_hash_format": migration_0018.KEY_HASH_FORMAT
    }


def test_migration_0019_lists_the_same_audit_actions_and_check_rules_as_the_models() -> None:
    # 0020 added one audit action and replaced the job rule; its test compares the new ones.
    assert (
        *migration_0019.AUDIT_ACTION_BEFORE,
        migration_0019.NEW_AUDIT_ACTION,
    ) == migration_0020.AUDIT_ACTION_BEFORE
    assert migration_0020.JOB_MATCHES_ACTION_BEFORE == migration_0019.JOB_MATCHES_ACTION
    assert _model_checks(("users",))["ck_users_token_version_not_negative"] == (
        migration_0019.TOKEN_VERSION_NOT_NEGATIVE
    )


def test_users_token_version_starts_at_zero_and_refuses_a_negative_number(
    engine: Engine,
) -> None:
    insert_user = text(
        "INSERT INTO users (account_id, email, password_hash, role)"
        " VALUES (:a, 'v@example.com', 'x', 'viewer') RETURNING token_version"
    )
    try:
        with engine.begin() as conn:
            account_id = conn.scalar(text("INSERT INTO accounts (name) VALUES ('v') RETURNING id"))
            assert conn.scalar(insert_user, {"a": account_id}) == 0
        with (
            pytest.raises(IntegrityError, match="ck_users_token_version_not_negative"),
            engine.begin() as conn,
        ):
            conn.execute(text("UPDATE users SET token_version = -1"))
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts"))


def test_password_reset_tokens_has_its_keys_the_cascading_user_link_indexes_and_check(
    engine: Engine,
) -> None:
    # Without the index Postgres makes for the unique key; that key is checked below.
    indexes = {
        i["name"]: i["column_names"]
        for i in inspect(engine).get_indexes(RESET_TABLE)
        if "duplicates_constraint" not in i
    }
    unique, foreign, checks = _keys_links_and_checks(engine, RESET_TABLE)

    assert indexes == {
        "ix_password_reset_tokens_account_id": ["account_id"],
        "ix_password_reset_tokens_account_id_user_id_created_at": [
            "account_id",
            "user_id",
            "created_at",
        ],
    }
    assert unique == {"uq_password_reset_tokens_token_hash": ["token_hash"]}
    assert foreign == {
        "fk_password_reset_tokens_user": (
            ["account_id", "user_id"],
            "users",
            ["account_id", "id"],
            "CASCADE",
        ),
    }
    assert checks == {"ck_password_reset_tokens_token_hash_format"}


def test_migration_0020_lists_the_same_audit_actions_and_check_rules_as_the_models() -> None:
    assert tuple(member.value for member in AuditAction) == (
        *migration_0020.AUDIT_ACTION_BEFORE,
        migration_0020.NEW_AUDIT_ACTION,
    )
    assert _model_checks(REVIEW_TABLES)[migration_0020.JOB_CHECK_NAME] == (
        migration_0020.JOB_MATCHES_ACTION
    )
    assert _model_checks((RESET_TABLE,)) == {
        "ck_password_reset_tokens_token_hash_format": migration_0020.TOKEN_HASH_FORMAT
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


def _type_names(engine: Engine, names: tuple[str, ...]) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT typname FROM pg_type WHERE typname = ANY(:names)"),
                {"names": list(names)},
            )
        )


def _review_columns(engine: Engine) -> tuple[set[str], set[str]]:
    inspector = inspect(engine)
    return (
        {column["name"] for column in inspector.get_columns("upload_drafts")},
        {column["name"] for column in inspector.get_columns("jobs")},
    )


def _store_a_draft_with_sql(engine: Engine) -> None:
    # Raw SQL, because at 0016 the models have columns the table does not.
    with engine.begin() as conn:
        account_id = conn.scalar(text("INSERT INTO accounts (name) VALUES ('m') RETURNING id"))
        user_id = conn.scalar(
            text(
                "INSERT INTO users (account_id, email, password_hash, role)"
                " VALUES (:a, 'm@example.com', 'x', 'reviewer') RETURNING id"
            ),
            {"a": account_id},
        )
        upload_id = conn.scalar(
            text(
                "INSERT INTO uploads (account_id, user_id, file_name, file_type, byte_size, text,"
                " text_sha256) VALUES (:a, :u, 'l.txt', 'text', 4, 'made', :h) RETURNING id"
            ),
            {"a": account_id, "u": user_id, "h": "0" * 64},
        )
        job_id = conn.scalar(
            text(
                "INSERT INTO jobs (account_id, upload_id, status, end_state, started_at,"
                " finished_at) VALUES (:a, :u, 'done', 'needs_review', now(), now()) RETURNING id"
            ),
            {"a": account_id, "u": upload_id},
        )
        conn.execute(
            text(
                "INSERT INTO upload_drafts (account_id, job_id, prompt_version,"
                " search_question_version, model_name, provider, text_sha256, letter)"
                " VALUES (:a, :j, 'v3', 'v3', 'm', 'primary', :h, '{}')"
            ),
            {"a": account_id, "j": job_id, "h": "0" * 64},
        )


def test_upgrade_to_0017_makes_existing_drafts_pending_and_existing_jobs_cost_nothing(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0016")
    try:
        _store_a_draft_with_sql(engine)
        command.upgrade(alembic_config, "0017")

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT d.review_status::text, d.reviewed_by, j.cost_usd"
                    " FROM upload_drafts d JOIN jobs j ON j.id = d.job_id"
                )
            ).one()
        assert tuple(row) == ("pending", None, 0)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts"))
        command.upgrade(alembic_config, "head")


def _audit_action_values(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text("SELECT unnest(enum_range(NULL::audit_action))::text")))


def _store_audit_events_with_sql(
    engine: Engine, actions: tuple[str, ...] = ("account_created", "password_changed")
) -> None:
    # One event of each kind without a job: the old one stays, the new one goes on downgrade.
    with engine.begin() as conn:
        account_id = conn.scalar(text("INSERT INTO accounts (name) VALUES ('p') RETURNING id"))
        user_id = conn.scalar(
            text(
                "INSERT INTO users (account_id, email, password_hash, role)"
                " VALUES (:a, 'p@example.com', 'x', 'admin') RETURNING id"
            ),
            {"a": account_id},
        )
        for action in actions:
            conn.execute(
                text(
                    "INSERT INTO audit_events (account_id, user_id, action)"
                    " VALUES (:a, :u, CAST(:action AS audit_action))"
                ),
                {"a": account_id, "u": user_id, "action": action},
            )


def test_downgrade_to_0019_removes_the_reset_table_password_reset_and_its_rows(
    engine: Engine, alembic_config: Config
) -> None:
    try:
        _store_audit_events_with_sql(engine, ("password_changed", "password_reset"))
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO password_reset_tokens"
                    " (account_id, user_id, token_hash, expires_at)"
                    " SELECT account_id, id, :hash, now() FROM users"
                ),
                {"hash": "0123456789abcdef" * 4},
            )
        command.downgrade(alembic_config, "0019")

        assert RESET_TABLE not in set(inspect(engine).get_table_names())
        assert _audit_action_values(engine) == list(migration_0020.AUDIT_ACTION_BEFORE)
        with engine.connect() as conn:
            actions = list(conn.scalars(text("SELECT action::text FROM audit_events")))
            rule = conn.scalar(
                text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
                {"name": migration_0020.JOB_CHECK_NAME},
            )
        assert actions == ["password_changed"]
        assert "password_reset" not in rule
        assert "password_changed" in rule
        assert _type_names(engine, (migration_0020.OLD_TYPE_NAME,)) == set()
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts"))

    assert _audit_action_values(engine) == [member.value for member in AuditAction]
    assert RESET_TABLE in set(inspect(engine).get_table_names())


def test_downgrade_to_0018_removes_password_changed_its_rows_and_the_token_version(
    engine: Engine, alembic_config: Config
) -> None:
    try:
        _store_audit_events_with_sql(engine)
        command.downgrade(alembic_config, "0018")

        assert _audit_action_values(engine) == list(migration_0019.AUDIT_ACTION_BEFORE)
        inspector = inspect(engine)
        assert "token_version" not in {c["name"] for c in inspector.get_columns("users")}
        assert {c["name"] for c in inspector.get_check_constraints("users")} == {
            "ck_users_email_normalized"
        }
        with engine.connect() as conn:
            actions = list(conn.scalars(text("SELECT action::text FROM audit_events")))
            rule = conn.scalar(
                text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
                {"name": migration_0019.JOB_CHECK_NAME},
            )
        assert actions == ["account_created"]
        assert "password_changed" not in rule
        assert _type_names(engine, (migration_0019.OLD_TYPE_NAME,)) == set()
        # The old rule is back: an upload event still needs its job.
        with (
            pytest.raises(IntegrityError, match=migration_0019.JOB_CHECK_NAME),
            engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "INSERT INTO audit_events (account_id, user_id, action)"
                    " SELECT account_id, id, 'upload_created' FROM users"
                )
            )
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts"))

    assert _audit_action_values(engine) == [member.value for member in AuditAction]
    assert "token_version" in {c["name"] for c in inspect(engine).get_columns("users")}


def test_downgrade_to_0017_removes_the_login_failures_table_and_its_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0017")
    try:
        tables = set(inspect(engine).get_table_names())
        assert LOGIN_FAILURE_TABLE not in tables
        assert set(REVIEW_TABLES) <= tables
        assert _type_names(engine, migration_0018.NEW_ENUMS) == set()
    finally:
        command.upgrade(alembic_config, "head")

    assert LOGIN_FAILURE_TABLE in set(inspect(engine).get_table_names())
    assert _type_names(engine, migration_0018.NEW_ENUMS) == set(migration_0018.NEW_ENUMS)


def test_downgrade_to_0016_removes_the_review_tables_columns_and_enums_and_keeps_the_drafts(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0016")
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables.isdisjoint(REVIEW_TABLES)
        assert set(UPLOAD_TABLES) <= tables
        draft_columns, job_columns = _review_columns(engine)
        assert draft_columns.isdisjoint(
            {"review_status", "reviewed_by", "reviewed_at", "reject_reason", "approved_version"}
        )
        assert "cost_usd" not in job_columns
        assert _type_names(engine, migration_0017.NEW_ENUMS) == set()
        unique, foreign, checks = _keys_links_and_checks(engine, "upload_drafts")
        assert set(unique) == {"uq_upload_drafts_job_id"}
        assert set(foreign) == {"fk_upload_drafts_job"}
        assert "ck_jobs_cost_usd_not_negative" not in _keys_links_and_checks(engine, "jobs")[2]
        assert len(checks) == 4
    finally:
        command.upgrade(alembic_config, "head")

    assert set(REVIEW_TABLES) <= set(inspect(engine).get_table_names())


def test_downgrade_to_0015_removes_the_upload_tables_and_enums_and_keeps_the_shared_enums(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0015")
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables.isdisjoint(UPLOAD_TABLES)
        assert {AGENT_RUN_TABLE, DRAFT_TABLE, "users"} <= tables
        assert _type_names(engine, migration_0016.NEW_ENUMS) == set()
        # Made by 0008 and 0013, used by 0016: the downgrade must not drop them.
        assert _type_names(engine, ("agent_end_state", "llm_provider")) == {
            "agent_end_state",
            "llm_provider",
        }
        assert {key["name"] for key in inspect(engine).get_unique_constraints("users")} == {
            "uq_users_email"
        }
    finally:
        command.upgrade(alembic_config, "head")

    assert set(UPLOAD_TABLES) <= set(inspect(engine).get_table_names())


def test_downgrade_to_0014_removes_the_password_hash_and_allows_one_email_per_account(
    engine: Engine, alembic_config: Config
) -> None:
    try:
        command.downgrade(alembic_config, "0014")

        inspector = inspect(engine)
        assert "password_hash" not in {column["name"] for column in inspector.get_columns("users")}
        assert {key["name"] for key in inspector.get_unique_constraints("users")} == {
            "uq_users_account_email"
        }
        assert inspector.get_check_constraints("users") == []
        with engine.begin() as conn:
            account_ids = list(
                conn.scalars(text("INSERT INTO accounts (name) VALUES ('a'), ('b') RETURNING id"))
            )
            for account_id in account_ids:
                conn.execute(
                    text(
                        "INSERT INTO users (account_id, email, role)"
                        " VALUES (:account_id, 'Same@example.com', 'viewer')"
                    ),
                    {"account_id": account_id},
                )
            stored = conn.scalar(text("SELECT count(*) FROM users"))
        assert stored == 2
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts"))
        command.upgrade(alembic_config, "head")

    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert "password_hash" in columns


def test_downgrade_to_0013_removes_the_manual_source_and_its_rows_and_keeps_the_rest(
    engine: Engine, alembic_config: Config
) -> None:
    insert_document = text(
        "INSERT INTO evidence_documents (source, source_document_id, section_number, title,"
        " version_number, effective_date, source_file_date) VALUES (:source, :document_id,"
        " '999.1', 'Made-up Section', 1, '2020-01-01', '2026-10-05') RETURNING id"
    )
    insert_chunk = text(
        "INSERT INTO evidence_chunks (evidence_document_id, chunk_index, text, text_sha256,"
        " chunker_version) VALUES (:document_id, 0, 'Made-up policy text.', :sha, 'v1')"
    )
    try:
        with engine.begin() as conn:
            conn.execute(insert_document, {"source": "cms_ncd", "document_id": "9001"})
            manual_id = conn.scalar(
                insert_document, {"source": "cms_manual", "document_id": "made-up-9001"}
            )
            conn.execute(insert_chunk, {"document_id": manual_id, "sha": "a" * 64})

        command.downgrade(alembic_config, "0013")

        assert _evidence_source_values(engine) == ["cms_ncd"]
        with engine.connect() as conn:
            sources = list(conn.scalars(text("SELECT source::text FROM evidence_documents")))
            chunks = conn.scalar(text("SELECT count(*) FROM evidence_chunks"))
            old_types = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'evidence_source_old'")
            )
        assert sources == ["cms_ncd"]
        assert chunks == 0
        assert old_types == 0
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM evidence_documents"))

    assert _evidence_source_values(engine) == ["cms_ncd", "cms_manual"]


def test_downgrade_to_0012_removes_agent_runs_and_its_enum_and_keeps_appeal_drafts(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0012")
    try:
        tables = set(inspect(engine).get_table_names())
        assert AGENT_RUN_TABLE not in tables
        assert DRAFT_TABLE in tables
        with engine.connect() as conn:
            leftover = conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'agent_end_state'")
            )
        assert leftover == 0
    finally:
        command.upgrade(alembic_config, "head")

    assert AGENT_RUN_TABLE in inspect(engine).get_table_names()


def test_downgrade_to_0011_removes_appeal_drafts_and_keeps_the_provider_enum(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0011")
    try:
        tables = set(inspect(engine).get_table_names())
        assert DRAFT_TABLE not in tables
        assert {EXTRACTION_TABLE, LLM_CALL_TABLE, EMBEDDING_TABLE} <= tables
        with engine.connect() as conn:
            kept = conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'llm_provider'"))
        assert kept == 1
    finally:
        command.upgrade(alembic_config, "head")

    assert DRAFT_TABLE in inspect(engine).get_table_names()


def test_downgrade_to_0010_removes_the_embeddings_table_and_keeps_the_vector_extension(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "0010")
    try:
        tables = set(inspect(engine).get_table_names())
        assert EMBEDDING_TABLE not in tables
        assert {EVIDENCE_DOCUMENT_TABLE, EVIDENCE_CHUNK_TABLE} <= tables
        with engine.connect() as conn:
            extensions = conn.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            )
        assert extensions == 1
    finally:
        command.upgrade(alembic_config, "head")

    assert EMBEDDING_TABLE in inspect(engine).get_table_names()


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
