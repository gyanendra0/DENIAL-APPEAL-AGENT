from fractions import Fraction

from src.ml.draws import repeatable_draw


def test_draw_is_the_same_every_time_for_the_same_text() -> None:
    assert repeatable_draw("v1:800000000000001") == repeatable_draw("v1:800000000000001")


def test_draw_is_the_first_eight_hash_bytes_over_two_to_the_64() -> None:
    # SHA-256 of "abc" starts with the bytes ba7816bf 8f01cfea.
    assert repeatable_draw("abc") == Fraction(0xBA7816BF8F01CFEA, 2**64)


def test_draws_stay_at_or_above_0_and_below_1_and_differ_between_texts() -> None:
    draws = [repeatable_draw(f"text {n}") for n in range(1000)]

    assert all(0 <= draw < 1 for draw in draws)
    assert len(set(draws)) == len(draws)
