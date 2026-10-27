from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from src.api.tokens import InvalidTokenError, TokenClaims, issue_token, read_token

SECRET = "a-made-up-test-secret-of-forty-characters"  # noqa: S105  (made up)
OTHER_SECRET = "another-made-up-secret-of-forty-characters"  # noqa: S105  (made up)
LONG_SECRET = "x" * 64


def _token(now: datetime | None = None, ttl_minutes: int = 60) -> str:
    return issue_token(
        user_id=7,
        account_id=3,
        token_version=2,
        secret=SECRET,
        ttl_minutes=ttl_minutes,
        now=now or datetime.now(UTC),
    )


def _raw(payload: dict[str, Any], algorithm: str = "HS256", secret: str = SECRET) -> str:
    return jwt.encode(payload, secret, algorithm=algorithm)


def _payload(**changes: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": "7",
        "acct": 3,
        "ver": 2,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(changes)
    return {key: value for key, value in payload.items() if value is not None}


def test_token_round_trip_gives_user_account_and_version() -> None:
    assert read_token(_token(), secret=SECRET) == TokenClaims(
        user_id=7, account_id=3, token_version=2
    )


def test_token_carries_the_lifetime() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    payload = jwt.decode(_token(now, ttl_minutes=30), SECRET, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == 30 * 60
    assert payload["sub"] == "7"


def test_issue_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_token(
            user_id=1,
            account_id=1,
            token_version=0,
            secret=SECRET,
            ttl_minutes=60,
            now=datetime.now(),
        )


def test_rejects_an_expired_token() -> None:
    token = _token(datetime.now(UTC) - timedelta(minutes=61))
    with pytest.raises(InvalidTokenError):
        read_token(token, secret=SECRET)


def test_rejects_a_token_signed_with_another_secret() -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_token(), secret=OTHER_SECRET)


def test_rejects_a_tampered_token() -> None:
    header, _payload_part, signature = _token().split(".")
    forged = _raw(_payload(sub="8")).split(".")[1]
    with pytest.raises(InvalidTokenError):
        read_token(f"{header}.{forged}.{signature}", secret=SECRET)


def test_rejects_another_algorithm_even_with_the_right_key() -> None:
    # HS512 wants a key of at least 64 bytes; the same key reads it back, so only the
    # algorithm can be the reason for the refusal.
    token = _raw(_payload(), algorithm="HS512", secret=LONG_SECRET)
    with pytest.raises(InvalidTokenError):
        read_token(token, secret=LONG_SECRET)


def test_rejects_an_unsigned_token() -> None:
    token = _raw(_payload(), algorithm="none", secret="")
    with pytest.raises(InvalidTokenError):
        read_token(token, secret=SECRET)


@pytest.mark.parametrize("missing", ["sub", "acct", "ver", "iat", "exp"])
def test_rejects_a_token_missing_a_claim(missing: str) -> None:
    payload = _payload()
    del payload[missing]
    with pytest.raises(InvalidTokenError):
        read_token(_raw(payload), secret=SECRET)


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": "seven"},
        {"sub": "-7"},
        {"sub": ""},
        {"acct": "3"},
        {"acct": True},
        {"acct": 3.0},
        {"ver": "2"},
        {"ver": True},
        {"ver": 2.0},
        {"ver": -1},
    ],
)
def test_rejects_claims_of_the_wrong_shape(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(_raw(_payload(**changes)), secret=SECRET)


@pytest.mark.parametrize("token", ["", "junk", "a.b.c"])
def test_rejects_text_that_is_not_a_token(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        read_token(token, secret=SECRET)


def test_error_message_does_not_repeat_the_token() -> None:
    token = _token(datetime.now(UTC) - timedelta(hours=2))
    with pytest.raises(InvalidTokenError) as caught:
        read_token(token, secret=SECRET)
    assert token not in str(caught.value)
