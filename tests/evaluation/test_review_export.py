"""Tests for the two review files. The items are built by hand: no database, nothing real."""

from src.evaluation.review_export import (
    REVIEW_FILE_NAME,
    SCORES_FILE_NAME,
    build_review_text,
    build_scores_csv,
)
from src.evaluation.review_rubric import RejectReason, Verdict
from tests.drafting.helpers import OPENING_TEXT, POLICY_TEXT
from tests.evaluation.review_helpers import (
    CITED_CHUNK_TEXT,
    CITED_SECTION_NUMBER,
    CITED_TITLE,
    citation,
    cited_letter,
    evidence,
    review_item,
    uncited_letter,
)
from tests.extraction.helpers import CLAIM_1, CLAIM_2, LETTER, document_text
from tests.rag.helpers import CHUNK_TEXT, SECTION_TITLE

SECOND_CHUNK_TEXT = f"{CHUNK_TEXT} 1-1."


def test_the_file_names_are_fixed() -> None:
    assert (REVIEW_FILE_NAME, SCORES_FILE_NAME) == ("review_drafts.md", "review_scores.csv")


def test_the_file_to_read_shows_the_draft_the_letter_document_and_the_full_cited_chunk() -> None:
    text = build_review_text([review_item(CLAIM_1, None, evidence())])

    assert f"## Draft 1 of 1: claim {CLAIM_1}" in text
    assert f"**Paragraph 1**\n\n```text\n{OPENING_TEXT}\n```" in text
    assert f"**Paragraph 2 (policy statement)**\n\n```text\n{POLICY_TEXT}\n```" in text
    assert f"Cites evidence 1, with this quote:\n\n```text\n{CITED_CHUNK_TEXT}\n```" in text
    assert (
        f"### The denial letter document\n\n```text\n{document_text(CLAIM_1, LETTER)}\n```" in text
    )
    assert (
        f"**Evidence 1: {CITED_TITLE}, section {CITED_SECTION_NUMBER} ({SECTION_TITLE})**"
        f"\n\n```text\n{CITED_CHUNK_TEXT}\n```"
    ) in text


def test_the_file_to_read_gives_the_rubric_word_for_word() -> None:
    text = build_review_text([review_item(CLAIM_1, uncited_letter())])

    assert "- `accept`: you would send the draft as it is." in text
    assert "- `accept_light_edits`: you would send it after changing the wording of at most 3" in (
        text
    )
    assert "- `reject`: anything else." in text
    for verdict in Verdict:
        assert f"- `{verdict.value}`: " in text
    for reason in RejectReason:
        assert f"- `{reason.value}`: " in text
    assert "A draft with no citation is judged by the same rubric." in text
    assert "The target is 60% of the drafts" in text


def test_every_draft_is_numbered_and_a_citation_names_the_evidence_it_points_at() -> None:
    letter = cited_letter(citation(1, text=SECOND_CHUNK_TEXT), citation())
    first = review_item(CLAIM_1, letter, evidence(1, text=SECOND_CHUNK_TEXT), evidence())
    second = review_item(CLAIM_2, uncited_letter())

    text = build_review_text([first, second])

    assert text.startswith("# Draft review\n\n2 drafted appeal letters to score.")
    assert f"## Draft 1 of 2: claim {CLAIM_1}" in text
    assert f"## Draft 2 of 2: claim {CLAIM_2}" in text
    assert text.index("Cites evidence 1, with this quote:") < text.index(
        "Cites evidence 2, with this quote:"
    )
    assert f"**Evidence 2: {CITED_TITLE}" in text
    assert text.count("This draft cites no evidence.") == 1


def test_evidence_without_a_section_title_shows_the_title_and_the_section_number() -> None:
    found = evidence().model_copy(update={"section_title": None})

    text = build_review_text([review_item(CLAIM_1, None, found)])

    assert f"**Evidence 1: {CITED_TITLE}, section {CITED_SECTION_NUMBER}**" in text


def test_text_that_holds_a_code_fence_is_still_shown_whole() -> None:
    document = "Made-up letter\n```\nwith a fence of its own\n````\nand more."

    text = build_review_text([review_item(CLAIM_1, uncited_letter(), document=document)])

    assert f"`````text\n{document}\n`````" in text


def test_the_same_items_always_give_the_same_two_files() -> None:
    items = [review_item(CLAIM_1, None, evidence()), review_item(CLAIM_2, uncited_letter())]

    assert build_review_text(items) == build_review_text(list(items))
    assert build_scores_csv(items) == build_scores_csv(list(items))


def test_the_score_file_has_one_empty_row_per_draft_with_its_fingerprint() -> None:
    first = review_item(CLAIM_1, None, evidence())
    second = review_item(CLAIM_2, uncited_letter())

    assert build_scores_csv([first, second]) == (
        "claim_id,draft_sha256,verdict,reject_reason,note\n"
        f"{CLAIM_1},{first.draft_sha256},,,\n"
        f"{CLAIM_2},{second.draft_sha256},,,\n"
    )


def test_neither_file_names_a_win_score_a_rules_verdict_or_the_proxy() -> None:
    items = [review_item(CLAIM_1, None, evidence())]

    for shown in (build_review_text(items).lower(), build_scores_csv(items).lower()):
        for hidden in ("proxy", "probability", "threshold", "win score", "rules", "must_review"):
            assert hidden not in shown
