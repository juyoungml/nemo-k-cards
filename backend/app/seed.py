"""Dashboard + policy seed data (BACKEND §11). Instagram numbers come from the Organization's own connected account
(#21: its token from Vault, read on the privileged path, sent in a header); the rest is seed."""

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from app import instagram_accounts, posts, store
from app.config import settings
from app.schemas import Job, JobStatus, Metric, PolicyEvent

CACHE_S = 300
_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
transport: httpx.BaseTransport | None = None  # tests fake Instagram here


def _account(org_id: UUID) -> tuple[str, str, str] | None:
    """(account id, username, token) of the Organization's first live Instagram account, or None."""
    with store.system() as c:
        row = c.execute("select id, username from instagram_accounts where org_id = %s "
                        "and status in ('connected', 'expiring') order by connected_at, id limit 1",
                        (org_id,)).fetchone()
        token = instagram_accounts.access_token(c, org_id, row[0]) if row else None
    return (str(row[0]), row[1], token) if row and token else None


def _cached(key: tuple[str, str], fetch) -> dict | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        data = fetch()
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        data = None
    _cache[key] = (time.time(), data)
    return data


def _client(token: str) -> httpx.Client:
    return httpx.Client(base_url=f"https://graph.instagram.com/{settings.instagram_api_version}", timeout=8,
                        transport=transport, headers={"Authorization": f"Bearer {token}"})


def ig_account(account: tuple[str, str, str]) -> dict | None:
    """Followers and media count of this account, cached 5 minutes."""
    acct, _, token = account

    def fetch() -> dict | None:
        with _client(token) as c:
            r = c.get("/me", params={"fields": "username,followers_count,media_count"})
        return r.json() if r.status_code == 200 else None
    return _cached((acct, "account"), fetch)


def ig_insights(account: tuple[str, str, str]) -> dict | None:
    """Reach (last 7 days vs the 7 before) and saves per post of this account, cached 5 minutes."""
    acct, _, token = account
    now = int(time.time())

    def fetch() -> dict:
        with _client(token) as c:
            def reach(since: int, until: int) -> int | None:
                r = c.get("/me/insights", params={"metric": "reach", "period": "day",
                                                  "metric_type": "total_value", "since": since, "until": until})
                if r.status_code != 200:
                    return None
                data = r.json().get("data") or [{}]
                return int(data[0].get("total_value", {}).get("value", 0))
            week = 7 * 86400
            this_week, last_week = reach(now - week, now), reach(now - 2 * week, now - week)
            media = c.get("/me/media", params={"fields": "id", "limit": 25}).json().get("data", [])
            saves = []
            for m in media:
                r = c.get(f"/{m['id']}/insights", params={"metric": "saved"})
                if r.status_code == 200:
                    saves.append(int((r.json().get("data") or [{}])[0].get("values", [{}])[0].get("value", 0)))
        return {"reach": this_week, "reach_prev": last_week, "saves": saves}
    return _cached((acct, "insights"), fetch)


# Representative events from a sandbox run (Figma Policy Log), shown only in DEMO_MODE=fixture.
# Live runs show only the parsed OpenShell logs.
POLICY_SEED = [
    PolicyEvent(time="10:14:52", sandbox="agent-3f9a1c", binary="/usr/bin/node", host="graph.facebook.com",
                request="DELETE /v21.0/17952…/", result="policy_denied", note="injected by event page"),
    PolicyEvent(time="10:14:51", sandbox="agent-3f9a1c", binary="/usr/bin/curl", host="pastebin.com",
                request="POST /api/api_post.php", result="policy_denied", note="exfiltration attempt"),
    PolicyEvent(time="10:14:40", sandbox="agent-3f9a1c", binary="/usr/bin/node", host="culture.seoul.go.kr",
                request="GET /event/night-market", result="audit"),
    PolicyEvent(time="10:12:05", sandbox="agent-3f9a1c", binary="/usr/bin/curl", host="apis.data.go.kr",
                request="GET /B551011/EngService2/searchFestival2", result="allowed"),
    PolicyEvent(time="09:40:58", sandbox="agent-77b0e2", binary="/usr/bin/python3", host="—",
                request="write /sandbox/agent/CLAUDE.md", result="fs_denied", note="read-only path"),
]


def _compact(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}K" if n >= 10_000 else f"{n:,}"


def metrics(org_id: UUID, jobs: list[Job]) -> list[Metric]:
    published = posts.published_since(org_id, datetime.now(UTC) - timedelta(days=7))
    waiting = sum(j.status == JobStatus.READY_FOR_REVIEW for j in jobs)
    account = _account(org_id)
    ig, ins = (ig_account(account), ig_insights(account)) if account else (None, None)
    if not account:
        followers = Metric(label="Followers", value="—", delta="connect an Instagram account")
    elif not ig:
        followers = Metric(label="Followers", value="—", delta=f"@{account[1]} · Instagram didn't answer")
    else:
        followers = Metric(label="Followers", value=f"{ig.get('followers_count', 0):,}",
                           delta=f"@{account[1]} · {ig.get('media_count', 0)} posts · live from Instagram")
    if ins and ins["reach"] is not None:
        prev = ins["reach_prev"]
        delta = (f"{(ins['reach'] - prev) / prev:+.0%} vs previous 7 days" if prev
                 else "first week of data · live from Instagram")
        reach = Metric(label="Reach (7d)", value=_compact(ins["reach"]), delta=delta)
    else:
        reach = Metric(label="Reach (7d)", value="—", delta="needs Instagram insights access")
    if ins and ins["saves"]:
        avg = sum(ins["saves"]) / len(ins["saves"])
        saves = Metric(label="Avg. saves / post", value=f"{avg:.1f}".removesuffix(".0"),
                       delta=f"across {len(ins['saves'])} post{'s' * (len(ins['saves']) != 1)} · live")
    else:
        saves = Metric(label="Avg. saves / post", value="—",
                       delta="no posts yet" if ins else "needs Instagram insights access")
    return [
        followers, reach, saves,
        Metric(label="Published this week", value=str(published), delta=f"{waiting} waiting for review"),
    ]


def policy_stats(events: list[PolicyEvent], policy_version: str) -> list[Metric]:
    """Header cards of the Policy Log, computed from the events shown below them."""
    denied = sum(e.result.endswith("denied") for e in events)
    publish = sum(e.host.startswith("graph.") and e.result == "policy_denied" for e in events)
    allowed = [e for e in events if e.result == "allowed"]
    return [
        Metric(label="Denied", value=str(denied), delta=f"{publish} publish / delete attempts"),
        Metric(label="Allowed reads", value=str(len(allowed)),
               delta=f"{len(hosts := {e.host for e in allowed})} host{'s' * (len(hosts) != 1)} · inference calls not listed"),
        Metric(label="Sandboxes run", value=str(len({e.sandbox for e in events})), delta="deleted after each stage"),
        Metric(label="Policy version", value=policy_version, delta="agent-policy.yaml"),
    ]
