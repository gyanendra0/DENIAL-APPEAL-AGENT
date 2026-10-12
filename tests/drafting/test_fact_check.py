import re
from pathlib import Path
from typing import Any

import pytest

import src.drafting.fact_check
from src.drafting.fact_check import FactProblem, FactProblemKind, find_fact_problems
from src.drafting.schemas import AppealDraft, DraftCitation, DraftParagraph, ParagraphKind
from src.extraction.schemas import DenialLetterExtraction

# What the check must never do: call a model, read the database, the settings or the clock.
FORBIDDEN_IN_THE_CHECK = re.compile(
    r"src\.llm|src\.config|sqlalchemy|Session|\.today\(|\.now\(|\butcnow\b|\bimport time\b"
)
AMOUNT = FactProblemKind.AMOUNT_NOT_A_DENIED_LINE
OTHER = FactProblemKind.OTHER_AS_REASON
# Made-up letter text.
PLAIN_TEXT = "I am writing to appeal the denial of this made-up claim."
# A made-up denial letter: two denied lines, and claim totals that are no line's amount.
DENIED_LINES: list[dict[str, Any]] = [
    {
        "line_number": 2,
        "hcpcs_code": "99213",
        "allowed_charge_amount": "70.00",
        "payment_amount": "0.00",
        "reason_category": "other",
    },
    {
        "line_number": 3,
        "hcpcs_code": "82044",
        "allowed_charge_amount": "1250.50",
        "payment_amount": "12.25",
        "reason_category": "noncovered",
    },
]
LETTER_VALUES: dict[str, Any] = {
    "claim_number": "123456789012345",
    "service_from_date": "2008-03-01",
    "service_thru_date": "2008-03-02",
    "diagnosis_codes": ["4011"],
    "denied_lines": DENIED_LINES,
    "total_allowed_charge_amount": "1900.75",
    "total_payment_amount": "480.20",
    "denial_reason_category": "other",
    "patient_name": "Zebulon Quillfeather",
    "member_id": "MBR-000001",
    "provider_name": "Dr. Made Up",
    "payer_name": "Made-Up Payer",
    "reference_number": "REF-000001",
    "letter_date": "2008-03-20",
    "appeal_deadline": "2008-09-16",
}


def _letter(**changes: Any) -> DenialLetterExtraction:
    values = {**LETTER_VALUES, **changes}
    return DenialLetterExtraction.model_validate(
        {name: {"value": value, "confidence": 0.5} for name, value in values.items()}
    )


def _draft(*texts: str) -> AppealDraft:
    return AppealDraft(
        paragraphs=tuple(DraftParagraph(kind=ParagraphKind.OTHER, text=text) for text in texts)
    )


def _kinds(text: str, letter: DenialLetterExtraction | None = None) -> list[FactProblemKind]:
    return [problem.kind for problem in find_fact_problems(_draft(text), letter or _letter())]


def test_a_draft_with_no_dollar_amount_and_no_reason_word_passes() -> None:
    assert find_fact_problems(_draft(PLAIN_TEXT, PLAIN_TEXT), _letter()) == ()


@pytest.mark.parametrize(
    "text",
    [
        "Line 2 was allowed at $70.00 and paid $0.00.",
        "Line 2 was allowed at $70 and paid $0.",
        "Line 2 was allowed at $70.0.",
        "Line 2 was allowed at $ 70.00.",
        "Line 3 was allowed at $1250.50, and $12.25 was paid.",
        "Line 3 was allowed at $1,250.50.",
        "Line 3 was allowed at $1,250.5 ($12.25 paid).",
    ],
)
def test_the_allowed_or_paid_amount_of_a_denied_line_passes_however_it_is_written(
    text: str,
) -> None:
    assert _kinds(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "I appeal the amount of $1900.75.",
        "I appeal the amount of $1,900.75.",
        "The plan paid $480.20 in all.",
        "Line 2 was allowed at $70.01.",
        "Line 2 was allowed at $700.",
        "The two lines come to $1320.50.",
        "Line 2 was allowed at $70.00, of a total charge of $1,900.75.",
    ],
)
def test_a_dollar_amount_that_no_denied_line_has_is_refused(text: str) -> None:
    assert _kinds(text) == [AMOUNT]


def test_a_claim_total_passes_when_it_is_also_the_amount_of_a_denied_line() -> None:
    letter = _letter(total_allowed_charge_amount="70.00")

    assert _kinds("The total charge is $70.00.", letter) == []


@pytest.mark.parametrize("denied_lines", [None, []])
def test_any_dollar_amount_is_refused_when_the_letter_has_no_denied_line(
    denied_lines: list[dict[str, Any]] | None,
) -> None:
    letter = _letter(denied_lines=denied_lines)

    assert _kinds("Line 2 was allowed at $70.00.", letter) == [AMOUNT]
    assert _kinds(PLAIN_TEXT, letter) == []


@pytest.mark.parametrize(
    "text",
    [
        "A charge of 1900.75 was made.",
        "A charge of 1,900.75 dollars was made.",
        "The price is USD 1900.75.",
        "A lone $ sign and a lone $, twice.",
    ],
)
def test_an_amount_without_a_dollar_sign_before_a_number_is_not_checked(text: str) -> None:
    assert _kinds(text) == []


@pytest.mark.parametrize(
    "text",
    [
        'The reason given for the denial was "other," and no more was said.',
        "The lines were denied under the category of 'other,' which names no reason.",
        "My claim was denied under the category 'other', which is not clear.",
        "The denial is marked as 'other.' Please explain it.",
        "The reason is “other” and nothing more.",
        "The reason is ‘Other’ and nothing more.",
        'The reason is "OTHER".',
        "The lines were denied under the category of other.",
        "The reason was other, with no further detail.",
        "The reason given was Other; no detail followed.",
        "The denial was categorized as other.",
        "The denial reason is classified in the letter as other",
    ],
)
def test_other_given_as_the_reason_is_refused(text: str) -> None:
    assert _kinds(text) == [OTHER]


@pytest.mark.parametrize(
    "text",
    [
        "Argue against this reason and against no other reason.",
        "The reason was other than medical necessity.",
        "The reason is other coverage held by the patient.",
        "The other plan must be billed first, and no other.",
        "Service 82044 and the other service were denied.",
        "The patients' other doctor and each other's notes agree.",
        "The letter names no reason. Other letters do.",
        "The service is a noncovered one, the letter says.",
        "The denial calls the service 'noncovered' and 'another' thing.",
    ],
)
def test_an_ordinary_use_of_the_word_other_and_the_word_noncovered_pass(text: str) -> None:
    assert _kinds(text) == []


def test_every_problem_is_named_by_its_kind_and_its_paragraph_from_0() -> None:
    draft = _draft(
        PLAIN_TEXT,
        "The reason was 'other' and the total was $1900.75.",
        "Line 2 was allowed at $70.00.",
        "The category given was other.",
        "I appeal $480.20.",
    )

    assert find_fact_problems(draft, _letter()) == (
        FactProblem(kind=AMOUNT, paragraph_index=1),
        FactProblem(kind=OTHER, paragraph_index=1),
        FactProblem(kind=OTHER, paragraph_index=3),
        FactProblem(kind=AMOUNT, paragraph_index=4),
    )


def test_a_paragraph_gives_one_problem_of_a_kind_however_often_it_has_it() -> None:
    text = "The totals $1900.75 and $480.20 were 'other' and \"other\"."

    assert _kinds(text) == [AMOUNT, OTHER]


def test_a_quote_is_not_checked_only_the_paragraph_text() -> None:
    draft = AppealDraft(
        paragraphs=(
            DraftParagraph(
                kind=ParagraphKind.POLICY,
                text="The made-up rule covers the widget.",
                citations=(
                    DraftCitation(
                        label="E1",
                        quote="a fee of $999.99 applies and the category is 'other' in the list",
                    ),
                ),
            ),
        )
    )

    assert find_fact_problems(draft, _letter()) == ()


def test_a_problem_holds_no_text_of_the_letter() -> None:
    (problem,) = find_fact_problems(_draft("Zebulon Quillfeather owes $1900.75."), _letter())

    assert set(problem.model_dump()) == {"kind", "paragraph_index"}
    assert "1900.75" not in repr(problem)
    assert "Zebulon" not in repr(problem)


def test_the_same_draft_always_gives_the_same_answer() -> None:
    draft = _draft(PLAIN_TEXT, "I appeal $480.20.")

    first = find_fact_problems(draft, _letter())

    assert first == find_fact_problems(draft, _letter())
    assert first == (FactProblem(kind=AMOUNT, paragraph_index=1),)


def test_the_check_makes_no_model_call_and_reads_no_database_settings_or_clock() -> None:
    source = Path(src.drafting.fact_check.__file__).read_text(encoding="utf-8")

    assert FORBIDDEN_IN_THE_CHECK.search(source) is None
