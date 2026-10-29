"""Instagram accounts (#15; GLOSSARY: Instagram account, Pilot Organization; docs/spec/v1.md pseudocode 2).

An Admin connects the Organization's Instagram Business or Creator account through Instagram's own sign-in:

  1. `POST /instagram-accounts/connect` (Admin) checks the Organization may connect (server configured, Pilot,
     under its account limit), saves a one-time nonce and returns Instagram's authorization URL with a signed
     `state` (Organization, Admin, nonce, 10-minute expiry). The Admin's browser goes there.
  2. Instagram sends the browser back to `GET /instagram-accounts/callback?code&state` on this API. That route
     has no session (it is a top-level redirect from instagram.com), so `state` is its only credential: the HMAC
     must match, it must not have expired, and its nonce must be unused (consumed here, so it works once).
  3. The code becomes a short-lived token, then a long-lived one (60 days); `/me` gives the account's id, username
     and type. A personal account is refused with a guide to switching to Business or Creator.
  4. In one privileged transaction, under a per-Organization lock: the Organization must still be a Pilot, the
     person must still be its Admin, and it must be under its limit; then the token goes into Supabase Vault
     (app/secret_store.py), the `instagram_accounts` row gets only its reference, and `instagram.connected` is
     written to the audit log.
  5. The browser lands on the Admin's Instagram accounts page with the outcome in the query string.

Disconnecting is an ordinary Admin write under RLS; a trigger drops the token from Vault in the same transaction.

One Instagram account belongs to at most one Organization (#21): two Organizations approving posts for the same
account would mean two approval trails and duplicate posts, so connecting an account another Organization already
has is refused (`taken`; a unique index backs it up for two connects racing).

The token never appears in an API response, a log line or the agent sandbox. The publisher (#21) and the dashboard
read it with `access_token(org, account)` below, on the privileged path."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from psycopg import Connection, errors
from pydantic import BaseModel

from app import audit, instagram_api, store
from app.auth import Admin, Ctx, Viewer
from app.config import settings
from app.schemas import InstagramAccount
from app.secret_store import SecretStoreUnavailable
from app.secret_store import secrets as secret_store

log = logging.getLogger("instagram")

STATE_TTL_S = 600
CALLBACK_PATH = "/instagram-accounts/callback"
ADMIN_PAGE = "/instagram"

# Why an Organization can't connect, as shown in the Admin.
NOT_CONFIGURED = "Connecting Instagram isn't set up on this server yet."
NO_VAULT = "Connecting Instagram needs Supabase Vault, which this server's database doesn't have."
NOT_PILOT = ("While Meta reviews our app, only Pilot Organizations can connect Instagram accounts. "
             "Contact us to join the pilot.")
NOT_ADMIN = "Only Admins can connect or disconnect Instagram accounts."


def limit_reached(limit: int) -> str:
    if limit == 0:
        return "Your plan doesn't include Instagram accounts."
    return (f"Your plan allows {limit} Instagram account{'s' if limit != 1 else ''}. "
            "Disconnect one to connect another.")


# ---------------------------------------------------------------- signed state

def _state_key() -> bytes:
    # Derived from the app secret (present whenever connecting is possible); separate from any other HMAC use.
    return hmac.new(settings.instagram_app_secret.encode(), b"wok/instagram-state/v1", hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_state(org_id: UUID, user_id: UUID, nonce: str, expires_at: int) -> str:
    payload = _b64(json.dumps({"o": str(org_id), "u": str(user_id), "n": nonce, "e": expires_at},
                              separators=(",", ":")).encode())
    return payload + "." + _b64(hmac.new(_state_key(), payload.encode(), hashlib.sha256).digest())


def verify_state(state: str) -> tuple[UUID, UUID, str] | None:
    """(org, user, nonce) if the signature matches and it hasn't expired; None for anything else."""
    try:
        payload, sig = state.split(".")
        want = hmac.new(_state_key(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(sig), want):
            return None
        claims = json.loads(_unb64(payload))
        if not isinstance(claims, dict) or int(claims["e"]) < time.time():
            return None
        return UUID(claims["o"]), UUID(claims["u"]), str(claims["n"])
    except (ValueError, KeyError, TypeError):
        return None


def _hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def consume_nonce(org_id: UUID, user_id: UUID, nonce: str) -> bool:
    """Marks the nonce used. False if it is unknown, already used, expired or issued to someone else. Privileged:
    the callback has no session, and the signed state is what names the Organization."""
    with store.system() as c:
        c.execute("delete from instagram_connect_states where expires_at < now() - interval '1 day'")
        row = c.execute("update instagram_connect_states set used_at = now() where nonce_hash = %s "
                        "and used_at is null and expires_at > now() returning org_id, user_id",
                        (_hash(nonce),)).fetchone()
    return bool(row) and row[0] == org_id and row[1] == user_id


# ---------------------------------------------------------------- standing: may this Organization connect?

class ConnectStatus(BaseModel):
    """What the Admin needs to show the Connect button or why it is missing."""
    configured: bool
    pilot: bool
    limit: int
    connected: int
    can_connect: bool
    reason: str | None


def configured() -> bool:
    return bool(settings.instagram_app_id and settings.instagram_app_secret and settings.instagram_redirect_uri)


def _limit(raw: int | None) -> int:
    return settings.instagram_accounts_per_org if raw is None else raw


def _vault_ok() -> bool:
    with store.system() as c:
        return secret_store.available(c)


def standing(ctx: Ctx) -> ConnectStatus:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        pilot, raw_limit = c.execute("select is_pilot, instagram_account_limit from orgs where id = %s",
                                     (ctx.org_id,)).fetchone()
        used, stale = c.execute("select count(*), count(*) filter (where status in ('expiring', 'revoked')) "
                                "from instagram_accounts where org_id = %s and status <> 'disconnected'",
                                (ctx.org_id,)).fetchone()
    limit = _limit(raw_limit)
    # At the limit, Connect still works while an account needs reconnecting (same account, no new slot); the
    # callback enforces the limit for whichever account actually comes back.
    full = used >= limit and not stale
    reason = (NOT_CONFIGURED if not configured() else NOT_PILOT if not pilot
              else limit_reached(limit) if full else NOT_ADMIN if ctx.role != "admin"
              else NO_VAULT if not _vault_ok() else None)
    return ConnectStatus(configured=configured(), pilot=pilot, limit=limit, connected=used,
                       can_connect=reason is None, reason=reason)


# ---------------------------------------------------------------- connect

def start(ctx: Ctx) -> str:
    """Instagram's authorization URL for this Admin, with a fresh signed, single-use state."""
    s = standing(ctx)
    if not s.can_connect:
        status = {NOT_CONFIGURED: 503, NO_VAULT: 503, NOT_PILOT: 403, NOT_ADMIN: 403}.get(s.reason or "", 409)
        raise HTTPException(status, s.reason)
    nonce = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + STATE_TTL_S
    # As the Admin under RLS: the policy refuses the insert unless they are an Admin of this Organization.
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        c.execute("insert into instagram_connect_states (nonce_hash, user_id, expires_at) "
                  "values (%s, %s, to_timestamp(%s))", (_hash(nonce), ctx.user.id, expires_at))
    return instagram_api.authorize_url(sign_state(ctx.org_id, ctx.user.id, nonce, expires_at))


class Refused(Exception):
    def __init__(self, code: str):
        self.code = code


def save(org_id: UUID, user_id: UUID, profile: instagram_api.Profile, token: instagram_api.Token) -> UUID:
    """Store the account and its token, re-checking everything under a per-Organization lock (the Admin may have
    been demoted, or another tab may have connected an account, since the flow started). One transaction: the
    Vault secret, the row and the audit event are saved together or not at all."""
    with store.system() as c:
        c.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"instagram:{org_id}",))
        org = c.execute("select is_pilot, instagram_account_limit from orgs where id = %s", (org_id,)).fetchone()
        member = c.execute("select role, email from members where org_id = %s and user_id = %s",
                           (org_id, user_id)).fetchone()
        if org is None or member is None or member[0] != "admin":
            raise Refused("not_admin")
        if not org[0]:
            raise Refused("not_pilot")
        others = c.execute("select count(*) from instagram_accounts where org_id = %s and ig_user_id <> %s "
                           "and status <> 'disconnected'", (org_id, profile.user_id)).fetchone()[0]
        if others >= _limit(org[1]):
            raise Refused("limit")
        # Privileged lookup across Organizations, answering only yes or no: the Admin never learns which one.
        if c.execute("select 1 from instagram_accounts where ig_user_id = %s and org_id <> %s "
                     "and status <> 'disconnected'", (profile.user_id, org_id)).fetchone():
            raise Refused("taken")
        before = c.execute("select status from instagram_accounts where org_id = %s and ig_user_id = %s",
                           (org_id, profile.user_id)).fetchone()
        ref = secret_store.put(c, f"instagram/{org_id}/{profile.user_id}", token.access_token,
                               f"Instagram access token for @{profile.username}")
        expires = (datetime.now(UTC) + timedelta(seconds=token.expires_in)) if token.expires_in else None
        (account_id,) = c.execute(
            "insert into instagram_accounts (org_id, ig_user_id, username, account_type, token_ref, "
            "token_expires_at, status, connected_by) values (%s, %s, %s, %s, %s, %s, 'connected', %s) "
            "on conflict (org_id, ig_user_id) do update set username = excluded.username, "
            "account_type = excluded.account_type, token_ref = excluded.token_ref, "
            "token_expires_at = excluded.token_expires_at, status = 'connected', "
            "connected_by = excluded.connected_by, connected_at = now(), disconnected_at = null returning id",
            (org_id, profile.user_id, profile.username, profile.account_type, ref, expires, user_id)).fetchone()
        audit.insert(c, org_id, user_id, member[1] or "", "instagram.connected", f"@{profile.username}",
                     {"account": str(account_id), "ig_user_id": profile.user_id,
                      "account_type": profile.account_type, "token_expires_at": expires,
                      "reconnected": before is not None and before[0] != "disconnected"})
    log.info("Instagram account @%s connected for Organization %s", profile.username, org_id)
    return account_id


def _to_admin(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.admin_url.rstrip('/')}{ADMIN_PAGE}?{urlencode(params)}", status_code=303)


def callback(code: str | None, state: str | None, error: str | None) -> RedirectResponse:
    claims = verify_state(state or "") if configured() else None
    if claims is None or not consume_nonce(*claims):
        return _to_admin(error="state")
    org_id, user_id, _ = claims
    if error or not code:  # the person cancelled or declined on Instagram
        return _to_admin(error="denied")
    try:
        long = instagram_api.exchange_long_lived(instagram_api.exchange_code(code))
        profile = instagram_api.me(long)
    except instagram_api.InstagramError as e:
        log.warning("Instagram connect failed for Organization %s: %s", org_id, e)
        return _to_admin(error="instagram")
    if not profile.professional:
        log.info("refused a non-professional Instagram account (%s) for Organization %s",
                 profile.account_type or "type not given", org_id)
        return _to_admin(error="personal_account")
    try:
        save(org_id, user_id, profile, long)
    except errors.UniqueViolation:  # another Organization connected the same account at the same moment
        return _to_admin(error="taken")
    except Refused as r:
        return _to_admin(error=r.code)
    except SecretStoreUnavailable:
        log.error("Instagram connect refused: Supabase Vault is not installed in this database")
        return _to_admin(error="unavailable")
    return _to_admin(connected=profile.username)


# ---------------------------------------------------------------- list, disconnect

_COLS = "id, username, account_type, status, token_expires_at, connected_at"


def list_accounts(ctx: Ctx) -> list[InstagramAccount]:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        rows = c.execute(f"select {_COLS} from instagram_accounts where org_id = %s and status <> 'disconnected' "
                         "order by connected_at, id", (ctx.org_id,)).fetchall()
    return [InstagramAccount(id=r[0], handle=f"@{r[1]}", account_type=r[2], status=r[3], token_expires_at=r[4],
                             connected_at=r[5], live=r[3] == "connected") for r in rows]


def disconnect(ctx: Ctx, account_id: UUID) -> None:
    """As the Admin under RLS (only Admins may, and only to `disconnected`); the trigger drops the Vault secret."""
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        row = c.execute("update instagram_accounts set status = 'disconnected', disconnected_at = now() "
                        "where id = %s and org_id = %s and status <> 'disconnected' returning username, ig_user_id",
                        (account_id, ctx.org_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Instagram account not found")
        audit.write(c, ctx.org_id, audit.entry(ctx, "instagram.disconnected", f"@{row[0]}",
                                               account=str(account_id), ig_user_id=row[1]))
    log.info("Instagram account @%s disconnected for Organization %s", row[0], ctx.org_id)


def access_token(c: Connection, org_id: UUID, account_id: UUID) -> str | None:
    """The account's token, for the publisher only (#21), on a privileged transaction. Never return it from a
    route, log it, or hand it to an agent."""
    row = c.execute("select token_ref from instagram_accounts where id = %s and org_id = %s "
                    "and status in ('connected', 'expiring')", (account_id, org_id)).fetchone()
    return secret_store.get(c, row[0]) if row and row[0] else None


# ---------------------------------------------------------------- routes

class ConnectOut(BaseModel):
    authorize_url: str


router = APIRouter()


@router.get("/instagram-accounts", response_model=list[InstagramAccount])
def accounts(ctx: Viewer) -> list[InstagramAccount]:
    return list_accounts(ctx)


@router.get("/instagram-accounts/connection", response_model=ConnectStatus)
def connection(ctx: Viewer) -> ConnectStatus:
    return standing(ctx)


@router.post("/instagram-accounts/connect", response_model=ConnectOut)
def connect(ctx: Admin) -> ConnectOut:
    """A JSON answer rather than a redirect: the Admin calls this with its Bearer token (a plain navigation
    couldn't send it), then sends the browser to `authorize_url`."""
    return ConnectOut(authorize_url=start(ctx))


@router.get(CALLBACK_PATH, include_in_schema=False)
def instagram_callback(code: Annotated[str | None, Query(max_length=2048)] = None,
                       state: Annotated[str | None, Query(max_length=1024)] = None,
                       error: Annotated[str | None, Query(max_length=200)] = None) -> RedirectResponse:
    """Public (no session): Instagram redirects the browser here. The signed, single-use `state` is the check."""
    return callback(code, state, error)


@router.delete("/instagram-accounts/{account_id}", status_code=204)
def delete_account(account_id: UUID, ctx: Admin) -> None:
    disconnect(ctx, account_id)
