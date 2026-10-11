import pytest

from src.evaluation.review_sample import (
    REVIEW_SAMPLE_SEED,
    REVIEW_SAMPLE_SIZE,
    select_review_sample,
)

SEED = 42
OTHER_SEED = 7


def _claim_ids(count: int) -> list[str]:
    """Made-up claim ids `000000000000001` to `count`, in rising order."""
    return [f"{number:015d}" for number in range(1, count + 1)]


def test_the_same_claim_ids_and_seed_give_the_same_sample() -> None:
    first = select_review_sample(_claim_ids(40), size=10, seed=SEED)
    second = select_review_sample(_claim_ids(40), size=10, seed=SEED)

    assert first == second


def test_the_order_the_claim_ids_arrive_in_does_not_change_the_sample() -> None:
    claim_ids = _claim_ids(40)
    shuffled = claim_ids[1::2] + claim_ids[-2::-2]

    assert sorted(shuffled) == claim_ids
    assert select_review_sample(shuffled, size=10, seed=SEED) == select_review_sample(
        claim_ids, size=10, seed=SEED
    )


def test_a_different_seed_gives_a_different_sample() -> None:
    claim_ids = _claim_ids(40)

    assert select_review_sample(claim_ids, size=10, seed=SEED) != select_review_sample(
        claim_ids, size=10, seed=OTHER_SEED
    )


def test_the_sample_holds_the_asked_number_of_given_claim_ids_each_once() -> None:
    claim_ids = _claim_ids(40)

    sample = select_review_sample(claim_ids, size=10, seed=SEED)

    assert len(sample) == 10
    assert len(set(sample)) == 10
    assert set(sample) <= set(claim_ids)


def test_the_draw_for_a_known_seed_is_pinned() -> None:
    claim_ids = _claim_ids(10)

    assert select_review_sample(claim_ids, size=3, seed=SEED) == [
        "000000000000001",
        "000000000000002",
        "000000000000005",
    ]
    assert select_review_sample(claim_ids, size=3, seed=OTHER_SEED) == [
        "000000000000003",
        "000000000000006",
        "000000000000007",
    ]


def test_the_sample_is_returned_sorted() -> None:
    sample = select_review_sample(_claim_ids(40), size=10, seed=SEED)

    assert sample == sorted(sample)


def test_a_size_equal_to_the_number_of_claim_ids_returns_all_of_them() -> None:
    claim_ids = _claim_ids(5)

    assert select_review_sample(reversed(claim_ids), size=5, seed=SEED) == claim_ids


def test_rejects_a_size_above_the_number_of_claim_ids() -> None:
    with pytest.raises(ValueError, match="30 drafts asked for, only 27 can be sampled"):
        select_review_sample(_claim_ids(27), size=30, seed=SEED)


def test_rejects_a_sample_from_no_claim_ids() -> None:
    with pytest.raises(ValueError, match="1 drafts asked for, only 0 can be sampled"):
        select_review_sample([], size=1, seed=SEED)


@pytest.mark.parametrize("size", [0, -1])
def test_rejects_a_size_below_one(size: int) -> None:
    with pytest.raises(ValueError, match=f"at least 1, got {size}"):
        select_review_sample(_claim_ids(5), size=size, seed=SEED)


def test_rejects_a_claim_id_given_twice() -> None:
    claim_ids = [*_claim_ids(5), "000000000000003"]

    with pytest.raises(ValueError, match=r"1 claim id\(s\) were given more than once"):
        select_review_sample(claim_ids, size=2, seed=SEED)


def test_the_review_defaults_are_thirty_drafts_and_seed_forty_two() -> None:
    assert REVIEW_SAMPLE_SIZE == 30
    assert REVIEW_SAMPLE_SEED == 42
