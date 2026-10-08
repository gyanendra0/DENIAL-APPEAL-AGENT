"""The monthly budget guard of the LLM gateway.

It answers "may this call be made?" from the spend stored in `llm_calls`, and it records
what an answered call cost. It calls no model. `llm_calls` is an operational table (no
`account_id`): the cap covers the whole project. The month is the calendar month in UTC.

Not handled here: two processes checking the budget at the same moment.
"""

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmCall, LlmProvider
from src.db.session import session_scope

TOKENS_PER_PRICE_UNIT = 1_000_000
# The smallest amount `llm_calls.cost_usd` (`Numeric(12, 6)`) can hold.
COST_STEP_USD = Decimal("0.000001")
MAX_TOKEN_COUNT = 2**31 - 1


class BudgetDecision(BaseModel):
    """Whether a call fits the monthly cap, with the numbers the answer came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    spent_usd: Decimal = Field(ge=0)
    highest_cost_usd: Decimal = Field(ge=0)
    cap_usd: Decimal = Field(ge=0)


class LlmCallRecord(BaseModel):
    """One answered model call, shaped like the `llm_calls` table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: LlmProvider
    model_name: str = Field(min_length=1, max_length=100)
    prompt_version: str = Field(min_length=1, max_length=40)
    purpose: str = Field(min_length=1, max_length=40)
    input_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    output_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    cost_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=6)


def call_cost(
    input_tokens: int,
    output_tokens: int,
    input_usd_per_mtok: Decimal,
    output_usd_per_mtok: Decimal,
) -> Decimal:
    """Return the cost of a call in US dollars, from token counts and prices per million tokens.

    The result is rounded up to a millionth of a dollar, so the recorded spend is never below
    the real spend. With a call's highest possible token counts it gives the highest possible
    cost.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be 0 or more")
    if input_usd_per_mtok < 0 or output_usd_per_mtok < 0:
        raise ValueError("prices must be 0 or more")
    exact = (
        input_tokens * input_usd_per_mtok + output_tokens * output_usd_per_mtok
    ) / TOKENS_PER_PRICE_UNIT
    return exact.quantize(COST_STEP_USD, rounding=ROUND_CEILING)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return the first instant of `now`'s UTC month and of the month after it."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must carry a time zone")
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


def month_to_date_spend(session: Session, now: datetime) -> Decimal:
    """Return the total `cost_usd` of the calls recorded in the UTC calendar month of `now`."""
    start, end = _month_bounds(now)
    total = session.scalar(
        select(func.sum(LlmCall.cost_usd)).where(
            LlmCall.created_at >= start, LlmCall.created_at < end
        )
    )
    return Decimal("0") if total is None else total


def check_budget(
    session: Session, *, cap_usd: Decimal, highest_cost_usd: Decimal, now: datetime
) -> BudgetDecision:
    """Decide whether a call that costs at most `highest_cost_usd` fits this month's cap.

    The call is allowed when the month's spend plus its highest possible cost is at or below
    the cap. A refused call must not be made: the caller falls back or refuses.
    """
    spent = month_to_date_spend(session, now)
    return BudgetDecision(
        allowed=spent + highest_cost_usd <= cap_usd,
        spent_usd=spent,
        highest_cost_usd=highest_cost_usd,
        cap_usd=cap_usd,
    )


def record_llm_call(session_factory: sessionmaker[Session], record: LlmCallRecord) -> None:
    """Store one answered call in `llm_calls`, committed in its own short transaction.

    It takes a session factory, not the caller's session: when the caller's work fails and
    rolls back, the money was still spent and the row must stay.
    """
    with session_scope(session_factory) as session:
        session.add(LlmCall(**record.model_dump()))
