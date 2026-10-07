"""Dashboard + policy seed data (BACKEND §11: IG Insights for @whatsonkorea later, rest is seed)."""

from app.schemas import Channel, Job, JobStatus, Metric, PolicyEvent

CHANNELS = [
    Channel(handle="@whatsonkorea", platform="Instagram", followers=12480, live=True),
    Channel(handle="@whatsonkorea.jp", platform="Instagram", followers=2104, live=False),
    Channel(handle="What's On Korea", platform="Threads", followers=860, live=False),
    Channel(handle="What's On Korea", platform="YouTube Shorts", followers=1320, live=False),
]

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


def metrics(jobs: list[Job]) -> list[Metric]:
    published = sum(j.status == JobStatus.PUBLISHED and "/p/MOCK" not in (j.published_url or "") for j in jobs)
    waiting = sum(j.status == JobStatus.READY_FOR_REVIEW for j in jobs)
    return [
        Metric(label="Followers", value="12,480", delta="+4.2% vs last week"),
        Metric(label="Reach (7d)", value="48.2K", delta="+11.8% vs last week"),
        Metric(label="Avg. saves / post", value="312", delta="+38 vs last week"),
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
