"""Password hashing with Argon2id.

The password and its hash are never logged or returned by the API.
"""

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# NIST SP 800-63B: a length rule only, no composition rules.
MIN_PASSWORD_CHARACTERS = 12
MAX_PASSWORD_CHARACTERS = 128

# Hashed once, only so an unknown email costs the same time as a wrong password.
_DUMMY_PASSWORD = "dummy-password-for-unknown-emails"  # noqa: S105  (never a real password)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return the Argon2id hash of `password` (salted, so two calls give two hashes).

    Takes tens of milliseconds on purpose.
    """
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return whether `password` matches `password_hash`.

    A wrong password gives False. A damaged stored hash raises `argon2.exceptions.InvalidHashError`:
    that is a data fault, not a failed login. Takes tens of milliseconds on purpose.
    """
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


@cache
def _dummy_hash() -> str:
    return hash_password(_DUMMY_PASSWORD)


def verify_dummy_password(password: str) -> None:
    """Check `password` against a fixed dummy hash and ignore the answer.

    Login calls it for an unknown email, so that case takes about as long as a wrong password.
    """
    verify_password(_dummy_hash(), password)
