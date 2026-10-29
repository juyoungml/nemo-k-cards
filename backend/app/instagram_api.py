"""Instagram's OAuth endpoints for "Instagram API with Instagram Login" (Business Login for Instagram), as documented
by Meta on 2026-10-29 (developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/
business-login and .../get-started):

  authorize   https://www.instagram.com/oauth/authorize?client_id&redirect_uri&response_type=code&scope&state
  code        POST https://api.instagram.com/oauth/access_token  (form: client_id, client_secret,
              grant_type=authorization_code, redirect_uri, code) → short-lived token (1 hour)
              The docs show the answer wrapped in {"data": [{...}]}; older answers were flat. Both are read.
  long-lived  GET  https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret&access_token
              → {access_token, token_type, expires_in} (60 days)
  me          GET  https://graph.instagram.com/<version>/me?fields=user_id,username,account_type
              `account_type` is documented as "Business" or "Media_Creator" (upper-cased elsewhere); compared
              case-insensitively. Also read flat or wrapped in "data".

Secrets never leave this module in a readable form: request URLs carry the token and the app secret as query
parameters (Meta's documented form), so `redact()` scrubs them from httpx's and uvicorn's log lines, and every
error raised here names the step and Meta's error type/code, never a URL or a response body."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
SCOPES = ("instagram_business_basic", "instagram_business_content_publish")
PROFESSIONAL = {"BUSINESS", "MEDIA_CREATOR"}

# Tests swap in an httpx.MockTransport here; nothing in the test suite ever reaches Meta.
transport: httpx.BaseTransport | None = None

_SECRET_PARAMS = re.compile(r"((?:access_token|client_secret|code|state|fb_exchange_token)=)[^&\s\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    return _SECRET_PARAMS.sub(r"\1[redacted]", text)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):  # a malformed record is not ours to fix; let it through unchanged
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def install_log_redaction() -> None:
    """httpx logs every request URL at INFO; uvicorn's access log has the callback's `code` and `state`."""
    for name in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, _RedactFilter) for f in lg.filters):
            lg.addFilter(_RedactFilter())


install_log_redaction()


class InstagramError(Exception):
    """A failed call to Instagram. The message is safe to log and show: no token, secret or URL."""


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_in: int | None = None

    def __repr__(self) -> str:  # never print the token by accident
        return f"Token(expires_in={self.expires_in})"


@dataclass(frozen=True)
class Profile:
    user_id: str
    username: str
    account_type: str  # upper-cased; "" when Instagram didn't say

    @property
    def professional(self) -> bool:
        return self.account_type in PROFESSIONAL


def authorize_url(state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": settings.instagram_app_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
        # The Admin signs in with the professional account's own credentials, not whichever Instagram account
        # this browser happens to be signed in to (often someone's personal one).
        "force_reauth": "true",
    })


def _client() -> httpx.Client:
    return httpx.Client(timeout=15, transport=transport, follow_redirects=False)


def _first(body: object) -> dict:
    """Meta answers either flat or as {"data": [{...}]}."""
    if isinstance(body, dict) and isinstance(body.get("data"), list) and body["data"]:
        body = body["data"][0]
    if not isinstance(body, dict):
        raise TypeError("unexpected answer")
    return body


def _call(step: str, send) -> dict:
    try:
        with _client() as client:
            r: httpx.Response = send(client)
    except httpx.HTTPError as e:
        raise InstagramError(f"{step}: could not reach Instagram ({type(e).__name__})") from None
    try:
        body = r.json()
    except ValueError:
        body = None
    if not r.is_success:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            kind, code = err.get("type"), err.get("code")
        elif isinstance(body, dict):
            kind, code = body.get("error_type"), body.get("code")
        else:
            kind = code = None
        raise InstagramError(f"{step}: Instagram answered HTTP {r.status_code}"
                             + (f" ({kind} {code})" if kind or code else ""))
    try:
        return _first(body)
    except TypeError:
        raise InstagramError(f"{step}: Instagram's answer was not understood") from None


def exchange_code(code: str) -> Token:
    """Authorization code → short-lived token. Instagram appends "#_" to the redirect; it isn't part of the code."""
    code = code.removesuffix("#_")
    body = _call("code exchange", lambda c: c.post(TOKEN_URL, data={
        "client_id": settings.instagram_app_id, "client_secret": settings.instagram_app_secret,
        "grant_type": "authorization_code", "redirect_uri": settings.instagram_redirect_uri, "code": code}))
    if not isinstance(body.get("access_token"), str):
        raise InstagramError("code exchange: no access token in Instagram's answer")
    return Token(body["access_token"])


def exchange_long_lived(short: Token) -> Token:
    body = _call("long-lived token", lambda c: c.get(f"{GRAPH}/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": settings.instagram_app_secret,
        "access_token": short.access_token}))
    if not isinstance(body.get("access_token"), str):
        raise InstagramError("long-lived token: no access token in Instagram's answer")
    try:
        expires_in = int(body["expires_in"]) if body.get("expires_in") is not None else None
    except (TypeError, ValueError):
        expires_in = None
    return Token(body["access_token"], expires_in)


def me(token: Token) -> Profile:
    body = _call("account lookup", lambda c: c.get(f"{GRAPH}/{settings.instagram_api_version}/me", params={
        "fields": "user_id,username,account_type", "access_token": token.access_token}))
    user_id = str(body.get("user_id") or body.get("id") or "")
    username = str(body.get("username") or "")
    if not re.fullmatch(r"[0-9]{1,32}", user_id) or not username:
        raise InstagramError("account lookup: Instagram did not return the account's id and username")
    return Profile(user_id, username, str(body.get("account_type") or "").upper())
