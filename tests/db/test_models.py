from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    Account,
    Claim,
    Denial,
    DenialStatus,
    ExchangeType,
    IssuerDenialStats,
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
