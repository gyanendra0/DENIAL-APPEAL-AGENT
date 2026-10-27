"""Google's side of the sign-in: the address, the code swap and every ID token check, against a
made-up Google (no network)."""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from src.api.google_cookies import GoogleFlow
from src.api.google_oidc import GoogleIdentity, GoogleOidc, GoogleSignInError, code_challenge
from tests.api.conftest import api_settings
from tests.api.fake_google import (
    CLIENT_ID,
    CLIENT_SECRET,
    FakeGoogle,
    google_settings,
    id_claims,
    key_set,
    rsa_key,
    sign,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
REDIRECT = "https://app.example.test/auth/google/callback"
NONCE = "made-up-nonce"
FLOW = GoogleFlow(state="made-up-state", nonce=NONCE, verifier="made-up-verifier")


@pytest.fixture
def fake() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def google(fake: FakeGoogle) -> GoogleOidc:
    return GoogleOidc(google_settings(), fake.client(), redirect_uri=REDIRECT)


def _verify(google: GoogleOidc, token: str) -> GoogleIdentity:
    return google.verify_id_token(token, nonce=NONCE, now=NOW)


# --- the address and the swap ---


def test_the_pkce_challenge_matches_the_rfc_7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_the_address_asks_google_for_a_code_with_state_nonce_and_s256(google: GoogleOidc) -> None:
    url = urlsplit(google.authorization_url(FLOW))
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query == {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid email",
        "state": "made-up-state",
        "nonce": NONCE,
        "code_challenge": code_challenge("made-up-verifier"),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }


def test_it_needs_the_client_id_and_secret() -> None:
    with pytest.raises(ValueError, match="not configured"):
        GoogleOidc(api_settings(), httpx.Client(), redirect_uri=REDIRECT)


def test_the_swap_sends_the_code_secret_and_verifier_and_returns_the_id_token(
    google: GoogleOidc, fake: FakeGoogle
) -> None:
    fake.id_token = "made-up-id-token"  # noqa: S105  (made up)
    assert google.swap_code("made-up-code", "made-up-verifier") == "made-up-id-token"
    assert fake.swaps == [
        {
            "code": "made-up-code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
            "code_verifier": "made-up-verifier",
        }
    ]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, json={"access_token": "only"}),
        httpx.Response(200, json={"id_token": ""}),
        httpx.Response(200, content=b"not json"),
    ],
)
def test_a_refused_or_strange_swap_answer_is_an_error_and_logs_no_secret(
    google: GoogleOidc, fake: FakeGoogle, answer: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    fake.token_answer = answer
    with caplog.at_level(logging.DEBUG), pytest.raises(GoogleSignInError):
        google.swap_code("made-up-code", "made-up-verifier")
    for secret in ("made-up-code", "made-up-verifier", CLIENT_SECRET):
        assert secret not in caplog.text


def test_a_swap_that_cannot_reach_google_is_an_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("made-up network failure", request=request)

    client = httpx.Client(transport=httpx.MockTransport(fail))
    google = GoogleOidc(google_settings(), client, redirect_uri=REDIRECT)
    with pytest.raises(GoogleSignInError):
        google.swap_code("made-up-code", "made-up-verifier")
    with pytest.raises(GoogleSignInError):
        _verify(google, sign(id_claims(nonce=NONCE, now=NOW)))


# --- the ID token ---


def test_a_good_id_token_gives_the_google_account(google: GoogleOidc) -> None:
    token = sign(id_claims(nonce=NONCE, now=NOW, email=" Ann@Gmail.com ", hd="clinic.example"))
    assert _verify(google, token) == GoogleIdentity(
        subject="100000000000000000001",
        email="ann@gmail.com",
        email_verified=True,
        hosted_domain="clinic.example",
    )


def test_the_issuer_without_https_is_accepted_too(google: GoogleOidc) -> None:
    token = sign(id_claims(nonce=NONCE, now=NOW, iss="accounts.google.com", email_verified=False))
    identity = _verify(google, token)
    assert (identity.email_verified, identity.hosted_domain) == (False, None)


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "someone-elses-client"},
        {"iss": "https://accounts.example.test"},
        {"iss": "accounts.google"},
        {"nonce": "another-nonce"},
        {"nonce": None},
        {"sub": None},
        {"sub": ""},
        {"email": None},
        {"email_verified": "true"},
        {"hd": ""},
        {"exp": int(NOW.timestamp())},
    ],
)
def test_an_id_token_failing_a_check_is_refused(
    google: GoogleOidc, change: dict[str, object]
) -> None:
    claims = {**id_claims(nonce=NONCE, now=NOW), **change}
    with pytest.raises(GoogleSignInError):
        _verify(google, sign({key: value for key, value in claims.items() if value is not None}))


def test_an_id_token_signed_by_another_key_or_with_hs256_is_refused(google: GoogleOidc) -> None:
    claims = id_claims(nonce=NONCE, now=NOW)
    forged = jwt.encode(
        claims,
        "a-made-up-shared-secret-of-32-chars!",
        algorithm="HS256",
        headers={"kid": "made-up-key-1"},
    )
    for token in (sign(claims, key=rsa_key("attacker")), forged, "not-a-token"):
        with pytest.raises(GoogleSignInError):
            _verify(google, token)


def test_the_keys_are_fetched_once_and_again_for_a_new_key(
    google: GoogleOidc, fake: FakeGoogle
) -> None:
    claims = id_claims(nonce=NONCE, now=NOW)
    _verify(google, sign(claims))
    _verify(google, sign(claims))
    assert fake.key_fetches == 1

    fake.keys = key_set("made-up-key-2")
    _verify(google, sign(claims, kid="made-up-key-2"))
    assert fake.key_fetches == 2


def test_a_token_naming_an_unknown_key_is_refused_after_one_new_fetch(
    google: GoogleOidc, fake: FakeGoogle
) -> None:
    with pytest.raises(GoogleSignInError):
        _verify(google, sign(id_claims(nonce=NONCE, now=NOW), kid="made-up-unknown-key"))
    assert fake.key_fetches == 1


def test_an_id_token_with_no_key_id_is_refused(google: GoogleOidc) -> None:
    token = jwt.encode(id_claims(nonce=NONCE, now=NOW), rsa_key(), algorithm="RS256")
    with pytest.raises(GoogleSignInError):
        _verify(google, token)


def test_an_unreadable_key_set_is_an_error(google: GoogleOidc, fake: FakeGoogle) -> None:
    fake.keys = {"keys": "not a list"}
    with pytest.raises(GoogleSignInError):
        _verify(google, sign(id_claims(nonce=NONCE, now=NOW + timedelta(seconds=1))))
