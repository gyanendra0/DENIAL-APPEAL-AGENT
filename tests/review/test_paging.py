import pytest

from src.review.paging import MAX_PAGE_SIZE, check_page_size, next_before


@pytest.mark.parametrize("limit", [1, MAX_PAGE_SIZE])
def test_accepts_a_page_size_in_range(limit: int) -> None:
    check_page_size(limit)


def test_a_full_page_continues_before_its_last_id() -> None:
    assert next_before([9, 7, 4], 3) == 4


def test_a_short_page_is_the_last() -> None:
    assert next_before([9, 7], 3) is None
    assert next_before([], 3) is None
