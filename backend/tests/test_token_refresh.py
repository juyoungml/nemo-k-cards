"""Instagram token refresh (#22): due tokens are refreshed daily, a revoked or expired one marks the account and
emails the Admins, transient failures are retried (with one warning close to expiry), and posting to an account
that needs reconnecting is refused with a message that says what to do. Instagram is faked with
httpx.MockTransport; mail goes to `notify.transport`."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from dbos import DBOS
from factories import connect_account

from app import instagram_api, notify, store, token_refresh
from app.secret_store import secrets as secret_store

NOW = datetime(2026, 10, 30, 3, 17, tzinfo=UTC)


class FakeInstagram:
    """GET /refresh_access_token: answers by token (`good`: a new token; `revoked`: code 190; `down`: HTTP 500)."""

    def __init__(self):
        self.calls: list[str] = []
        self.answers: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/refresh_access_token"
        assert request.url.params["grant_type"] == "ig_refresh_token"
        token = request.url.params["access_token"]
        self.calls.append(token)
        kind = self.answers.get(token, "good")
        if kind == "revoked":
            return httpx.Response(400, json={"error": {"message": "Error validating access token",
                                                       "type": "OAuthException", "code": 190}})
        if kind == "down":
            return httpx.Response(500, json={"error": {"message": "unknown", "type": "OAuthException", "code": 2}})
        if kind == "limited":
            return httpx.Response(400, json={"error": {"message": "limit", "type": "OAuthException", "code": 4}})
        return httpx.Response(200, json={"access_token": f"{token}-r", "token_type": "bearer",
                                         "expires_in": 60 * 86400})


@pytest.fixture
def ig(monkeypatch):
    fake = FakeInstagram()
    monkeypatch.setattr(instagram_api, "transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def org(sign_in):
    """An Organization whose Admin has an email address (alerts go to Admins)."""
    return sign_in(org="Jeju tourism").org_id


@pytest.fixture
def mail(monkeypatch):
    sent: list[notify.Email] = []
    monkeypatch.setattr(notify, "transport", sent.append)
    return sent


def _account(org_id, *, expires_in: timedelta | None, age=timedelta(days=50), username="jejutour"):
    acct = connect_account(org_id, username)
    with store.system() as c:
        c.execute("update instagram_accounts set token_expires_at = %s, connected_at = %s where id = %s",
                  (NOW + expires_in if expires_in is not None else None, NOW - age, acct["id"]))
    return acct


def _row(acct) -> dict:
    with store.system() as c:
        r = c.execute("select status, token_expires_at, token_refreshed_at, revoked_reason, token_ref "
                      "from instagram_accounts where id = %s", (acct["id"],)).fetchone()
        token = secret_store.get(c, r[4]) if r[4] else None
    return {"status": r[0], "expires": r[1], "refreshed": r[2], "reason": r[3], "token": token}


def _audit(org_id) -> list[tuple[str, dict]]:
    with store.system() as c:
        return [(r[0], r[1]) for r in c.execute("select action, detail from audit_events where org_id = %s "
                                                "and action like 'instagram.%%' order by id", (org_id,)).fetchall()]


# ---------------------------------------------------------------- which accounts are due

def test_due_lists_tokens_within_14_days_and_unknown_expiries(org):
    soon = _account(org, expires_in=timedelta(days=13), username="soon")
    later = _account(org, expires_in=timedelta(days=30), username="later")
    unknown = _account(org, expires_in=None, username="unknown")
    gone = _account(org, expires_in=timedelta(days=2), username="gone")
    token_refresh.revoke(org, gone["id"], "test")
    ids = [a for o, a in token_refresh.due(NOW) if o == org]
    assert ids == [unknown["id"], soon["id"]] and later["id"] not in ids


# ---------------------------------------------------------------- refresh

def test_a_due_token_is_refreshed_into_vault_with_a_new_expiry(org, ig, mail):
    acct = _account(org, expires_in=timedelta(days=13))
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "refreshed"
    row = _row(acct)
    assert row["token"] == acct["token"] + "-r" and row["status"] == "connected"
    assert row["expires"] == NOW + timedelta(days=60) and row["refreshed"] == NOW
    [(action, detail)] = _audit(org)
    assert action == "instagram.token_refreshed" and acct["token"] not in str(detail)
    assert mail == []
    # Refreshed: no longer due, so the next day's run leaves it alone.
    assert acct["id"] not in [a for _, a in token_refresh.due(NOW + timedelta(days=1))]


def test_a_token_younger_than_a_day_is_not_refreshed(org, ig):
    acct = _account(org, expires_in=timedelta(days=13), age=timedelta(hours=5))
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "too_new" and ig.calls == []


def test_a_revoked_token_marks_the_account_and_emails_the_admins_once(org, ig, mail):
    acct = _account(org, expires_in=timedelta(days=10))
    ig.answers[acct["token"]] = "revoked"
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "revoked"
    row = _row(acct)
    assert row["status"] == "revoked" and "no longer accepts" in row["reason"]
    [email] = mail
    assert email.subject == "Reconnect Instagram account @jejutour" and email.to  # the Org's Admin
    assert "/instagram" in email.text and acct["token"] not in email.text
    assert [a for a, _ in _audit(org)] == ["instagram.revoked"]
    # Already revoked: not refreshed again, no second email.
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "skipped"
    assert token_refresh.revoke(org, acct["id"], "again") is False and len(mail) == 1


def test_an_expired_token_is_revoked_without_asking_instagram(org, ig, mail):
    acct = _account(org, expires_in=-timedelta(hours=1))
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "expired"
    assert ig.calls == [] and _row(acct)["status"] == "revoked" and len(mail) == 1


@pytest.mark.parametrize("kind", ["down", "limited"])
def test_a_transient_failure_retries_tomorrow_and_warns_once_near_expiry(org, ig, mail, kind):
    acct = _account(org, expires_in=timedelta(days=10))
    ig.answers[acct["token"]] = kind
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "failed"
    assert _row(acct)["status"] == "connected" and mail == []
    # Two days left and still failing: shown as expiring, Admins emailed once.
    later = NOW + timedelta(days=8)
    assert token_refresh.refresh_account(org, acct["id"], later) == "warned"
    assert _row(acct)["status"] == "expiring" and len(mail) == 1 and "expires soon" in mail[0].subject
    assert token_refresh.refresh_account(org, acct["id"], later + timedelta(hours=1)) == "failed" and len(mail) == 1
    # Instagram recovers: refreshed, back to connected.
    ig.answers.pop(acct["token"])
    assert token_refresh.refresh_account(org, acct["id"], later + timedelta(days=1)) == "refreshed"
    assert _row(acct)["status"] == "connected"


def test_a_reconnect_during_the_refresh_wins(org, ig, monkeypatch):
    acct = _account(org, expires_in=timedelta(days=5))
    real = instagram_api.refresh

    def reconnect_meanwhile(token):
        with store.system() as c:  # an Admin reconnects: a new token_ref
            ref = secret_store.put(c, f"instagram/{org}/other", "brand-new", "")
            c.execute("update instagram_accounts set token_ref = %s where id = %s", (ref, acct["id"]))
        return real(token)
    monkeypatch.setattr(instagram_api, "refresh", reconnect_meanwhile)
    assert token_refresh.refresh_account(org, acct["id"], NOW) == "skipped"
    assert _row(acct)["token"] == "brand-new"


def test_no_smtp_means_logged_not_lost(org, caplog):
    sent = notify.send(notify.Email(("a@example.com",), "Reconnect Instagram account @x", "body"))
    assert sent is False and "SMTP_URL is not set" in caplog.text


# ---------------------------------------------------------------- the daily workflow

async def test_the_daily_workflow_refreshes_every_due_account(org, ig, mail, monkeypatch):
    monkeypatch.setattr(token_refresh, "_now", lambda: NOW)
    good = _account(org, expires_in=timedelta(days=12), username="good")
    bad = _account(org, expires_in=timedelta(days=12), username="bad")
    ig.answers[bad["token"]] = "revoked"
    counts = await token_refresh.refresh_workflow(NOW, None)
    assert counts.get("refreshed", 0) >= 1 and counts.get("revoked", 0) >= 1
    assert _row(good)["status"] == "connected" and _row(bad)["status"] == "revoked"


def test_the_schedule_is_registered_daily_in_kst():
    [s] = [s for s in DBOS.list_schedules() if s["schedule_name"] == token_refresh.SCHEDULE_NAME]
    assert s["schedule"] == token_refresh.SCHEDULE and s["cron_timezone"] == "Asia/Seoul"


# ---------------------------------------------------------------- approving to an account that needs reconnecting

def test_approve_to_a_revoked_account_says_reconnect(sign_in, monkeypatch):
    from factories import allowed_job

    from app.config import settings
    monkeypatch.setattr(settings, "publish_mode", "dryrun")
    a = sign_in(org="A")
    acct = connect_account(a.org_id)
    token_refresh.revoke(a.org_id, acct["id"], "test")
    job = store.save_job(a.org_id, allowed_job())
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "mode": "dryrun"})
    assert r.status_code == 409 and "needs reconnecting" in r.json()["detail"] and "Instagram page" in r.json()["detail"]
    listed = a.get("/instagram-accounts").json()
    assert listed[0]["status"] == "revoked" and listed[0]["revoked_reason"] == "test"
