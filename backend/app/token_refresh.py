"""Instagram token refresh (#22; HARDENING PUB-3; docs/spec/v1.md pseudocode 2).

Long-lived Instagram tokens last 60 days. Once a day (03:17 KST, a DBOS schedule registered at launch) every
account whose token expires within 14 days is refreshed: Instagram swaps a valid token that is at least 24 hours old
for a new one with another 60 days. The new token replaces the old one in Supabase Vault, in the same transaction
that saves its expiry.

  refreshed      → status `connected`, new expiry, audit `instagram.token_refreshed`
  invalid (190)  → status `revoked` (Instagram revoked it, or the person changed their password or removed our app)
  already expired → status `revoked`: Instagram can't refresh an expired token
  anything else  → nothing changes; tried again tomorrow. If it is still failing with 3 days left, the account shows
                   `expiring` and the Admins get one email.

`revoked` means the account needs reconnecting: the publisher won't use it, Approve refuses it with a message that
says so, the Instagram page shows "Reconnect needed", and the Organization's Admins are emailed (app/notify.py).
The publisher calls `revoke()` too when Instagram rejects a token mid-publish.

Everything here runs on the privileged path (`store.system()`): it is background work for every Organization, and
the token never leaves this process. Each account is its own DBOS step, so one slow or failing account doesn't hold
up the rest, and a restart doesn't refresh an account twice."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dbos import DBOS, WorkflowSerializationFormat

from app import audit, instagram_api, notify, store
from app.secret_store import secrets as secret_store

log = logging.getLogger("token_refresh")

SCHEDULE_NAME = "instagram-token-refresh"
SCHEDULE = "17 3 * * *"            # every day at 03:17 (cron_timezone below): off the hour, out of business hours
SCHEDULE_TZ = "Asia/Seoul"
REFRESH_WITHIN = timedelta(days=14)
MIN_TOKEN_AGE = timedelta(hours=24)  # Instagram refuses to refresh a younger token
WARN_WITHIN = timedelta(days=3)      # still failing this close to expiry: show `expiring` and email the Admins
SYSTEM = UUID(int=0)                 # audit actor for background work (no person did it)
SYSTEM_EMAIL = "token refresh"


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(c, org_id: UUID, action: str, target: str, **detail) -> None:
    audit.insert(c, org_id, SYSTEM, SYSTEM_EMAIL, action, target, detail)


def due(now: datetime | None = None) -> list[tuple[UUID, UUID]]:
    """(org_id, account_id) of every live account whose token expires within 14 days, or whose expiry we never
    learned, soonest first."""
    now = now or _now()
    with store.system() as c:
        rows = c.execute(
            "select org_id, id from instagram_accounts where status in ('connected', 'expiring') "
            "and token_ref is not null and (token_expires_at is null or token_expires_at < %s) "
            "order by token_expires_at nulls first, id", (now + REFRESH_WITHIN,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def revoke(org_id: UUID, account_id: UUID, reason: str) -> bool:
    """Mark a live account `revoked` (needs reconnecting), audit it and email the Admins. False if it wasn't live
    (already revoked, disconnected or another Organization's), so a second call sends no second email."""
    with store.system() as c:
        row = c.execute(
            "update instagram_accounts set status = 'revoked', revoked_at = now(), revoked_reason = %s "
            "where id = %s and org_id = %s and status in ('connected', 'expiring') returning username",
            (reason[:300], account_id, org_id)).fetchone()
        if row is None:
            return False
        _audit(c, org_id, "instagram.revoked", f"@{row[0]}", account=str(account_id), reason=reason)
    log.warning("Instagram account @%s (Organization %s) needs reconnecting: %s", row[0], org_id, reason)
    notify.alert_admins(
        org_id, f"Reconnect Instagram account @{row[0]}",
        f"We can't publish to @{row[0]} any more: {reason}.\n\n"
        "Approved Decks won't be posted to it until an Admin reconnects it. Open the Instagram page and choose "
        "Connect Instagram, then sign in as @" + row[0] + ".")
    return True


def refresh_account(org_id: UUID, account_id: UUID, now: datetime | None = None) -> str:
    """Refresh one account's token. Returns what happened: refreshed, revoked, expired, failed, warned, too_new or
    skipped (no longer live, or no longer due)."""
    now = now or _now()
    with store.system() as c:
        row = c.execute(
            "select username, ig_user_id, token_ref, token_expires_at, coalesce(token_refreshed_at, connected_at) "
            "from instagram_accounts where id = %s and org_id = %s and status in ('connected', 'expiring') "
            "and token_ref is not null", (account_id, org_id)).fetchone()
        if row is None:
            return "skipped"
        username, ig_user_id, ref, expires_at, issued_at = row
        if expires_at and expires_at > now + REFRESH_WITHIN:
            return "skipped"  # refreshed (or reconnected) since the list was made
        if expires_at and expires_at <= now:
            expired = True
        else:
            expired = False
            if issued_at > now - MIN_TOKEN_AGE:
                return "too_new"
            token = secret_store.get(c, ref)
    if expired:
        revoke(org_id, account_id, "its Instagram token expired before it could be refreshed")
        return "expired"
    if not token:
        revoke(org_id, account_id, "its Instagram token is missing from the secret store")
        return "revoked"
    try:
        new = instagram_api.refresh(instagram_api.Token(token))
    except instagram_api.InstagramError as e:
        if e.token_invalid:
            revoke(org_id, account_id, "Instagram no longer accepts its token (it was revoked, the password "
                                       "changed, or our app was removed from the account)")
            return "revoked"
        return _failed(org_id, account_id, username, expires_at, str(e), now)
    new_expiry = now + timedelta(seconds=new.expires_in) if new.expires_in else None
    with store.system() as c:
        # The same token_ref: an Admin who reconnected meanwhile already stored a newer token; keep theirs.
        if not c.execute("select 1 from instagram_accounts where id = %s and org_id = %s and token_ref = %s "
                         "and status in ('connected', 'expiring') for update", (account_id, org_id, ref)).fetchone():
            return "skipped"
        secret_store.put(c, f"instagram/{org_id}/{ig_user_id}", new.access_token,
                         f"Instagram access token for @{username}")
        c.execute("update instagram_accounts set token_expires_at = %s, token_refreshed_at = %s, "
                  "status = 'connected', token_alerted_at = null where id = %s", (new_expiry, now, account_id))
        _audit(c, org_id, "instagram.token_refreshed", f"@{username}", account=str(account_id),
               token_expires_at=new_expiry)
    log.info("refreshed the Instagram token of @%s (Organization %s), now valid until %s", username, org_id,
             new_expiry)
    return "refreshed"


def _failed(org_id: UUID, account_id: UUID, username: str, expires_at: datetime | None, error: str,
            now: datetime) -> str:
    """A refresh that may work tomorrow (rate limit, timeout, Instagram down). Close to expiry, say so once."""
    log.warning("could not refresh the Instagram token of @%s (Organization %s): %s", username, org_id, error)
    if not expires_at or expires_at > now + WARN_WITHIN:
        return "failed"
    with store.system() as c:
        row = c.execute(
            "update instagram_accounts set status = 'expiring', token_alerted_at = %s where id = %s and org_id = %s "
            "and status in ('connected', 'expiring') and token_alerted_at is null returning id",
            (now, account_id, org_id)).fetchone()
        if row is None:
            return "failed"  # already warned
        _audit(c, org_id, "instagram.token_expiring", f"@{username}", account=str(account_id), error=error,
               token_expires_at=expires_at)
    notify.alert_admins(
        org_id, f"Instagram account @{username} expires soon",
        f"We couldn't refresh the Instagram token of @{username} ({error}). It expires on "
        f"{expires_at:%Y-%m-%d %H:%M} UTC; we'll keep trying daily. If it still fails, reconnect the account before "
        "then so approved Decks can be posted.")
    return "warned"


# ---------------------------------------------------------------- the daily DBOS schedule

def _refresh_step(org: str, account: str) -> str:
    try:
        return refresh_account(UUID(org), UUID(account))
    except Exception:  # one account's surprise must not stop the others; tomorrow tries again
        log.exception("token refresh crashed for account %s (Organization %s)", account, org)
        return "error"


@DBOS.workflow(name="wok.token_refresh", serialization_type=WorkflowSerializationFormat.PORTABLE)
async def refresh_workflow(scheduled_at=None, context=None) -> dict[str, int]:
    """Refresh every due account, one checkpointed step each. Arguments come from the DBOS scheduler."""
    accounts = await DBOS.run_step_async({"name": "due"}, lambda: [[str(o), str(a)] for o, a in due()])
    counts: dict[str, int] = {}
    for org, account in accounts:
        outcome = await DBOS.run_step_async({"name": "refresh"}, _refresh_step, org, account)
        counts[outcome] = counts.get(outcome, 0) + 1
    if accounts:
        log.info("token refresh: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return counts


def register_schedule() -> None:
    """Create or update the daily schedule (idempotent; called from `durable.launch`)."""
    DBOS.apply_schedules([{"schedule_name": SCHEDULE_NAME, "workflow_fn": refresh_workflow, "schedule": SCHEDULE,
                           "cron_timezone": SCHEDULE_TZ}])
