"""Members, Roles and invitations (#14; GLOSSARY: Member, Role). Admins invite people by email with a Role,
change Roles and remove Members; an Organization always keeps at least one Admin.

Inviting: the Admin's call creates an `invitations` row (only the SHA-256 of its one-time token is stored) and
asks Supabase Auth to email the link `ADMIN_URL/invite?token=…`:
  * a person with no account gets Supabase's invite email (`POST /auth/v1/invite`), which creates the account and
    signs them in on the way to the link;
  * a person who already has one gets a sign-in link instead (`POST /auth/v1/otp`, never creating an account).
The Admin always gets the link back too, so an invitation still works when email is not configured (no
SUPABASE_SERVICE_KEY) or Supabase's mail limit is hit: they can send it themselves.

Accepting needs both the token and a signed-in account whose email is the invited address, so neither a
forwarded link nor an account registered under someone else's address is enough alone. It runs as the
connecting role (`store.system`), like creating an Organization: the person is not a Member yet, and the token is
what names the Organization, so no `app.org_id` exists beforehand for a policy to scope the write to. Every
other step here runs as `wok_api` under RLS, whose policies allow these writes only to an Admin
(supabase/migrations/20261029150000_members_roles_audit.sql)."""

import hashlib
import logging
import re
import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection
from pydantic import BaseModel, Field, field_validator

from app import audit, store
from app.auth import Admin, Ctx, SignedIn, Viewer
from app.config import settings
from app.orgs import OrgOut

log = logging.getLogger("members")

Role = Literal["viewer", "editor", "approver", "admin"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LAST_ADMIN = "an Organization needs at least one Admin: make someone else Admin first"


class MemberOut(BaseModel):
    user_id: UUID
    email: str | None
    role: Role
    invited_by: UUID | None
    joined_at: datetime


class InvitationOut(BaseModel):
    id: UUID
    email: str
    role: Role
    invited_by: UUID
    created_at: datetime
    expires_at: datetime


class InvitationCreated(InvitationOut):
    invite_url: str = Field(description="The link to send; it works only for the invited email address")
    email_sent: bool


class InviteRequest(BaseModel):
    email: str = Field(max_length=320)
    role: Role

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("not an email address")
        return v


class RoleChange(BaseModel):
    role: Role


class AcceptRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_url(token: str) -> str:
    return f"{settings.admin_url.rstrip('/')}/invite?token={token}"


# ---------------------------------------------------------------- email (Supabase Auth)

def send_invite_email(email: str, link: str) -> bool:
    """Ask Supabase Auth to email `link`. False when email isn't configured or Supabase refused (the Admin then
    shares the link by hand). Supabase only redirects to URLs on the project's redirect allow list."""
    key = settings.supabase_service_key
    if not (settings.supabase_url and key):
        return False
    headers = {"apikey": key}
    if not key.startswith("sb_"):  # a legacy service_role JWT also goes in Authorization; sb_secret_ keys don't
        headers["authorization"] = f"Bearer {key}"
    base = settings.supabase_url.rstrip("/") + "/auth/v1"
    try:
        with httpx.Client(timeout=10, headers=headers, follow_redirects=False) as client:
            r = client.post(f"{base}/invite", params={"redirect_to": link}, json={"email": email})
            if r.status_code == 422 and r.json().get("error_code") in ("email_exists", "user_already_exists"):
                r = client.post(f"{base}/otp", params={"redirect_to": link},
                                json={"email": email, "create_user": False})
        if not r.is_success:
            log.warning("Supabase did not send the invitation email: %s %s", r.status_code, r.text[:200])
        return r.is_success
    except (httpx.HTTPError, ValueError) as e:
        log.warning("could not reach Supabase to send the invitation email: %s", e)
        return False


# ---------------------------------------------------------------- store

def _lock_members(c: Connection, org_id: UUID) -> None:
    """Serialise Member changes per Organization, so two Admins demoting each other at once can't both pass the
    last-Admin check."""
    c.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"members:{org_id}",))


# Every query on `members` below names the Organization: under `wok_api` the read policy also shows the caller's
# own memberships in their other Organizations (sign-in needs that), so RLS alone would not narrow it to one.

def _admins(c: Connection, org_id: UUID) -> int:
    return c.execute("select count(*) from members where org_id = %s and role = 'admin'", (org_id,)).fetchone()[0]


def list_members(ctx: Ctx) -> list[MemberOut]:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        rows = c.execute("select user_id, email, role, invited_by, created_at from members where org_id = %s "
                         "order by created_at, user_id", (ctx.org_id,)).fetchall()
    return [MemberOut(user_id=r[0], email=r[1], role=r[2], invited_by=r[3], joined_at=r[4]) for r in rows]


_INV_COLS = "id, email, role, invited_by, created_at, expires_at"


def _invitation(r) -> InvitationOut:
    return InvitationOut(id=r[0], email=r[1], role=r[2], invited_by=r[3], created_at=r[4], expires_at=r[5])


def list_invitations(ctx: Ctx) -> list[InvitationOut]:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        rows = c.execute(f"select {_INV_COLS} from invitations where accepted_at is null and revoked_at is null "
                         "and expires_at > now() order by created_at desc").fetchall()
    return [_invitation(r) for r in rows]


def invite(ctx: Ctx, email: str, role: str) -> tuple[InvitationOut, str]:
    """A new invitation (replacing any open one for the same address) and its one-time token."""
    token = secrets.token_urlsafe(32)
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        _lock_members(c, ctx.org_id)
        if c.execute("select 1 from members where org_id = %s and lower(email) = %s",
                     (ctx.org_id, email)).fetchone():
            raise HTTPException(409, "that person is already a Member")
        c.execute("update invitations set revoked_at = now() where email = %s "
                  "and accepted_at is null and revoked_at is null", (email,))
        row = c.execute(f"insert into invitations (email, role, token_hash, invited_by) values (%s, %s, %s, %s) "
                        f"returning {_INV_COLS}", (email, role, _hash(token), ctx.user.id)).fetchone()
        inv = _invitation(row)
        audit.write(c, ctx.org_id, audit.entry(ctx, "member.invited", email, role=role, invitation=str(inv.id)))
    return inv, token


def revoke(ctx: Ctx, invitation_id: UUID) -> None:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        row = c.execute("update invitations set revoked_at = now() where id = %s "
                        "and accepted_at is null and revoked_at is null returning email", (invitation_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "invitation not found")
        audit.write(c, ctx.org_id, audit.entry(ctx, "invitation.revoked", row[0], invitation=str(invitation_id)))


def change_role(ctx: Ctx, user_id: UUID, role: str) -> MemberOut:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        _lock_members(c, ctx.org_id)
        cur = c.execute("select role, email from members where org_id = %s and user_id = %s",
                        (ctx.org_id, user_id)).fetchone()
        if cur is None:
            raise HTTPException(404, "member not found")
        if cur[0] == "admin" and role != "admin" and _admins(c, ctx.org_id) <= 1:
            raise HTTPException(409, LAST_ADMIN)
        row = c.execute("update members set role = %s where org_id = %s and user_id = %s "
                        "returning user_id, email, role, invited_by, created_at",
                        (role, ctx.org_id, user_id)).fetchone()
        if cur[0] != role:
            audit.write(c, ctx.org_id, audit.entry(ctx, "member.role_changed", cur[1] or str(user_id),
                                                   user_id=str(user_id), before=cur[0], after=role))
    return MemberOut(user_id=row[0], email=row[1], role=row[2], invited_by=row[3], joined_at=row[4])


def remove(ctx: Ctx, user_id: UUID) -> None:
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        _lock_members(c, ctx.org_id)
        cur = c.execute("select role, email from members where org_id = %s and user_id = %s",
                        (ctx.org_id, user_id)).fetchone()
        if cur is None:
            raise HTTPException(404, "member not found")
        if cur[0] == "admin" and _admins(c, ctx.org_id) <= 1:
            raise HTTPException(409, LAST_ADMIN)
        # Written first: once the caller removes themselves, the policy no longer counts them as a Member.
        audit.write(c, ctx.org_id, audit.entry(ctx, "member.removed", cur[1] or str(user_id),
                                               user_id=str(user_id), role=cur[0]))
        c.execute("delete from members where org_id = %s and user_id = %s", (ctx.org_id, user_id))


class InvitationError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def accept(user_id: UUID, email: str, token: str) -> OrgOut:
    """Join the Organization a valid, open invitation for this person's email address names (privileged step,
    see the module docstring). Accepting twice, or as someone who is already a Member, keeps their Role."""
    email = email.strip().lower()
    with store.system() as c:
        row = c.execute("select i.id, i.org_id, i.email, i.role, i.invited_by, i.expires_at < now(), "
                        "i.accepted_at is not null, i.accepted_by, i.revoked_at is not null, o.name "
                        "from invitations i join orgs o on o.id = i.org_id where i.token_hash = %s for update of i",
                        (_hash(token),)).fetchone()
        if row is None:
            raise InvitationError(404, "this invitation link is not valid")
        inv_id, org_id, inv_email, role, invited_by, expired, accepted, accepted_by, revoked, org_name = row
        if accepted and accepted_by == user_id:
            mine = c.execute("select role from members where org_id = %s and user_id = %s",
                             (org_id, user_id)).fetchone()
            if mine:
                return OrgOut(id=org_id, name=org_name, role=mine[0])
        if accepted or revoked:
            raise InvitationError(410, "this invitation was already used or withdrawn: ask for a new one")
        if expired:
            raise InvitationError(410, "this invitation has expired: ask for a new one")
        if not email or email != inv_email:
            raise InvitationError(403, "this invitation was sent to a different email address: "
                                       "sign in with that address")
        _lock_members(c, org_id)
        existing = c.execute("select role from members where org_id = %s and user_id = %s",
                             (org_id, user_id)).fetchone()
        if existing is None:
            c.execute("insert into members (org_id, user_id, role, invited_by, email) values (%s, %s, %s, %s, %s)",
                      (org_id, user_id, role, invited_by, email))
        c.execute("update invitations set accepted_at = now(), accepted_by = %s where id = %s", (user_id, inv_id))
        audit.insert(c, org_id, user_id, email, "member.joined", email,
                     {"role": existing[0] if existing else role, "invitation": str(inv_id),
                      "invited_by": str(invited_by)})
    return OrgOut(id=org_id, name=org_name, role=existing[0] if existing else role)


# ---------------------------------------------------------------- routes

router = APIRouter()


@router.get("/orgs/current/members", response_model=list[MemberOut])
def members(ctx: Viewer) -> list[MemberOut]:
    return list_members(ctx)


@router.post("/orgs/current/members", response_model=InvitationCreated, status_code=201)
def create_invitation(req: InviteRequest, ctx: Admin) -> InvitationCreated:
    inv, token = invite(ctx, req.email, req.role)
    link = invite_url(token)
    return InvitationCreated(**inv.model_dump(), invite_url=link, email_sent=send_invite_email(req.email, link))


@router.patch("/orgs/current/members/{user_id}", response_model=MemberOut)
def patch_member(user_id: UUID, req: RoleChange, ctx: Admin) -> MemberOut:
    try:
        return change_role(ctx, user_id, req.role)
    except psycopg.errors.CheckViolation:  # the database's last-Admin backstop
        raise HTTPException(409, LAST_ADMIN) from None


@router.delete("/orgs/current/members/{user_id}", status_code=204)
def delete_member(user_id: UUID, ctx: Admin) -> None:
    try:
        remove(ctx, user_id)
    except psycopg.errors.CheckViolation:
        raise HTTPException(409, LAST_ADMIN) from None


@router.get("/orgs/current/invitations", response_model=list[InvitationOut])
def invitations(ctx: Admin) -> list[InvitationOut]:
    return list_invitations(ctx)


@router.delete("/orgs/current/invitations/{invitation_id}", status_code=204)
def delete_invitation(invitation_id: UUID, ctx: Admin) -> None:
    revoke(ctx, invitation_id)


@router.post("/invitations/accept", response_model=OrgOut)
def accept_invitation(req: AcceptRequest, ctx: SignedIn) -> OrgOut:
    """Works without an Organization: an invited person may have none yet."""
    try:
        return accept(ctx.user.id, ctx.user.email, req.token)
    except InvitationError as e:
        raise HTTPException(e.status, e.detail) from None


@router.get("/audit", response_model=list[audit.AuditEvent])
def audit_log(ctx: Admin, limit: int = Query(100, ge=1, le=500),
              before: int | None = Query(None, description="Page back: events older than this id")) -> list:
    return audit.events(ctx, limit, before)
