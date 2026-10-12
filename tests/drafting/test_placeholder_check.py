import re
from pathlib import Path

import pytest

import src.drafting.placeholder_check
from src.drafting.placeholder_check import find_placeholder_paragraphs
from src.drafting.schemas import AppealDraft, DraftCitation, DraftParagraph, ParagraphKind

# What the check must never do: call a model, read the database, the settings or the clock.
FORBIDDEN_IN_THE_CHECK = re.compile(
    r"src\.llm|src\.config|sqlalchemy|Session|\.today\(|\.now\(|\butcnow\b|\bimport time\b"
)
# Made-up letter text.
PLAIN_TEXT = "I am writing to appeal the denial of this made-up claim."


def _draft(*texts: str) -> AppealDraft:
    return AppealDraft(
        paragraphs=tuple(DraftParagraph(kind=ParagraphKind.OTHER, text=text) for text in texts)
    )


@pytest.mark.parametrize(
    "text",
    [
        "[Your Name]",
        "Sincerely, [Your Name]",
        "Please write to me at [Your Address] before [Date].",
        "My member id is [] and my claim is made up.",
        "The name goes [here\non the next line].",
        "A blank [ ] to tick.",
        "An opening [ bracket [twice] is still found.",
    ],
)
def test_text_in_square_brackets_is_found(text: str) -> None:
    assert find_placeholder_paragraphs(_draft(text)) == (0,)


@pytest.mark.parametrize(
    "text",
    [
        PLAIN_TEXT,
        "The amount (130.10) and the code {99213} are in other brackets.",
        "A lone opening [ bracket.",
        "A lone closing ] bracket.",
        "A closing ] bracket before an opening [ one.",
    ],
)
def test_text_without_a_pair_of_square_brackets_passes(text: str) -> None:
    assert find_placeholder_paragraphs(_draft(text)) == ()


def test_every_paragraph_with_a_placeholder_is_named_by_its_position_from_0() -> None:
    draft = _draft("Dear [Payer],", PLAIN_TEXT, PLAIN_TEXT, "Sincerely, [Your Name]")

    assert find_placeholder_paragraphs(draft) == (0, 3)


def test_a_quote_is_not_checked_only_the_paragraph_text() -> None:
    draft = AppealDraft(
        paragraphs=(
            DraftParagraph(
                kind=ParagraphKind.POLICY,
                text="The made-up rule covers the widget.",
                citations=(DraftCitation(label="E1", quote="widgets [see section 2] are covered"),),
            ),
        )
    )

    assert find_placeholder_paragraphs(draft) == ()


def test_the_same_draft_always_gives_the_same_answer() -> None:
    draft = _draft(PLAIN_TEXT, "Sincerely, [Your Name]")

    assert find_placeholder_paragraphs(draft) == find_placeholder_paragraphs(draft) == (1,)


def test_the_check_makes_no_model_call_and_reads_no_database_settings_or_clock() -> None:
    source = Path(src.drafting.placeholder_check.__file__).read_text(encoding="utf-8")

    assert FORBIDDEN_IN_THE_CHECK.search(source) is None
