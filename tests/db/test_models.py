from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from src.agent.trace import AGENT_VERSION, AgentTrace, ExtractionStep, RulesStep, StepStatus
from src.db.models import (
    CLAIM_LINE_AMOUNT_COLUMNS,
    EMBEDDING_DIMENSIONS,
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    PLAN_COUNT_COLUMNS,
    Account,
    AgentRun,
    AuditAction,
    AuditEvent,
    Claim,
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    Denial,
    DenialReasonCategory,
    DenialStatus,
    DocumentExtraction,
    DocumentType,
    DraftRejectReason,
    DraftReviewStatus,
    DraftRevision,
    EndState,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceSource,
    ExchangeType,
    GeneratedDocument,
    IssuerDenialStats,
    Job,
    JobFailure,
    JobStatus,
    LlmCall,
    LlmProvider,
    LoginFailure,
    LoginKeyKind,
    MetalLevel,
    PlanDenialStats,
    PlanType,
    StoredAppealDraft,
    Upload,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
    UploadFileType,
    User,
    UserRole,
)
from src.evaluation.review_rubric import RejectReason
from src.rules.verdict import RULES_VERSION, RuleOutcome, RuleReason

# Not a real hash of any password; the column only stores text.
MADE_UP_HASH = "$argon2id$v=19$m=65536,t=3,p=4$bWFkZS11cA$bWFkZS11cC1oYXNo"


def _account(session: Session, name: str) -> Account:
    account = Account(name=name)
    session.add(account)
    session.flush()
    return account


def _user(account: Account, email: str) -> User:
    return User(account_id=account.id, email=email, password_hash=MADE_UP_HASH)


def _claim(session: Session, account: Account, number: str = "C-1") -> Claim:
    claim = Claim(
        account_id=account.id,
        claim_number=number,
        payer="Acme Health",
        billed_amount=Decimal("1234.50"),
        service_date=date(2026, 1, 15),
    )
    session.add(claim)
    session.flush()
    return claim


def _issuer_stats(
    session: Session, issuer_id: str, plan_year: int, **overrides: Any
) -> IssuerDenialStats:
    fields: dict[str, Any] = {
        "plan_year": plan_year,
        "issuer_id": issuer_id,
        "issuer_name": "Example Health Plan",
        "state": "ZZ",
        "exchange_type": ExchangeType.SBE_FP,
        "is_new_to_exchange": False,
        "claims_received_in_network": 3_000_000_000,
        "claims_denied_in_network": 500,
        "internal_appeals_overturned_pct": Decimal("39.86"),
    }
    stats = IssuerDenialStats(**(fields | overrides))
    session.add(stats)
    session.flush()
    return stats


def test_stores_money_as_exact_decimal(session: Session) -> None:
    claim = _claim(session, _account(session, "a"))
    session.expire_all()

    assert session.get(Claim, claim.id).billed_amount == Decimal("1234.50")  # type: ignore[union-attr]


def test_sets_timestamps_on_insert(session: Session) -> None:
    account = _account(session, "a")
    session.refresh(account)

    assert account.created_at is not None
    assert account.updated_at is not None


def test_applies_default_role_and_status(session: Session) -> None:
    account = _account(session, "a")
    user = User(account_id=account.id, email="a@example.com", password_hash=MADE_UP_HASH)
    denial = Denial(
        account_id=account.id,
        claim_id=_claim(session, account).id,
        reason_code="CO-50",
        denied_on=date(2026, 2, 1),
    )
    session.add_all([user, denial])
    session.flush()

    assert user.role is UserRole.VIEWER
    assert denial.status is DenialStatus.NEW


def test_rejects_duplicate_claim_number_in_same_account(session: Session) -> None:
    account = _account(session, "a")
    _claim(session, account, "C-1")

    with pytest.raises(IntegrityError):
        _claim(session, account, "C-1")


def test_allows_same_claim_number_in_different_accounts(session: Session) -> None:
    _claim(session, _account(session, "a"), "C-1")
    _claim(session, _account(session, "b"), "C-1")


def test_rejects_row_without_account(session: Session) -> None:
    session.add(User(email="orphan@example.com", password_hash=MADE_UP_HASH, role=UserRole.ADMIN))

    with pytest.raises(IntegrityError):
        session.flush()


def test_rejects_the_same_email_in_two_accounts(session: Session) -> None:
    session.add(_user(_account(session, "a"), "same@example.com"))
    session.flush()
    session.add(_user(_account(session, "b"), "same@example.com"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_rejects_a_user_without_a_password_hash(session: Session) -> None:
    session.add(User(account_id=_account(session, "a").id, email="a@example.com"))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "email", ["A@example.com", " a@example.com", "a@example.com\t", "\na@example.com"]
)
def test_rejects_an_email_that_is_not_trimmed_and_lowercased(session: Session, email: str) -> None:
    session.add(_user(_account(session, "a"), email))

    with pytest.raises(IntegrityError):
        session.flush()


def test_stores_issuer_stats_with_exact_percent_and_large_counts(session: Session) -> None:
    stats = _issuer_stats(session, "00012", 2026)
    session.expire_all()

    stored = session.get(IssuerDenialStats, stats.id)
    assert stored is not None
    assert stored.issuer_id == "00012"
    assert stored.exchange_type is ExchangeType.SBE_FP
    assert stored.claims_received_in_network == 3_000_000_000
    assert stored.internal_appeals_overturned_pct == Decimal("39.86")


def test_keeps_suppressed_issuer_counts_as_null(session: Session) -> None:
    stats = _issuer_stats(session, "00012", 2026)
    session.expire_all()

    stored = session.get(IssuerDenialStats, stats.id)
    assert stored is not None
    assert stored.external_appeals_filed is None
    assert stored.external_appeals_overturned_pct is None


def test_rejects_duplicate_issuer_in_same_plan_year(session: Session) -> None:
    _issuer_stats(session, "00012", 2026)

    with pytest.raises(IntegrityError, match="uq_issuer_denial_stats_issuer_year"):
        _issuer_stats(session, "00012", 2026)


def test_allows_same_issuer_in_different_plan_years(session: Session) -> None:
    _issuer_stats(session, "00012", 2025)
    _issuer_stats(session, "00012", 2026)


@pytest.mark.parametrize("issuer_id", ["12", "1234A", "    1"])
def test_rejects_issuer_id_that_is_not_five_digits(session: Session, issuer_id: str) -> None:
    with pytest.raises(IntegrityError, match="ck_issuer_denial_stats_issuer_id_format"):
        _issuer_stats(session, issuer_id, 2026)


@pytest.mark.parametrize("column", ISSUER_COUNT_COLUMNS)
def test_rejects_negative_issuer_count(session: Session, column: str) -> None:
    with pytest.raises(IntegrityError, match="ck_issuer_denial_stats_counts_non_negative"):
        _issuer_stats(session, "00012", 2026, **{column: -1})


@pytest.mark.parametrize("column", ISSUER_PERCENT_COLUMNS)
@pytest.mark.parametrize("percent", [Decimal("-0.01"), Decimal("100.01")])
def test_rejects_issuer_percent_outside_0_to_100(
    session: Session, column: str, percent: Decimal
) -> None:
    with pytest.raises(IntegrityError, match="ck_issuer_denial_stats_percents_in_range"):
        _issuer_stats(session, "00012", 2026, **{column: percent})


def test_rejects_issuer_stats_without_state(session: Session) -> None:
    with pytest.raises(IntegrityError, match='null value in column "state"'):
        _issuer_stats(session, "00012", 2026, state=None)


def test_rejects_unknown_exchange_type(session: Session) -> None:
    insert = text(
        "INSERT INTO issuer_denial_stats "
        "(plan_year, issuer_id, issuer_name, state, exchange_type, is_new_to_exchange) "
        "VALUES (2026, '00012', 'Example Health Plan', 'ZZ', 'UNKNOWN', false)"
    )

    with pytest.raises(DataError):
        session.execute(insert)


def _plan_stats(
    session: Session, plan_id: str = "00012ZZ0010001", plan_year: int = 2026, **overrides: Any
) -> PlanDenialStats:
    """Store a reported plan of issuer 00012 (state ZZ). The issuer row must exist first."""
    fields: dict[str, Any] = {
        "plan_year": plan_year,
        "plan_id": plan_id,
        "issuer_id": "00012",
        "state": "ZZ",
        "plan_type": PlanType.HMO,
        "metal_level": MetalLevel.SILVER,
        "is_reported": True,
        "claims_received_in_network": 3_000_000_000,
        "claims_denied_in_network": 500,
        "denied_other": 0,
    }
    stats = PlanDenialStats(**(fields | overrides))
    session.add(stats)
    session.flush()
    return stats


def test_stores_reported_plan_with_zero_and_suppressed_counts(session: Session) -> None:
    _issuer_stats(session, "00012", 2026)
    stats = _plan_stats(session)
    session.expire_all()

    stored = session.get(PlanDenialStats, stats.id)
    assert stored is not None
    assert stored.claims_received_in_network == 3_000_000_000
    assert stored.denied_other == 0  # a published zero is kept
    assert stored.denied_services_excluded is None  # suppressed
    assert stored.plan_type is PlanType.HMO
    assert stored.metal_level is MetalLevel.SILVER
    assert stored.created_at is not None


def test_stores_unreported_plan_without_counts(session: Session) -> None:
    _issuer_stats(session, "00012", 2026)
    counts: dict[str, Any] = dict.fromkeys(PLAN_COUNT_COLUMNS)

    stats = _plan_stats(session, is_reported=False, **counts)

    assert stats.id is not None


@pytest.mark.parametrize("column", PLAN_COUNT_COLUMNS)
def test_rejects_count_on_unreported_plan(session: Session, column: str) -> None:
    _issuer_stats(session, "00012", 2026)
    counts: dict[str, Any] = dict.fromkeys(PLAN_COUNT_COLUMNS) | {column: 0}

    with pytest.raises(IntegrityError, match="ck_plan_denial_stats_unreported_has_no_counts"):
        _plan_stats(session, is_reported=False, **counts)


def test_rejects_duplicate_plan_in_same_plan_year(session: Session) -> None:
    _issuer_stats(session, "00012", 2026)
    _plan_stats(session)

    with pytest.raises(IntegrityError, match="uq_plan_denial_stats_plan_year"):
        _plan_stats(session)


def test_allows_same_plan_in_different_plan_years(session: Session) -> None:
    _issuer_stats(session, "00012", 2025)
    _issuer_stats(session, "00012", 2026)

    _plan_stats(session, plan_year=2025)
    _plan_stats(session, plan_year=2026)


@pytest.mark.parametrize("plan_id", ["00012ZZ001", "00012zz0010001", "00012ZZ00100AB"])
def test_rejects_plan_id_in_the_wrong_format(session: Session, plan_id: str) -> None:
    _issuer_stats(session, "00012", 2026)

    with pytest.raises(IntegrityError, match="ck_plan_denial_stats_plan_id_format"):
        _plan_stats(session, plan_id)


@pytest.mark.parametrize("plan_id", ["00099ZZ0010001", "00012YY0010001"])
def test_rejects_plan_id_that_does_not_match_issuer_and_state(
    session: Session, plan_id: str
) -> None:
    _issuer_stats(session, "00012", 2026)

    with pytest.raises(IntegrityError, match="ck_plan_denial_stats_plan_id_matches_issuer"):
        _plan_stats(session, plan_id)


@pytest.mark.parametrize("column", PLAN_COUNT_COLUMNS)
def test_rejects_negative_plan_count(session: Session, column: str) -> None:
    _issuer_stats(session, "00012", 2026)

    negative: dict[str, Any] = {column: -1}

    with pytest.raises(IntegrityError, match="ck_plan_denial_stats_counts_non_negative"):
        _plan_stats(session, **negative)


def test_rejects_plan_without_its_issuer_row(session: Session) -> None:
    _issuer_stats(session, "00012", 2025)  # same issuer, another year

    with pytest.raises(IntegrityError, match="fk_plan_denial_stats_issuer_year"):
        _plan_stats(session, plan_year=2026)


def test_deleting_an_issuer_row_removes_its_plans(session: Session) -> None:
    issuer = _issuer_stats(session, "00012", 2026)
    _plan_stats(session)

    session.delete(issuer)
    session.flush()

    assert session.scalars(select(PlanDenialStats)).all() == []


@pytest.mark.parametrize(("column", "value"), [("plan_type", "XYZ"), ("metal_level", "Diamond")])
def test_rejects_unknown_plan_type_or_metal_level(
    session: Session, column: str, value: str
) -> None:
    _issuer_stats(session, "00012", 2026)
    values = {"plan_type": "HMO", "metal_level": "Silver"} | {column: value}
    insert = text(
        "INSERT INTO plan_denial_stats "
        "(plan_year, plan_id, issuer_id, state, plan_type, metal_level, is_reported) "
        "VALUES (2026, '00012ZZ0010001', '00012', 'ZZ', :plan_type, :metal_level, false)"
    )

    with pytest.raises(DataError):
        session.execute(insert, values)


SAMPLE_CLAIM_ID = "800000000000001"


def _claim_sample(
    session: Session, source_claim_id: str = SAMPLE_CLAIM_ID, **overrides: Any
) -> ClaimSample:
    fields: dict[str, Any] = {
        "source_claim_id": source_claim_id,
        "claim_from_date": date(2009, 3, 1),
        "claim_thru_date": date(2009, 3, 2),
        "diagnosis_codes": ["4019", "V5869"],
    }
    sample = ClaimSample(**(fields | overrides))
    session.add(sample)
    session.flush()
    return sample


def _claim_sample_line(session: Session, line_number: int = 1, **overrides: Any) -> ClaimSampleLine:
    """Store a line of the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "line_number": line_number,
        "hcpcs_code": "99213",
        "line_diagnosis_code": "4019",
        "processing_indicator": "A",
        "payment_amount": Decimal("40.00"),
        "deductible_amount": Decimal("0.00"),
        "primary_payer_paid_amount": Decimal("0.00"),
        "coinsurance_amount": Decimal("10.00"),
        "allowed_charge_amount": Decimal("70.00"),
    }
    line = ClaimSampleLine(**(fields | overrides))
    session.add(line)
    session.flush()
    return line


def test_stores_claim_sample_with_lines_and_exact_amounts(session: Session) -> None:
    sample = _claim_sample(session)
    _claim_sample_line(session, 1)
    _claim_sample_line(
        session, 2, hcpcs_code=None, line_diagnosis_code=None, processing_indicator="<"
    )
    session.expire_all()

    stored = session.get(ClaimSample, sample.id)
    assert stored is not None
    assert stored.diagnosis_codes == ["4019", "V5869"]  # order is kept
    assert stored.created_at is not None
    lines = session.scalars(select(ClaimSampleLine).order_by(ClaimSampleLine.line_number)).all()
    assert [line.line_number for line in lines] == [1, 2]
    # The source's amounts do not have to add up: 40 + 10 is not 70.
    assert lines[0].payment_amount == Decimal("40.00")
    assert lines[0].allowed_charge_amount == Decimal("70.00")
    assert lines[1].hcpcs_code is None
    assert lines[1].processing_indicator == "<"


def test_stores_a_twelve_digit_line_amount_exactly(session: Session) -> None:
    largest = Decimal("9999999999.99")
    _claim_sample(session)
    line = _claim_sample_line(session, payment_amount=largest)
    session.expire_all()

    assert session.get(ClaimSampleLine, line.id).payment_amount == largest  # type: ignore[union-attr]


def test_stores_claim_sample_without_diagnosis_codes(session: Session) -> None:
    sample = _claim_sample(session, diagnosis_codes=[])
    session.expire_all()

    assert session.get(ClaimSample, sample.id).diagnosis_codes == []  # type: ignore[union-attr]


def test_rejects_duplicate_source_claim_id(session: Session) -> None:
    _claim_sample(session)

    with pytest.raises(IntegrityError, match="uq_claim_samples_source_claim_id"):
        _claim_sample(session)


@pytest.mark.parametrize(
    "source_claim_id", ["80000000000001", "80000000000000A", " 8000000000001 "]
)
def test_rejects_source_claim_id_that_is_not_fifteen_digits(
    session: Session, source_claim_id: str
) -> None:
    with pytest.raises(IntegrityError, match="ck_claim_samples_source_claim_id_format"):
        _claim_sample(session, source_claim_id)


def test_rejects_claim_sample_that_ends_before_it_starts(session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_claim_samples_from_not_after_thru"):
        _claim_sample(session, claim_from_date=date(2009, 3, 2), claim_thru_date=date(2009, 3, 1))


def test_rejects_duplicate_line_number_on_one_claim(session: Session) -> None:
    _claim_sample(session)
    _claim_sample_line(session, 1)

    with pytest.raises(IntegrityError, match="uq_claim_sample_lines_claim_line"):
        _claim_sample_line(session, 1)


@pytest.mark.parametrize("line_number", [0, 14])
def test_rejects_line_number_outside_1_to_13(session: Session, line_number: int) -> None:
    _claim_sample(session)

    with pytest.raises(IntegrityError, match="ck_claim_sample_lines_line_number_in_range"):
        _claim_sample_line(session, line_number)


def test_rejects_empty_processing_indicator(session: Session) -> None:
    _claim_sample(session)

    with pytest.raises(IntegrityError, match="ck_claim_sample_lines_processing_indicator_one_char"):
        _claim_sample_line(session, processing_indicator="")


@pytest.mark.parametrize("column", CLAIM_LINE_AMOUNT_COLUMNS)
def test_rejects_negative_line_amount(session: Session, column: str) -> None:
    _claim_sample(session)
    negative: dict[str, Any] = {column: Decimal("-0.01")}

    with pytest.raises(IntegrityError, match="ck_claim_sample_lines_amounts_non_negative"):
        _claim_sample_line(session, **negative)


def test_rejects_line_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_claim_sample_lines_claim"):
        _claim_sample_line(session)


def test_deleting_a_claim_sample_removes_its_lines(session: Session) -> None:
    sample = _claim_sample(session)
    _claim_sample_line(session, 1)
    _claim_sample_line(session, 2)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(ClaimSampleLine)).all() == []


def _claim_sample_label(session: Session, **overrides: Any) -> ClaimSampleLabel:
    """Store a denied label for the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "is_denied": True,
        "denial_reason_category": DenialReasonCategory.NONCOVERED,
        "appeal_success_proxy": False,
        "label_rule_version": "v1",
        "split": DatasetSplit.TRAIN,
        "split_seed": 42,
    }
    label = ClaimSampleLabel(**(fields | overrides))
    session.add(label)
    session.flush()
    return label


NOT_DENIED: dict[str, Any] = {
    "is_denied": False,
    "denial_reason_category": None,
    "appeal_success_proxy": None,
}


def test_stores_denied_label_with_category_proxy_version_and_split(session: Session) -> None:
    _claim_sample(session)
    label = _claim_sample_label(session, appeal_success_proxy=True, split=DatasetSplit.VALIDATION)
    session.expire_all()

    stored = session.get(ClaimSampleLabel, label.id)
    assert stored is not None
    assert stored.is_denied is True
    assert stored.denial_reason_category is DenialReasonCategory.NONCOVERED
    assert stored.appeal_success_proxy is True
    assert stored.label_rule_version == "v1"
    assert stored.split is DatasetSplit.VALIDATION
    assert stored.split_seed == 42
    assert stored.created_at is not None


def test_stores_not_denied_label_without_category_or_proxy(session: Session) -> None:
    _claim_sample(session)
    label = _claim_sample_label(session, **NOT_DENIED)
    session.expire_all()

    stored = session.get(ClaimSampleLabel, label.id)
    assert stored is not None
    assert stored.is_denied is False
    assert stored.denial_reason_category is None
    assert stored.appeal_success_proxy is None


@pytest.mark.parametrize("column", ["denial_reason_category", "appeal_success_proxy"])
def test_rejects_denied_label_without_category_or_proxy(session: Session, column: str) -> None:
    _claim_sample(session)
    missing: dict[str, Any] = {column: None}

    with pytest.raises(IntegrityError, match="ck_claim_sample_labels_denied_fields_match"):
        _claim_sample_label(session, **missing)


@pytest.mark.parametrize(
    ("column", "value"),
    [("denial_reason_category", DenialReasonCategory.OTHER), ("appeal_success_proxy", False)],
)
def test_rejects_not_denied_label_with_category_or_proxy(
    session: Session, column: str, value: Any
) -> None:
    _claim_sample(session)
    filled = NOT_DENIED | {column: value}

    with pytest.raises(IntegrityError, match="ck_claim_sample_labels_denied_fields_match"):
        _claim_sample_label(session, **filled)


def test_rejects_second_label_for_the_same_claim(session: Session) -> None:
    _claim_sample(session)
    _claim_sample_label(session)

    with pytest.raises(IntegrityError, match="uq_claim_sample_labels_source_claim_id"):
        _claim_sample_label(session, label_rule_version="v2")


def test_rejects_empty_label_rule_version(session: Session) -> None:
    _claim_sample(session)

    with pytest.raises(IntegrityError, match="ck_claim_sample_labels_rule_version_not_empty"):
        _claim_sample_label(session, label_rule_version="")


def test_rejects_label_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_claim_sample_labels_claim"):
        _claim_sample_label(session)


def test_deleting_a_claim_sample_removes_its_label(session: Session) -> None:
    sample = _claim_sample(session)
    _claim_sample_label(session)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(ClaimSampleLabel)).all() == []


@pytest.mark.parametrize(
    ("column", "value"), [("split", "holdout"), ("denial_reason_category", "fraud")]
)
def test_rejects_unknown_split_or_reason_category(
    session: Session, column: str, value: str
) -> None:
    _claim_sample(session)
    values = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "denial_reason_category": "noncovered",
        "split": "train",
    } | {column: value}
    insert = text(
        "INSERT INTO claim_sample_labels (source_claim_id, is_denied, denial_reason_category,"
        " appeal_success_proxy, label_rule_version, split, split_seed)"
        " VALUES (:source_claim_id, true, :denial_reason_category, false, 'v1', :split, 42)"
    )

    with pytest.raises(DataError):
        session.execute(insert, values)


ANSWER_KEY: dict[str, Any] = {
    "claim_number": SAMPLE_CLAIM_ID,
    "payer_name": "Example Mutual Health",
    "diagnosis_codes": ["4019", "V5869"],
    "total_payment_amount": "0.00",
}
NOISE_RECORD: dict[str, Any] = {"level": "light", "swaps": 2, "drops": 0}


def _generated_document(session: Session, **overrides: Any) -> GeneratedDocument:
    """Store a denial letter for the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "document_type": DocumentType.DENIAL_LETTER,
        "template_id": "formal_letter",
        "seed": 42,
        "generator_version": "v1",
        "text": "Made-up denial letter text.",
        "answer_key": ANSWER_KEY,
        "noise_version": "v1",
        "noise_record": NOISE_RECORD,
    }
    document = GeneratedDocument(**(fields | overrides))
    session.add(document)
    session.flush()
    return document


def test_stores_generated_document_with_text_and_answer_key(session: Session) -> None:
    _claim_sample(session)
    document = _generated_document(session)
    session.expire_all()

    stored = session.get(GeneratedDocument, document.id)
    assert stored is not None
    assert stored.document_type is DocumentType.DENIAL_LETTER
    assert stored.template_id == "formal_letter"
    assert stored.seed == 42
    assert stored.generator_version == "v1"
    assert stored.text == "Made-up denial letter text."
    assert stored.answer_key == ANSWER_KEY
    assert stored.noise_version == "v1"
    assert stored.noise_record == NOISE_RECORD
    assert stored.created_at is not None


def test_rejects_second_document_of_the_same_type_for_one_claim(session: Session) -> None:
    _claim_sample(session)
    _generated_document(session)

    with pytest.raises(IntegrityError, match="uq_generated_documents_claim_type"):
        _generated_document(session, seed=7)


def test_allows_documents_of_different_types_for_one_claim(session: Session) -> None:
    _claim_sample(session)
    _generated_document(session)
    _generated_document(session, document_type=DocumentType.CLINICAL_NOTE)

    assert len(session.scalars(select(GeneratedDocument)).all()) == 2


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("template_id", "", "ck_generated_documents_template_id_not_empty"),
        ("seed", -1, "ck_generated_documents_seed_not_negative"),
        ("generator_version", "", "ck_generated_documents_generator_version_not_empty"),
        ("text", "", "ck_generated_documents_text_not_empty"),
        ("noise_version", "", "ck_generated_documents_noise_version_not_empty"),
    ],
)
def test_rejects_document_with_an_empty_field_or_negative_seed(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    _claim_sample(session)
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _generated_document(session, **bad)


def test_rejects_document_without_an_answer_key(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO generated_documents (source_claim_id, document_type, template_id, seed,"
        " generator_version, text) VALUES (:source_claim_id, 'denial_letter', 'formal_letter',"
        " 42, 'v1', 'Made-up text.')"
    )

    with pytest.raises(IntegrityError, match="answer_key"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID})


def test_rejects_document_without_a_noise_record(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO generated_documents (source_claim_id, document_type, template_id, seed,"
        " generator_version, text, answer_key, noise_version) VALUES (:source_claim_id,"
        " 'denial_letter', 'formal_letter', 42, 'v1', 'Made-up text.', '{}', 'v1')"
    )

    with pytest.raises(IntegrityError, match="noise_record"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID})


def test_rejects_document_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_generated_documents_claim"):
        _generated_document(session)


def test_deleting_a_claim_sample_removes_its_documents(session: Session) -> None:
    sample = _claim_sample(session)
    _generated_document(session)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(GeneratedDocument)).all() == []


def test_rejects_unknown_document_type(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO generated_documents (source_claim_id, document_type, template_id, seed,"
        " generator_version, text, answer_key, noise_version, noise_record) VALUES"
        " (:source_claim_id, 'fax_cover', 'formal_letter', 42, 'v1', 'Made-up text.', '{}',"
        " 'v1', '{}')"
    )

    with pytest.raises(DataError):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID})


def _llm_call(session: Session, **overrides: Any) -> LlmCall:
    fields: dict[str, Any] = {
        "provider": LlmProvider.PRIMARY,
        "model_name": "example-small-model",
        "prompt_version": "extraction-v1",
        "purpose": "extraction",
        "input_tokens": 520,
        "output_tokens": 75,
        "cost_usd": Decimal("0.000123"),
    }
    call = LlmCall(**(fields | overrides))
    session.add(call)
    session.flush()
    return call


def test_stores_llm_call_with_model_prompt_version_and_exact_cost(session: Session) -> None:
    call = _llm_call(session)
    session.expire_all()

    stored = session.get(LlmCall, call.id)
    assert stored is not None
    assert stored.provider is LlmProvider.PRIMARY
    assert stored.model_name == "example-small-model"
    assert stored.prompt_version == "extraction-v1"
    assert stored.purpose == "extraction"
    assert stored.input_tokens == 520
    assert stored.output_tokens == 75
    assert stored.cost_usd == Decimal("0.000123")
    assert isinstance(stored.cost_usd, Decimal)
    assert stored.created_at is not None
    assert stored.updated_at is not None


def test_keeps_one_millionth_of_a_dollar_apart_from_zero(session: Session) -> None:
    call = _llm_call(session, cost_usd=Decimal("0.000001"))
    session.expire_all()

    stored = session.get(LlmCall, call.id)
    assert stored is not None
    assert stored.cost_usd == Decimal("0.000001")


def test_allows_a_free_fallback_call_with_no_tokens(session: Session) -> None:
    call = _llm_call(
        session, provider=LlmProvider.FALLBACK, input_tokens=0, output_tokens=0, cost_usd=0
    )
    session.expire_all()

    stored = session.get(LlmCall, call.id)
    assert stored is not None
    assert stored.provider is LlmProvider.FALLBACK
    assert stored.cost_usd == Decimal("0")


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("model_name", "", "ck_llm_calls_model_name_not_empty"),
        ("prompt_version", "", "ck_llm_calls_prompt_version_not_empty"),
        ("purpose", "", "ck_llm_calls_purpose_not_empty"),
        ("input_tokens", -1, "ck_llm_calls_input_tokens_not_negative"),
        ("output_tokens", -1, "ck_llm_calls_output_tokens_not_negative"),
        ("cost_usd", Decimal("-0.000001"), "ck_llm_calls_cost_usd_not_negative"),
    ],
)
def test_rejects_llm_call_with_an_empty_field_or_a_negative_number(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _llm_call(session, **bad)


def test_rejects_unknown_llm_provider(session: Session) -> None:
    insert = text(
        "INSERT INTO llm_calls (provider, model_name, prompt_version, purpose, input_tokens,"
        " output_tokens, cost_usd) VALUES ('someone_else', 'example-small-model',"
        " 'extraction-v1', 'extraction', 1, 1, 0)"
    )

    with pytest.raises(DataError):
        session.execute(insert)


TEXT_SHA256 = "0123456789abcdef" * 4
EXTRACTED_FIELDS: dict[str, Any] = {
    "claim_number": {"value": SAMPLE_CLAIM_ID, "confidence": 0.98},
    "member_id": {"value": None, "confidence": 0.4},
    "diagnosis_codes": {"value": ["4019", "V5869"], "confidence": 0.9},
}


def _document_extraction(session: Session, **overrides: Any) -> DocumentExtraction:
    """Store a result for the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "document_type": DocumentType.DENIAL_LETTER,
        "prompt_version": "v1",
        "model_name": "example-small-model",
        "provider": LlmProvider.PRIMARY,
        "text_sha256": TEXT_SHA256,
        "fields": EXTRACTED_FIELDS,
    }
    extraction = DocumentExtraction(**(fields | overrides))
    session.add(extraction)
    session.flush()
    return extraction


def test_stores_document_extraction_with_model_prompt_version_and_fields(
    session: Session,
) -> None:
    _claim_sample(session)
    extraction = _document_extraction(session)
    session.expire_all()

    stored = session.get(DocumentExtraction, extraction.id)
    assert stored is not None
    assert stored.source_claim_id == SAMPLE_CLAIM_ID
    assert stored.document_type is DocumentType.DENIAL_LETTER
    assert stored.prompt_version == "v1"
    assert stored.model_name == "example-small-model"
    assert stored.provider is LlmProvider.PRIMARY
    assert stored.text_sha256 == TEXT_SHA256
    assert stored.fields == EXTRACTED_FIELDS
    assert stored.created_at is not None


def test_rejects_second_extraction_for_one_document_and_prompt_version(session: Session) -> None:
    _claim_sample(session)
    _document_extraction(session)

    with pytest.raises(IntegrityError, match="uq_document_extractions_claim_type_prompt"):
        _document_extraction(session, provider=LlmProvider.FALLBACK)


def test_allows_another_prompt_version_or_document_type_for_one_claim(session: Session) -> None:
    _claim_sample(session)
    _document_extraction(session)
    _document_extraction(session, prompt_version="v2")
    _document_extraction(session, document_type=DocumentType.CLINICAL_NOTE)

    assert len(session.scalars(select(DocumentExtraction)).all()) == 3


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("prompt_version", "", "ck_document_extractions_prompt_version_not_empty"),
        ("model_name", "", "ck_document_extractions_model_name_not_empty"),
        ("text_sha256", "abc123", "ck_document_extractions_text_sha256_format"),
        ("text_sha256", TEXT_SHA256.upper(), "ck_document_extractions_text_sha256_format"),
        ("text_sha256", "g" * 64, "ck_document_extractions_text_sha256_format"),
    ],
)
def test_rejects_extraction_with_an_empty_field_or_a_malformed_text_hash(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    _claim_sample(session)
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _document_extraction(session, **bad)


def test_rejects_extraction_without_fields(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO document_extractions (source_claim_id, document_type, prompt_version,"
        " model_name, provider, text_sha256) VALUES (:source_claim_id, 'denial_letter', 'v1',"
        " 'example-small-model', 'primary', :text_sha256)"
    )

    with pytest.raises(IntegrityError, match="fields"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID, "text_sha256": TEXT_SHA256})


def test_rejects_extraction_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_document_extractions_claim"):
        _document_extraction(session)


def test_deleting_a_claim_sample_removes_its_extractions(session: Session) -> None:
    sample = _claim_sample(session)
    _document_extraction(session)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(DocumentExtraction)).all() == []


def test_extraction_stays_when_its_document_is_replaced(session: Session) -> None:
    _claim_sample(session)
    document = _generated_document(session)
    _document_extraction(session)

    session.delete(document)
    session.flush()
    _generated_document(session, seed=7)

    assert len(session.scalars(select(DocumentExtraction)).all()) == 1


def _evidence_document(session: Session, **overrides: Any) -> EvidenceDocument:
    fields: dict[str, Any] = {
        "source": EvidenceSource.CMS_NCD,
        "source_document_id": "9001",
        "section_number": "999.1",
        "title": "Made-up Determination",
        "version_number": 2,
        "effective_date": date(2020, 1, 1),
        "source_file_date": date(2026, 10, 5),
    }
    document = EvidenceDocument(**(fields | overrides))
    session.add(document)
    session.flush()
    return document


def _evidence_chunk(
    session: Session, document: EvidenceDocument, chunk_index: int = 0, **overrides: Any
) -> EvidenceChunk:
    fields: dict[str, Any] = {
        "evidence_document_id": document.id,
        "chunk_index": chunk_index,
        "section_title": "Made-up Section",
        "text": "Made-up policy text.",
        "text_sha256": TEXT_SHA256,
        "chunker_version": "v1",
    }
    chunk = EvidenceChunk(**(fields | overrides))
    session.add(chunk)
    session.flush()
    return chunk


def test_stores_evidence_document_with_its_chunks(session: Session) -> None:
    document = _evidence_document(session)
    first = _evidence_chunk(session, document)
    _evidence_chunk(session, document, 1, text="More made-up policy text.")
    session.expire_all()

    stored = session.get(EvidenceDocument, document.id)
    assert stored is not None
    assert stored.source is EvidenceSource.CMS_NCD
    assert stored.source_document_id == "9001"
    assert stored.section_number == "999.1"
    assert stored.title == "Made-up Determination"
    assert stored.version_number == 2
    assert stored.effective_date == date(2020, 1, 1)
    assert stored.source_file_date == date(2026, 10, 5)
    assert stored.created_at is not None
    assert stored.updated_at is not None

    stored_chunk = session.get(EvidenceChunk, first.id)
    assert stored_chunk is not None
    assert stored_chunk.evidence_document_id == document.id
    assert stored_chunk.chunk_index == 0
    assert stored_chunk.section_title == "Made-up Section"
    assert stored_chunk.text == "Made-up policy text."
    assert stored_chunk.text_sha256 == TEXT_SHA256
    assert stored_chunk.chunker_version == "v1"
    assert stored_chunk.created_at is not None
    assert len(session.scalars(select(EvidenceChunk)).all()) == 2


def test_stores_evidence_chunk_without_a_section_title(session: Session) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session), section_title=None)
    session.expire_all()

    stored = session.get(EvidenceChunk, chunk.id)
    assert stored is not None
    assert stored.section_title is None


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"section_number": "999.2"}, "uq_evidence_documents_source_document"),
        ({"source_document_id": "9002"}, "uq_evidence_documents_source_section"),
    ],
)
def test_rejects_second_evidence_document_with_the_same_id_or_section_number(
    session: Session, overrides: dict[str, Any], constraint: str
) -> None:
    _evidence_document(session)

    with pytest.raises(IntegrityError, match=constraint):
        _evidence_document(session, **overrides)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("source_document_id", "", "ck_evidence_documents_source_document_id_not_empty"),
        ("section_number", "", "ck_evidence_documents_section_number_not_empty"),
        ("title", "", "ck_evidence_documents_title_not_empty"),
        ("version_number", 0, "ck_evidence_documents_version_number_positive"),
    ],
)
def test_rejects_evidence_document_with_an_empty_field_or_a_version_below_one(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _evidence_document(session, **bad)


def test_stores_evidence_document_of_the_manual_source(session: Session) -> None:
    document = _evidence_document(session, source=EvidenceSource.CMS_MANUAL)
    session.expire_all()

    stored = session.get(EvidenceDocument, document.id)
    assert stored is not None
    assert stored.source is EvidenceSource.CMS_MANUAL


def test_allows_the_same_document_id_and_section_number_once_per_source(session: Session) -> None:
    _evidence_document(session)
    _evidence_document(session, source=EvidenceSource.CMS_MANUAL)

    assert len(session.scalars(select(EvidenceDocument)).all()) == 2


def test_rejects_unknown_evidence_source(session: Session) -> None:
    insert = text(
        "INSERT INTO evidence_documents (source, source_document_id, section_number, title,"
        " version_number, effective_date, source_file_date) VALUES ('somewhere_else', '9001',"
        " '999.1', 'Made-up Determination', 1, '2020-01-01', '2026-10-05')"
    )

    with pytest.raises(DataError):
        session.execute(insert)


def test_rejects_second_chunk_with_the_same_index_in_one_document(session: Session) -> None:
    document = _evidence_document(session)
    _evidence_chunk(session, document)

    with pytest.raises(IntegrityError, match="uq_evidence_chunks_document_index"):
        _evidence_chunk(session, document, text="Other made-up text.")


def test_allows_the_same_chunk_index_in_two_documents(session: Session) -> None:
    _evidence_chunk(session, _evidence_document(session))
    other = _evidence_document(session, source_document_id="9002", section_number="999.2")
    _evidence_chunk(session, other)

    assert len(session.scalars(select(EvidenceChunk)).all()) == 2


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("chunk_index", -1, "ck_evidence_chunks_chunk_index_not_negative"),
        ("section_title", "", "ck_evidence_chunks_section_title_not_empty"),
        ("text", "", "ck_evidence_chunks_text_not_empty"),
        ("text_sha256", "abc123", "ck_evidence_chunks_text_sha256_format"),
        ("text_sha256", TEXT_SHA256.upper(), "ck_evidence_chunks_text_sha256_format"),
        ("chunker_version", "", "ck_evidence_chunks_chunker_version_not_empty"),
    ],
)
def test_rejects_evidence_chunk_with_an_empty_field_a_negative_index_or_a_malformed_hash(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    document = _evidence_document(session)
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _evidence_chunk(session, document, **bad)


def test_rejects_evidence_chunk_without_its_document(session: Session) -> None:
    document = _evidence_document(session)
    missing_id = document.id + 1

    with pytest.raises(IntegrityError, match="fk_evidence_chunks_document"):
        _evidence_chunk(session, document, evidence_document_id=missing_id)


def test_deleting_an_evidence_document_removes_its_chunks(session: Session) -> None:
    document = _evidence_document(session)
    _evidence_chunk(session, document)
    _evidence_chunk(session, document, 1)

    session.delete(document)
    session.flush()

    assert session.scalars(select(EvidenceChunk)).all() == []


EMBEDDING_MODEL = "made-up-embedding-model"
# Halves and quarters are stored exactly in the column's four-byte numbers.
EMBEDDING = [0.5, -0.25] * (EMBEDDING_DIMENSIONS // 2)


def _chunk_embedding(
    session: Session, chunk: EvidenceChunk, **overrides: Any
) -> EvidenceChunkEmbedding:
    fields: dict[str, Any] = {
        "evidence_chunk_id": chunk.id,
        "model_name": EMBEDDING_MODEL,
        "input_version": "v1",
        "embedding": EMBEDDING,
    }
    embedding = EvidenceChunkEmbedding(**(fields | overrides))
    session.add(embedding)
    session.flush()
    return embedding


def test_stores_chunk_embedding_with_its_model_input_version_and_every_number(
    session: Session,
) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    embedding = _chunk_embedding(session, chunk)
    session.expire_all()

    stored = session.get(EvidenceChunkEmbedding, embedding.id)
    assert stored is not None
    assert stored.evidence_chunk_id == chunk.id
    assert stored.model_name == EMBEDDING_MODEL
    assert stored.input_version == "v1"
    assert len(stored.embedding) == EMBEDDING_DIMENSIONS
    assert list(stored.embedding) == EMBEDDING
    assert stored.created_at is not None
    assert stored.updated_at is not None


def test_rejects_second_embedding_for_one_chunk_model_and_input_version(session: Session) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    _chunk_embedding(session, chunk)

    with pytest.raises(IntegrityError, match="uq_evidence_chunk_embeddings_chunk_model_version"):
        _chunk_embedding(session, chunk)


def test_allows_another_model_or_input_version_for_one_chunk(session: Session) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    _chunk_embedding(session, chunk)
    _chunk_embedding(session, chunk, model_name="other-made-up-model")
    _chunk_embedding(session, chunk, input_version="v2")

    assert len(session.scalars(select(EvidenceChunkEmbedding)).all()) == 3


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("model_name", "ck_evidence_chunk_embeddings_model_name_not_empty"),
        ("input_version", "ck_evidence_chunk_embeddings_input_version_not_empty"),
    ],
)
def test_rejects_chunk_embedding_with_an_empty_model_name_or_input_version(
    session: Session, column: str, constraint: str
) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    bad: dict[str, Any] = {column: ""}

    with pytest.raises(IntegrityError, match=constraint):
        _chunk_embedding(session, chunk, **bad)


@pytest.mark.parametrize("size", [3, EMBEDDING_DIMENSIONS - 1, EMBEDDING_DIMENSIONS + 1])
def test_rejects_chunk_embedding_of_another_size(session: Session, size: int) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))

    with pytest.raises(DataError, match=f"expected {EMBEDDING_DIMENSIONS} dimensions"):
        _chunk_embedding(session, chunk, embedding=[0.5] * size)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_rejects_chunk_embedding_with_a_number_that_is_not_finite(
    session: Session, value: float
) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    numbers = [value] + EMBEDDING[1:]

    with pytest.raises(DataError, match="not allowed in vector"):
        _chunk_embedding(session, chunk, embedding=numbers)


def test_rejects_chunk_embedding_without_a_vector(session: Session) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))

    with pytest.raises(IntegrityError, match="embedding"):
        _chunk_embedding(session, chunk, embedding=None)


def test_rejects_chunk_embedding_without_its_chunk(session: Session) -> None:
    chunk = _evidence_chunk(session, _evidence_document(session))
    missing_id = chunk.id + 1

    with pytest.raises(IntegrityError, match="fk_evidence_chunk_embeddings_chunk"):
        _chunk_embedding(session, chunk, evidence_chunk_id=missing_id)


def test_deleting_an_evidence_chunk_removes_its_embeddings(session: Session) -> None:
    document = _evidence_document(session)
    chunk = _evidence_chunk(session, document)
    kept = _chunk_embedding(session, _evidence_chunk(session, document, 1))
    _chunk_embedding(session, chunk)

    session.delete(chunk)
    session.flush()

    assert [row.id for row in session.scalars(select(EvidenceChunkEmbedding))] == [kept.id]


def test_deleting_an_evidence_document_removes_its_chunks_embeddings(session: Session) -> None:
    # What a new load of the corpus does: the documents go, and with them chunks and vectors.
    document = _evidence_document(session)
    _chunk_embedding(session, _evidence_chunk(session, document))
    _chunk_embedding(session, _evidence_chunk(session, document, 1))

    session.delete(document)
    session.flush()

    assert session.scalars(select(EvidenceChunkEmbedding)).all() == []


# The shape of a checked `AppealLetter` (`src/drafting/schemas.py`), with made-up text.
DRAFT_LETTER: dict[str, Any] = {
    "paragraphs": [
        {"kind": "other", "text": "I am writing to appeal the denial.", "citations": []},
        {
            "kind": "policy",
            "text": "The made-up determination covers the service.",
            "citations": [
                {
                    "source": "cms_ncd",
                    "source_document_id": "9001",
                    "chunk_index": 0,
                    "text_sha256": TEXT_SHA256,
                    "quote": "Made-up policy text.",
                }
            ],
        },
    ]
}


def _appeal_draft(session: Session, **overrides: Any) -> StoredAppealDraft:
    """Store a draft for the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "prompt_version": "v1",
        "search_question_version": "v1",
        "model_name": "example-small-model",
        "provider": LlmProvider.PRIMARY,
        "text_sha256": TEXT_SHA256,
        "letter": DRAFT_LETTER,
    }
    draft = StoredAppealDraft(**(fields | overrides))
    session.add(draft)
    session.flush()
    return draft


def test_stores_appeal_draft_with_model_versions_and_its_letter(session: Session) -> None:
    _claim_sample(session)
    draft = _appeal_draft(session)
    session.expire_all()

    stored = session.get(StoredAppealDraft, draft.id)
    assert stored is not None
    assert stored.source_claim_id == SAMPLE_CLAIM_ID
    assert stored.prompt_version == "v1"
    assert stored.search_question_version == "v1"
    assert stored.model_name == "example-small-model"
    assert stored.provider is LlmProvider.PRIMARY
    assert stored.text_sha256 == TEXT_SHA256
    assert stored.letter == DRAFT_LETTER
    assert stored.created_at is not None


def test_rejects_second_appeal_draft_for_one_claim_and_prompt_version(session: Session) -> None:
    _claim_sample(session)
    _appeal_draft(session)

    with pytest.raises(IntegrityError, match="uq_appeal_drafts_claim_prompt"):
        _appeal_draft(session, provider=LlmProvider.FALLBACK, search_question_version="v2")


def test_allows_another_prompt_version_for_one_claim(session: Session) -> None:
    _claim_sample(session)
    _appeal_draft(session)
    _appeal_draft(session, prompt_version="v2")

    assert len(session.scalars(select(StoredAppealDraft)).all()) == 2


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("prompt_version", "", "ck_appeal_drafts_prompt_version_not_empty"),
        ("search_question_version", "", "ck_appeal_drafts_search_question_version_not_empty"),
        ("model_name", "", "ck_appeal_drafts_model_name_not_empty"),
        ("text_sha256", "abc123", "ck_appeal_drafts_text_sha256_format"),
        ("text_sha256", TEXT_SHA256.upper(), "ck_appeal_drafts_text_sha256_format"),
        ("text_sha256", "g" * 64, "ck_appeal_drafts_text_sha256_format"),
    ],
)
def test_rejects_appeal_draft_with_an_empty_field_or_a_malformed_text_hash(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    _claim_sample(session)
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _appeal_draft(session, **bad)


def test_rejects_appeal_draft_without_a_letter(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO appeal_drafts (source_claim_id, prompt_version, search_question_version,"
        " model_name, provider, text_sha256) VALUES (:source_claim_id, 'v1', 'v1',"
        " 'example-small-model', 'primary', :text_sha256)"
    )

    with pytest.raises(IntegrityError, match="letter"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID, "text_sha256": TEXT_SHA256})


def test_rejects_appeal_draft_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_appeal_drafts_claim"):
        _appeal_draft(session)


def test_deleting_a_claim_sample_removes_its_appeal_drafts(session: Session) -> None:
    sample = _claim_sample(session)
    _appeal_draft(session)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(StoredAppealDraft)).all() == []


def test_appeal_draft_stays_when_the_evidence_it_cites_is_replaced(session: Session) -> None:
    # What a new load of the corpus does: the cited chunk goes and comes back with a new row id.
    _claim_sample(session)
    document = _evidence_document(session)
    _evidence_chunk(session, document)
    draft = _appeal_draft(session)

    session.delete(document)
    session.flush()
    _evidence_chunk(session, _evidence_document(session))
    session.expire_all()

    stored = session.get(StoredAppealDraft, draft.id)
    assert stored is not None
    assert stored.letter == DRAFT_LETTER


# An agent version that is never the current one.
OTHER_AGENT_VERSION = "v999"


def _agent_trace(text_sha256: str) -> dict[str, Any]:
    """A trace of a claim the rules stopped, as the loop would write it."""
    trace = AgentTrace(
        end_state=EndState.STOPPED_BY_RULES,
        extraction=ExtractionStep(
            status=StepStatus.DONE, prompt_version="v2", text_sha256=text_sha256
        ),
        rules=RulesStep(
            status=StepStatus.STOPPED,
            rules_version=RULES_VERSION,
            today=date(2010, 1, 1),
            amount_floor=Decimal("25"),
            outcome=RuleOutcome.HARD_FAIL,
            reasons=(RuleReason.DEADLINE_PASSED,),
        ),
    )
    return trace.model_dump(mode="json")


def _agent_run(session: Session, **overrides: Any) -> AgentRun:
    """Store a run for the claim `SAMPLE_CLAIM_ID`. The claim row must exist first."""
    fields: dict[str, Any] = {
        "source_claim_id": SAMPLE_CLAIM_ID,
        "agent_version": AGENT_VERSION,
        "end_state": EndState.STOPPED_BY_RULES,
        "text_sha256": TEXT_SHA256,
        "trace": _agent_trace(TEXT_SHA256),
    }
    run = AgentRun(**(fields | overrides))
    session.add(run)
    session.flush()
    return run


def test_stores_agent_run_with_its_end_state_and_a_trace_that_reads_back(session: Session) -> None:
    _claim_sample(session)
    run = _agent_run(session)
    session.expire_all()

    stored = session.get(AgentRun, run.id)
    assert stored is not None
    assert stored.source_claim_id == SAMPLE_CLAIM_ID
    assert stored.agent_version == AGENT_VERSION
    assert stored.end_state is EndState.STOPPED_BY_RULES
    assert stored.text_sha256 == TEXT_SHA256
    assert stored.created_at is not None
    trace = AgentTrace.model_validate(stored.trace)
    assert trace.end_state is EndState.STOPPED_BY_RULES
    assert trace.rules.amount_floor == Decimal("25")
    assert trace.drafting.status is StepStatus.NOT_RUN


def test_rejects_second_agent_run_for_one_claim_and_agent_version(session: Session) -> None:
    _claim_sample(session)
    _agent_run(session)

    with pytest.raises(IntegrityError, match="uq_agent_runs_claim_agent_version"):
        _agent_run(session)


def test_allows_another_agent_version_for_one_claim(session: Session) -> None:
    _claim_sample(session)
    _agent_run(session)
    _agent_run(
        session,
        agent_version=OTHER_AGENT_VERSION,
        trace=_agent_trace(TEXT_SHA256) | {"agent_version": OTHER_AGENT_VERSION},
    )

    assert len(session.scalars(select(AgentRun)).all()) == 2


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("agent_version", "", "ck_agent_runs_agent_version_not_empty"),
        ("text_sha256", "abc123", "ck_agent_runs_text_sha256_format"),
        ("text_sha256", TEXT_SHA256.upper(), "ck_agent_runs_text_sha256_format"),
        ("text_sha256", "g" * 64, "ck_agent_runs_text_sha256_format"),
    ],
)
def test_rejects_agent_run_with_an_empty_version_or_a_malformed_text_hash(
    session: Session, column: str, value: Any, constraint: str
) -> None:
    _claim_sample(session)
    bad: dict[str, Any] = {column: value}

    with pytest.raises(IntegrityError, match=constraint):
        _agent_run(session, **bad)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"end_state": EndState.NEEDS_REVIEW}, "ck_agent_runs_trace_end_state_matches"),
        ({"agent_version": OTHER_AGENT_VERSION}, "ck_agent_runs_trace_agent_version_matches"),
        ({"trace": {"agent_version": AGENT_VERSION}}, "ck_agent_runs_trace_end_state_matches"),
        (
            {"trace": {"end_state": "stopped_by_rules"}},
            "ck_agent_runs_trace_agent_version_matches",
        ),
        ({"trace": []}, "ck_agent_runs_trace_"),
        ({"trace": None}, "ck_agent_runs_trace_"),  # stored as the JSON value null
    ],
)
def test_rejects_agent_run_whose_trace_disagrees_with_its_columns(
    session: Session, overrides: dict[str, Any], constraint: str
) -> None:
    _claim_sample(session)

    with pytest.raises(IntegrityError, match=constraint):
        _agent_run(session, **overrides)


def test_rejects_agent_run_with_an_unknown_end_state(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO agent_runs (source_claim_id, agent_version, end_state, text_sha256, trace)"
        " VALUES (:source_claim_id, 'v1', 'approved', :text_sha256,"
        ' \'{"end_state": "approved", "agent_version": "v1"}\')'
    )

    with pytest.raises(DataError, match="agent_end_state"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID, "text_sha256": TEXT_SHA256})


def test_rejects_agent_run_without_a_trace(session: Session) -> None:
    _claim_sample(session)
    insert = text(
        "INSERT INTO agent_runs (source_claim_id, agent_version, end_state, text_sha256)"
        " VALUES (:source_claim_id, 'v1', 'stopped_by_rules', :text_sha256)"
    )

    with pytest.raises(IntegrityError, match="trace"):
        session.execute(insert, {"source_claim_id": SAMPLE_CLAIM_ID, "text_sha256": TEXT_SHA256})


def test_rejects_agent_run_without_its_claim(session: Session) -> None:
    _claim_sample(session, "800000000000002")  # another claim

    with pytest.raises(IntegrityError, match="fk_agent_runs_claim"):
        _agent_run(session)


def test_deleting_a_claim_sample_removes_its_agent_runs(session: Session) -> None:
    sample = _claim_sample(session)
    _agent_run(session)

    session.delete(sample)
    session.flush()

    assert session.scalars(select(AgentRun)).all() == []


def test_agent_run_stays_when_the_evidence_and_the_draft_are_removed(session: Session) -> None:
    # No link to the evidence tables or to `appeal_drafts`: the trace names both by value.
    _claim_sample(session)
    document = _evidence_document(session)
    _evidence_chunk(session, document)
    draft = _appeal_draft(session)
    run = _agent_run(session)

    session.delete(document)
    session.delete(draft)
    session.flush()
    session.expire_all()

    assert session.get(AgentRun, run.id) is not None


UPLOAD_TEXT = "Made-up denial letter for a made-up patient."
STARTED_AT = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=30)


def _upload(session: Session, account: Account, **overrides: Any) -> Upload:
    """Store an upload by a new reviewer of `account`."""
    user = _user(account, f"reviewer-{account.id}@example.com")
    session.add(user)
    session.flush()
    fields: dict[str, Any] = {
        "account_id": account.id,
        "user_id": user.id,
        "file_name": "letter.txt",
        "file_type": UploadFileType.TEXT,
        "byte_size": len(UPLOAD_TEXT.encode()),
        "text": UPLOAD_TEXT,
        "text_sha256": TEXT_SHA256,
    }
    upload = Upload(**(fields | overrides))
    session.add(upload)
    session.flush()
    return upload


def _job(session: Session, upload: Upload, **overrides: Any) -> Job:
    job = Job(**({"account_id": upload.account_id, "upload_id": upload.id} | overrides))
    session.add(job)
    session.flush()
    return job


def _done_job(session: Session, upload: Upload) -> Job:
    return _job(
        session,
        upload,
        status=JobStatus.DONE,
        end_state=EndState.STOPPED_BY_RULES,
        started_at=STARTED_AT,
        finished_at=FINISHED_AT,
    )


def _upload_extraction(session: Session, job: Job, **overrides: Any) -> UploadExtraction:
    fields: dict[str, Any] = {
        "account_id": job.account_id,
        "job_id": job.id,
        "prompt_version": "v2",
        "model_name": "example-small-model",
        "provider": LlmProvider.PRIMARY,
        "text_sha256": TEXT_SHA256,
        "fields": EXTRACTED_FIELDS,
    }
    row = UploadExtraction(**(fields | overrides))
    session.add(row)
    session.flush()
    return row


def _upload_agent_run(session: Session, job: Job, **overrides: Any) -> UploadAgentRun:
    fields: dict[str, Any] = {
        "account_id": job.account_id,
        "job_id": job.id,
        "agent_version": AGENT_VERSION,
        "end_state": EndState.STOPPED_BY_RULES,
        "text_sha256": TEXT_SHA256,
        "trace": _agent_trace(TEXT_SHA256),
    }
    row = UploadAgentRun(**(fields | overrides))
    session.add(row)
    session.flush()
    return row


def _upload_draft(session: Session, job: Job, **overrides: Any) -> UploadDraft:
    fields: dict[str, Any] = {
        "account_id": job.account_id,
        "job_id": job.id,
        "prompt_version": "v3",
        "search_question_version": "v3",
        "model_name": "example-small-model",
        "provider": LlmProvider.PRIMARY,
        "text_sha256": TEXT_SHA256,
        "letter": DRAFT_LETTER,
    }
    row = UploadDraft(**(fields | overrides))
    session.add(row)
    session.flush()
    return row


def test_stores_an_upload_with_its_job_and_the_three_results_that_read_back(
    session: Session,
) -> None:
    upload = _upload(session, _account(session, "a"))
    job = _done_job(session, upload)
    extraction = _upload_extraction(session, job)
    run = _upload_agent_run(session, job)
    draft = _upload_draft(session, job)
    session.expire_all()

    stored_upload = session.get(Upload, upload.id)
    stored_job = session.get(Job, job.id)
    assert stored_upload is not None and stored_job is not None
    assert stored_upload.file_type is UploadFileType.TEXT
    assert stored_upload.text == UPLOAD_TEXT
    assert stored_job.status is JobStatus.DONE
    assert stored_job.end_state is EndState.STOPPED_BY_RULES
    assert stored_job.failure is None
    assert stored_job.finished_at == FINISHED_AT
    assert session.get(UploadExtraction, extraction.id).fields == EXTRACTED_FIELDS  # type: ignore[union-attr]
    assert session.get(UploadAgentRun, run.id).trace == _agent_trace(TEXT_SHA256)  # type: ignore[union-attr]
    assert session.get(UploadDraft, draft.id).letter == DRAFT_LETTER  # type: ignore[union-attr]


def test_a_new_job_is_queued_with_no_times(session: Session) -> None:
    job = _job(session, _upload(session, _account(session, "a")))
    session.expire_all()

    stored = session.get(Job, job.id)
    assert stored is not None
    assert stored.status is JobStatus.QUEUED
    assert (stored.started_at, stored.finished_at, stored.end_state) == (None, None, None)


def test_rejects_an_upload_naming_a_user_of_another_account(session: Session) -> None:
    other_user = _user(_account(session, "other"), "someone@example.com")
    session.add(other_user)
    session.flush()

    with pytest.raises(IntegrityError, match="fk_uploads_user"):
        _upload(session, _account(session, "a"), user_id=other_user.id)


def test_rejects_a_job_naming_an_upload_of_another_account(session: Session) -> None:
    other_upload = _upload(session, _account(session, "other"))
    account = _account(session, "a")

    with pytest.raises(IntegrityError, match="fk_jobs_upload"):
        _job(session, other_upload, account_id=account.id)


@pytest.mark.parametrize(
    ("make_row", "constraint"),
    [
        (_upload_extraction, "fk_upload_extractions_job"),
        (_upload_agent_run, "fk_upload_agent_runs_job"),
        (_upload_draft, "fk_upload_drafts_job"),
    ],
)
def test_rejects_a_result_naming_a_job_of_another_account(
    session: Session, make_row: Any, constraint: str
) -> None:
    other_job = _done_job(session, _upload(session, _account(session, "other")))
    account = _account(session, "a")

    with pytest.raises(IntegrityError, match=constraint):
        make_row(session, other_job, account_id=account.id)


def test_rejects_a_second_job_for_one_upload(session: Session) -> None:
    upload = _upload(session, _account(session, "a"))
    _job(session, upload)

    with pytest.raises(IntegrityError, match="uq_jobs_upload_id"):
        _job(session, upload)


@pytest.mark.parametrize(
    ("make_row", "constraint"),
    [
        (_upload_extraction, "uq_upload_extractions_job_id"),
        (_upload_agent_run, "uq_upload_agent_runs_job_id"),
        (_upload_draft, "uq_upload_drafts_job_id"),
    ],
)
def test_rejects_a_second_result_of_one_kind_for_one_job(
    session: Session, make_row: Any, constraint: str
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    make_row(session, job)

    with pytest.raises(IntegrityError, match=constraint):
        make_row(session, job)


@pytest.mark.parametrize(
    ("bad", "constraint"),
    [
        ({"file_name": ""}, "ck_uploads_file_name_not_empty"),
        ({"byte_size": 0}, "ck_uploads_byte_size_positive"),
        ({"text": ""}, "ck_uploads_text_not_empty"),
        ({"text_sha256": TEXT_SHA256.upper()}, "ck_uploads_text_sha256_format"),
    ],
)
def test_rejects_an_upload_with_an_empty_name_or_text_no_bytes_or_a_bad_hash(
    session: Session, bad: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _upload(session, _account(session, "a"), **bad)


def test_rejects_an_upload_of_an_unknown_file_type(session: Session) -> None:
    upload = _upload(session, _account(session, "a"))

    with pytest.raises(DataError, match="upload_file_type"):
        session.execute(
            text("UPDATE uploads SET file_type = 'image' WHERE id = :id"), {"id": upload.id}
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"status": JobStatus.RUNNING, "started_at": STARTED_AT},
        {
            "status": JobStatus.FAILED,
            "failure": JobFailure.EXTRACTION_FAILED,
            "started_at": STARTED_AT,
            "finished_at": FINISHED_AT,
        },
        # Interrupted while still queued: it never started.
        {"status": JobStatus.FAILED, "failure": JobFailure.INTERRUPTED, "finished_at": FINISHED_AT},
        {
            "status": JobStatus.DONE,
            "end_state": EndState.NEEDS_REVIEW,
            "started_at": STARTED_AT,
            "finished_at": STARTED_AT,
        },
    ],
)
def test_stores_a_job_whose_status_failure_end_state_and_times_fit(
    session: Session, fields: dict[str, Any]
) -> None:
    job = _job(session, _upload(session, _account(session, "a")), **fields)

    assert job.id is not None


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"failure": JobFailure.INTERNAL_ERROR}, "ck_jobs_failure_only_when_failed"),
        (
            {"status": JobStatus.FAILED, "finished_at": FINISHED_AT},
            "ck_jobs_failure_only_when_failed",
        ),
        ({"end_state": EndState.NEEDS_REVIEW}, "ck_jobs_end_state_only_when_done"),
        (
            {"status": JobStatus.DONE, "started_at": STARTED_AT, "finished_at": FINISHED_AT},
            "ck_jobs_end_state_only_when_done",
        ),
        (
            {
                "status": JobStatus.FAILED,
                "failure": JobFailure.INTERRUPTED,
                "end_state": EndState.NEEDS_REVIEW,
                "finished_at": FINISHED_AT,
            },
            "ck_jobs_end_state_only_when_done",
        ),
        ({"started_at": STARTED_AT}, "ck_jobs_times_match_status"),
        ({"status": JobStatus.RUNNING}, "ck_jobs_times_match_status"),
        (
            {"status": JobStatus.RUNNING, "started_at": STARTED_AT, "finished_at": FINISHED_AT},
            "ck_jobs_times_match_status",
        ),
        (
            {
                "status": JobStatus.DONE,
                "end_state": EndState.NEEDS_REVIEW,
                "finished_at": FINISHED_AT,
            },
            "ck_jobs_times_match_status",
        ),
        (
            {"status": JobStatus.FAILED, "failure": JobFailure.INTERRUPTED},
            "ck_jobs_times_match_status",
        ),
        (
            {
                "status": JobStatus.DONE,
                "end_state": EndState.NEEDS_REVIEW,
                "started_at": FINISHED_AT,
                "finished_at": STARTED_AT,
            },
            "ck_jobs_finished_not_before_started",
        ),
    ],
)
def test_rejects_a_job_whose_status_failure_end_state_or_times_do_not_fit(
    session: Session, fields: dict[str, Any], constraint: str
) -> None:
    upload = _upload(session, _account(session, "a"))

    with pytest.raises(IntegrityError, match=constraint):
        _job(session, upload, **fields)


@pytest.mark.parametrize(
    ("make_row", "bad", "constraint"),
    [
        (
            _upload_extraction,
            {"prompt_version": ""},
            "ck_upload_extractions_prompt_version_not_empty",
        ),
        (_upload_extraction, {"model_name": ""}, "ck_upload_extractions_model_name_not_empty"),
        (_upload_extraction, {"text_sha256": "abc"}, "ck_upload_extractions_text_sha256_format"),
        (_upload_agent_run, {"agent_version": ""}, "ck_upload_agent_runs_agent_version_not_empty"),
        (_upload_agent_run, {"text_sha256": "g" * 64}, "ck_upload_agent_runs_text_sha256_format"),
        (
            _upload_agent_run,
            {"end_state": EndState.NEEDS_REVIEW},
            "ck_upload_agent_runs_trace_end_state_matches",
        ),
        (
            _upload_agent_run,
            {"trace": _agent_trace(TEXT_SHA256) | {"agent_version": OTHER_AGENT_VERSION}},
            "ck_upload_agent_runs_trace_agent_version_matches",
        ),
        (_upload_draft, {"prompt_version": ""}, "ck_upload_drafts_prompt_version_not_empty"),
        (
            _upload_draft,
            {"search_question_version": ""},
            "ck_upload_drafts_search_question_version_not_empty",
        ),
        (_upload_draft, {"model_name": ""}, "ck_upload_drafts_model_name_not_empty"),
        (
            _upload_draft,
            {"text_sha256": TEXT_SHA256.upper()},
            "ck_upload_drafts_text_sha256_format",
        ),
    ],
)
def test_rejects_a_job_result_with_an_empty_version_or_name_a_bad_hash_or_a_mismatched_trace(
    session: Session, make_row: Any, bad: dict[str, Any], constraint: str
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))

    with pytest.raises(IntegrityError, match=constraint):
        make_row(session, job, **bad)


def test_deleting_an_upload_removes_its_job_and_the_three_results(session: Session) -> None:
    upload = _upload(session, _account(session, "a"))
    job = _done_job(session, upload)
    _upload_extraction(session, job)
    _upload_agent_run(session, job)
    _upload_draft(session, job)

    session.delete(upload)
    session.flush()

    for model in (Job, UploadExtraction, UploadAgentRun, UploadDraft):
        assert session.scalars(select(model)).all() == [], model


def test_rejects_deleting_a_user_who_still_has_an_upload(session: Session) -> None:
    upload = _upload(session, _account(session, "a"))

    with pytest.raises(IntegrityError, match="fk_uploads_user"):
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": upload.user_id})


def test_deleting_an_account_removes_its_users_uploads_jobs_and_results(session: Session) -> None:
    account = _account(session, "a")
    account_id = account.id  # read before the delete; the expired object cannot be refreshed
    kept = _done_job(session, _upload(session, _account(session, "kept")))
    job = _done_job(session, _upload(session, account))
    _upload_extraction(session, job)
    _upload_agent_run(session, job)
    _upload_draft(session, job)

    # One statement: the user and the upload go together, so the user link does not refuse it.
    session.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": account_id})
    session.expire_all()

    assert [row.id for row in session.scalars(select(Job))] == [kept.id]
    assert session.scalars(select(Upload).where(Upload.account_id == account_id)).all() == []
    for model in (UploadExtraction, UploadAgentRun, UploadDraft):
        assert session.scalars(select(model)).all() == [], model


REVIEWED_AT = FINISHED_AT + timedelta(minutes=5)


def _reviewer(session: Session, account_id: int, email: str = "second@example.com") -> User:
    user = User(account_id=account_id, email=email, password_hash=MADE_UP_HASH)
    session.add(user)
    session.flush()
    return user


def _draft_of_new_job(session: Session, account: Account) -> UploadDraft:
    return _upload_draft(session, _done_job(session, _upload(session, account)))


def _revision(session: Session, draft: UploadDraft, **overrides: Any) -> DraftRevision:
    upload = session.get(Upload, session.get(Job, draft.job_id).upload_id)  # type: ignore[union-attr]
    assert upload is not None
    fields: dict[str, Any] = {
        "account_id": draft.account_id,
        "job_id": draft.job_id,
        "version": 1,
        "letter": DRAFT_LETTER,
        "text_sha256": TEXT_SHA256,
        "edited_by": upload.user_id,
    }
    row = DraftRevision(**(fields | overrides))
    session.add(row)
    session.flush()
    return row


def _audit_event(session: Session, job: Job, **overrides: Any) -> AuditEvent:
    upload = session.get(Upload, job.upload_id)
    assert upload is not None
    fields: dict[str, Any] = {
        "account_id": job.account_id,
        "user_id": upload.user_id,
        "action": AuditAction.DRAFT_EDITED,
        "job_id": job.id,
        "detail": {"version": 1},
    }
    row = AuditEvent(**(fields | overrides))
    session.add(row)
    session.flush()
    return row


def test_draft_reject_reasons_are_the_review_rubric_reasons() -> None:
    assert [member.value for member in DraftRejectReason] == [
        member.value for member in RejectReason
    ]


def test_a_new_draft_is_pending_and_a_new_job_cost_nothing(session: Session) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))
    session.expire_all()

    stored = session.get(UploadDraft, draft.id)
    job = session.get(Job, draft.job_id)
    assert stored is not None and job is not None
    assert stored.review_status is DraftReviewStatus.PENDING
    assert (stored.reviewed_by, stored.reviewed_at, stored.reject_reason) == (None, None, None)
    assert stored.approved_version is None
    assert job.cost_usd == Decimal("0")


def test_stores_a_job_cost_as_an_exact_decimal(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    job.cost_usd = Decimal("0.002186")
    session.flush()
    session.expire_all()

    assert session.get(Job, job.id).cost_usd == Decimal("0.002186")  # type: ignore[union-attr]


def test_rejects_a_negative_job_cost(session: Session) -> None:
    upload = _upload(session, _account(session, "a"))

    with pytest.raises(IntegrityError, match="ck_jobs_cost_usd_not_negative"):
        _job(session, upload, cost_usd=Decimal("-0.000001"))


def _approved(user_id: int, version: int = 0) -> dict[str, Any]:
    return {
        "review_status": DraftReviewStatus.APPROVED,
        "reviewed_by": user_id,
        "reviewed_at": REVIEWED_AT,
        "approved_version": version,
    }


def _rejected(user_id: int) -> dict[str, Any]:
    return {
        "review_status": DraftReviewStatus.REJECTED,
        "reviewed_by": user_id,
        "reviewed_at": REVIEWED_AT,
        "reject_reason": DraftRejectReason.CITATION_DOES_NOT_FIT,
    }


@pytest.mark.parametrize("decision", [_approved, _rejected])
def test_stores_an_approved_or_rejected_draft_that_reads_back(
    session: Session, decision: Any
) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))
    reviewer = _reviewer(session, draft.account_id)
    for key, value in decision(reviewer.id).items():
        setattr(draft, key, value)
    session.flush()
    session.expire_all()

    stored = session.get(UploadDraft, draft.id)
    assert stored is not None
    assert {key: getattr(stored, key) for key in decision(reviewer.id)} == decision(reviewer.id)
    assert stored.letter == DRAFT_LETTER


@pytest.mark.parametrize(
    "bad",
    [
        {"reviewed_at": REVIEWED_AT},
        {"reject_reason": DraftRejectReason.OTHER},
        {"approved_version": 0},
        {"review_status": DraftReviewStatus.APPROVED, "reviewed_at": REVIEWED_AT},
        {"review_status": DraftReviewStatus.REJECTED, "reviewed_at": REVIEWED_AT},
    ],
)
def test_rejects_a_draft_whose_review_columns_do_not_fit_its_status(
    session: Session, bad: dict[str, Any]
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))

    with pytest.raises(IntegrityError, match="ck_upload_drafts_review_matches_status"):
        _upload_draft(session, job, **bad)


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ({"reject_reason": DraftRejectReason.OTHER}, "ck_upload_drafts_review_matches_status"),
        ({"reviewed_by": None}, "ck_upload_drafts_review_matches_status"),
        ({"approved_version": None}, "ck_upload_drafts_review_matches_status"),
        ({"approved_version": -1}, "ck_upload_drafts_approved_version_not_negative"),
    ],
)
def test_rejects_an_approved_draft_with_a_reason_no_reviewer_or_a_bad_version(
    session: Session, change: dict[str, Any], constraint: str
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    reviewer = _reviewer(session, job.account_id)

    with pytest.raises(IntegrityError, match=constraint):
        _upload_draft(session, job, **(_approved(reviewer.id) | change))


def test_rejects_a_rejected_draft_without_a_reason_or_with_a_version(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    reviewer = _reviewer(session, job.account_id)
    with (
        pytest.raises(IntegrityError, match="ck_upload_drafts_review_matches_status"),
        session.begin_nested(),
    ):
        _upload_draft(session, job, **(_rejected(reviewer.id) | {"reject_reason": None}))

    with pytest.raises(IntegrityError, match="ck_upload_drafts_review_matches_status"):
        _upload_draft(session, job, **(_rejected(reviewer.id) | {"approved_version": 1}))


def test_rejects_an_unknown_reject_reason(session: Session) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))

    with pytest.raises(DataError, match="draft_reject_reason"):
        session.execute(
            text("UPDATE upload_drafts SET reject_reason = 'too_long' WHERE id = :id"),
            {"id": draft.id},
        )


def test_rejects_a_draft_reviewed_by_a_user_of_another_account(session: Session) -> None:
    other_user = _reviewer(session, _account(session, "other").id, "someone@example.com")
    job = _done_job(session, _upload(session, _account(session, "a")))

    with pytest.raises(IntegrityError, match="fk_upload_drafts_reviewer"):
        _upload_draft(session, job, **_approved(other_user.id))


def test_rejects_deleting_a_user_who_reviewed_a_draft(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    reviewer = _reviewer(session, job.account_id)
    _upload_draft(session, job, **_approved(reviewer.id))

    with pytest.raises(IntegrityError, match="fk_upload_drafts_reviewer"):
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": reviewer.id})


def test_stores_two_revisions_of_one_draft_that_read_back(session: Session) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))
    edited = DRAFT_LETTER | {"edited": True}
    first = _revision(session, draft)
    second = _revision(session, draft, version=2, letter=edited)
    session.expire_all()

    assert session.get(DraftRevision, first.id).letter == DRAFT_LETTER  # type: ignore[union-attr]
    assert session.get(DraftRevision, second.id).letter == edited  # type: ignore[union-attr]
    assert session.get(UploadDraft, draft.id).letter == DRAFT_LETTER  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("bad", "constraint"),
    [
        ({"version": 0}, "ck_draft_revisions_version_positive"),
        ({"text_sha256": TEXT_SHA256.upper()}, "ck_draft_revisions_text_sha256_format"),
    ],
)
def test_rejects_a_revision_with_version_zero_or_a_bad_hash(
    session: Session, bad: dict[str, Any], constraint: str
) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))

    with pytest.raises(IntegrityError, match=constraint):
        _revision(session, draft, **bad)


def test_rejects_a_second_revision_with_the_same_version(session: Session) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))
    _revision(session, draft)

    with pytest.raises(IntegrityError, match="uq_draft_revisions_job_version"):
        _revision(session, draft)


def test_rejects_a_revision_of_a_job_that_has_no_draft(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    upload = session.get(Upload, job.upload_id)
    assert upload is not None

    with pytest.raises(IntegrityError, match="fk_draft_revisions_draft"):
        session.add(
            DraftRevision(
                account_id=job.account_id,
                job_id=job.id,
                version=1,
                letter=DRAFT_LETTER,
                text_sha256=TEXT_SHA256,
                edited_by=upload.user_id,
            )
        )
        session.flush()


def test_rejects_a_revision_naming_a_draft_or_an_editor_of_another_account(
    session: Session,
) -> None:
    other_draft = _draft_of_new_job(session, _account(session, "other"))
    account = _account(session, "a")
    draft = _draft_of_new_job(session, account)
    other_user = session.get(Upload, session.get(Job, other_draft.job_id).upload_id).user_id  # type: ignore[union-attr]
    with pytest.raises(IntegrityError, match="fk_draft_revisions_draft"), session.begin_nested():
        _revision(session, other_draft, account_id=account.id)

    with pytest.raises(IntegrityError, match="fk_draft_revisions_editor"):
        _revision(session, draft, edited_by=other_user)


@pytest.mark.parametrize(
    ("action", "with_job"),
    [
        (AuditAction.ACCOUNT_CREATED, False),
        (AuditAction.UPLOAD_CREATED, True),
        (AuditAction.DRAFT_EDITED, True),
        (AuditAction.DRAFT_APPROVED, True),
        (AuditAction.DRAFT_REJECTED, True),
    ],
)
def test_stores_an_audit_event_with_a_job_exactly_when_the_action_needs_one(
    session: Session, action: AuditAction, with_job: bool
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    event = _audit_event(session, job, action=action, job_id=job.id if with_job else None)
    session.expire_all()

    stored = session.get(AuditEvent, event.id)
    assert stored is not None
    assert stored.action is action
    assert stored.job_id == (job.id if with_job else None)
    assert stored.detail == {"version": 1}


@pytest.mark.parametrize(
    ("bad", "constraint"),
    [
        ({"action": AuditAction.ACCOUNT_CREATED}, "ck_audit_events_job_matches_action"),
        ({"job_id": None}, "ck_audit_events_job_matches_action"),
        ({"detail": ["version", 1]}, "ck_audit_events_detail_is_object"),
        ({"detail": "text"}, "ck_audit_events_detail_is_object"),
    ],
)
def test_rejects_an_audit_event_whose_job_does_not_fit_the_action_or_whose_detail_is_no_object(
    session: Session, bad: dict[str, Any], constraint: str
) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))

    with pytest.raises(IntegrityError, match=constraint):
        _audit_event(session, job, **bad)


def test_an_audit_event_without_detail_stores_an_empty_object(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    upload = session.get(Upload, job.upload_id)
    assert upload is not None
    session.execute(
        text(
            "INSERT INTO audit_events (account_id, user_id, action, job_id)"
            " VALUES (:a, :u, 'upload_created', :j)"
        ),
        {"a": job.account_id, "u": upload.user_id, "j": job.id},
    )

    assert session.scalars(select(AuditEvent.detail)).one() == {}


def test_rejects_an_audit_event_naming_a_user_or_a_job_of_another_account(
    session: Session,
) -> None:
    other_job = _done_job(session, _upload(session, _account(session, "other")))
    job = _done_job(session, _upload(session, _account(session, "a")))
    other_user = session.get(Upload, other_job.upload_id).user_id  # type: ignore[union-attr]
    with pytest.raises(IntegrityError, match="fk_audit_events_job"), session.begin_nested():
        _audit_event(session, job, job_id=other_job.id)

    with pytest.raises(IntegrityError, match="fk_audit_events_user"):
        _audit_event(session, job, user_id=other_user)


def test_deleting_an_upload_removes_the_revisions_of_its_draft(session: Session) -> None:
    draft = _draft_of_new_job(session, _account(session, "a"))
    _revision(session, draft)
    upload = session.get(Upload, session.get(Job, draft.job_id).upload_id)  # type: ignore[union-attr]

    session.delete(upload)
    session.flush()

    assert session.scalars(select(DraftRevision)).all() == []


def test_rejects_deleting_an_upload_whose_job_has_an_audit_event(session: Session) -> None:
    job = _done_job(session, _upload(session, _account(session, "a")))
    _audit_event(session, job)

    with pytest.raises(IntegrityError, match="fk_audit_events_job"):
        session.execute(text("DELETE FROM uploads WHERE id = :id"), {"id": job.upload_id})


def test_deleting_an_account_removes_its_reviews_revisions_and_audit_events(
    session: Session,
) -> None:
    account = _account(session, "a")
    account_id = account.id  # read before the delete; the expired object cannot be refreshed
    kept = _draft_of_new_job(session, _account(session, "kept"))
    _revision(session, kept)
    draft = _draft_of_new_job(session, account)
    reviewer = _reviewer(session, account_id)
    for key, value in _approved(reviewer.id, version=1).items():
        setattr(draft, key, value)
    _revision(session, draft)
    job = session.get(Job, draft.job_id)
    assert job is not None
    _audit_event(session, job)
    _audit_event(session, job, action=AuditAction.ACCOUNT_CREATED, job_id=None)

    session.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": account_id})
    session.expire_all()

    assert [row.job_id for row in session.scalars(select(DraftRevision))] == [kept.job_id]
    assert session.scalars(select(AuditEvent)).all() == []
    assert [row.id for row in session.scalars(select(UploadDraft))] == [kept.id]


# A made-up HMAC: 64 lowercase hex characters, as the login limit writes them.
KEY_HASH = "fedcba9876543210" * 4


def _login_failure(session: Session, **overrides: Any) -> LoginFailure:
    fields: dict[str, Any] = {"key_kind": LoginKeyKind.EMAIL, "key_hash": KEY_HASH}
    failure = LoginFailure(**(fields | overrides))
    session.add(failure)
    session.flush()
    return failure


@pytest.mark.parametrize("kind", list(LoginKeyKind))
def test_stores_a_login_failure_with_its_key_kind_hash_and_timestamps(
    session: Session, kind: LoginKeyKind
) -> None:
    failure = _login_failure(session, key_kind=kind)
    session.expire_all()

    stored = session.get(LoginFailure, failure.id)
    assert stored is not None
    assert stored.key_kind is kind
    assert stored.key_hash == KEY_HASH
    assert stored.created_at is not None
    assert stored.updated_at is not None


def test_stores_many_failures_for_the_same_key(session: Session) -> None:
    for _ in range(3):
        _login_failure(session)

    assert len(session.scalars(select(LoginFailure)).all()) == 3


@pytest.mark.parametrize(
    "key_hash",
    [KEY_HASH[:63], KEY_HASH.upper(), "g" + KEY_HASH[1:], "", "ada@example.test"],
    ids=["too-short", "uppercase", "not-hex", "empty", "plain-email"],
)
def test_rejects_a_login_failure_whose_hash_is_not_64_lowercase_hex(
    session: Session, key_hash: str
) -> None:
    with pytest.raises(IntegrityError, match="ck_login_failures_key_hash_format"):
        _login_failure(session, key_hash=key_hash)


def test_rejects_unknown_login_key_kind(session: Session) -> None:
    insert = text(
        "INSERT INTO login_failures (key_kind, key_hash) VALUES ('user_agent', :key_hash)"
    )

    with pytest.raises(DataError):
        session.execute(insert, {"key_hash": KEY_HASH})
