"""Core tables (accounts, users, claims, denials), public reference tables and `llm_calls`."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AccountScopedMixin, Base, TimestampMixin


class UserRole(StrEnum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class DenialStatus(StrEnum):
    NEW = "new"
    IN_REVIEW = "in_review"
    APPEALED = "appealed"
    CLOSED = "closed"


class ExchangeType(StrEnum):
    FFE = "FFE"
    SPE = "SPE"
    SBE_FP = "SBE-FP"


class PlanType(StrEnum):
    HMO = "HMO"
    EPO = "EPO"
    PPO = "PPO"
    POS = "POS"


class MetalLevel(StrEnum):
    BRONZE = "Bronze"
    SILVER = "Silver"
    GOLD = "Gold"
    PLATINUM = "Platinum"
    CATASTROPHIC = "Catastrophic"


class DenialReasonCategory(StrEnum):
    NONCOVERED = "noncovered"
    MEDICAL_NECESSITY = "medical_necessity"
    DUPLICATE = "duplicate"
    BENEFITS_EXHAUSTED = "benefits_exhausted"
    COORDINATION_OF_BENEFITS = "coordination_of_benefits"
    OTHER = "other"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class DocumentType(StrEnum):
    DENIAL_LETTER = "denial_letter"
    CLINICAL_NOTE = "clinical_note"
    PRIOR_AUTH = "prior_auth"


class LlmProvider(StrEnum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


def _values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class Account(TimestampMixin, Base):
    """A customer organisation. Owns every business row."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)


class User(AccountScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("account_id", "email", name="uq_users_account_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=_values),
        nullable=False,
        default=UserRole.VIEWER,
    )


class Claim(AccountScopedMixin, TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("account_id", "claim_number", name="uq_claims_account_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_number: Mapped[str] = mapped_column(String(64), nullable=False)
    payer: Mapped[str] = mapped_column(String(200), nullable=False)
    billed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)


class Denial(AccountScopedMixin, TimestampMixin, Base):
    __tablename__ = "denials"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    denied_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[DenialStatus] = mapped_column(
        Enum(DenialStatus, name="denial_status", values_callable=_values),
        nullable=False,
        default=DenialStatus.NEW,
    )


ISSUER_COUNT_COLUMNS = (
    "claims_received_out_of_network",
    "claims_received_in_network",
    "claims_denied_out_of_network",
    "claims_denied_in_network",
    "claims_resubmitted_out_of_network",
    "claims_resubmitted_in_network",
    "internal_appeals_filed",
    "internal_appeals_overturned",
    "external_appeals_filed",
    "external_appeals_overturned",
)
ISSUER_PERCENT_COLUMNS = ("internal_appeals_overturned_pct", "external_appeals_overturned_pct")


class IssuerDenialStats(TimestampMixin, Base):
    """Issuer-level claim and appeal counts from the CMS Transparency in Coverage PUF.

    Public reference table shared by all accounts, so it has no `account_id`.
    A count or percent is NULL when the source suppressed it or it did not apply.
    """

    __tablename__ = "issuer_denial_stats"
    __table_args__ = (
        UniqueConstraint("issuer_id", "plan_year", name="uq_issuer_denial_stats_issuer_year"),
        CheckConstraint("issuer_id ~ '^[0-9]{5}$'", name="ck_issuer_denial_stats_issuer_id_format"),
        CheckConstraint(
            " AND ".join(f"{column} >= 0" for column in ISSUER_COUNT_COLUMNS),
            name="ck_issuer_denial_stats_counts_non_negative",
        ),
        CheckConstraint(
            " AND ".join(f"{column} BETWEEN 0 AND 100" for column in ISSUER_PERCENT_COLUMNS),
            name="ck_issuer_denial_stats_percents_in_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_year: Mapped[int] = mapped_column(Integer, nullable=False)
    issuer_id: Mapped[str] = mapped_column(String(5), nullable=False)
    issuer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    exchange_type: Mapped[ExchangeType] = mapped_column(
        Enum(ExchangeType, name="exchange_type", values_callable=_values), nullable=False
    )
    is_new_to_exchange: Mapped[bool] = mapped_column(Boolean, nullable=False)
    claims_received_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_received_in_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_denied_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_denied_in_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_resubmitted_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_resubmitted_in_network: Mapped[int | None] = mapped_column(BigInteger)
    internal_appeals_filed: Mapped[int | None] = mapped_column(BigInteger)
    internal_appeals_overturned: Mapped[int | None] = mapped_column(BigInteger)
    internal_appeals_overturned_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    external_appeals_filed: Mapped[int | None] = mapped_column(BigInteger)
    external_appeals_overturned: Mapped[int | None] = mapped_column(BigInteger)
    external_appeals_overturned_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


PLAN_COUNT_COLUMNS = (
    "claims_received_out_of_network",
    "claims_received_in_network",
    "claims_denied_out_of_network",
    "claims_denied_in_network",
    "claims_resubmitted_out_of_network",
    "claims_resubmitted_in_network",
    "denied_referral_required",
    "denied_out_of_network",
    "denied_services_excluded",
    "denied_not_medically_necessary_non_bh",
    "denied_not_medically_necessary_bh",
    "denied_benefit_limit_reached",
    "denied_member_not_covered",
    "denied_investigational_experimental_cosmetic",
    "denied_administrative_reason",
    "denied_other",
)


class PlanDenialStats(TimestampMixin, Base):
    """Plan-level claim counts and denial reasons from the CMS Transparency in Coverage PUF.

    Public reference table shared by all accounts, so it has no `account_id`.
    `is_reported` is false for a plan with no published figures (`N/A`, `***` or `*` in every
    count cell, typically a plan new to the Exchange): all its counts are NULL.
    On a reported plan, a NULL count means the source suppressed it (a small number, not zero).
    The denial reasons overlap: their sum can be larger than the claims denied.
    """

    __tablename__ = "plan_denial_stats"
    __table_args__ = (
        UniqueConstraint("plan_id", "plan_year", name="uq_plan_denial_stats_plan_year"),
        ForeignKeyConstraint(
            ["issuer_id", "plan_year"],
            ["issuer_denial_stats.issuer_id", "issuer_denial_stats.plan_year"],
            name="fk_plan_denial_stats_issuer_year",
            ondelete="CASCADE",
        ),
        Index("ix_plan_denial_stats_issuer_year", "issuer_id", "plan_year"),
        CheckConstraint(
            "plan_id ~ '^[0-9]{5}[A-Z]{2}[0-9]{7}$'", name="ck_plan_denial_stats_plan_id_format"
        ),
        CheckConstraint(
            "left(plan_id, 5) = issuer_id AND substr(plan_id, 6, 2) = state",
            name="ck_plan_denial_stats_plan_id_matches_issuer",
        ),
        CheckConstraint(
            " AND ".join(f"{column} >= 0" for column in PLAN_COUNT_COLUMNS),
            name="ck_plan_denial_stats_counts_non_negative",
        ),
        CheckConstraint(
            "is_reported OR ("
            + " AND ".join(f"{column} IS NULL" for column in PLAN_COUNT_COLUMNS)
            + ")",
            name="ck_plan_denial_stats_unreported_has_no_counts",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_year: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(14), nullable=False)
    issuer_id: Mapped[str] = mapped_column(String(5), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    plan_type: Mapped[PlanType] = mapped_column(
        Enum(PlanType, name="plan_type", values_callable=_values), nullable=False
    )
    metal_level: Mapped[MetalLevel] = mapped_column(
        Enum(MetalLevel, name="metal_level", values_callable=_values), nullable=False
    )
    is_reported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    claims_received_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_received_in_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_denied_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_denied_in_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_resubmitted_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    claims_resubmitted_in_network: Mapped[int | None] = mapped_column(BigInteger)
    denied_referral_required: Mapped[int | None] = mapped_column(BigInteger)
    denied_out_of_network: Mapped[int | None] = mapped_column(BigInteger)
    denied_services_excluded: Mapped[int | None] = mapped_column(BigInteger)
    denied_not_medically_necessary_non_bh: Mapped[int | None] = mapped_column(BigInteger)
    denied_not_medically_necessary_bh: Mapped[int | None] = mapped_column(BigInteger)
    denied_benefit_limit_reached: Mapped[int | None] = mapped_column(BigInteger)
    denied_member_not_covered: Mapped[int | None] = mapped_column(BigInteger)
    denied_investigational_experimental_cosmetic: Mapped[int | None] = mapped_column(BigInteger)
    denied_administrative_reason: Mapped[int | None] = mapped_column(BigInteger)
    denied_other: Mapped[int | None] = mapped_column(BigInteger)


CLAIM_LINE_AMOUNT_COLUMNS = (
    "payment_amount",
    "deductible_amount",
    "primary_payer_paid_amount",
    "coinsurance_amount",
    "allowed_charge_amount",
)
CLAIM_LINE_MAX_NUMBER = 13


class ClaimSample(TimestampMixin, Base):
    """One synthetic claim from the CMS DE-SynPUF carrier claims file.

    Public reference table shared by all accounts, so it has no `account_id`. The source is
    fully synthetic: no row is a real person's claim.
    `diagnosis_codes` holds the claim's ICD-9 codes in source order with empty slots dropped;
    it can be empty.
    """

    __tablename__ = "claim_samples"
    __table_args__ = (
        UniqueConstraint("source_claim_id", name="uq_claim_samples_source_claim_id"),
        CheckConstraint(
            "source_claim_id ~ '^[0-9]{15}$'", name="ck_claim_samples_source_claim_id_format"
        ),
        CheckConstraint(
            "claim_from_date <= claim_thru_date", name="ck_claim_samples_from_not_after_thru"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(String(15), nullable=False)
    claim_from_date: Mapped[date] = mapped_column(Date, nullable=False)
    claim_thru_date: Mapped[date] = mapped_column(Date, nullable=False)
    diagnosis_codes: Mapped[list[str]] = mapped_column(ARRAY(String(5)), nullable=False)


class ClaimSampleLine(TimestampMixin, Base):
    """One service line of a `ClaimSample`.

    Public reference table, no `account_id`. `processing_indicator` is the source's one-character
    code (`A` means allowed); it is kept as published because the file uses values the CMS
    codebook does not define. The amounts are kept as published too: they do not have to add
    up, and a payment can be above the allowed charge.
    """

    __tablename__ = "claim_sample_lines"
    __table_args__ = (
        UniqueConstraint("source_claim_id", "line_number", name="uq_claim_sample_lines_claim_line"),
        CheckConstraint(
            f"line_number BETWEEN 1 AND {CLAIM_LINE_MAX_NUMBER}",
            name="ck_claim_sample_lines_line_number_in_range",
        ),
        CheckConstraint(
            "char_length(processing_indicator) = 1",
            name="ck_claim_sample_lines_processing_indicator_one_char",
        ),
        CheckConstraint(
            " AND ".join(f"{column} >= 0" for column in CLAIM_LINE_AMOUNT_COLUMNS),
            name="ck_claim_sample_lines_amounts_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey(
            "claim_samples.source_claim_id",
            name="fk_claim_sample_lines_claim",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hcpcs_code: Mapped[str | None] = mapped_column(String(5))
    line_diagnosis_code: Mapped[str | None] = mapped_column(String(5))
    processing_indicator: Mapped[str] = mapped_column(String(1), nullable=False)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    deductible_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    primary_payer_paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    coinsurance_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    allowed_charge_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class ClaimSampleLabel(TimestampMixin, Base):
    """The proxy label and the dataset split of one `ClaimSample`.

    Public reference table, no `account_id`. One row per claim; a new rule version replaces
    the row. Nothing here is an observed outcome: the source has no appeals, so
    `appeal_success_proxy` comes from a documented rule, named by `label_rule_version`.
    A denied claim has a reason category and a proxy value; a claim that is not denied has
    neither.
    """

    __tablename__ = "claim_sample_labels"
    __table_args__ = (
        UniqueConstraint("source_claim_id", name="uq_claim_sample_labels_source_claim_id"),
        CheckConstraint(
            "is_denied = (denial_reason_category IS NOT NULL)"
            " AND is_denied = (appeal_success_proxy IS NOT NULL)",
            name="ck_claim_sample_labels_denied_fields_match",
        ),
        CheckConstraint(
            "label_rule_version <> ''", name="ck_claim_sample_labels_rule_version_not_empty"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey(
            "claim_samples.source_claim_id",
            name="fk_claim_sample_labels_claim",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    is_denied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    denial_reason_category: Mapped[DenialReasonCategory | None] = mapped_column(
        Enum(DenialReasonCategory, name="denial_reason_category", values_callable=_values)
    )
    appeal_success_proxy: Mapped[bool | None] = mapped_column(Boolean)
    label_rule_version: Mapped[str] = mapped_column(String(20), nullable=False)
    split: Mapped[DatasetSplit] = mapped_column(
        Enum(DatasetSplit, name="dataset_split", values_callable=_values), nullable=False
    )
    split_seed: Mapped[int] = mapped_column(Integer, nullable=False)


class GeneratedDocument(TimestampMixin, Base):
    """One fabricated document written for a `ClaimSample`.

    Public reference table, no `account_id`: it is built only from the synthetic claims sample
    and holds no customer data. One row per claim and document type; a new run replaces the
    rows. `text` is the document as plain text, after the noise step: it can hold look-alike
    characters, dropped characters, damaged layout and a blanked field. `answer_key` holds
    every value printed in the clean document as structured fields, so an extractor can be
    marked against them. `noise_record` says what the noise step did to the text; its contents
    are not checked by the table. `generator_version` names the generator of the clean text,
    `noise_version` the noise step. The same claim, `seed`, `generator_version` and
    `noise_version` always give the same text.
    """

    __tablename__ = "generated_documents"
    __table_args__ = (
        UniqueConstraint(
            "source_claim_id", "document_type", name="uq_generated_documents_claim_type"
        ),
        CheckConstraint("template_id <> ''", name="ck_generated_documents_template_id_not_empty"),
        CheckConstraint("seed >= 0", name="ck_generated_documents_seed_not_negative"),
        CheckConstraint(
            "generator_version <> ''", name="ck_generated_documents_generator_version_not_empty"
        ),
        CheckConstraint("text <> ''", name="ck_generated_documents_text_not_empty"),
        CheckConstraint(
            "noise_version <> ''", name="ck_generated_documents_noise_version_not_empty"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey(
            "claim_samples.source_claim_id",
            name="fk_generated_documents_claim",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type", values_callable=_values), nullable=False
    )
    template_id: Mapped[str] = mapped_column(String(40), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    generator_version: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    noise_version: Mapped[str] = mapped_column(String(20), nullable=False)
    noise_record: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class LlmCall(TimestampMixin, Base):
    """One model call that returned an answer, with what it cost.

    Operational table, no `account_id`: it holds token counts and cost only, never a prompt,
    a document text, an answer or a personal field. The monthly budget is checked against the
    sum of `cost_usd` by `created_at`, so the cap survives a restart. `provider` is the role
    that answered (the host behind each role is a setting); `model_name` and `prompt_version`
    are stored with every call.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("model_name <> ''", name="ck_llm_calls_model_name_not_empty"),
        CheckConstraint("prompt_version <> ''", name="ck_llm_calls_prompt_version_not_empty"),
        CheckConstraint("purpose <> ''", name="ck_llm_calls_purpose_not_empty"),
        CheckConstraint("input_tokens >= 0", name="ck_llm_calls_input_tokens_not_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_llm_calls_output_tokens_not_negative"),
        CheckConstraint("cost_usd >= 0", name="ck_llm_calls_cost_usd_not_negative"),
        Index("ix_llm_calls_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[LlmProvider] = mapped_column(
        Enum(LlmProvider, name="llm_provider", values_callable=_values), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)


class DocumentExtraction(TimestampMixin, Base):
    """The fields a model read from one generated document, with a confidence per field.

    Public reference table, no `account_id`: it only ever holds results for the fabricated
    documents of the synthetic claims sample. One row per claim, document type and prompt
    version, so a new prompt version keeps the old results and a second run can skip what is
    already there. The row is linked to the claim, not to the `generated_documents` row: a new
    document run replaces those rows, and the results were paid for. `text_sha256` is the
    SHA-256 of the text the model read (UTF-8, lowercase hex); a result whose value differs
    from the stored document's is out of date. `fields` holds the validated extraction schema
    of the document type; its contents are not checked by the table. `provider` is the role
    that answered; `model_name` and `prompt_version` are stored with every result.
    """

    __tablename__ = "document_extractions"
    __table_args__ = (
        UniqueConstraint(
            "source_claim_id",
            "document_type",
            "prompt_version",
            name="uq_document_extractions_claim_type_prompt",
        ),
        CheckConstraint(
            "prompt_version <> ''", name="ck_document_extractions_prompt_version_not_empty"
        ),
        CheckConstraint("model_name <> ''", name="ck_document_extractions_model_name_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_extractions_text_sha256_format"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey(
            "claim_samples.source_claim_id",
            name="fk_document_extractions_claim",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type", values_callable=_values), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[LlmProvider] = mapped_column(
        Enum(LlmProvider, name="llm_provider", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
