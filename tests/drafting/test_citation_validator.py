import re
from pathlib import Path

import pytest

import src.drafting.citation_validator
from src.db.models import EvidenceSource
from src.drafting.citation_validator import (
    MIN_QUOTE_CHARACTERS,
    CitationCheck,
    CitationProblem,
    CitationProblemKind,
    validate_citations,
)
from src.drafting.schemas import AppealDraft, DraftCitation, DraftParagraph, ParagraphKind
from src.rag.retrieval import EvidenceMatch

# Made-up policy text. No real determination says this.
FIRST_TEXT = "Blue widgets are covered when a doctor orders them.\nGreen widgets are never covered."
SECOND_TEXT = "A  home visit is covered once in every calendar month."

# What the validator must never do: call a model, read the database, the settings or the clock.
FORBIDDEN_IN_THE_VALIDATOR = re.compile(
    r"src\.llm|src\.config|sqlalchemy|Session|\.today\(|\.now\(|\butcnow\b|\bimport time\b"
)


def _match(position: int, text: str) -> EvidenceMatch:
    return EvidenceMatch(
        source=EvidenceSource.CMS_NCD,
        source_document_id=str(900 + position),
        chunk_index=position,
        text_sha256=f"{position:064x}",
        section_number=f"999.{position}",
        document_title="A made-up determination",
        section_title=None,
        text=text,
        similarity=0.5,
    )


MATCHES = [_match(0, FIRST_TEXT), _match(1, SECOND_TEXT)]


def _other(text: str = "I am writing to appeal this denial.") -> DraftParagraph:
    return DraftParagraph(kind=ParagraphKind.OTHER, text=text)


def _policy(*citations: tuple[str, str]) -> DraftParagraph:
    return DraftParagraph(
        kind=ParagraphKind.POLICY,
        text="The policy covers this service.",
        citations=tuple(DraftCitation(label=label, quote=quote) for label, quote in citations),
    )


def _draft(*paragraphs: DraftParagraph) -> AppealDraft:
    return AppealDraft(paragraphs=paragraphs)


def _problem(kind: CitationProblemKind, paragraph_index: int, label: str | None) -> CitationProblem:
    return CitationProblem(kind=kind, paragraph_index=paragraph_index, label=label)


def test_the_minimum_quote_length_is_20_characters() -> None:
    assert MIN_QUOTE_CHARACTERS == 20


def test_the_problem_kinds_are_the_four_named_ones() -> None:
    assert [kind.value for kind in CitationProblemKind] == [
        "policy_without_citation",
        "unknown_label",
        "quote_too_short",
        "quote_not_in_evidence",
    ]


def test_a_draft_with_no_policy_paragraph_passes() -> None:
    check = validate_citations(_draft(_other(), _other("Thank you.")), MATCHES)

    assert check == CitationCheck(problems=())
    assert check.passed is True


def test_a_draft_with_no_policy_paragraph_passes_with_no_evidence_at_all() -> None:
    assert validate_citations(_draft(_other()), []).passed is True


def test_a_draft_whose_policy_paragraphs_are_all_cited_passes() -> None:
    draft = _draft(
        _other(),
        _policy(("E1", "Blue widgets are covered when a doctor orders them.")),
        _policy(
            ("E2", "covered once in every calendar month"),
            ("E1", "Green widgets are never covered."),
        ),
    )

    assert validate_citations(draft, MATCHES).passed is True


def test_a_policy_paragraph_with_no_citation_is_blocked() -> None:
    check = validate_citations(_draft(_other(), _policy()), MATCHES)

    assert check.passed is False
    assert check.problems == (_problem(CitationProblemKind.POLICY_WITHOUT_CITATION, 1, None),)


def test_a_label_that_is_not_among_the_matches_is_blocked() -> None:
    draft = _draft(_policy(("E3", "Blue widgets are covered when a doctor orders them.")))

    check = validate_citations(draft, MATCHES)

    assert check.problems == (_problem(CitationProblemKind.UNKNOWN_LABEL, 0, "E3"),)


def test_every_label_is_unknown_when_nothing_was_retrieved() -> None:
    draft = _draft(_policy(("E1", "Blue widgets are covered when a doctor orders them.")))

    check = validate_citations(draft, [])

    assert check.problems == (_problem(CitationProblemKind.UNKNOWN_LABEL, 0, "E1"),)


def test_a_quote_the_cited_text_does_not_hold_is_blocked() -> None:
    # One word changed: "never" became "always".
    draft = _draft(_policy(("E1", "Green widgets are always covered.")))

    check = validate_citations(draft, MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, 0, "E1"),)


def test_a_quote_from_another_match_than_the_one_cited_is_blocked() -> None:
    # The words are in E2's text, the citation names E1.
    draft = _draft(_policy(("E1", "covered once in every calendar month")))

    check = validate_citations(draft, MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, 0, "E1"),)


@pytest.mark.parametrize(
    "quote",
    [
        # The text has a line break here, the quote a space.
        "doctor orders them. Green widgets are never covered.",
        "doctor  orders\tthem.\n\nGreen widgets",
        "  Blue widgets are covered  ",
    ],
)
def test_a_quote_that_differs_only_in_whitespace_passes(quote: str) -> None:
    assert validate_citations(_draft(_policy(("E1", quote))), MATCHES).passed is True


def test_two_spaces_in_the_evidence_text_count_as_one() -> None:
    draft = _draft(_policy(("E2", "A home visit is covered once")))

    assert validate_citations(draft, MATCHES).passed is True


@pytest.mark.parametrize(
    "quote",
    [
        "blue widgets are covered when a doctor orders them.",  # lower-case first letter
        "Bluewidgets are covered when a doctor orders them.",  # a space removed
        "Blue widgets are covered when a doctor orders them!",  # another full stop
    ],
)
def test_a_quote_that_differs_in_more_than_whitespace_is_blocked(quote: str) -> None:
    check = validate_citations(_draft(_policy(("E1", quote))), MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, 0, "E1"),)


def test_a_quote_under_the_minimum_length_is_blocked_although_the_text_holds_it() -> None:
    quote = "Blue widgets are co"
    assert len(quote) == MIN_QUOTE_CHARACTERS - 1

    check = validate_citations(_draft(_policy(("E1", quote))), MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_TOO_SHORT, 0, "E1"),)


def test_a_quote_of_exactly_the_minimum_length_passes() -> None:
    quote = "Blue widgets are cov"
    assert len(quote) == MIN_QUOTE_CHARACTERS

    assert validate_citations(_draft(_policy(("E1", quote))), MATCHES).passed is True


def test_spaces_do_not_make_a_short_quote_long_enough() -> None:
    quote = "Blue      widgets      are"

    check = validate_citations(_draft(_policy(("E1", quote))), MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_TOO_SHORT, 0, "E1"),)


def test_a_short_quote_with_an_unknown_label_gives_both_problems() -> None:
    check = validate_citations(_draft(_policy(("E9", "is covered"))), MATCHES)

    assert check.problems == (
        _problem(CitationProblemKind.QUOTE_TOO_SHORT, 0, "E9"),
        _problem(CitationProblemKind.UNKNOWN_LABEL, 0, "E9"),
    )


def test_one_bad_citation_blocks_a_paragraph_that_also_has_a_good_one() -> None:
    draft = _draft(
        _policy(
            ("E1", "Green widgets are never covered."),
            ("E2", "Green widgets are never covered."),
        )
    )

    check = validate_citations(draft, MATCHES)

    assert check.problems == (_problem(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, 0, "E2"),)


def test_every_problem_is_returned_in_paragraph_order() -> None:
    draft = _draft(
        _policy(),
        _other(),
        _policy(("E1", "Green widgets are always covered."), ("E4", "is covered")),
        _policy(("E2", "A home visit is covered once")),
        _policy(),
    )

    check = validate_citations(draft, MATCHES)

    assert check.passed is False
    assert check.problems == (
        _problem(CitationProblemKind.POLICY_WITHOUT_CITATION, 0, None),
        _problem(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, 2, "E1"),
        _problem(CitationProblemKind.QUOTE_TOO_SHORT, 2, "E4"),
        _problem(CitationProblemKind.UNKNOWN_LABEL, 2, "E4"),
        _problem(CitationProblemKind.POLICY_WITHOUT_CITATION, 4, None),
    )


def test_the_labels_follow_the_order_of_the_matches() -> None:
    draft = _draft(_policy(("E1", "covered once in every calendar month")))

    assert validate_citations(draft, MATCHES).passed is False
    assert validate_citations(draft, list(reversed(MATCHES))).passed is True


def test_the_same_draft_and_matches_always_give_the_same_answer() -> None:
    draft = _draft(_policy(("E1", "Green widgets are always covered.")), _policy())

    assert validate_citations(draft, MATCHES) == validate_citations(draft, tuple(MATCHES))


def test_a_problem_shows_no_quote_and_no_evidence_text() -> None:
    draft = _draft(_policy(("E1", "Green widgets are always covered.")))

    shown = repr(validate_citations(draft, MATCHES))

    assert "E1" in shown
    assert "widgets" not in shown


def test_the_validator_reads_no_model_no_database_no_settings_and_no_clock() -> None:
    source = Path(src.drafting.citation_validator.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert FORBIDDEN_IN_THE_VALIDATOR.search(code) is None
