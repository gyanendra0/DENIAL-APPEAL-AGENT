import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import src.rules
from src.rules.verdict import RULES_VERSION, RuleInputs, RuleOutcome, RuleReason, RuleVerdict

INPUTS: dict[str, Any] = {
    "letter_date": date(2009, 7, 15),
    "appeal_deadline": date(2010, 1, 11),
    "total_allowed_charge_amount": Decimal("90.00"),
}

# What `src/rules` must never do: read an extraction result, the settings or the clock.
FORBIDDEN_IN_RULES = re.compile(
    r"src\.extraction|src\.config|\.today\(|\.now\(|\butcnow\b|\bimport time\b|\bfrom time\b"
)


def test_the_rules_version_is_v1() -> None:
    assert RULES_VERSION == "v1"


def test_the_outcomes_are_pass_must_review_and_hard_fail() -> None:
    assert [outcome.value for outcome in RuleOutcome] == ["pass", "must_review", "hard_fail"]


def test_the_reasons_are_listed_in_their_fixed_order() -> None:
    assert [reason.value for reason in RuleReason] == [
        "deadline_not_after_letter_date",
        "deadline_missing",
        "deadline_passed",
        "amount_missing",
        "amount_zero",
        "amount_below_floor",
    ]


def test_the_inputs_are_the_three_letter_values_and_nothing_else() -> None:
    # No confidence score, no claim id.
    assert set(RuleInputs.model_fields) == {
        "letter_date",
        "appeal_deadline",
        "total_allowed_charge_amount",
    }


def test_every_input_may_be_missing() -> None:
    inputs = RuleInputs(letter_date=None, appeal_deadline=None, total_allowed_charge_amount=None)

    assert inputs.letter_date is None
    assert inputs.appeal_deadline is None
    assert inputs.total_allowed_charge_amount is None


@pytest.mark.parametrize("field", sorted(INPUTS))
def test_a_missing_input_must_be_said_and_not_left_out(field: str) -> None:
    values = {name: value for name, value in INPUTS.items() if name != field}

    with pytest.raises(ValidationError, match=field):
        RuleInputs(**values)


def test_the_amount_stays_an_exact_decimal() -> None:
    amount = RuleInputs(**INPUTS).total_allowed_charge_amount

    assert isinstance(amount, Decimal)
    assert amount == Decimal("90.00")


def test_an_amount_of_zero_is_accepted() -> None:
    inputs = RuleInputs(**{**INPUTS, "total_allowed_charge_amount": Decimal("0.00")})

    assert inputs.total_allowed_charge_amount == Decimal(0)


@pytest.mark.parametrize("amount", [90.0, 90, "90.00"], ids=["float", "int", "text"])
def test_an_amount_that_is_not_a_decimal_is_rejected(amount: object) -> None:
    with pytest.raises(ValidationError, match="total_allowed_charge_amount"):
        RuleInputs(**{**INPUTS, "total_allowed_charge_amount": amount})


def test_a_negative_amount_is_rejected() -> None:
    with pytest.raises(ValidationError, match="total_allowed_charge_amount"):
        RuleInputs(**{**INPUTS, "total_allowed_charge_amount": Decimal("-0.01")})


def test_the_inputs_cannot_be_changed() -> None:
    inputs = RuleInputs(**INPUTS)

    with pytest.raises(ValidationError):
        inputs.appeal_deadline = None


def test_the_inputs_refuse_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        RuleInputs(**{**INPUTS, "confidence": 0.9})


def test_a_verdict_carries_the_rules_version() -> None:
    verdict = RuleVerdict(outcome=RuleOutcome.PASS, reasons=())

    assert verdict.rules_version == "v1"


def test_a_verdict_keeps_every_reason_in_the_order_given() -> None:
    reasons = (RuleReason.DEADLINE_PASSED, RuleReason.AMOUNT_ZERO)

    verdict = RuleVerdict(outcome=RuleOutcome.HARD_FAIL, reasons=reasons)

    assert verdict.reasons == reasons


def test_a_verdict_refuses_an_outcome_that_is_not_one_of_the_three() -> None:
    with pytest.raises(ValidationError, match="outcome"):
        RuleVerdict(outcome="maybe", reasons=())  # type: ignore[arg-type]


def test_a_verdict_refuses_a_reason_that_is_free_text() -> None:
    with pytest.raises(ValidationError, match="reasons"):
        RuleVerdict(outcome=RuleOutcome.MUST_REVIEW, reasons=("looks odd",))  # type: ignore[arg-type]


def test_a_verdict_cannot_be_changed() -> None:
    verdict = RuleVerdict(outcome=RuleOutcome.PASS, reasons=())

    with pytest.raises(ValidationError):
        verdict.outcome = RuleOutcome.HARD_FAIL


def test_a_verdict_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="note"):
        RuleVerdict(outcome=RuleOutcome.PASS, reasons=(), note="x")  # type: ignore[call-arg]


def test_the_rules_package_reads_no_extraction_no_settings_and_no_clock() -> None:
    sources = sorted(Path(src.rules.__file__).parent.glob("*.py"))
    assert len(sources) >= 3  # the package file, the types and at least one rule

    for source in sources:
        found = FORBIDDEN_IN_RULES.search(source.read_text(encoding="utf-8"))
        assert found is None, f"{source.name} uses {found.group(0) if found else ''}"
