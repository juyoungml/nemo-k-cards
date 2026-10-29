"""The audit log (docs/spec/v1.md `audit_events`): who approved, overrode, rejected, edited or changed Members, and
when. Append-only: the API role may insert and (Admins only) read, and a trigger refuses every update and delete,
the table owner's included (supabase/migrations/20261029150000_members_roles_audit.sql).

Actions written today:
  org.created · member.invited · invitation.revoked · member.joined · member.role_changed · member.removed ·
  job.created · deck.edited · deck.approved · deck.overridden · deck.rejected ·
  deck.approve_refused (the Safeguard blocked the Deck, or it has no verdict) · safeguard.blocked (written by the
  host for the Member who started the Job, or approved it when the publish-time re-check blocked) ·
  safeguard.edit_blocked (a caption edit refused at Approve or before upload)"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel

from app import store
from app.auth import Ctx


class AuditEvent(BaseModel):
    id: int
    actor: UUID
    actor_email: str | None
    action: str
    target: str | None
    detail: dict[str, Any]
    at: datetime


class Entry(BaseModel):
    """An event to write in the same transaction as the change it describes (see `store.save_job(audit=)`)."""
    actor: UUID
    actor_email: str = ""
    action: str
    target: str | None = None
    detail: dict[str, Any] = {}


def entry(ctx: Ctx, action: str, target: str | None = None, **detail: Any) -> Entry:
    return Entry(actor=ctx.user.id, actor_email=ctx.user.email, action=action, target=target, detail=detail)


def insert(c: Connection, org_id: UUID, actor: UUID, actor_email: str, action: str, target: str | None,
           detail: dict[str, Any]) -> None:
    """Write one event on an open transaction. Under `store.tenant` the policy also checks that `org_id` and
    `actor` are the transaction's own Organization and caller."""
    c.execute("insert into audit_events (org_id, actor, actor_email, action, target, detail) "
              "values (%s, %s, %s, %s, %s, %s)",
              (org_id, actor, actor_email or None, action, target, json.dumps(detail, default=str)))


def write(c: Connection, org_id: UUID, e: Entry) -> None:
    insert(c, org_id, e.actor, e.actor_email, e.action, e.target, e.detail)


def record(ctx: Ctx, action: str, target: str | None = None, **detail: Any) -> None:
    """Write one event in its own transaction, as the caller."""
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        write(c, ctx.org_id, entry(ctx, action, target, **detail))


def events(ctx: Ctx, limit: int = 100, before: int | None = None) -> list[AuditEvent]:
    """Newest first. RLS shows rows only to an Admin of the caller's Organization."""
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        rows = c.execute("select id, actor, actor_email, action, target, detail, at from audit_events "
                         "where (%s::bigint is null or id < %s) order by id desc limit %s",
                         (before, before, limit)).fetchall()
    return [AuditEvent(id=r[0], actor=r[1], actor_email=r[2], action=r[3], target=r[4], detail=r[5], at=r[6])
            for r in rows]
