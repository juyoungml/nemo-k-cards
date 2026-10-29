"""Posts (#21; GLOSSARY "Post"): the record of one publication of an approved Deck to one Instagram account.

The `posts` table (supabase/migrations/*_posts.sql) is the source of truth for a publish; the DBOS workflow in
app/pipeline/publishing.py only drives it. Every state change goes through `transition`, which:
  * runs under the Post's Organization (RLS), as the Approver, so the audit event can be written as them;
  * applies only if the Post is still in one of the expected states (a person resolving it by hand and the
    reconciler can't both settle it);
  * moves the Job's status with it in the same transaction (PUBLISHING while a Post is open, PUBLISHED once one
    is live, back to READY_FOR_REVIEW when nothing was posted), so the Admin never sees the two disagree.

Routes: GET /posts (?job_id=), GET /posts/{id}, POST /posts/{id}/resolve."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection, errors
from psycopg.types.json import Jsonb

from app import audit, store
from app.auth import Approver, Viewer
from app.schemas import Job, JobStatus, Post, PostProgress, ResolvePostRequest

log = logging.getLogger("posts")

_COLS = ("id, job_id, deck_version, instagram_account_id, username, mode, state, caption, creation_id, media_id, "
         "permalink, attempts, last_error, progress, approved_by_email, created_at, updated_at, "
         "publishing_started_at, unknown_since, next_check_at, alerted_at, published_at, finished_at, resolved_by, "
         "legacy")
_NAMES = [c.strip() for c in _COLS.split(",")]


def _post(row: tuple) -> Post:
    d = dict(zip(_NAMES, row, strict=True))
    d["handle"] = f"@{d.pop('username')}" if d.get("username") else None
    return Post.model_validate(d)


class Taken(Exception):
    """A unique index refused the Post: one is already open for the Job, or this Deck version is already on the
    account. `open` says which."""

    def __init__(self, open_: bool):
        self.open = open_


def insert(c: Connection, *, job_id: str, mode: str, approved_by: UUID, approved_by_email: str,
           account_id: UUID | None, ig_user_id: str | None, username: str | None, caption: str | None,
           deck_version: int | None = None) -> Post:
    """A new `queued` Post, on the caller's open tenant transaction (with the approval's audit event)."""
    try:
        with c.transaction():  # a savepoint: a refused insert leaves the outer transaction usable
            row = c.execute(
                "insert into posts (job_id, mode, approved_by, approved_by_email, instagram_account_id, ig_user_id, "
                "username, caption, deck_version, progress) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"returning {_COLS}",
                (job_id, mode, approved_by, approved_by_email or None, account_id, ig_user_id, username, caption,
                 deck_version, Jsonb({"step": "upload", "label": "Approved, waiting for the publisher"}))).fetchone()
    except errors.UniqueViolation as e:
        raise Taken("posts_one_open_per_job" in str(e.diag.constraint_name or "")) from None
    return _post(row)


def get(org_id: UUID, post_id: UUID) -> Post | None:
    with store.tenant(org_id) as c:
        row = c.execute(f"select {_COLS} from posts where id = %s", (post_id,)).fetchone()
    return _post(row) if row else None


def raw(org_id: UUID, post_id: UUID) -> dict | None:
    """Every column, for the publisher (the approver, the account, the container's age)."""
    with store.tenant(org_id) as c:
        cur = c.execute("select * from posts where id = %s", (post_id,))
        row = cur.fetchone()
        return dict(zip([d.name for d in cur.description], row, strict=True)) if row else None


def list_posts(org_id: UUID, job_id: str | None = None, limit: int = 50) -> list[Post]:
    with store.tenant(org_id) as c:
        rows = c.execute(f"select {_COLS} from posts where (%s::text is null or job_id = %s) "
                         "order by created_at desc, id desc limit %s", (job_id, job_id, limit)).fetchall()
    return [_post(r) for r in rows]


def open_posts() -> list[tuple[UUID, UUID, str, int]]:
    """Every Organization's unfinished Posts (org, id, state, runs), for restart recovery (privileged, read only)."""
    with store.system() as c:
        return c.execute("select org_id, id, state, runs from posts where finished_at is null and not legacy "
                         "order by created_at").fetchall()


def published_since(org_id: UUID, since: datetime) -> int:
    with store.tenant(org_id) as c:
        return c.execute("select count(*) from posts where state = 'published' and mode = 'graph' "
                         "and published_at >= %s", (since,)).fetchone()[0]


# Job status that follows each Post state (None: leave it).
JOB_STATUS = {"queued": JobStatus.PUBLISHING, "container_ready": JobStatus.PUBLISHING,
              "publishing": JobStatus.PUBLISHING, "unknown": JobStatus.PUBLISHING,
              "published": JobStatus.PUBLISHED, "failed": JobStatus.READY_FOR_REVIEW}


def transition(org_id: UUID, post_id: UUID, expect: set[str], *, actor: UUID | None = None,
               job_status: JobStatus | None = None, job_update=None, audit_action: str | None = None,
               audit_detail: dict[str, Any] | None = None, **fields: Any) -> Post | None:
    """Set `fields` on the Post if its state is one of `expect`; None if it wasn't (someone else moved it).
    `state` in `fields` also moves the Job (JOB_STATUS, or `job_status`); `job_update(job)` may change the Job
    further in the same transaction. An audit event is written as `actor` (default: the Approver) when given; if
    RLS refuses it (that Member has left the Organization) the change still lands and the miss is logged."""
    fields = {**fields, "updated_at": datetime.now(UTC)}
    if "progress" in fields and isinstance(fields["progress"], PostProgress):
        fields["progress"] = Jsonb(fields["progress"].model_dump())
    elif "progress" in fields and isinstance(fields["progress"], dict):
        fields["progress"] = Jsonb(fields["progress"])
    sets = ", ".join(f"{k} = %s" for k in fields)
    with store.tenant(org_id) as c:
        who = c.execute("select approved_by, approved_by_email from posts where id = %s", (post_id,)).fetchone()
        if who is None:
            return None
        actor = actor or who[0]
        c.execute("select set_config('app.user_id', %s, true)", (str(actor),))
        row = c.execute(f"update posts set {sets} where id = %s and state = any(%s) returning {_COLS}",
                        (*fields.values(), post_id, list(expect))).fetchone()
        if row is None:
            return None
        post = _post(row)
        status = job_status or (JOB_STATUS.get(fields["state"]) if "state" in fields else None)
        if status or job_update:
            jrow = c.execute("select doc::text from jobs where id = %s", (post.job_id,)).fetchone()
            job = Job.model_validate_json(jrow[0]) if jrow else None
            if job:
                if status:
                    job.status = status
                if job_update:
                    job_update(job)
                c.execute("update jobs set doc = %s where id = %s", (store._json(job), job.id))
        if audit_action:
            email = who[1] if actor == who[0] else None
            try:
                with c.transaction():
                    audit.insert(c, org_id, actor, email or "", audit_action, post.job_id,
                                 {"post": str(post.id), "state": post.state, "mode": post.mode,
                                  "account": post.handle, **(audit_detail or {})})
            except errors.InsufficientPrivilege:
                log.warning("audit %s for post %s not written: %s is no longer a Member", audit_action, post_id,
                            actor)
    return post


# ---------------------------------------------------------------- routes

router = APIRouter()


@router.get("/posts", response_model=list[Post])
def posts(ctx: Viewer, job_id: str | None = Query(None, max_length=64),
          limit: int = Query(50, ge=1, le=200)) -> list[Post]:
    return list_posts(ctx.org_id, job_id, limit)


@router.get("/posts/{post_id}", response_model=Post)
def post(post_id: UUID, ctx: Viewer) -> Post:
    if not (p := get(ctx.org_id, post_id)):
        raise HTTPException(404, "post not found")
    return p


@router.post("/posts/{post_id}/resolve", response_model=Post)
def resolve(post_id: UUID, req: ResolvePostRequest, ctx: Approver) -> Post:
    """For an `unknown` Post: "I checked Instagram: it is posted / it is not posted" (logged). "Not posted" is
    accepted only after the reconciler has alerted (30 minutes) or stopped checking: before then it may still find
    the post, and a premature "not posted" invites a duplicate."""
    p = get(ctx.org_id, post_id)
    if not p:
        raise HTTPException(404, "post not found")
    if p.state != "unknown":
        raise HTTPException(409, f"post is {p.state}; only an unknown post can be resolved by hand")
    if not req.posted and not (p.alerted_at or p.next_check_at is None):
        raise HTTPException(409, "still checking Instagram; if it hasn't settled after 30 minutes you can mark it "
                                 "not posted")
    now = datetime.now(UTC)
    if req.posted:
        fields = {"state": "published", "permalink": req.permalink or p.permalink, "published_at": now,
                  "finished_at": now, "next_check_at": None, "resolved_by": ctx.user.id,
                  "progress": PostProgress(step="done", label="Marked as posted after checking Instagram",
                                           percent=100)}
    else:
        fields = {"state": "failed", "finished_at": now, "next_check_at": None, "resolved_by": ctx.user.id,
                  "last_error": (p.last_error or "") + " · marked not posted after checking Instagram",
                  "progress": PostProgress(step="failed", label="Marked as not posted after checking Instagram")}
    out = transition(ctx.org_id, post_id, {"unknown"}, actor=ctx.user.id, audit_action="post.resolved",
                     audit_detail={"posted": req.posted, "permalink": req.permalink, "by": "person"},
                     **fields)
    if out is None:
        raise HTTPException(409, "the post changed meanwhile; reload")
    log.info("post %s resolved by hand: %s", post_id, "posted" if req.posted else "not posted")
    return out
