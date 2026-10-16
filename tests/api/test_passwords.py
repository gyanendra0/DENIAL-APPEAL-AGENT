import pytest
from argon2.exceptions import InvalidHashError

from src.api.passwords import hash_password, verify_dummy_password, verify_password

PASSWORD = "correct horse battery"  # noqa: S105  (made up)


def test_hash_is_argon2id_and_not_the_password() -> None:
    password_hash = hash_password(PASSWORD)
    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash


def test_same_password_hashes_differently_each_time() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_accepts_the_right_password() -> None:
    assert verify_password(hash_password(PASSWORD), PASSWORD) is True


def test_rejects_a_wrong_password() -> None:
    assert verify_password(hash_password(PASSWORD), PASSWORD + "x") is False


def test_damaged_stored_hash_raises_instead_of_failing_quietly() -> None:
    with pytest.raises(InvalidHashError):
        verify_password("not-a-hash", PASSWORD)


def test_dummy_check_runs_for_any_password() -> None:
    verify_dummy_password(PASSWORD)
    verify_dummy_password("")
