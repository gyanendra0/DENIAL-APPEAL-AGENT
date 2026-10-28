"""Core tables, uploads, jobs, draft review, audit, password reset, emailed codes and outside
logins (business), reference tables, and the operational tables `llm_calls`, `login_failures`
and `mail_sends`."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
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


class EvidenceSource(StrEnum):
    CMS_NCD = "cms_ncd"
    CMS_MANUAL = "cms_manual"


class EndState(StrEnum):
    """Where the agent loop stopped for one claim. Every value is a stop for a person."""

    NEEDS_REVIEW = "needs_review"  # a checked draft is stored and waits for a person
    NEEDS_REVIEW_NO_DRAFT = "needs_review_no_draft"  # the rules said `must_review`
    STOPPED_BY_RULES = "stopped_by_rules"  # the rules said `hard_fail`
    BELOW_THRESHOLD = "below_threshold"  # the score is under the threshold
    DRAFT_FAILED = "draft_failed"  # a step failed for this claim; no draft


class UploadFileType(StrEnum):
    TEXT = "text"
    PDF = "pdf"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobFailure(StrEnum):
    """Why a job failed. A fixed code only: never document text or an exception message."""

    EXTRACTION_FAILED = "extraction_failed"  # the answer was cut off or not parseable
    BUDGET_EXCEEDED = "budget_exceeded"
    LLM_UNAVAILABLE = "llm_unavailable"
    EVIDENCE_NOT_READY = "evidence_not_ready"  # no evidence vector stored
    INTERRUPTED = "interrupted"  # the process stopped while the job was queued or running
    INTERNAL_ERROR = "internal_error"


class DraftReviewStatus(StrEnum):
    """A person's decision on a draft. `approved` and `rejected` are final."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DraftRejectReason(StrEnum):
    """Why a reviewer rejected a draft: the review rubric's reasons, never free text."""

    WRONG_FACT = "wrong_fact"
    NO_POLICY_ARGUMENT = "no_policy_argument"
    CITATION_DOES_NOT_FIT = "citation_does_not_fit"
    POOR_WORDING = "poor_wording"
    OTHER = "other"


class AuditAction(StrEnum):
    ACCOUNT_CREATED = "account_created"
    UPLOAD_CREATED = "upload_created"
    DRAFT_EDITED = "draft_edited"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_REJECTED = "draft_rejected"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105  (an audit action, not a password)
    PASSWORD_RESET = "password_reset"  # noqa: S105  (an audit action, not a password)
    GOOGLE_LINKED = "google_linked"
    EMAIL_VERIFIED = "email_verified"
    USER_JOINED = "user_joined"


class IdentityProvider(StrEnum):
    """Who vouches for a login other than the password."""

    GOOGLE = "google"


class EmailCodePurpose(StrEnum):
    """What an emailed code is for; also what an email was sent for."""

    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"  # noqa: S105  (a purpose, not a password)


class LoginKeyKind(StrEnum):
    """What a failed login is counted against: the email that was typed, or the client IP."""

    EMAIL = "email"
    IP = "ip"


def _values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class Account(TimestampMixin, Base):
    """A customer organisation. Owns every business row."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)


class User(AccountScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    # One email belongs to one account, so login needs only email + password. The email is
    # stored trimmed and lowercased; the check stops `A@x.com` passing the key beside `a@x.com`.
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        # The target of links that carry `account_id`, so a row never names another account's user.
        UniqueConstraint("account_id", "id", name="uq_users_account_id"),
        # `\s` and not `btrim`: btrim removes spaces only, a tab or a newline would pass.
        CheckConstraint(
            "email = lower(email) AND email !~ '^\\s|\\s$'", name="ck_users_email_normalized"
        ),
        CheckConstraint("token_version >= 0", name="ck_users_token_version_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # NULL: no password set yet (a user made through Google); login refuses every password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=_values),
        nullable=False,
        default=UserRole.VIEWER,
    )
    # Carried in every login token; a password change adds 1, so the older tokens stop working.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # When the user proved they read this address (a code, or Google vouching); NULL: not yet.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class LoginFailure(TimestampMixin, Base):
    """One failed login, counted against one key: the typed email or the client IP.

    Operational table, no `account_id`: a failure on an unknown email has no account, and the
    row never holds a personal field. `key_hash` is an HMAC-SHA256 of the email or IP (lowercase
    hex) with a key derived from the token secret, so neither can be read back from the table.
    A failed login writes one row per key; the login limit counts the rows of a key inside its
    window by `created_at`, so the count survives a restart.
    """

    __tablename__ = "login_failures"
    __table_args__ = (
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_login_failures_key_hash_format"),
        Index(
            "ix_login_failures_key_kind_key_hash_created_at", "key_kind", "key_hash", "created_at"
        ),
        # Every try deletes the rows older than the window by `created_at`.
        Index("ix_login_failures_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key_kind: Mapped[LoginKeyKind] = mapped_column(
        Enum(LoginKeyKind, name="login_key_kind", values_callable=_values), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class MailSend(TimestampMixin, Base):
    """One email the app sent, counted for the daily send cap.

    Operational table, no `account_id`: the cap counts every account's emails together, and
    the row holds only what the email was for and when (`created_at`); never an address, a
    user or a code.
    """

    __tablename__ = "mail_sends"
    __table_args__ = (
        # The cap counts the rows of the current UTC day; old rows are deleted by `created_at`.
        Index("ix_mail_sends_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[EmailCodePurpose] = mapped_column(
        Enum(EmailCodePurpose, name="email_code_purpose", values_callable=_values),
        nullable=False,
    )


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


class EvidenceDocument(TimestampMixin, Base):
    """One published policy document that a draft can cite, such as a coverage determination.

    Public reference table, no `account_id`: it is loaded from a public source and holds no
    customer data. `source` says where the document comes from. `source_document_id` is the
    source's own id, and `section_number` the number a reader would cite (for `cms_ncd` the
    manual section, such as "220.6.17"); each is unique within a source. `version_number` and
    `effective_date` describe the version that was loaded. `source_file_date` is the date of
    the downloaded file the row was read from; the source states no release date. A new load
    replaces the rows.
    """

    __tablename__ = "evidence_documents"
    __table_args__ = (
        UniqueConstraint(
            "source", "source_document_id", name="uq_evidence_documents_source_document"
        ),
        UniqueConstraint("source", "section_number", name="uq_evidence_documents_source_section"),
        CheckConstraint(
            "source_document_id <> ''", name="ck_evidence_documents_source_document_id_not_empty"
        ),
        CheckConstraint(
            "section_number <> ''", name="ck_evidence_documents_section_number_not_empty"
        ),
        CheckConstraint("title <> ''", name="ck_evidence_documents_title_not_empty"),
        CheckConstraint(
            "version_number >= 1", name="ck_evidence_documents_version_number_positive"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[EvidenceSource] = mapped_column(
        Enum(EvidenceSource, name="evidence_source", values_callable=_values), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(String(40), nullable=False)
    section_number: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_file_date: Mapped[date] = mapped_column(Date, nullable=False)


class EvidenceChunk(TimestampMixin, Base):
    """One piece of an `EvidenceDocument`'s text, small enough to retrieve and to cite.

    Public reference table, no `account_id`. `chunk_index` counts the chunks of one document
    from 0, in reading order. A citation names a chunk by its document, its `chunk_index` and
    its `text_sha256` (SHA-256 of `text`, UTF-8, lowercase hex), so a citation written before
    a re-load that changed the chunk is detectably out of date. `section_title` is the part of
    the document the chunk comes from, when the source has parts. `chunker_version` names the
    rules that cut the text; the same text and version always give the same chunks. The
    chunk's vectors are in `EvidenceChunkEmbedding`.
    """

    __tablename__ = "evidence_chunks"
    __table_args__ = (
        UniqueConstraint(
            "evidence_document_id", "chunk_index", name="uq_evidence_chunks_document_index"
        ),
        CheckConstraint("chunk_index >= 0", name="ck_evidence_chunks_chunk_index_not_negative"),
        CheckConstraint("section_title <> ''", name="ck_evidence_chunks_section_title_not_empty"),
        CheckConstraint("text <> ''", name="ck_evidence_chunks_text_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evidence_chunks_text_sha256_format"
        ),
        CheckConstraint(
            "chunker_version <> ''", name="ck_evidence_chunks_chunker_version_not_empty"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_document_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_documents.id", name="fk_evidence_chunks_document", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(20), nullable=False)


# The number of values in one stored vector. The column is created with this size, so a model
# that returns another size cannot be stored.
EMBEDDING_DIMENSIONS = 1536


class EvidenceChunkEmbedding(TimestampMixin, Base):
    """The vector an embedding model gave for one `EvidenceChunk`, used to search the chunks.

    Public reference table, no `account_id`: it is made from public policy text only.
    `model_name` is the model that made the vector and `input_version` names how the embedded
    text was built from the chunk; vectors are only comparable when both are the same, so a
    chunk has one row per pair. `embedding` always holds `EMBEDDING_DIMENSIONS` values.
    Deleting a chunk deletes its vectors, so a new load of the evidence corpus leaves this
    table empty until the chunks are embedded again.
    """

    __tablename__ = "evidence_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "evidence_chunk_id",
            "model_name",
            "input_version",
            name="uq_evidence_chunk_embeddings_chunk_model_version",
        ),
        CheckConstraint(
            "model_name <> ''", name="ck_evidence_chunk_embeddings_model_name_not_empty"
        ),
        CheckConstraint(
            "input_version <> ''", name="ck_evidence_chunk_embeddings_input_version_not_empty"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_chunk_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evidence_chunks.id", name="fk_evidence_chunk_embeddings_chunk", ondelete="CASCADE"
        ),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_version: Mapped[str] = mapped_column(String(40), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)


class StoredAppealDraft(TimestampMixin, Base):
    """A drafted appeal letter that passed the citation validator, kept for a person to review.

    Public reference table, no `account_id`: it only ever holds drafts for the fabricated
    claims of the synthetic claims sample. One row per claim and prompt version, so a new
    prompt version keeps the old drafts and a second run can skip what is already there.
    `text_sha256` is the SHA-256 of the denial letter document the draft was built from
    (UTF-8, lowercase hex); a draft whose value differs from the stored document's is out of
    date. `letter` holds the validated `AppealLetter` of `src/drafting/schemas.py`; its
    contents are not checked by the table. A citation inside it names its chunk by `source`,
    `source_document_id`, `chunk_index` and `text_sha256`, never by a row id, and no foreign
    key points at the evidence tables: a new load of the evidence corpus gives new row ids
    and must not remove a draft. `search_question_version` names the wording of the question
    that found the evidence. `provider` is the role that answered; `model_name` and
    `prompt_version` are stored with every draft. Nothing here is ever sent to an insurer.
    """

    __tablename__ = "appeal_drafts"
    __table_args__ = (
        UniqueConstraint("source_claim_id", "prompt_version", name="uq_appeal_drafts_claim_prompt"),
        CheckConstraint("prompt_version <> ''", name="ck_appeal_drafts_prompt_version_not_empty"),
        CheckConstraint(
            "search_question_version <> ''",
            name="ck_appeal_drafts_search_question_version_not_empty",
        ),
        CheckConstraint("model_name <> ''", name="ck_appeal_drafts_model_name_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_appeal_drafts_text_sha256_format"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey(
            "claim_samples.source_claim_id", name="fk_appeal_drafts_claim", ondelete="CASCADE"
        ),
        nullable=False,
    )
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    search_question_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[LlmProvider] = mapped_column(
        Enum(LlmProvider, name="llm_provider", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    letter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AgentRun(TimestampMixin, Base):
    """What the agent loop did with one claim: its end state and the trace of its five steps.

    Public reference table, no `account_id`: it only ever holds runs on the fabricated claims
    of the synthetic claims sample. One row per claim and agent version, so a new agent
    version keeps the old runs and a second run can skip what is already finished.
    `text_sha256` is the SHA-256 of the denial letter document the loop read (UTF-8, lowercase
    hex). `trace` holds the `AgentTrace` of `src/agent/trace.py`: statuses, versions, outcomes
    and a score, never document text or an extracted value. The table checks only that the
    end state and the agent version inside `trace` equal the two columns. No foreign key
    points at `appeal_drafts` or at the evidence tables: the trace names the draft by its
    prompt version and a chunk by `source`, `source_document_id`, `chunk_index` and
    `text_sha256`. Every end state is a stop for a person; nothing here is ever sent.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_claim_id", "agent_version", name="uq_agent_runs_claim_agent_version"
        ),
        CheckConstraint("agent_version <> ''", name="ck_agent_runs_agent_version_not_empty"),
        CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_agent_runs_text_sha256_format"),
        # IS NOT DISTINCT FROM, so a trace without the key is refused too (`=` would give NULL).
        CheckConstraint(
            "(trace ->> 'end_state') IS NOT DISTINCT FROM end_state::text",
            name="ck_agent_runs_trace_end_state_matches",
        ),
        CheckConstraint(
            "(trace ->> 'agent_version') IS NOT DISTINCT FROM agent_version",
            name="ck_agent_runs_trace_agent_version_matches",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(
        String(15),
        ForeignKey("claim_samples.source_claim_id", name="fk_agent_runs_claim", ondelete="CASCADE"),
        nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    end_state: Mapped[EndState] = mapped_column(
        Enum(EndState, name="agent_end_state", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    trace: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Upload(AccountScopedMixin, TimestampMixin, Base):
    """One denial letter a user uploaded: the text read from the file, never the file itself.

    Business table. `file_type` is decided from the file's first bytes, not from its name.
    `text_sha256` is the SHA-256 of `text` (UTF-8, lowercase hex). The link to the user
    carries `account_id`, so an upload never names another account's user, and it has no
    cascade: a user who still has uploads cannot be deleted by accident. A non-admin reads only
    the jobs of their own uploads, hence the index on `account_id` + `user_id`.
    """

    __tablename__ = "uploads"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_uploads_account_id"),
        Index("ix_uploads_account_id_user_id", "account_id", "user_id"),
        ForeignKeyConstraint(
            ["account_id", "user_id"], ["users.account_id", "users.id"], name="fk_uploads_user"
        ),
        CheckConstraint("file_name <> ''", name="ck_uploads_file_name_not_empty"),
        CheckConstraint("byte_size > 0", name="ck_uploads_byte_size_positive"),
        CheckConstraint("text <> ''", name="ck_uploads_text_not_empty"),
        CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_uploads_text_sha256_format"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[UploadFileType] = mapped_column(
        Enum(UploadFileType, name="upload_file_type", values_callable=_values), nullable=False
    )
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class Job(AccountScopedMixin, TimestampMixin, Base):
    """The background run that turns one upload into a draft: `queued` -> `running` -> end.

    Business table, one job per upload. A `failed` job has a fixed `failure` code and nothing
    else about the error; a `done` job has the loop's `end_state` (a job the rules stopped is
    `done`, not `failed`). A job marked `interrupted` while still queued never started, so
    a failed job may have no `started_at`. The link to the upload carries `account_id`.
    `cost_usd` is what the job's recorded model calls cost; it stays `0` until the job ends.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_jobs_account_id"),
        UniqueConstraint("upload_id", name="uq_jobs_upload_id"),
        ForeignKeyConstraint(
            ["account_id", "upload_id"],
            ["uploads.account_id", "uploads.id"],
            name="fk_jobs_upload",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure IS NOT NULL)", name="ck_jobs_failure_only_when_failed"
        ),
        CheckConstraint(
            "(status = 'done') = (end_state IS NOT NULL)", name="ck_jobs_end_state_only_when_done"
        ),
        CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL)"
            " OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL)"
            " OR (status = 'done' AND started_at IS NOT NULL AND finished_at IS NOT NULL)"
            " OR (status = 'failed' AND finished_at IS NOT NULL)",
            name="ck_jobs_times_match_status",
        ),
        CheckConstraint("finished_at >= started_at", name="ck_jobs_finished_not_before_started"),
        CheckConstraint("cost_usd >= 0", name="ck_jobs_cost_usd_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=_values),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    failure: Mapped[JobFailure | None] = mapped_column(
        Enum(JobFailure, name="job_failure", values_callable=_values), nullable=True
    )
    end_state: Mapped[EndState | None] = mapped_column(
        Enum(EndState, name="agent_end_state", values_callable=_values), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"), server_default="0"
    )


class UploadExtraction(AccountScopedMixin, TimestampMixin, Base):
    """The denial-letter fields a model read from one job's upload, with a confidence each.

    Business table, one row per job; the account version of `document_extractions`.
    `fields` holds the validated `DenialLetterExtraction`; its contents are not checked by
    the table. `text_sha256` is the hash of the upload text the model read.
    """

    __tablename__ = "upload_extractions"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_upload_extractions_job_id"),
        ForeignKeyConstraint(
            ["account_id", "job_id"],
            ["jobs.account_id", "jobs.id"],
            name="fk_upload_extractions_job",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "prompt_version <> ''", name="ck_upload_extractions_prompt_version_not_empty"
        ),
        CheckConstraint("model_name <> ''", name="ck_upload_extractions_model_name_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_upload_extractions_text_sha256_format"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[LlmProvider] = mapped_column(
        Enum(LlmProvider, name="llm_provider", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class UploadAgentRun(AccountScopedMixin, TimestampMixin, Base):
    """What the agent loop did with one job's upload; the account version of `agent_runs`.

    Business table, one row per job. `trace` holds the `AgentTrace`: statuses, versions,
    outcomes and a score, never document text or an extracted value. As in `agent_runs`, the
    table checks that the end state and the agent version inside `trace` equal the columns.
    """

    __tablename__ = "upload_agent_runs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_upload_agent_runs_job_id"),
        ForeignKeyConstraint(
            ["account_id", "job_id"],
            ["jobs.account_id", "jobs.id"],
            name="fk_upload_agent_runs_job",
            ondelete="CASCADE",
        ),
        CheckConstraint("agent_version <> ''", name="ck_upload_agent_runs_agent_version_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_upload_agent_runs_text_sha256_format"
        ),
        # IS NOT DISTINCT FROM, so a trace without the key is refused too (`=` would give NULL).
        CheckConstraint(
            "(trace ->> 'end_state') IS NOT DISTINCT FROM end_state::text",
            name="ck_upload_agent_runs_trace_end_state_matches",
        ),
        CheckConstraint(
            "(trace ->> 'agent_version') IS NOT DISTINCT FROM agent_version",
            name="ck_upload_agent_runs_trace_agent_version_matches",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    end_state: Mapped[EndState] = mapped_column(
        Enum(EndState, name="agent_end_state", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    trace: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class UploadDraft(AccountScopedMixin, TimestampMixin, Base):
    """A checked appeal draft for one job's upload; the account version of `appeal_drafts`.

    Business table, one row per job. `letter` holds the validated `AppealLetter`; a citation
    inside it names its chunk by value, never by a row id, so no link points at the evidence
    tables. Nothing here is ever sent to an insurer.

    `letter` is the model's draft and never changes; a person's edits are `draft_revisions`.
    The review columns follow `review_status`: a `pending` draft has none of them, an
    `approved` one names the reviewer, the time and the version approved (`0` is the model's
    draft, `1` and up a revision), a `rejected` one the reviewer, the time and a rubric
    reason. The link to the reviewer carries `account_id` and has no cascade.
    """

    __tablename__ = "upload_drafts"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_upload_drafts_job_id"),
        # The target of the revisions' link, which carries `account_id`.
        UniqueConstraint("account_id", "job_id", name="uq_upload_drafts_account_job_id"),
        ForeignKeyConstraint(
            ["account_id", "job_id"],
            ["jobs.account_id", "jobs.id"],
            name="fk_upload_drafts_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "reviewed_by"],
            ["users.account_id", "users.id"],
            name="fk_upload_drafts_reviewer",
        ),
        CheckConstraint(
            "(review_status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL"
            " AND reject_reason IS NULL AND approved_version IS NULL)"
            " OR (review_status = 'approved' AND reviewed_by IS NOT NULL"
            " AND reviewed_at IS NOT NULL AND reject_reason IS NULL"
            " AND approved_version IS NOT NULL)"
            " OR (review_status = 'rejected' AND reviewed_by IS NOT NULL"
            " AND reviewed_at IS NOT NULL AND reject_reason IS NOT NULL"
            " AND approved_version IS NULL)",
            name="ck_upload_drafts_review_matches_status",
        ),
        CheckConstraint(
            "approved_version >= 0", name="ck_upload_drafts_approved_version_not_negative"
        ),
        CheckConstraint("prompt_version <> ''", name="ck_upload_drafts_prompt_version_not_empty"),
        CheckConstraint(
            "search_question_version <> ''",
            name="ck_upload_drafts_search_question_version_not_empty",
        ),
        CheckConstraint("model_name <> ''", name="ck_upload_drafts_model_name_not_empty"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_upload_drafts_text_sha256_format"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    search_question_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[LlmProvider] = mapped_column(
        Enum(LlmProvider, name="llm_provider", values_callable=_values), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    letter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[DraftReviewStatus] = mapped_column(
        Enum(DraftReviewStatus, name="draft_review_status", values_callable=_values),
        nullable=False,
        default=DraftReviewStatus.PENDING,
        server_default=DraftReviewStatus.PENDING.value,
    )
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[DraftRejectReason | None] = mapped_column(
        Enum(DraftRejectReason, name="draft_reject_reason", values_callable=_values),
        nullable=True,
    )
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DraftRevision(AccountScopedMixin, TimestampMixin, Base):
    """One edited version of a job's draft, made by a person; the model's draft stays as it was.

    Business table. `version` counts 1, 2, ... per job; `letter` holds the edited
    `AppealLetter`, `text_sha256` its hash. The link to the draft carries `account_id` and
    cascades; the link to the editor carries `account_id` and has no cascade.
    """

    __tablename__ = "draft_revisions"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_draft_revisions_job_version"),
        ForeignKeyConstraint(
            ["account_id", "job_id"],
            ["upload_drafts.account_id", "upload_drafts.job_id"],
            name="fk_draft_revisions_draft",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "edited_by"],
            ["users.account_id", "users.id"],
            name="fk_draft_revisions_editor",
        ),
        CheckConstraint("version >= 1", name="ck_draft_revisions_version_positive"),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_draft_revisions_text_sha256_format"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    letter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    edited_by: Mapped[int] = mapped_column(Integer, nullable=False)


class AuditEvent(AccountScopedMixin, TimestampMixin, Base):
    """One thing a person did: who, what, on which job, when (`created_at`).

    Business table, written in the same transaction as the change; the code only inserts.
    `detail` holds fixed keys only (a version number, a reject reason), never document text.
    `job_id` is empty exactly for `account_created`, `password_changed`, `password_reset`,
    `google_linked`, `email_verified` and `user_joined`.
    The links to the user and the job carry `account_id` and have no cascade, so an audited
    job cannot be deleted on its own; deleting the account removes everything.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id"], ["users.account_id", "users.id"], name="fk_audit_events_user"
        ),
        ForeignKeyConstraint(
            ["account_id", "job_id"], ["jobs.account_id", "jobs.id"], name="fk_audit_events_job"
        ),
        CheckConstraint(
            "(action::text IN ('account_created', 'password_changed', 'password_reset',"
            " 'google_linked', 'email_verified', 'user_joined')) = (job_id IS NULL)",
            name="ck_audit_events_job_matches_action",
        ),
        CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_audit_events_detail_is_object"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", values_callable=_values), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class EmailCode(AccountScopedMixin, TimestampMixin, Base):
    """One emailed 6-digit code, stored only as a keyed hash.

    Business table. `code_hash` is an HMAC-SHA256 (lowercase hex) of purpose, user and code,
    with a key derived from the token secret, so the code cannot be read back from the table.
    Only the user's newest code of a purpose works, until `expires_at`; `wrong_tries` counts
    the wrong codes typed against it. The resend limits count a user's rows by `created_at`.
    The link to the user carries `account_id` and cascades: a code means nothing once its
    user is gone.
    """

    __tablename__ = "email_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["users.account_id", "users.id"],
            name="fk_email_codes_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("code_hash ~ '^[0-9a-f]{64}$'", name="ck_email_codes_code_hash_format"),
        CheckConstraint("wrong_tries >= 0", name="ck_email_codes_wrong_tries_not_negative"),
        Index(
            "ix_email_codes_account_id_user_id_purpose_created_at",
            "account_id",
            "user_id",
            "purpose",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[EmailCodePurpose] = mapped_column(
        Enum(EmailCodePurpose, name="email_code_purpose", values_callable=_values),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    wrong_tries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserIdentity(AccountScopedMixin, TimestampMixin, Base):
    """One outside login (today: a Google account) linked to a user.

    Business table. `subject` is the provider's permanent id for the account (Google's `sub`);
    `email_at_link` is the verified email it had when linked, kept for the audit trail. One
    provider account opens at most one user in the whole system, and a user has at most one
    identity per provider. No provider token is stored. The link to the user carries
    `account_id` and cascades: an identity means nothing once its user is gone.
    """

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        UniqueConstraint(
            "account_id",
            "user_id",
            "provider",
            name="uq_user_identities_account_id_user_id_provider",
        ),
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["users.account_id", "users.id"],
            name="fk_user_identities_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("subject <> ''", name="ck_user_identities_subject_not_empty"),
        # The same rule as `users.email`.
        CheckConstraint(
            "email_at_link = lower(email_at_link) AND email_at_link !~ '^\\s|\\s$'",
            name="ck_user_identities_email_at_link_normalized",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[IdentityProvider] = mapped_column(
        Enum(IdentityProvider, name="identity_provider", values_callable=_values), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_at_link: Mapped[str] = mapped_column(String(320), nullable=False)
