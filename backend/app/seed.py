"""Dashboard + policy seed data (BACKEND §11: IG Insights for @whatsonkorea later, rest is seed)."""

from app.schemas import Channel, Job, JobStatus, Metric, PolicyEvent

CHANNELS = [
    Channel(handle="@whatsonkorea", platform="Instagram", followers=12480, live=True),
    Channel(handle="@whatsonkorea.jp", platform="Instagram", followers=2104, live=False),
    Channel(handle="What's On Korea", platform="Threads", followers=860, live=False),
    Channel(handle="What's On Korea", platform="YouTube Shorts", followers=1320, live=False),
]

# Representative events from a sandbox run (Figma Policy Log). Real runs append parsed OpenShell logs.
POLICY_SEED = [
    PolicyEvent(time="10:14:52", sandbox="agent-3f9a1c", binary="/usr/bin/node", host="graph.facebook.com",
                request="DELETE /v21.0/17952…/", result="policy_denied", note="injected by event page"),
    PolicyEvent(time="10:14:51", sandbox="agent-3f9a1c", binary="/usr/bin/curl", host="pastebin.com",
                request="POST /api/api_post.php", result="policy_denied", note="exfiltration attempt"),
    PolicyEvent(time="10:14:40", sandbox="agent-3f9a1c", binary="/usr/bin/node", host="culture.seoul.go.kr",
                request="GET /event/night-market", result="audit"),
    PolicyEvent(time="10:12:05", sandbox="agent-3f9a1c", binary="/usr/bin/curl", host="apis.data.go.kr",
                request="GET /B551011/EngService2/searchFestival2", result="allowed"),
    PolicyEvent(time="10:12:03", sandbox="agent-3f9a1c", binary="/usr/bin/node", host="api.anthropic.com",
                request="POST /v1/messages  (provider key injected)", result="allowed"),
    PolicyEvent(time="09:40:58", sandbox="agent-77b0e2", binary="/usr/bin/python3", host="—",
                request="write /sandbox/agent/CLAUDE.md", result="fs_denied", note="read-only path"),
]


def metrics(jobs: list[Job]) -> list[Metric]:
    published = sum(j.status == JobStatus.PUBLISHED for j in jobs)
    waiting = sum(j.status == JobStatus.READY_FOR_REVIEW for j in jobs)
    return [
        Metric(label="Followers", value="12,480", delta="+4.2% vs last week"),
        Metric(label="Reach (7d)", value="48.2K", delta="+11.8% vs last week"),
        Metric(label="Avg. saves / post", value="312", delta="+38 vs last week"),
        Metric(label="Published this week", value=str(published), delta=f"{waiting} waiting for review"),
    ]


def policy_stats(events: list[PolicyEvent], n_jobs: int) -> list[Metric]:
    denied = sum(e.result.endswith("denied") for e in events)
    publish = sum(e.host.startswith("graph.") and e.result == "policy_denied" for e in events)
    return [
        Metric(label="Denied (24h)", value=str(denied), delta=f"{publish} publish / delete attempts"),
        Metric(label="Audited (24h)", value=str(sum(e.result == "audit" for e in events)),
               delta="event_pages in audit mode"),
        Metric(label="Sandboxes run", value=str(n_jobs * 3), delta="all discarded (--no-keep)"),
        Metric(label="Policy version", value="v3", delta="prover: no risky diff"),
    ]
