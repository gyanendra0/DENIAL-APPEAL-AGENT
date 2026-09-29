from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from src.db.models import Account, Claim
from src.db.query import for_account


def test_for_account_returns_only_that_accounts_rows(session: Session) -> None:
    mine, theirs = Account(name="mine"), Account(name="theirs")
    session.add_all([mine, theirs])
    session.flush()
    for account, number in [(mine, "M-1"), (mine, "M-2"), (theirs, "T-1")]:
        session.add(
            Claim(
                account_id=account.id,
                claim_number=number,
                payer="Acme Health",
                billed_amount=Decimal("10.00"),
                service_date=date(2026, 1, 1),
            )
        )
    session.flush()

    numbers = {c.claim_number for c in session.scalars(for_account(Claim, mine.id))}

    assert numbers == {"M-1", "M-2"}
