"""Runs one job through SPEC §4: research -> verify -> copy -> render -> QA -> review (BACKEND §5), as a durable
DBOS workflow (#17, ADR 0002): each stage is a checkpointed step, so a restart resumes a Job where it stopped.

DEMO_MODE=fixture replays demo/scenarios/<name>/ for the agent stages (no API cost) while render/QA run for real.
Scenarios match the Admin mock backend: good (READY_FOR_REVIEW), bad (REJECTED), fail (FAILED); Brainstorm jobs
replay demo/scenarios/brainstorm/. Files a scenario doesn't provide fall back to good/.
DEMO_MODE=live calls the Claude Code subagents through the configured runner.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID, WorkflowSerializationFormat

from app import brainstorms, store
from app.config import settings
from app.pipeline import durable
from app.pipeline.agent_runner import run_stage
from app.renderer.base import RenderedSlide
from app.renderer.html import HtmlRenderer, weekend_label
from app.schemas import (
    Brainstorm,
    CardDeck,
    EventBrief,
    ImageAsset,
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
from app.services import image_host, link_checker, photos, publisher, visual_qa

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


def new_job(job_id: str, prompt: str, source: str = "quick", brainstorm_id: str | None = None) -> Job:
    keys = [k for k in STEPS if not (source == "brainstorm" and k == "research")]
    steps = [PipelineStep(name=STEPS[k][0], where=STEPS[k][1]) for k in keys]
    steps[-1].note = "Approve in Review"
    return Job(id=job_id, prompt=prompt, created_at=datetime.now(UTC), source=source,
               brainstorm_id=brainstorm_id, pipeline=steps)


class Run:
    """Small helper that mutates the job and persists after every change (SSE reads the store). Everything it
    stores (the job, policy events) goes under the job's Organization, `org_id`."""

    def __init__(self, org_id: UUID, job: Job) -> None:
        self.org_id = org_id
        self.job = job
        self._dirty = False
        self._writer: asyncio.Task | None = None

    def save(self) -> None:
        """Write off the event loop; saves made while a write is in flight coalesce into one."""
        self._dirty = True
        if not self._writer or self._writer.done():
            self._writer = asyncio.create_task(self._write())

    async def _write(self) -> None:
        while self._dirty:
            self._dirty = False
            try:
                await asyncio.to_thread(store.save_job, self.org_id, self.job.model_copy(deep=True))
            except Exception:
                log.exception("could not save job %s", self.job.id)

    async def flush(self) -> None:
        while self._writer and not self._writer.done():
            await self._writer

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


# ---------------------------------------------------------------- the pipeline as a durable workflow (#17)
#
# Each stage is one DBOS step (ADR 0002). A step loads the Job under its Organization, runs the stage through a
# Run (live log lines and saves, as before), and saves the Job before it returns. DBOS then checkpoints the step's
# small hand-off (`carry`: what a later stage needs that isn't on the Job, e.g. the vetted photos); the Job
# document itself is the stage's large output, so checkpoints never hold a copy of the deck or the briefs.
# After a restart the workflow replays: finished steps return their checkpoint without running, and the pipeline
# continues at the first unfinished stage. A stage that was cut off mid-way runs again from its start.
#
# The workflow function only walks the stage names and passes `carry` along (no clock, store, network or
# randomness), so a replay always takes the same path. Changing STAGES needs a new WORKFLOW_VERSION (settings).

STAGES: list[tuple[str, str]] = [  # (stage = DBOS step name, the Admin pipeline step it reports under)
    ("research", "research"),  # Quick Jobs only: a Brainstorm's board replaces it
    ("verify", "verify"),
    ("photos", "verify"),
    ("copy", "copy"),
    ("stock", "copy"),
    ("render", "render"),
    ("qa", "qa"),
    ("review", "review"),
]
UI_STEP = dict(STAGES)
RUNNING = {JobStatus.QUEUED, JobStatus.RESEARCHING, JobStatus.VERIFYING, JobStatus.WRITING, JobStatus.RENDERING,
           JobStatus.QA, JobStatus.REVIEWING}


def stages_for(source: str) -> list[str]:
    return [s for s, _ in STAGES if not (source == "brainstorm" and s == "research")]


def workflow_id(org_id: UUID | str, job_id: str) -> str:
    """One workflow per Job: enqueueing the same Job twice attaches to the first instead of starting another."""
    return f"job-{org_id}-{job_id}"


@dataclass
class Stage:
    """What a stage function gets: the Run (the Job, its log and saves), the Brainstorm snapshot (with unverified
    facts demoted once Verify has run), the fixture scenario, the hand-off from earlier stages (`carry`) and
    `out`, where it puts its own hand-off."""
    r: Run
    brainstorm: Brainstorm | None
    scenario: str
    carry: dict
    out: dict = field(default_factory=dict)

    @property
    def job(self) -> Job:
        return self.r.job

    @property
    def live(self) -> bool:
        return settings.demo_mode == "live"

    def fx(self, fname: str):
        return _scenario("brainstorm" if self.brainstorm else self.scenario)(fname)

    def fixture_fail(self, stage: str) -> None:
        """fail scenario: stop the run at `fail_at` the way a real stage error would."""
        fail = None if self.live else self.fx("fail.json")
        if fail and fail.get("fail_at") == stage:
            for line in fail.get("log", []):
                self.r.log(stage, line, "warn")
            raise RuntimeError(fail["error"])


def _ok_urls(job: Job) -> set[str]:
    return {c.url for c in job.verification.checks if c.status == "ok"} if job.verification else set()


def _included(job: Job) -> list[EventBrief]:
    excluded = set(job.verification.excluded_event_ids) if job.verification else set()
    return [b for b in job.briefs if b.id not in excluded]


def _vetted(s: Stage) -> dict[str, photos.Vetted]:
    return {k: photos.Vetted(event_id=k, asset=ImageAsset.model_validate(v["asset"]), remote_url=v["remote_url"])
            for k, v in s.carry.get("vetted", {}).items()}


def _rendered(s: Stage) -> list[RenderedSlide]:
    return [RenderedSlide.model_validate(x) for x in s.carry.get("rendered", [])]


async def _research(s: Stage) -> None:
    """1. Research (sandbox)."""
    job, r = s.job, s.r
    r.step("research", "running")
    r.log("research", f"sandbox started ({settings.agent_runner}, policy: {settings.openshell_policy.name})")
    if s.live:
        briefs = await run_stage(
            "researcher",
            "Use the researcher subagent. Research events for this request and return EventBrief items. "
            f"Today is {datetime.now(KST):%Y-%m-%d} (Asia/Seoul). Request: {job.prompt}",
            {"prompt": job.prompt, "today": datetime.now(KST).date().isoformat()},
            list[EventBrief], job_id=job.id, org_id=r.org_id)
    else:
        await asyncio.sleep(FIXTURE_DELAY)
        s.fixture_fail("research")
        briefs = [EventBrief.model_validate(b) for b in s.fx("briefs.json")]
        await _replay_policy_events(r, s.fx("policy_events.json") or [])
    job.briefs = briefs
    n_src = sum(len(b.sources) for b in briefs)
    r.log("research", f"{len(briefs)} EventBrief validated (schema ok)")
    # Nothing for the operator to decide in Review, and copy / review would only fill an empty deck.
    if not briefs:
        raise RuntimeError("the researcher found no events for this request — try a broader one "
                           "(another city or date range)")
    r.step("research", "done", f"{len(briefs)} events · {n_src} sources")


async def _verify(s: Stage) -> None:
    """2. Verify links (host)."""
    job, r = s.job, s.r
    r.step("verify", "running")
    s.fixture_fail("verify")
    host_issues: list[Issue] = []  # board problems found on the host; block approval like QA issues
    if s.brainstorm:
        # Brainstorm skips Research: the board's facts become the brief, so its sources get the same check.
        brief, problems = brainstorms.brief_from_brainstorm(s.brainstorm, datetime.now(KST).date())
        job.briefs = [brief] if brief else []
        for p in problems:
            r.log("verify", p, "warn")
            host_issues.append(Issue(severity="block", category="fact", message=f"Brainstorm board: {p}."))
    fixture_ver = None if s.live else s.fx("verification.json")
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
    ok_urls = _ok_urls(job)
    for b in job.briefs:
        for src in b.sources:
            if str(src.url) in ok_urls:
                src.fetched_at = datetime.now(UTC)
    included = _included(job)
    if s.brainstorm:
        board, demoted = brainstorms.demote_unverified(s.brainstorm, ok_urls)
        s.out["brainstorm"] = board.model_dump(mode="json")
        if demoted:
            r.log("verify", f"{demoted} board fact(s) marked unverified: source failed the link check", "warn")
        if job.briefs and not included:
            host_issues.append(Issue(severity="block", category="link", message=(
                "Every source for this event failed link verification — add the official event page in "
                "Brainstorm and generate again.")))
    elif not included:
        # Quick: every event lost its sources — the deck would have no verified event to show.
        raise RuntimeError(f"all {len(job.briefs)} event(s) failed link verification "
                           f"({len(report.excluded_event_ids)} excluded) — run the request again")
    s.out["host_issues"] = [i.model_dump(mode="json") for i in host_issues]


async def _photos(s: Stage) -> None:
    """Photos (host): vet the researcher's candidates; events without one get the Poster style."""
    job, r = s.job, s.r
    included = _included(job)
    vetted: dict[str, photos.Vetted] = {}
    if any(b.images for b in included):
        vetted, photo_log = await photos.collect(included, _ok_urls(job), settings.output_dir / job.id)
        for line in photo_log:
            r.log("verify", line, "info" if "photo ok" in line else "warn")
    r.log("verify", f"photos: {len(vetted)}/{len(included)} events have a usable photo"
          + (f" · {len(included) - len(vetted)} poster" if len(included) > len(vetted) else ""))
    s.out["vetted"] = {k: {"asset": v.asset.model_dump(mode="json"), "remote_url": v.remote_url}
                       for k, v in vetted.items()}


async def _copy(s: Stage) -> None:
    """3. Outline & copy (sandbox)."""
    job, r, brainstorm = s.job, s.r, s.brainstorm
    r.step("copy", "running")
    s.fixture_fail("copy")
    included, vetted = _included(job), s.carry.get("vetted", {})
    if s.live:
        constraints = brainstorm.model_dump(include={"facts", "angles", "selected_angle_id", "targets", "tones",
                                                "outline"}) if brainstorm else None
        deck = await run_stage(
            "copywriter",
            "Use the copywriter subagent. Turn the verified events (and brainstorm constraints, if any) "
            "into a CardDeck for foreigners in Korea.",
            {"briefs": [b.model_dump(mode="json") for b in included], "constraints": constraints,
             "prompt": job.prompt,
             "has_official_photo": sorted(vetted),
             "stock_photos": [{k: c[k] for k in ("id", "shows", "fits", "never_for", "rule")}
                              for c in photos.stock_catalog()]},
            CardDeck, job_id=job.id, org_id=r.org_id)
    else:
        await asyncio.sleep(FIXTURE_DELAY)
        deck = (_deck_from_brainstorm(brainstorm, included[0].id if included else None) if brainstorm
                else CardDeck.model_validate(s.fx("deck.json")))
    deck.job_id = job.id
    job.deck = deck


async def _stock(s: Stage) -> None:
    """Slide images: the host's vetted photos, then stock picks for the rest (copy finishes here)."""
    job, r = s.job, s.r
    deck = job.deck
    for line in photos.attach_vetted(deck, _vetted(s)):
        r.log("copy", line, "warn")
    for line in photos.attach_stock(deck):
        r.log("copy", line)
    n_tags = len(visual_qa.HASHTAG.findall(deck.caption))
    r.log("copy", f"CardDeck {len(deck.slides)} slides · caption {n_tags} hashtags")
    r.step("copy", "done", f"{len(deck.slides)} slides")


async def _render(s: Stage) -> None:
    """4. Render (host)."""
    job, r = s.job, s.r
    r.step("render", "running")
    s.fixture_fail("render")
    rendered = await HtmlRenderer(theme=settings.theme).render(job.deck, settings.output_dir / job.id,
                                                               _included(job))
    job.slide_urls = [f"/assets/{job.id}/{x.path.name}" for x in rendered]
    r.log("render", f"{rendered[0].path.name} … {rendered[-1].path.name}")
    r.step("render", "done", f"{len(rendered)} JPEG · 1080×1350")
    s.out["rendered"] = [x.model_dump(mode="json") for x in rendered]


async def _qa(s: Stage) -> None:
    """5. Visual QA (host)."""
    job, r = s.job, s.r
    r.step("qa", "running")
    s.fixture_fail("qa")
    secrets = tuple(x for x in (settings.ig_access_token, settings.supabase_service_key) if x)
    qa = visual_qa.run_qa(_rendered(s), job.deck, secrets)
    nb = sum(i.severity == "block" for i in qa.issues)
    nw = len(qa.issues) - nb
    r.log("qa", f"overflow/font/PII/secret/hashtag checks → {nb} block · {nw} warn")
    r.step("qa", "done", f"{nb} block · {nw} warn")
    s.out["qa_issues"] = [i.model_dump(mode="json") for i in qa.issues]


async def _review(s: Stage) -> None:
    """6. Final review (sandbox), then hand the Deck to the operator."""
    job, r, brainstorm = s.job, s.r, s.brainstorm
    deck, included, rendered = job.deck, _included(job), _rendered(s)
    qa_issues = [Issue.model_validate(i) for i in s.carry.get("qa_issues", [])]
    r.step("review", "running")
    s.fixture_fail("review")
    if s.live:
        verdict = await run_stage(
            "reviewer",
            "Use the reviewer subagent. Review this card deck before a human approves it. Check facts against "
            "the briefs, sensitive dates/phrasing (rules in `sensitive_topics_yaml`), PII and tone. "
            "Look at the rendered slide images listed in `slide_paths`.",
            {"deck": deck.model_dump(mode="json"), "briefs": [b.model_dump(mode="json") for b in included],
             "qa_issues": [i.model_dump() for i in qa_issues],
             "slide_paths": [str(x.path) for x in rendered],
             "board_facts": [f.model_dump() for f in brainstorm.facts] if brainstorm else None,
             "sensitive_topics_yaml": (settings.agent_dir.parent / "policies/content/sensitive_topics.yaml")
             .read_text(encoding="utf-8"),
             "today": datetime.now(KST).date().isoformat(),
             "cover_label": weekend_label(datetime.now(KST).date())},
            ReviewVerdict, files=[x.path for x in rendered], job_id=job.id, org_id=r.org_id)
    else:
        await asyncio.sleep(FIXTURE_DELAY)
        verdict = ReviewVerdict.model_validate(s.fx("review.json") or {"verdict": "pass"})
    r.log("review", f"verdict {verdict.verdict} · {len(verdict.issues)} issue(s)",
          "info" if verdict.verdict == "pass" else "warn")
    r.step("review", "done", f"{verdict.verdict} · {len(verdict.issues)} issue(s)")

    host_issues = [Issue.model_validate(i) for i in s.carry.get("host_issues", [])]
    job.issues = host_issues + qa_issues + verdict.issues
    # Automated checks advise; the human decides. Never auto-reject — blocking issues are shown in Review.
    n_block = sum(i.severity == "block" for i in job.issues)
    if n_block:
        r.log("review", f"{n_block} blocking issue(s) flagged for the operator — decide in Review", "warn")
    job.status = JobStatus.READY_FOR_REVIEW
    r.step("publish", "pending", f"Review {n_block} blocking issue(s), then decide" if n_block
           else "Approve in Review")


STAGE_FUNCS: dict[str, Callable[[Stage], Awaitable[None]]] = {
    "research": _research, "verify": _verify, "photos": _photos, "copy": _copy, "stock": _stock,
    "render": _render, "qa": _qa, "review": _review,
}
_FIRST = {ui: next(s for s, u in STAGES if u == ui) for ui in UI_STEP.values()}


async def _run_stage_step(org: str, job_id: str, stage: str, brainstorm: dict | None, scenario: str,
                          carry: dict) -> dict:
    """The body of one DBOS step: load the Job under its Organization, run `stage`, save the Job. A stage error
    fails the Job (as before) and is a normal result here, so the workflow stops without retrying. Returns
    {"ok": continue?, "carry": this stage's hand-off}. Only an error saving the Job escapes (the step fails)."""
    org_id = UUID(org)
    if not (job := await asyncio.to_thread(store.get_job, org_id, job_id)):
        return {"ok": False, "carry": {}}  # not this Organization's Job (RLS), or gone
    r = Run(org_id, job)
    board = carry.get("brainstorm") or brainstorm
    s = Stage(r, Brainstorm.model_validate(board) if board else None, scenario, carry)
    ui = UI_STEP[stage]
    if _FIRST[ui] == stage and any(p.name == STEPS[ui][0] and p.state == "running" for p in job.pipeline):
        r.log(ui, f"{STEPS[ui][0]} was cut off by a server restart; running it again "
                  "(finished stages are not repeated)", "warn")
    ok = True
    try:
        await STAGE_FUNCS[stage](s)
    except Exception as e:
        log.exception("job %s failed at %s", job_id, stage)
        job.error = f"{STEPS[ui][0]} failed: {e}"
        job.status = JobStatus.FAILED
        r.step(ui, "failed", str(e)[:120])
        ok = False
    await r.flush()
    # The checkpoint must never get ahead of the Job: save once more and let an error fail the step.
    await asyncio.to_thread(store.save_job, org_id, job.model_copy(deep=True))
    return {"ok": ok, "carry": s.out}


@DBOS.workflow(name="wok.job", max_recovery_attempts=5, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def job_workflow(org: str, job_id: str, source: str, brainstorm: dict | None = None,
                       scenario: str = "good") -> str:
    """The pipeline for one Job. Arguments are plain JSON (stored by DBOS): the Organization, the Job, its source,
    the Brainstorm board as it was when the Job started, and the fixture scenario."""
    carry: dict = {}
    for stage in stages_for(source):
        out = await DBOS.run_step_async({"name": stage}, _run_stage_step, org, job_id, stage, brainstorm, scenario,
                                        carry)
        if not out["ok"]:
            return "stopped"
        carry = {**carry, **out["carry"]}
    return "done"


async def start_job(org_id: UUID, job: Job, *, brainstorm: Brainstorm | None = None, scenario: str = "good") -> None:
    """Queue a saved Job under its Organization's slot (`JOBS_PER_ORG`). Once this returns the Job survives a
    restart: a worker picks it up when a slot is free and runs it to the end."""
    queue = await DBOS.retrieve_queue_async(durable.JOB_QUEUE)
    with SetWorkflowID(workflow_id(org_id, job.id)), SetEnqueueOptions(queue_partition_key=str(org_id)):
        await queue.enqueue_async(job_workflow, str(org_id), job.id, job.source,
                                  brainstorm.model_dump(mode="json") if brainstorm else None, scenario)


async def run_job(org_id: UUID, job_id: str, brainstorm: Brainstorm | None = None, scenario: str = "good") -> None:
    """Run `job_id` of Organization `org_id` now, in this task, as the same durable workflow (tests, golden runs;
    the API queues with `start_job`). The job (and `brainstorm`, loaded by the caller under the same
    Organization) is all the agent stages receive; a job id from another Organization is simply not found."""
    if not (job := await asyncio.to_thread(store.get_job, org_id, job_id)):
        return
    if not durable.launched():
        await asyncio.to_thread(durable.launch)
    with SetWorkflowID(workflow_id(org_id, job_id)):
        await job_workflow(str(org_id), job_id, job.source,
                           brainstorm.model_dump(mode="json") if brainstorm else None, scenario)


def reconcile_after_restart() -> None:
    """At startup, after `durable.launch()` has resumed what it can: a Job still in a running state whose workflow
    will not continue (none was ever queued, it errored, ran out of recovery attempts, or belongs to another
    WORKFLOW_VERSION) is marked failed instead of spinning forever. Every Organization's Jobs (via `system`),
    each saved back under its own Organization. Blocking: run it off the event loop."""
    for org_id, job in store.jobs_in(RUNNING):
        wid = workflow_id(org_id, job.id)
        st = DBOS.get_workflow_status(wid)
        alive = st is not None and st.status in ("PENDING", "ENQUEUED")
        if alive and st.app_version == settings.workflow_version:
            continue
        if alive:
            DBOS.cancel_workflow(wid)  # checkpoints from another pipeline version: never resumed
        # Read again now that the workflow is known to be finished: its last save may have landed meanwhile.
        fresh = store.get_job(org_id, job.id)
        if fresh and fresh.status in RUNNING:
            fresh.status = JobStatus.FAILED
            fresh.error = ("Interrupted by a server restart and could not be resumed"
                           + (" (the pipeline changed since it started)" if alive else "") + "; start the job again.")
            store.save_job(org_id, fresh)


async def _replay_policy_events(r: Run, events: list[dict]) -> None:
    """Fixture stand-in for `openshell logs`: what the sandbox tried and the policy denied, tied to this job."""
    if not events:
        return
    now = datetime.now(KST).strftime("%H:%M:%S")
    rows = [PolicyEvent.model_validate({"time": now, **e, "sandbox": f"agent-{r.job.id}", "job_id": r.job.id})
            for e in events]
    await asyncio.to_thread(store.add_policy_events, r.org_id, rows)
    for e in rows:
        r.log("research", f"{e.request.split()[0]} {e.host} → {e.result} (OpenShell)",
              "warn" if e.result.endswith("denied") else "info")


def _deck_from_brainstorm(brainstorm: Brainstorm, event_id: str | None = None) -> CardDeck:
    """Fixture copywriter for Brainstorm: outline headings + verified facts."""
    angle = next((a for a in brainstorm.angles if a.id == brainstorm.selected_angle_id), None)
    title = angle.title if angle else (brainstorm.outline[0].heading if brainstorm.outline else "What's on in Korea")
    facts = "\n".join(f.value for f in brainstorm.facts if f.verified)
    slides = [{"index": i, "layout": o.layout, "heading": o.heading,
               "body": facts if o.layout == "event" and i == 1 else "",
               "event_id": event_id if o.layout == "event" and i == 1 else None}
              for i, o in enumerate(brainstorm.outline[:8])]
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


async def publish_job(org_id: UUID, job_id: str, caption: str | None, mode: str) -> None:
    job = await asyncio.to_thread(store.get_job, org_id, job_id)
    if not job or not job.deck:
        return
    r = Run(org_id, job)
    try:
        await _publish_job(r, caption, mode)
    finally:
        await r.flush()


async def _publish_job(r: Run, caption: str | None, mode: str) -> None:
    job = r.job
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
            # Private slides (#18): links expire in minutes and uploads are deleted when the block ends.
            async with image_host.exposed(r.org_id, job.id, paths, lambda d, t: progress("upload", d, t)) as urls:
                r.log("publish", f"{len(urls)} slides via {settings.image_host} on links that expire in "
                      f"{settings.publish_link_ttl_s // 60} min")
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