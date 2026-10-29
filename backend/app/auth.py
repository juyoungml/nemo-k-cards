"""Sign-in and Roles (docs/spec/v1.md, pseudocode 1). Every request except /health carries a Supabase Auth access
token as `Authorization: Bearer <jwt>`; the middleware verifies it, looks up the caller's Organization and Role in
`members`, and leaves a `Ctx` on `request.state` for handlers.

Which Organization: a person may be a Member of several, and the Admin sends the one they are working in as
`X-Org-Id` on every call. The header is only a choice, never a grant: it must name an Organization the verified
caller is a Member of, or the request is refused (403). Without it the request acts for their oldest membership.

Roles (GLOSSARY): every route declares the least Role it needs with `Viewer`, `Editor`, `Approver` or `Admin`
(or `SignedIn` for the few routes that work without an Organization). tests/test_roles.py fails if a route
declares none.

Deny by default: a new route is protected without doing anything. Only /health, CORS preflights and the
rendered-slide files under /assets skip this check; /assets answers only links signed by app/assets.py
(an <img> tag can't send a Bearer token, and Instagram fetches anonymously). The Instagram OAuth callback is
public too: its signed, single-use `state` is checked by app/instagram_accounts.py.

Token checks: signature, `exp`, `aud == "authenticated"`, `iss == SUPABASE_URL + "/auth/v1"`, `role ==
"authenticated"`, a UUID `sub`, not anonymous. Supabase signs with an asymmetric key (ES256/RS256, published
at `/auth/v1/.well-known/jwks.json`) on new cloud projects and on the local CLI (v2.72: ES256); older projects
sign with HS256 and the shared JWT secret, accepted only when SUPABASE_JWT_SECRET is set."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings

log = logging.getLogger("auth")

ASYMMETRIC = ("ES256", "RS256", "EdDSA")
LEEWAY_S = 30  # clock skew between Supabase and this host

# /instagram-accounts/callback: Instagram redirects the browser there, so it carries no session; its signed,
# single-use `state` is the credential (app/instagram_accounts.py).
PUBLIC_PATHS = {"/health", "/instagram-accounts/callback"}
PUBLIC_PREFIXES = ("/assets/",)  # rendered slides: the signed link is the credential (app/assets.py)
# Signed in is enough here: these are how a person without an Organization gets one.
NO_ORG_ROUTES = {("GET", "/me"), ("POST", "/orgs"), ("POST", "/invitations/accept")}
ORG_HEADER = "x-org-id"
NOT_A_MEMBER = "not a member of this Organization"

ROLES = ("viewer", "editor", "approver", "admin")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    expires_at: float  # token `exp`; long-lived responses (SSE) end here so the client reconnects with a fresh one


@dataclass(frozen=True)
class Ctx:
    user: User
    org_id: UUID | None  # None only on NO_ORG_ROUTES
    org_name: str | None
    role: str | None


class Identity(Protocol):
    def verify(self, token: str) -> User | None: ...


class SupabaseIdentity:
    """Verifies Supabase Auth access tokens. JWKS keys are cached for `ttl` seconds; a token with an unknown
    `kid` (key rotation) triggers a refetch, at most once per `min_refresh` seconds so forged kids can't make
    this host hammer Supabase. A failed refetch keeps the keys it already has."""

    def __init__(self, supabase_url: str, jwt_secret: str = "", *, ttl: float = 600, min_refresh: float = 30,
                 fetch: Callable[[str], dict] | None = None):
        self.issuer = supabase_url.rstrip("/") + "/auth/v1"
        self.jwks_url = self.issuer + "/.well-known/jwks.json"
        self._secret = jwt_secret
        self._ttl, self._min_refresh = ttl, min_refresh
        self._fetch = fetch or _fetch_json
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0  # last successful fetch
        self._tried_at = float("-inf")  # last attempt, successful or not
        self._lock = threading.Lock()

    def _refresh(self, unknown_kid: bool) -> None:
        with self._lock:
            now = time.monotonic()
            expired = now - self._fetched_at > self._ttl
            if not (expired or unknown_kid) or now - self._tried_at < self._min_refresh:
                return
            self._tried_at = now
            try:
                raw = self._fetch(self.jwks_url).get("keys", [])
                keys = {}
                for k in raw:
                    if k.get("use", "sig") != "sig" or k.get("kty") == "oct" or not k.get("kid"):
                        continue
                    try:
                        keys[k["kid"]] = jwt.PyJWK(k)
                    except jwt.PyJWTError:
                        continue  # an algorithm this host can't use; the others still work
                self._keys, self._fetched_at = keys, now
            except (httpx.HTTPError, ValueError, TypeError, AttributeError, OSError) as e:
                # network, HTTP or JSON error: keep serving with the keys we have
                log.warning("could not fetch Supabase JWKS from %s: %s", self.jwks_url, e)

    def _key(self, kid: str | None) -> jwt.PyJWK | None:
        if not kid:
            return None
        self._refresh(unknown_kid=kid not in self._keys)
        return self._keys.get(kid)

    def verify(self, token: str) -> User | None:
        try:
            header = jwt.get_unverified_header(token)
            alg = header.get("alg")
            if alg in ASYMMETRIC:
                jwk = self._key(header.get("kid"))
                if jwk is None or jwk.algorithm_name != alg:
                    return None
                key = jwk.key
            elif alg == "HS256" and self._secret:
                key = self._secret
            else:  # "none", HS256 without a configured secret, anything unexpected
                return None
            claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=self.issuer,
                                leeway=LEEWAY_S, options={"require": ["exp", "sub", "aud", "iss"]})
            if claims.get("role") != "authenticated" or claims.get("is_anonymous"):
                return None
            return User(id=UUID(str(claims["sub"])), email=str(claims.get("email") or ""),
                        expires_at=float(claims["exp"]))
        except (jwt.PyJWTError, ValueError, TypeError):
            return None


def _fetch_json(url: str) -> dict:
    r = httpx.get(url, timeout=5, follow_redirects=False)
    r.raise_for_status()
    return r.json()


_identity: tuple[tuple[str, str], SupabaseIdentity] | None = None
_identity_lock = threading.Lock()


def identity() -> Identity | None:
    """The configured Identity, or None when SUPABASE_URL is unset (then every protected route is 401)."""
    global _identity
    conf = (settings.supabase_url, settings.supabase_jwt_secret)
    if not conf[0]:
        return None
    with _identity_lock:
        if _identity is None or _identity[0] != conf:
            _identity = (conf, SupabaseIdentity(*conf))
        return _identity[1]


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _deny(status: int, detail: str) -> JSONResponse:
    headers = {"www-authenticate": "Bearer"} if status == 401 else None
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


async def require_session(request: Request, call_next):
    """HTTP middleware. Registered before CORS so CORS wraps it and a 401 still carries CORS headers."""
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    ident, token = identity(), _bearer(request)
    if ident is None:
        return _deny(401, "sign-in is not configured on this server (set SUPABASE_URL)")
    user = await asyncio.to_thread(ident.verify, token) if token else None
    if user is None:
        return _deny(401, "sign in required")
    from app import orgs  # imported here: orgs imports this module for its dependencies

    no_org_ok = (request.method, path) in NO_ORG_ROUTES
    mine = await asyncio.to_thread(orgs.memberships, user.id)
    m = mine[0] if mine else None
    if wanted := request.headers.get(ORG_HEADER, "").strip():
        try:
            wanted_id = UUID(wanted)
        except ValueError:
            wanted_id = None
        chosen = next((x for x in mine if x.org_id == wanted_id), None)
        if chosen is not None:
            m = chosen
        elif not no_org_ok:  # /me and friends fall back to the default so the Admin can recover
            return _deny(403, NOT_A_MEMBER)
    if m is None and not no_org_ok:
        return _deny(403, "no Organization yet: create one first")
    request.state.ctx = Ctx(user, m.org_id if m else None, m.org_name if m else None, m.role if m else None)
    return await call_next(request)


def signed_in(request: Request) -> Ctx:
    """Dependency: the caller's context (Organization may be None, on NO_ORG_ROUTES only)."""
    ctx = getattr(request.state, "ctx", None)
    if ctx is None:  # the middleware always sets it on protected routes; fail closed if it ever doesn't
        raise HTTPException(401, "sign in required")
    return ctx


def member(request: Request) -> Ctx:
    """Dependency: a signed-in Member of an Organization."""
    ctx = signed_in(request)
    if ctx.org_id is None:
        raise HTTPException(403, "no Organization yet: create one first")
    return ctx


def requires(min_role: str) -> Callable[[Request], Ctx]:
    """Dependency factory: a Member whose Role is at least `min_role` (viewer < editor < approver < admin)."""
    if min_role not in ROLE_RANK:
        raise ValueError(f"unknown role {min_role!r}")

    def dep(request: Request) -> Ctx:
        ctx = member(request)
        if ROLE_RANK.get(ctx.role or "", -1) < ROLE_RANK[min_role]:
            raise HTTPException(403, f"{min_role} role required")
        return ctx
    dep.min_role = min_role  # read by tests/test_roles.py
    dep.__name__ = f"requires_{min_role}"
    return dep


def at_least(role: str | None, min_role: str) -> bool:
    return ROLE_RANK.get(role or "", -1) >= ROLE_RANK[min_role]


SignedIn = Annotated[Ctx, Depends(signed_in)]
Viewer = Annotated[Ctx, Depends(requires("viewer"))]
Editor = Annotated[Ctx, Depends(requires("editor"))]
Approver = Annotated[Ctx, Depends(requires("approver"))]
Admin = Annotated[Ctx, Depends(requires("admin"))]
Member = Viewer  # any Role
