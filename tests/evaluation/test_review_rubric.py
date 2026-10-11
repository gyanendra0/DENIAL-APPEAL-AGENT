"""Tests for the review rubric: every verdict and reason has a meaning a reviewer can read."""

from decimal import Decimal

from src.evaluation.review_rubric import (
    ACCEPTANCE_TARGET,
    ACCEPTED_VERDICTS,
    REJECT_REASON_MEANINGS,
    VERDICT_MEANINGS,
    RejectReason,
    Verdict,
)


def test_the_target_is_the_60_percent_of_the_done_condition() -> None:
    assert Decimal("0.60") == ACCEPTANCE_TARGET


def test_there_are_three_verdicts_and_two_of_them_count_as_accepted() -> None:
    assert [verdict.value for verdict in Verdict] == ["accept", "accept_light_edits", "reject"]
    assert {Verdict.ACCEPT, Verdict.ACCEPT_LIGHT_EDITS} == ACCEPTED_VERDICTS


def test_a_reject_has_one_of_five_reasons() -> None:
    assert [reason.value for reason in RejectReason] == [
        "wrong_fact",
        "no_policy_argument",
        "citation_does_not_fit",
        "poor_wording",
        "other",
    ]


def test_every_verdict_and_every_reason_has_a_meaning_in_rubric_order() -> None:
    assert list(VERDICT_MEANINGS) == list(Verdict)
    assert list(REJECT_REASON_MEANINGS) == list(RejectReason)
    assert all(meaning.strip() for meaning in VERDICT_MEANINGS.values())
    assert all(meaning.strip() for meaning in REJECT_REASON_MEANINGS.values())


def test_light_edits_means_at_most_three_sentences_and_no_fact_changed() -> None:
    meaning = VERDICT_MEANINGS[Verdict.ACCEPT_LIGHT_EDITS]

    assert "at most 3 sentences" in meaning
    assert "No fact is corrected and no policy argument is added or removed" in meaning
