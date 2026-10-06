from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    CLAIM_LINE_AMOUNT_COLUMNS,
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    PLAN_COUNT_COLUMNS,
    Account,
    Claim,
    ClaimSample,
    ClaimSampleLine,
    Denial,
    DenialStatus,
    ExchangeType,
    IssuerDenialStats,
    MetalLevel,
    PlanDenialStats,
    PlanType,
    User,
    UserRole,
)


def _account(session: Session, name: str) -> Account:
    account = Account(name=name)
    session.add(account)
    session.flush()
    return account


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
    user = User(account_id=account.id, email="a@example.com")
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
    session.add(User(email="orphan@example.com", role=UserRole.ADMIN))

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
