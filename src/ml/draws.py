"""Repeatable draws: a number that looks random but is fixed by its input text."""

import hashlib
from fractions import Fraction

DRAW_BYTES = 8
DRAW_RANGE = 2 ** (8 * DRAW_BYTES)


def repeatable_draw(text: str) -> Fraction:
    """Return a number from 0 up to (not including) 1 that is always the same for `text`.

    It is the first 8 bytes of the SHA-256 hash of `text`, read as a big-endian integer and
    divided by 2**64.
    """
    digest = hashlib.sha256(text.encode()).digest()
    return Fraction(int.from_bytes(digest[:DRAW_BYTES], "big"), DRAW_RANGE)
