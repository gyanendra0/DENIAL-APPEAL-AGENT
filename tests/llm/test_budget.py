from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmCall, LlmProvider
from src.db.session import create_session_factory, session_scope
from src.llm.budget import (
    BudgetDecision,
    LlmCallRecord,
    call_cost,
    check_budget,
    month_to_date_spend,
    record_llm_call,
)

# A month far from today, so a row another test stored with the real clock never counts.
NOW = datetime(2031, 3, 15, 12, 0, tzinfo=UTC)
CAP = Decimal("3.00")
ONE_MILLIONTH = Decimal("0.000001")
# Marks the rows the committing tests store, so they can be deleted afterwards.
RECORD_PURPOSE = "budget-guard-test"


def _stored_call(session: Session, cost_usd: str, created_at: datetime) -> None:
    session.add(
        LlmCall(
            provider=LlmProvider.PRIMARY,
            model_name="example-small-model",
            prompt_version="extraction-v1",
            purpose="extraction",
            input_tokens=520,
            output_tokens=75,
            cost_usd=Decimal(cost_usd),
            created_at=created_at,
        )
    )
    session.flush()


def _record(**overrides: Any) -> LlmCallRecord:
    fields: dict[str, Any] = {
        "provider": LlmProvider.PRIMARY,
        "model_name": "example-small-model",
        "prompt_version": "extraction-v1",
        "purpose": RECORD_PURPOSE,
        "input_tokens": 520,
        "output_tokens": 75,
        "cost_usd": Decimal("0.000123"),
    }
    return LlmCallRecord(**(fields | overrides))


def test_cost_is_tokens_times_the_price_per_million_tokens() -> None:
    cost = call_cost(1000, 200, Decimal("0.15"), Decimal("0.60"))

    # 1000 x 0.15 / 1,000,000 + 200 x 0.60 / 1,000,000
    assert cost == Decimal("0.000270")
    assert isinstance(cost, Decimal)


def test_cost_is_rounded_up_to_a_millionth_of_a_dollar() -> None:
    # 1 token at $0.15 per million is $0.00000015: it must not be recorded as free.
    assert call_cost(1, 0, Decimal("0.15"), Decimal("0.60")) == ONE_MILLIONTH
    # 7 tokens at $0.15 per million is $0.00000105.
    assert call_cost(7, 0, Decimal("0.15"), Decimal("0.60")) == Decimal("0.000002")


def test_cost_that_is_already_a_whole_millionth_is_not_rounded_further() -> None:
    assert call_cost(1_000_000, 1_000_000, Decimal("0.15"), Decimal("0.60")) == Decimal("0.75")


def test_cost_is_zero_when_both_prices_are_zero() -> None:
    assert call_cost(5000, 900, Decimal("0"), Decimal("0")) == Decimal("0")


def test_cost_is_zero_for_a_call_with_no_tokens() -> None:
    assert call_cost(0, 0, Decimal("0.15"), Decimal("0.60")) == Decimal("0")


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(-1, 0), (0, -1)])
def test_cost_rejects_a_negative_token_count(input_tokens: int, output_tokens: int) -> None:
    with pytest.raises(ValueError, match="token counts"):
        call_cost(input_tokens, output_tokens, Decimal("0.15"), Decimal("0.60"))


@pytest.mark.parametrize(("input_price", "output_price"), [("-0.15", "0.60"), ("0.15", "-0.60")])
def test_cost_rejects_a_negative_price(input_price: str, output_price: str) -> None:
    with pytest.raises(ValueError, match="prices"):
        call_cost(10, 10, Decimal(input_price), Decimal(output_price))


def test_spend_is_zero_in_a_month_with_no_calls(session: Session) -> None:
    spent = month_to_date_spend(session, NOW)

    assert spent == Decimal("0")
    assert isinstance(spent, Decimal)


def test_spend_adds_up_the_calls_of_the_month_exactly(session: Session) -> None:
    _stored_call(session, "0.100001", datetime(2031, 3, 2, 9, 0, tzinfo=UTC))
    _stored_call(session, "0.200002", datetime(2031, 3, 14, 18, 30, tzinfo=UTC))
    _stored_call(session, "0.000003", datetime(2031, 3, 15, 11, 59, tzinfo=UTC))

    assert month_to_date_spend(session, NOW) == Decimal("0.300006")


def test_spend_leaves_out_a_call_of_the_previous_month(session: Session) -> None:
    _stored_call(session, "2.50", datetime(2031, 2, 28, 23, 59, 59, tzinfo=UTC))
    _stored_call(session, "0.25", datetime(2031, 3, 10, 8, 0, tzinfo=UTC))

    assert month_to_date_spend(session, NOW) == Decimal("0.25")


def test_spend_counts_a_call_at_the_first_instant_of_the_month(session: Session) -> None:
    _stored_call(session, "0.40", datetime(2031, 3, 1, 0, 0, tzinfo=UTC))

    assert month_to_date_spend(session, NOW) == Decimal("0.40")


def test_spend_leaves_out_a_call_at_the_first_instant_of_the_next_month(session: Session) -> None:
    _stored_call(session, "0.40", datetime(2031, 4, 1, 0, 0, tzinfo=UTC))

    assert month_to_date_spend(session, NOW) == Decimal("0")


def test_spend_in_december_ends_at_the_new_year(session: Session) -> None:
    _stored_call(session, "0.10", datetime(2031, 12, 31, 23, 59, 59, tzinfo=UTC))
    _stored_call(session, "0.70", datetime(2032, 1, 1, 0, 0, tzinfo=UTC))

    assert month_to_date_spend(session, datetime(2031, 12, 20, tzinfo=UTC)) == Decimal("0.10")
    assert month_to_date_spend(session, datetime(2032, 1, 5, tzinfo=UTC)) == Decimal("0.70")


def test_month_is_the_utc_month_whatever_the_time_zone_of_now(session: Session) -> None:
    _stored_call(session, "0.30", datetime(2031, 2, 20, 12, 0, tzinfo=UTC))
    _stored_call(session, "0.90", datetime(2031, 3, 5, 12, 0, tzinfo=UTC))
    # 00:30 on 1 March two hours east of UTC is still 22:30 on 28 February in UTC.
    late_february = datetime(2031, 3, 1, 0, 30, tzinfo=timezone(timedelta(hours=2)))

    assert month_to_date_spend(session, late_february) == Decimal("0.30")


def test_spend_rejects_a_now_without_a_time_zone(session: Session) -> None:
    with pytest.raises(ValueError, match="time zone"):
        month_to_date_spend(session, datetime(2031, 3, 15, 12, 0))


def test_allows_a_call_under_the_cap(session: Session) -> None:
    _stored_call(session, "1.00", datetime(2031, 3, 3, tzinfo=UTC))

    decision = check_budget(session, cap_usd=CAP, highest_cost_usd=Decimal("0.01"), now=NOW)

    assert decision == BudgetDecision(
        allowed=True,
        spent_usd=Decimal("1.00"),
        highest_cost_usd=Decimal("0.01"),
        cap_usd=CAP,
    )


def test_allows_a_call_that_lands_exactly_on_the_cap(session: Session) -> None:
    _stored_call(session, "2.99", datetime(2031, 3, 3, tzinfo=UTC))

    decision = check_budget(session, cap_usd=CAP, highest_cost_usd=Decimal("0.01"), now=NOW)

    assert decision.allowed is True


def test_refuses_a_call_one_millionth_of_a_dollar_over_the_cap(session: Session) -> None:
    _stored_call(session, "2.99", datetime(2031, 3, 3, tzinfo=UTC))

    decision = check_budget(
        session, cap_usd=CAP, highest_cost_usd=Decimal("0.01") + ONE_MILLIONTH, now=NOW
    )

    assert decision.allowed is False
    assert decision.spent_usd == Decimal("2.99")


def test_refuses_every_paid_call_once_the_cap_is_spent(session: Session) -> None:
    _stored_call(session, "3.00", datetime(2031, 3, 3, tzinfo=UTC))

    paid = check_budget(session, cap_usd=CAP, highest_cost_usd=ONE_MILLIONTH, now=NOW)
    free = check_budget(session, cap_usd=CAP, highest_cost_usd=Decimal("0"), now=NOW)

    assert paid.allowed is False
    assert free.allowed is True


def test_a_new_month_starts_from_zero(session: Session) -> None:
    _stored_call(session, "3.00", datetime(2031, 3, 31, 23, 0, tzinfo=UTC))
    first_of_april = datetime(2031, 4, 1, 0, 0, tzinfo=UTC)

    in_march = check_budget(session, cap_usd=CAP, highest_cost_usd=Decimal("0.01"), now=NOW)
    in_april = check_budget(
        session, cap_usd=CAP, highest_cost_usd=Decimal("0.01"), now=first_of_april
    )

    assert in_march.allowed is False
    assert in_april.allowed is True
    assert in_april.spent_usd == Decimal("0")


def test_a_cap_of_zero_allows_only_a_free_call(session: Session) -> None:
    zero = Decimal("0")

    free = check_budget(session, cap_usd=zero, highest_cost_usd=zero, now=NOW)
    paid = check_budget(session, cap_usd=zero, highest_cost_usd=ONE_MILLIONTH, now=NOW)

    assert free.allowed is True
    assert paid.allowed is False


def test_check_rejects_a_negative_highest_cost(session: Session) -> None:
    with pytest.raises(ValidationError, match="highest_cost_usd"):
        check_budget(session, cap_usd=CAP, highest_cost_usd=Decimal("-0.01"), now=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_name", ""),
        ("model_name", "m" * 101),
        ("prompt_version", ""),
        ("prompt_version", "p" * 41),
        ("purpose", ""),
        ("purpose", "p" * 41),
        ("input_tokens", -1),
        ("output_tokens", -1),
        ("input_tokens", 2**31),
        ("cost_usd", Decimal("-0.000001")),
        ("cost_usd", Decimal("0.0000001")),
        ("cost_usd", Decimal("1000000")),
        ("provider", "openai"),
    ],
)
def test_record_rejects_a_value_the_table_cannot_hold(field: str, value: Any) -> None:
    with pytest.raises(ValidationError, match=field):
        _record(**{field: value})


def test_record_rejects_a_field_the_table_does_not_have() -> None:
    with pytest.raises(ValidationError, match="prompt_text"):
        _record(prompt_text="never stored")


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """A factory whose sessions really commit; the rows these tests store are deleted after."""
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        with session_scope(factory) as cleanup:
            cleanup.execute(delete(LlmCall).where(LlmCall.purpose == RECORD_PURPOSE))


def _recorded_calls(factory: sessionmaker[Session]) -> list[LlmCall]:
    with session_scope(factory) as reader:
        return list(reader.scalars(select(LlmCall).where(LlmCall.purpose == RECORD_PURPOSE)))


def test_records_a_call_with_every_field(session_factory: sessionmaker[Session]) -> None:
    record_llm_call(
        session_factory,
        _record(provider=LlmProvider.FALLBACK, model_name="example-open-model"),
    )

    (stored,) = _recorded_calls(session_factory)
    assert stored.provider is LlmProvider.FALLBACK
    assert stored.model_name == "example-open-model"
    assert stored.prompt_version == "extraction-v1"
    assert stored.purpose == RECORD_PURPOSE
    assert stored.input_tokens == 520
    assert stored.output_tokens == 75
    assert stored.cost_usd == Decimal("0.000123")
    assert stored.created_at is not None


def test_a_recorded_call_counts_towards_this_months_spend(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_scope(session_factory) as reader:
        before = month_to_date_spend(reader, now)

    record_llm_call(session_factory, _record(cost_usd=Decimal("0.004000")))

    with session_scope(session_factory) as reader:
        after = month_to_date_spend(reader, now)
    assert after - before == Decimal("0.004")


def test_a_recorded_call_stays_when_the_callers_work_rolls_back(
    session_factory: sessionmaker[Session],
) -> None:
    with (
        pytest.raises(RuntimeError, match="extraction failed"),
        session_scope(session_factory) as caller,
    ):
        caller.execute(select(LlmCall).limit(1))
        record_llm_call(session_factory, _record())
        raise RuntimeError("extraction failed")

    assert len(_recorded_calls(session_factory)) == 1
