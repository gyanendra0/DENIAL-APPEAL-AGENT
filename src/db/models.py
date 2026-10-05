"""Core tables (accounts, users, claims, denials) and public reference tables."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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


class IssuerDenialStats(TimestampMixin, Base):
    """Issuer-level claim and appeal counts from the CMS Transparency in Coverage PUF.

    Public reference table shared by all accounts, so it has no `account_id`.
    A count or percent is NULL when the source suppressed it or it did not apply.
    """

    __tablename__ = "issuer_denial_stats"
    __table_args__ = (
        UniqueConstraint("issuer_id", "plan_year", name="uq_issuer_denial_stats_issuer_year"),
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
