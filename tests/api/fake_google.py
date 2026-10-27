"""A made-up Google for tests: a token endpoint and a key set behind `httpx.MockTransport`.

ID tokens are signed with an RSA key made here, so no test reaches Google. Every value is made up.
"""

import json
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from src.config.settings import ApiSettings
from tests.api.conftest import api_settings

CLIENT_ID = "made-up-client-id.apps.example.test"
CLIENT_SECRET = "made-up-google-client-secret"  # noqa: S105  (made up)
ISSUER = "https://accounts.google.com"
KID = "made-up-key-1"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL)
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


@lru_cache(maxsize=2)
def rsa_key(name: str = "google") -> rsa.RSAPrivateKey:
    """A made-up RSA key, made once per name (making one takes tens of milliseconds)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def google_settings() -> ApiSettings:
    """The made-up API settings with Google sign-in turned on."""
    return api_settings().model_copy(
        update={"google_client_id": CLIENT_ID, "google_client_secret": SecretStr(CLIENT_SECRET)}
    )


def id_claims(*, nonce: str, now: datetime, **changes: Any) -> dict[str, Any]:
    """The claims of a good ID token for ann@gmail.com; a change of `None` drops that claim."""
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "100000000000000000001",
        "email": "ann@gmail.com",
        "email_verified": True,
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(changes)
    return {key: value for key, value in claims.items() if value is not None}


def sign(
    claims: dict[str, Any],
    *,
    kid: str = KID,
    key: rsa.RSAPrivateKey | None = None,
) -> str:
    """An RS256 ID token for `claims`, signed with the fake Google key unless `key` is given."""
    return jwt.encode(claims, key or rsa_key(), algorithm="RS256", headers={"kid": kid})


def key_set(*kids: str) -> dict[str, Any]:
    """The JSON key set publishing the fake Google key under each of `kids`."""
    public = rsa_key().public_key()
    keys = []
    for kid in kids or (KID,):
        jwk = RSAAlgorithm.to_jwk(public, as_dict=True)
        keys.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": keys}


class FakeGoogle:
    """Answers the token swap with `id_token` (or `token_answer`) and the keys with `keys`.

    `swaps` keeps the form of every token request; `key_fetches` counts the key set requests.
    """

    def __init__(self) -> None:
        self.id_token: str | None = None
        self.token_answer: httpx.Response | None = None
        self.keys: dict[str, Any] = key_set()
        self.swaps: list[dict[str, str]] = []
        self.key_fetches = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url == TOKEN_URL and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.swaps.append(form)
            if self.token_answer is not None:
                return self.token_answer
            return httpx.Response(200, json={"id_token": self.id_token, "access_token": "unused"})
        if request.url == JWKS_URL and request.method == "GET":
            self.key_fetches += 1
            return httpx.Response(200, content=json.dumps(self.keys))
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))
