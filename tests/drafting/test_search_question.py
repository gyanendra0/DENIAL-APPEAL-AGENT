import pytest

from src.db.models import DenialReasonCategory
from src.drafting.search_question import (
    MIN_EVIDENCE_SIMILARITY,
    SEARCH_PHRASES,
    SEARCH_QUESTION_VERSION,
    build_search_question,
)
from src.rag.retrieval import HIGHEST_SIMILARITY, LOWEST_SIMILARITY


def test_every_denial_reason_category_has_its_own_phrase() -> None:
    assert set(SEARCH_PHRASES) == set(DenialReasonCategory)
    assert len(set(SEARCH_PHRASES.values())) == len(DenialReasonCategory)


def test_the_phrases_are_the_decided_ones() -> None:
    # A change to a phrase needs a new version: stored drafts name the wording they used.
    # `v2` added the minimum similarity and left the wording alone: these are the `v1` phrases.
    assert SEARCH_QUESTION_VERSION == "v2"
    assert {category.value: phrase for category, phrase in SEARCH_PHRASES.items()} == {
        "noncovered": (
            "Medicare coverage of the item or service and the conditions under which it is covered"
        ),
        "medical_necessity": (
            "reasonable and necessary: indications and limitations of coverage for the service"
        ),
        "duplicate": "duplicate claim for the same service",
        "benefits_exhausted": "frequency limits and the number of covered services",
        "coordination_of_benefits": "Medicare as secondary payer and coverage by another insurer",
        "other": "coverage of the item or service",
    }


def test_the_minimum_similarity_is_the_decided_one() -> None:
    # A change to the minimum changes what a search finds, so it needs a new version too.
    assert MIN_EVIDENCE_SIMILARITY == 0.55


def test_the_minimum_similarity_is_a_value_the_search_accepts() -> None:
    assert LOWEST_SIMILARITY <= MIN_EVIDENCE_SIMILARITY <= HIGHEST_SIMILARITY


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_with_no_code_the_question_is_the_phrase_alone(category: DenialReasonCategory) -> None:
    assert build_search_question(category, []) == SEARCH_PHRASES[category]


def test_the_codes_follow_the_phrase() -> None:
    question = build_search_question(DenialReasonCategory.DUPLICATE, ["99215", "71020"])

    assert question == "duplicate claim for the same service. Procedure codes: 99215, 71020."


def test_a_code_given_twice_is_asked_once_in_the_order_first_seen() -> None:
    question = build_search_question(
        DenialReasonCategory.OTHER, ["71020", "99215", "71020", "99215", "A0425"]
    )

    assert question == "coverage of the item or service. Procedure codes: 71020, 99215, A0425."


def test_an_unknown_reason_gets_the_phrase_of_other() -> None:
    assert build_search_question(None, ["99215"]) == build_search_question(
        DenialReasonCategory.OTHER, ["99215"]
    )


def test_the_same_inputs_always_give_the_same_question() -> None:
    first = build_search_question(DenialReasonCategory.NONCOVERED, ("99215",))
    second = build_search_question(DenialReasonCategory.NONCOVERED, ["99215"])

    assert first == second
