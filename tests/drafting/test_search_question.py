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
    # These are the `v3` phrases: `other`, `noncovered` and `medical_necessity` are new, the
    # other three are those of `v1`.
    assert SEARCH_QUESTION_VERSION == "v3"
    assert {category.value: phrase for category, phrase in SEARCH_PHRASES.items()} == {
        "noncovered": (
            "services excluded from Medicare coverage and the exceptions to the exclusions"
        ),
        "medical_necessity": (
            "services that are not reasonable and necessary for the diagnosis or treatment of"
            " illness or injury and limitation on liability for such a denial"
        ),
        "duplicate": "duplicate claim for the same service",
        "benefits_exhausted": "frequency limits and the number of covered services",
        "coordination_of_benefits": "Medicare as secondary payer and coverage by another insurer",
        "other": (
            "how to appeal a denied claim: the right to request a redetermination of an initial"
            " determination"
        ),
    }


def test_the_minimum_similarity_is_the_decided_one() -> None:
    # A change to the minimum changes what a search finds, so it needs a new version too.
    assert MIN_EVIDENCE_SIMILARITY == 0.55


def test_the_minimum_similarity_is_a_value_the_search_accepts() -> None:
    assert LOWEST_SIMILARITY <= MIN_EVIDENCE_SIMILARITY <= HIGHEST_SIMILARITY


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_the_question_is_the_phrase_of_the_category_alone(category: DenialReasonCategory) -> None:
    assert build_search_question(category) == SEARCH_PHRASES[category]


def test_an_unknown_reason_gets_the_phrase_of_other() -> None:
    assert build_search_question(None) == SEARCH_PHRASES[DenialReasonCategory.OTHER]


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_no_question_holds_a_digit(category: DenialReasonCategory) -> None:
    # A procedure code in the question lowers the similarity of the passages that fit.
    assert not any(character.isdigit() for character in build_search_question(category))
