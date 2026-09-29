from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import Account, Claim, Denial, DenialStatus, User, UserRole


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
