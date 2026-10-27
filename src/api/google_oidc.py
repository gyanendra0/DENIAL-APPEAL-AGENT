"""Talking to Google for "Continue with Google" (OpenID Connect code flow with PKCE).

Builds the address the browser is sent to, swaps the one-time code for an ID token (the API does
this, with the client secret and the PKCE verifier), and checks that ID token: an RS256 signature
by one of Google's published keys, the issuer, the audience (our client ID), the expiry and the
nonce. Nothing here touches the database, and no Google token is kept.
"""

import base64
import hashlib
import logging
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ConfigDict

from src.api.google_cookies import GoogleFlow
from src.config.settings import ApiSettings

logger = logging.getLogger(__name__)

ID_TOKEN_ALGORITHM = "RS256"  # noqa: S105  (an algorithm name, not a password)
SCOPES = "openid email"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "nonce")


class GoogleSignInError(Exception):
    """Google's answer could not be used: the swap failed or the ID token did not pass a check.

    The message is fixed and never repeats the code, a token or what Google sent.
    """


class GoogleIdentity(BaseModel):
    """What a checked ID token says about the Google account.

    `subject` is Google's permanent id (`sub`). `email` is trimmed and lowercased.
    `hosted_domain` is the `hd` claim, set only for a Google Workspace account.
    """

    model_config = ConfigDict(frozen=True)

    subject: str
    email: str
    email_verified: bool
    hosted_domain: str | None


def code_challenge(verifier: str) -> str:
    """The PKCE S256 challenge of `verifier`: base64url of its SHA-256, without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class GoogleOidc:
    """Google's side of the sign-in, for one client ID.

    `http` makes every call to Google (tests give it a fake transport). Google's public keys are
    fetched on first use and again when a token names a key that is not known yet (Google
    rotates its keys).
    """

    def __init__(self, settings: ApiSettings, http: httpx.Client, *, redirect_uri: str) -> None:
        if settings.google_client_id is None or settings.google_client_secret is None:
            raise ValueError("Google sign-in is not configured")
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret
        self._auth_url = settings.google_auth_url
        self._token_url = settings.google_token_url
        self._jwks_url = settings.google_jwks_url
        self._issuers = settings.google_issuer_list
        self._http = http
        self.redirect_uri = redirect_uri
        self._keys: jwt.PyJWKSet | None = None

    def authorization_url(self, flow: GoogleFlow) -> str:
        """The Google address that asks the user to pick an account and comes back with a code."""
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": flow.state,
                "nonce": flow.nonce,
                "code_challenge": code_challenge(flow.verifier),
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._auth_url}?{query}"

    def swap_code(self, code: str, verifier: str) -> str:
        """Swap the one-time `code` for Google's ID token and return the token, unchecked.

        Calls Google over the network, so it can take up to the client's timeout. Any failure
        raises `GoogleSignInError`.
        """
        try:
            response = self._http.post(
                self._token_url,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            # The class only: the message of a request error can hold the request.
            logger.warning("google token swap failed: %s", type(error).__name__)
            raise GoogleSignInError("the Google sign-in could not be completed") from None
        if response.status_code != httpx.codes.OK:
            logger.warning("google token swap failed: status %d", response.status_code)
            raise GoogleSignInError("the Google sign-in could not be completed")
        try:
            id_token = response.json().get("id_token")
        except ValueError:
            id_token = None
        if not isinstance(id_token, str) or not id_token:
            logger.warning("google token swap failed: no id token")
            raise GoogleSignInError("the Google sign-in could not be completed")
        return id_token

    def verify_id_token(self, id_token: str, *, nonce: str, now: datetime) -> GoogleIdentity:
        """Check `id_token` against Google's keys, our client ID, `nonce` and `now`.

        May call Google for its public keys. Every failed check raises `GoogleSignInError`.
        """
        error = GoogleSignInError("the Google ID token is not valid")
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError:
            raise error from None
        if not isinstance(kid, str):
            raise error
        key = self._key(kid)
        try:
            # The expiry is checked against `now` below, so tests can move the clock.
            payload: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=[ID_TOKEN_ALGORITHM],
                audience=self._client_id,
                issuer=self._issuers,
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError:
            raise error from None
        exp, sub, token_nonce = payload["exp"], payload["sub"], payload["nonce"]
        email, verified, hd = payload.get("email"), payload.get("email_verified"), payload.get("hd")
        if (
            type(exp) is not int
            or exp <= now.timestamp()
            or not isinstance(token_nonce, str)
            or not secrets.compare_digest(token_nonce.encode(), nonce.encode())
            or not (isinstance(sub, str) and sub)
            or not (isinstance(email, str) and email.strip())
            or type(verified) is not bool
            or not (hd is None or (isinstance(hd, str) and hd))
        ):
            raise error
        return GoogleIdentity(
            subject=sub,
            email=email.strip().lower(),
            email_verified=verified,
            hosted_domain=hd,
        )

    def _key(self, kid: str) -> jwt.PyJWK:
        """Google's public key `kid`, fetching the key set again once if it is not known."""
        if self._keys is not None:
            try:
                return self._keys[kid]
            except KeyError:
                pass
        self._keys = self._fetch_keys()
        try:
            return self._keys[kid]
        except KeyError:
            raise GoogleSignInError("the Google ID token is not valid") from None

    def _fetch_keys(self) -> jwt.PyJWKSet:
        try:
            response = self._http.get(self._jwks_url)
            response.raise_for_status()
            return jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as error:
            logger.warning("google keys fetch failed: %s", type(error).__name__)
            raise GoogleSignInError("the Google sign-in could not be completed") from None
