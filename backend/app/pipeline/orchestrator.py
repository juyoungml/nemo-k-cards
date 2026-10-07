"""Runs one job through SPEC §4: research -> verify -> copy -> render -> QA -> review (BACKEND §5).

DEMO_MODE=fixture replays demo/scenarios/<name>/ for the agent stages (no API cost) while render/QA run for real.
Scenarios match the Admin mock backend: good (READY_FOR_REVIEW), bad (REJECTED), fail (FAILED); Brainstorm jobs
replay demo/scenarios/brainstorm/. Files a scenario doesn't provide fall back to good/.
DEMO_MODE=live calls the Claude Code subagents through the configured runner.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app import drafts, store
from app.config import settings
from app.pipeline.agent_runner import run_stage
from app.renderer.html import HtmlRenderer, weekend_label
from app.schemas import (
    CardDeck,
    Draft,
    EventBrief,
    Issue,
    Job,
    JobStatus,
    LogLine,
    PipelineStep,
    PolicyEvent,
    PublishProgress,
    ReviewVerdict,
    VerificationReport,
)
from app.services import image_host, link_checker, publisher, visual_qa

log = logging.getLogger("orchestrator")
KST = ZoneInfo("Asia/Seoul")
FIXTURE_DELAY = 1.2  # seconds per agent stage in fixture mode, so the UI shows progress

STEPS = {
    "research": ("Research", "researcher · sandbox"),
    "verify": ("Verify links", "host"),
    "copy": ("Outline & copy", "copywriter · sandbox"),
    "render": ("Render", "host · Playwright"),
    "qa": ("Visual QA", "host"),
    "review": ("Final review", "reviewer · sandbox"),
    "publish": ("Human publish", "you"),
}
STATUS = {"research": JobStatus.RESEARCHING, "verify": JobStatus.VERIFYING, "copy": JobStatus.WRITING,
          "render": JobStatus.RENDERING, "qa": JobStatus.QA, "review": JobStatus.REVIEWING}

_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def new_job(job_id: str, prompt: str, source: str = "quick", draft_id: str | None = None) -> Job:
    keys = [k for k in STEPS if not (source == "brainstorm" and k == "research")]
    steps = [PipelineStep(name=STEPS[k][0], where=STEPS[k][1]) for k in keys]
    steps[-1].note = "Approve in Review"
    return Job(id=job_id, prompt=prompt, created_at=datetime.now(UTC), source=source,
               draft_id=draft_id, pipeline=steps)


class Run:
    """Small helper that mutates the job and persists after every change (SSE reads the store)."""

    def __init__(self, job: Job) -> None:
        self.job = job

    def save(self) -> None:
        store.save_job(self.job)

    def step(self, key: str, state: str, note: str | None = None) -> None:
        name = STEPS[key][0]
        for s in self.job.pipeline:
            if s.name == name:
                s.state = state
                if note is not None:
                    s.note = note
        if state == "running" and key in STATUS:
            self.job.status = STATUS[key]
        self.save()

    def log(self, stage: str, message: str, level: str = "info") -> None:
        self.job.log.append(LogLine(time=datetime.now(KST).strftime("%H:%M:%S"), stage=stage,
                                    message=message, level=level))
        self.save()


def _scenario(name: str = "good"):
    dirs = [settings.scenarios_dir / name, settings.scenarios_dir / "good"]

    def load(fname: str):
        for d in dirs:
            if (p := d / fname).exists():
                return json.loads(p.read_text())
        return None
    return load


async def run_job(job_id: str, draft: Draft | None = None, scenario: str = "good") -> None:
    job = store.get_job(job_id)
    if not job:
        return
    r = Run(job)
    fx = _scenario("brainstorm" if draft else scenario)
    live = settings.demo_mode == "live"
    fail = None if live else fx("fail.json")
    current = "research"

    def fixture_fail(stage: str) -> None:
        """fail scenario: stop the run at `fail_at` the way a real stage error would."""
        if fail and fail.get("fail_at") == stage:
            for line in fail.get("log", []):
                r.log(stage, line, "warn")
            raise RuntimeError(fail["error"])

    try:
        # 1. Research (sandbox) ------------------------------------------------------------
        if job.source == "quick":
            current = "research"
            r.step("research", "running")
            r.log("research", f"sandbox started ({settings.agent_runner}, policy: {settings.openshell_policy.name})")
            if live:
                briefs = await run_stage(
                    "researcher",
                    "Use the researcher subagent. Research events for this request and return EventBrief items. "
                    f"Today is {datetime.now(KST):%Y-%m-%d} (Asia/Seoul). Request: {job.prompt}",
                    {"prompt": job.prompt, "today": datetime.now(KST).date().isoformat()},
                    list[EventBrief], job_id=job.id)
            else:
                await asyncio.sleep(FIXTURE_DELAY)
                fixture_fail("research")
                briefs = [EventBrief.model_validate(b) for b in fx("briefs.json")]
                _replay_policy_events(r, fx("policy_events.json") or [])
            job.briefs = briefs
            n_src = sum(len(b.sources) for b in briefs)
            r.log("research", f"{len(briefs)} EventBrief validated (schema ok)")
            r.step("research", "done", f"{len(briefs)} events · {n_src} sources")

        # 2. Verify (host) -------------------------------------------------------------------
        current = "verify"
        r.step("verify", "running")
        fixture_fail("verify")
        host_issues: list[Issue] = []  # board problems found on the host; block approval like QA issues
        if draft:
            # Brainstorm skips Research: the board's facts become the brief, so its sources get the same check.
            brief, problems = drafts.brief_from_draft(draft, datetime.now(KST).date())
            job.briefs = [brief] if brief else []
            for p in problems:
                r.log("verify", p, "warn")
                host_issues.append(Issue(severity="block", category="fact", message=f"Brainstorm board: {p}."))
        fixture_ver = None if live else fx("verification.json")
        known = {c.url: c for c in VerificationReport.model_validate(fixture_ver).checks} if fixture_ver else None
        report = await link_checker.verify(job.briefs, known)
        job.verification = report
        for c in report.checks:
            if c.status != "ok":
                r.log("verify", f"{c.url} → {c.status.upper()}" + (f" ({c.reason or c.http_code})"), "warn")
        bad = [c for c in report.checks if c.status != "ok"]
        dead = sum(c.status == "dead" for c in bad)
        sus = sum(c.status == "suspicious" for c in bad)
        r.step("verify", "done", f"{dead} dead · {sus} lookalike → {len(report.excluded_event_ids)} excluded"
               if bad else f"{len(report.checks)} links ok")
        ok_urls = {c.url for c in report.checks if c.status == "ok"}
        for b in job.briefs:
            for src in b.sources:
                if str(src.url) in ok_urls:
                    src.fetched_at = datetime.now(UTC)
        included = [b for b in job.briefs if b.id not in report.excluded_event_ids]
        if draft:
            draft, demoted = drafts.demote_unverified(draft, ok_urls)
            if demoted:
                r.log("verify", f"{demoted} board fact(s) marked unverified: source failed the link check", "warn")
            if job.briefs and not included:
                host_issues.append(Issue(severity="block", category="link", message=(
                    "Every source for this event failed link verification — add the official event page in "
                    "Brainstorm and generate again.")))

        # 3. Outline & copy (sandbox) ----------------------------------------------------------
        current = "copy"
        r.step("copy", "running")
        fixture_fail("copy")
        if live:
            constraints = draft.model_dump(include={"facts", "angles", "selected_angle_id", "targets", "tones",
                                                    "outline"}) if draft else None
            deck = await run_stage(
                "copywriter",
                "Use the copywriter subagent. Turn the verified events (and brainstorm constraints, if any) "
                "into a CardDeck for foreigners in Korea.",
                {"briefs": [b.model_dump(mode="json") for b in included], "constraints": constraints,
                 "prompt": job.prompt},
                CardDeck, job_id=job.id)
        else:
            await asyncio.sleep(FIXTURE_DELAY)
            deck = (_deck_from_draft(draft, included[0].id if included else None) if draft
                    else CardDeck.model_validate(fx("deck.json")))
        deck.job_id = job.id
        job.deck = deck
        n_tags = len(visual_qa.HASHTAG.findall(deck.caption))
        r.log("copy", f"CardDeck {len(deck.slides)} slides · caption {n_tags} hashtags")
        r.step("copy", "done", f"{len(deck.slides)} slides")

        # 4. Render (host) ------------------------------------------------------------------
        current = "render"
        r.step("render", "running")
        fixture_fail("render")
        out_dir = settings.output_dir / job.id
        rendered = await HtmlRenderer(theme=settings.theme).render(deck, out_dir, included)
        job.slide_urls = [f"/assets/{job.id}/{s.path.name}" for s in rendered]
        r.log("render", f"{rendered[0].path.name} … {rendered[-1].path.name}")
        r.step("render", "done", f"{len(rendered)} JPEG · 1080×1350")

        # 5. Visual QA (host) ------------------------------------------------------------------
        current = "qa"
        r.step("qa", "running")
        fixture_fail("qa")
        secrets = tuple(s for s in (settings.ig_access_token, settings.supabase_service_key) if s)
        qa = visual_qa.run_qa(rendered, deck, secrets)
        nb = sum(i.severity == "block" for i in qa.issues)
        nw = len(qa.issues) - nb
        r.log("qa", f"overflow/font/PII/secret/hashtag checks → {nb} block · {nw} warn")
        r.step("qa", "done", f"{nb} block · {nw} warn")

        # 6. Final review (sandbox) -------------------------------------------------------------
        current = "review"
        r.step("review", "running")
        fixture_fail("review")
        if live:
            verdict = await run_stage(
                "reviewer",
                "Use the reviewer subagent. Review this card deck before a human approves it. Check facts against "
                "the briefs, sensitive dates/phrasing (rules in `sensitive_topics_yaml`), PII and tone. "
                "Look at the rendered slide images listed in `slide_paths`.",
                {"deck": deck.model_dump(mode="json"), "briefs": [b.model_dump(mode="json") for b in included],
                 "qa_issues": [i.model_dump() for i in qa.issues],
                 "slide_paths": [str(s.path) for s in rendered],
                 "board_facts": [f.model_dump() for f in draft.facts] if draft else None,
                 "sensitive_topics_yaml": (settings.agent_dir.parent / "policies/content/sensitive_topics.yaml")
                 .read_text(encoding="utf-8"),
                 "today": datetime.now(KST).date().isoformat(),
                 "cover_label": weekend_label(datetime.now(KST).date())},
                ReviewVerdict, files=[s.path for s in rendered], job_id=job.id)
        else:
            await asyncio.sleep(FIXTURE_DELAY)
            verdict = ReviewVerdict.model_validate(fx("review.json") or {"verdict": "pass"})
        r.log("review", f"verdict {verdict.verdict} · {len(verdict.issues)} issue(s)",
              "info" if verdict.verdict == "pass" else "warn")
        r.step("review", "done", f"{verdict.verdict} · {len(verdict.issues)} issue(s)")

        job.issues = host_issues + qa.issues + verdict.issues
        # Automated checks advise; the human decides. Never auto-reject — blocking issues are shown in Review.
        n_block = sum(i.severity == "block" for i in job.issues)
        if n_block:
            r.log("review", f"{n_block} blocking issue(s) flagged for the operator — decide in Review", "warn")
        job.status = JobStatus.READY_FOR_REVIEW
        r.step("publish", "pending", f"Review {n_block} blocking issue(s), then decide" if n_block
               else "Approve in Review")
    except Exception as e:
        log.exception("job %s failed at %s", job_id, current)
        job.error = f"{STEPS[current][0]} failed: {e}"
        job.status = JobStatus.FAILED
        r.step(current, "failed", str(e)[:120])


def _replay_policy_events(r: Run, events: list[dict]) -> None:
    """Fixture stand-in for `openshell logs`: what the sandbox tried and the policy denied, tied to this job."""
    if not events:
        return
    now = datetime.now(KST).strftime("%H:%M:%S")
    rows = [PolicyEvent.model_validate({"time": now, **e, "sandbox": f"agent-{r.job.id}", "job_id": r.job.id})
            for e in events]
    store.add_policy_events(rows)
    for e in rows:
        r.log("research", f"{e.request.split()[0]} {e.host} → {e.result} (OpenShell)",
              "warn" if e.result.endswith("denied") else "info")


def _deck_from_draft(draft: Draft, event_id: str | None = None) -> CardDeck:
    """Fixture copywriter for Brainstorm: outline headings + verified facts."""
    angle = next((a for a in draft.angles if a.id == draft.selected_angle_id), None)
    title = angle.title if angle else (draft.outline[0].heading if draft.outline else "What's on in Korea")
    facts = "\n".join(f.value for f in draft.facts if f.verified)
    slides = [{"index": i, "layout": o.layout, "heading": o.heading,
               "body": facts if o.layout == "event" and i == 1 else "",
               "event_id": event_id if o.layout == "event" and i == 1 else None}
              for i, o in enumerate(draft.outline[:8])]
    while len(slides) < 6:
        slides.insert(-1, {"index": 0, "layout": "tips", "heading": "Good to know", "body": ""})
    for i, s in enumerate(slides):
        s["index"] = i
    hook = angle.hook if angle else ""
    return CardDeck(title=title, caption=f"{title}\n{hook}\nDetails & links in bio.\n#seoul #korea #koreatravel "
                    "#thingstodoinseoul #whatsonkorea", slides=slides)


# Progress bands per publish step (percent of the bar), so the UI shows steady, honest progress.
BANDS = {"upload": (0, 30), "containers": (30, 60), "processing": (60, 85), "publish": (85, 99)}
LABELS = {"upload": "Uploading slides to public storage", "containers": "Sending slides to Instagram",
          "processing": "Instagram is processing the images", "publish": "Publishing to @whatsonkorea"}


async def publish_job(job_id: str, caption: str | None, mode: str) -> None:
    job = store.get_job(job_id)
    if not job or not job.deck:
        return
    r = Run(job)
    if caption:
        job.deck.caption = caption
    job.status = JobStatus.PUBLISHING
    job.error = None
    job.publish_progress = PublishProgress(mode=mode, step="upload", label=LABELS["upload"],
                                           started_at=datetime.now(UTC))
    n_block = sum(i.severity == "block" for i in job.issues)
    r.step("publish", "running", f"publishing ({mode})")
    r.log("publish", f"approved by operator → publisher on host (mode: {mode})"
          + (f" · operator overrode {n_block} blocking issue(s)" if n_block else ""))

    def progress(step: str, done: int, total: int) -> None:
        lo, hi = BANDS[step]
        pp = job.publish_progress
        pp.step, pp.done, pp.total = step, done, total
        pp.label = f"{LABELS[step]} ({done}/{total})" if total > 1 else LABELS[step]
        pp.percent = int(lo + (hi - lo) * (done / total if total else 0))
        r.save()

    try:
        paths = sorted((settings.output_dir / job.id).glob("slide-*.jpg"))
        if mode in ("graph", "dryrun"):
            urls = await image_host.publish_images(job.id, paths, lambda d, t: progress("upload", d, t))
            r.log("publish", f"{len(urls)} slides public via {settings.image_host} → {urls[0].rsplit('/', 2)[0]}/…")
            result = await publisher.publish_carousel(urls, job.deck.caption, publish=(mode == "graph"),
                                                      on_progress=progress)
            if mode == "dryrun":
                cid = result.split(":", 1)[1]
                r.log("publish", f"dry run: Instagram carousel container {cid} FINISHED · media_publish skipped "
                      "(nothing posted)")
                job.status = JobStatus.READY_FOR_REVIEW
                job.publish_progress.step, job.publish_progress.percent = "done", 100
                job.publish_progress.label = "Dry run complete: carousel ready on Instagram, nothing posted"
                job.publish_progress.finished_at = datetime.now(UTC)
                r.step("publish", "pending", "dry run OK · approve again with graph mode to post")
                return
            job.published_url = result
        else:
            for step in ("upload", "containers", "processing", "publish"):  # mock: simulate the same steps
                for i in range(1, len(paths) + 1 if step in ("upload", "containers") else 2):
                    progress(step, i, len(paths) if step in ("upload", "containers") else 1)
                    await asyncio.sleep(0.15)
            job.published_url = f"https://www.instagram.com/p/MOCK{job.id.upper()}/"
            r.log("publish", "mock publish: no network call (set PUBLISH_MODE=dryrun or graph)")
        job.status = JobStatus.PUBLISHED
        pp = job.publish_progress
        pp.step, pp.percent, pp.finished_at = "done", 100, datetime.now(UTC)
        pp.label = "Published to @whatsonkorea" if mode == "graph" else "Mock publish complete (nothing posted)"
        r.step("publish", "done", "posted to @whatsonkorea" if mode == "graph" else "mock post created")
    except Exception as e:  # noqa: BLE001
        job.status = JobStatus.READY_FOR_REVIEW
        job.error = f"Publish failed: {e}"
        pp = job.publish_progress
        pp.label = f"Failed: {LABELS.get(pp.step, 'publishing').lower()}"
        pp.step, pp.error, pp.finished_at = "failed", str(e)[:300], datetime.now(UTC)
        r.log("publish", f"publish failed: {e}", "warn")
        r.step("publish", "failed", str(e)[:120])