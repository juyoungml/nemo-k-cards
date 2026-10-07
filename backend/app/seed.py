"""Dashboard + policy seed data (BACKEND §11: IG Insights for @whatsonkorea later, rest is seed)."""

import time

import httpx

from app.config import settings
from app.schemas import Channel, Job, JobStatus, Metric, PolicyEvent

_ig_cache: dict = {"at": 0.0, "data": None}


def ig_account() -> dict | None:
    """Live @whatsonkorea stats from the Instagram API (host-only token), cached 5 minutes."""
    if not (settings.ig_access_token and settings.ig_user_id):
        return None
    if time.time() - _ig_cache["at"] < 300:
        return _ig_cache["data"]
    try:
        r = httpx.get("https://graph.instagram.com/v24.0/me", timeout=5,
                      params={"fields": "username,followers_count,media_count",
                              "access_token": settings.ig_access_token})
        data = r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        data = None
    _ig_cache.update(at=time.time(), data=data)
    return data


def channels() -> list[Channel]:
    """Connected channels only: no row (and no invented follower count) for an account without a token."""
    ig = ig_account()
    return [Channel(handle=f"@{ig.get('username', 'whatsonkorea')}", platform="Instagram",
                    followers=int(ig.get("followers_count", 0)), live=True)] if ig else []

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


_insights_cache: dict = {"at": 0.0, "data": None}


def ig_insights() -> dict | None:
    """Real reach (last 7 days vs the 7 before) and saves per post, cached 5 minutes. None without a token."""
    if not (settings.ig_access_token and settings.ig_user_id):
        return None
    if time.time() - _insights_cache["at"] < 300:
        return _insights_cache["data"]
    base, token = "https://graph.instagram.com/v24.0", settings.ig_access_token
    now = int(time.time())

    def reach(since: int, until: int) -> int | None:
        r = httpx.get(f"{base}/me/insights", timeout=8, params={
            "metric": "reach", "period": "day", "metric_type": "total_value",
            "since": since, "until": until, "access_token": token})
        if r.status_code != 200:
            return None
        data = r.json().get("data") or [{}]
        return int(data[0].get("total_value", {}).get("value", 0))

    try:
        week = 7 * 86400
        this_week, last_week = reach(now - week, now), reach(now - 2 * week, now - week)
        media = httpx.get(f"{base}/me/media", timeout=8,
                          params={"fields": "id", "limit": 25, "access_token": token}).json().get("data", [])
        saves = []
        for m in media:
            r = httpx.get(f"{base}/{m['id']}/insights", timeout=8, params={"metric": "saved", "access_token": token})
            if r.status_code == 200:
                saves.append(int((r.json().get("data") or [{}])[0].get("values", [{}])[0].get("value", 0)))
        data = {"reach": this_week, "reach_prev": last_week, "saves": saves}
    except httpx.HTTPError:
        data = None
    _insights_cache.update(at=time.time(), data=data)
    return data


def _compact(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}K" if n >= 10_000 else f"{n:,}"


def metrics(jobs: list[Job]) -> list[Metric]:
    published = sum(j.status == JobStatus.PUBLISHED and "/p/MOCK" not in (j.published_url or "") for j in jobs)
    waiting = sum(j.status == JobStatus.READY_FOR_REVIEW for j in jobs)
    ig, ins = ig_account(), ig_insights()
    if not ig:
        followers = Metric(label="Followers", value="—", delta="connect Instagram (no token)")
    else:
        followers = Metric(label="Followers", value=f"{ig.get('followers_count', 0):,}",
                           delta=f"{ig.get('media_count', 0)} posts · live from Instagram")
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
        saves = Metric(label="Avg. saves / post", value="—", delta="no posts yet" if ins else "needs Instagram insights access")
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
