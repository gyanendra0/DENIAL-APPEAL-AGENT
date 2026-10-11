import pytest
from pydantic import ValidationError

from src.drafting.schemas import (
    AppealDraft,
    DraftCitation,
    DraftParagraph,
    ParagraphKind,
    evidence_label,
)

QUOTE = "a made-up sentence of policy text"


def test_the_paragraph_kinds_are_policy_and_other() -> None:
    assert [kind.value for kind in ParagraphKind] == ["policy", "other"]


def test_the_first_match_has_the_label_e1() -> None:
    assert [evidence_label(position) for position in range(3)] == ["E1", "E2", "E3"]


def test_a_position_below_zero_has_no_label() -> None:
    with pytest.raises(ValueError, match="at least 0"):
        evidence_label(-1)


def test_a_draft_is_read_from_the_json_the_model_answers() -> None:
    draft = AppealDraft.model_validate_json("""
        {"paragraphs": [
            {"kind": "other", "text": "I am writing to appeal this denial."},
            {"kind": "policy", "text": "The policy covers it.",
             "citations": [{"label": "E2", "quote": "a made-up sentence of policy text"}]}
        ]}
        """)

    assert [paragraph.kind for paragraph in draft.paragraphs] == [
        ParagraphKind.OTHER,
        ParagraphKind.POLICY,
    ]
    assert draft.paragraphs[0].citations == ()
    assert draft.paragraphs[1].citations == (DraftCitation(label="E2", quote=QUOTE),)


def test_a_draft_with_no_paragraph_is_refused() -> None:
    with pytest.raises(ValidationError):
        AppealDraft(paragraphs=())


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_a_blank_paragraph_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        DraftParagraph(kind=ParagraphKind.OTHER, text=text)


@pytest.mark.parametrize("quote", ["", "  \n "])
def test_a_blank_quote_is_refused(quote: str) -> None:
    with pytest.raises(ValidationError):
        DraftCitation(label="E1", quote=quote)


@pytest.mark.parametrize("label", ["", "E", "E0", "e1", "1", "E100", "E1 ", "E1\n", "F1", "E-1"])
def test_a_label_that_is_not_e_and_a_position_is_refused(label: str) -> None:
    with pytest.raises(ValidationError):
        DraftCitation(label=label, quote=QUOTE)


def test_a_paragraph_of_kind_other_must_not_have_a_citation() -> None:
    with pytest.raises(ValidationError, match="must not have a citation"):
        DraftParagraph(
            kind=ParagraphKind.OTHER,
            text="Please review the claim again.",
            citations=(DraftCitation(label="E1", quote=QUOTE),),
        )


def test_a_policy_paragraph_without_a_citation_fits_the_shape() -> None:
    # The shape allows it; the citation validator is what blocks it.
    paragraph = DraftParagraph(kind=ParagraphKind.POLICY, text="The policy covers it.")

    assert paragraph.citations == ()


def test_a_field_the_shape_does_not_know_is_refused() -> None:
    # The model must not write the fields that name a chunk.
    with pytest.raises(ValidationError):
        DraftCitation.model_validate({"label": "E1", "quote": QUOTE, "text_sha256": "abc"})
    with pytest.raises(ValidationError):
        DraftParagraph.model_validate({"kind": "other", "text": "Hello.", "source": "cms_ncd"})
    with pytest.raises(ValidationError):
        AppealDraft.model_validate({"paragraphs": [{"kind": "other", "text": "Hi."}], "x": 1})


def test_a_kind_that_is_not_policy_or_other_is_refused() -> None:
    with pytest.raises(ValidationError):
        DraftParagraph.model_validate({"kind": "legal", "text": "Hello."})


def test_the_repr_shows_no_letter_text_and_no_quote() -> None:
    draft = AppealDraft(
        paragraphs=(
            DraftParagraph(
                kind=ParagraphKind.POLICY,
                text="The policy covers it.",
                citations=(DraftCitation(label="E1", quote=QUOTE),),
            ),
        )
    )

    assert "covers" not in repr(draft)
    assert "made-up" not in repr(draft)


def test_a_draft_cannot_be_changed_after_it_is_built() -> None:
    paragraph = DraftParagraph(kind=ParagraphKind.OTHER, text="Hello.")

    with pytest.raises(ValidationError):
        paragraph.kind = ParagraphKind.POLICY
