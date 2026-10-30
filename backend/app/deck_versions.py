"""Deck versions and text edits (#19; GLOSSARY "Deck version"; docs/spec/v1.md pseudocode 6 "Slide editing").

In Review, an Editor changes a slide's heading or body, reorders slides, or edits the caption and hashtags. A save:

1. is refused unless the Deck is in review (READY_FOR_REVIEW) and was loaded at its current version
   (`base_version`): two people editing at once never overwrite each other;
2. runs the Safeguard's host rules on the new text at once, and refuses a blocked phrase with the same
   finding-style explanation as everywhere else (ADR 0004). Nothing is saved;
3. re-renders only the slides whose HTML changed (same renderer and templates as the pipeline). A moved slide
   changes (its page number does), and so does every slide when a Deck-wide value such as the cover's place name
   changes. Each re-rendered slide gets a new file name, so no earlier version's files are ever overwritten;
4. warns about overflowing text before it saves, from the renderer's own overflow QA. The Editor can fix the text or
   save anyway (`allow_overflow`); the overflow then stays as a blocking Issue, like one the pipeline found;
5. stores the next Deck version (`deck_versions`, insert-only), makes it the Job's current Deck, and writes
   `deck.edited` with before and after for every changed field, all in one transaction under the Job's row lock;
6. drops the old Safeguard verdict and starts the full Safeguard check (rules plus model) of the new version as a
   durable DBOS workflow. Until it allows exactly this version, the Deck can't be approved (`safeguard_pending`).
   A verdict carries the version it vouches for and the hash of its content, so no version is ever approved on an
   earlier version's verdict.

If the full check blocks an edited version, the Deck can't be approved (nobody, no Override), as with any block.
Unlike a pipeline block it stays in review, so an Editor can fix the text and save again, which starts a new
check. One bad edit doesn't kill a good Deck (ADR 0004, addendum).

Approving pins a version: `ApproveRequest.deck_version` must be the current one (`pin`), and publishing stops if
the Deck changed after approval.

Old Jobs have no rows. They read as version 1, and version 1 is stored the first time the Deck is edited or
approved."""

import asyncio
import logging
import secrets
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dbos import DBOS, SetWorkflowID, WorkflowSerializationFormat
from fastapi import APIRouter, HTTPException
from psycopg import Connection
from pydantic import BaseModel, Field

from app import assets, audit, auth, slide_sources, store
from app.config import settings
from app.pipeline import durable, orchestrator
from app.renderer.base import RenderedSlide
from app.renderer.html import HtmlRenderer
from app.schemas import (
    CardDeck,
    DeckEdit,
    FitBlock,
    FitCheck,
    FitResult,
    Issue,
    Job,
    JobStatus,
    LogLine,
    SafeguardVerdict,
)
from app.services import safeguard, visual_qa

log = logging.getLogger("deck_versions")
router = APIRouter()


class DeckVersion(BaseModel):
    """One saved state of a Deck, as stored in `deck_versions.doc`."""
    version: int
    deck: CardDeck
    files: list[str] = Field(default_factory=list)       # rendered slide files, in slide order
    rerendered: list[int] = Field(default_factory=list)  # slides rendered for this version (others reuse files)
    changes: list[dict[str, Any]] = Field(default_factory=list)  # what changed from the previous version
    edited_by: UUID | None = None                        # None: version 1, the pipeline's
    edited_by_email: str | None = None
    created_at: datetime


class DeckVersionOut(BaseModel):
    """A version as the Admin lists it (history): who, when, what changed, and the Deck itself."""
    version: int
    deck: CardDeck
    rerendered: list[int]
    changes: list[dict[str, Any]]
    edited_by: UUID | None
    edited_by_email: str | None
    created_at: datetime
    current: bool


# ---------------------------------------------------------------- storage

def initial(job: Job) -> DeckVersion:
    """Version 1: the Deck the pipeline produced (also how a Job from before versions reads)."""
    return DeckVersion(version=1, deck=job.deck, files=[u.rsplit("/", 1)[-1] for u in job.slide_urls],
                       created_at=job.created_at)


def _insert(c: Connection, job_id: str, v: DeckVersion) -> None:
    c.execute("insert into deck_versions (job_id, version, edited_by, created_at, doc) values (%s, %s, %s, %s, %s) "
              "on conflict (job_id, version) do nothing",
              (job_id, v.version, v.edited_by, v.created_at, store._json(v)))


def ensure_initial(c: Connection, job: Job) -> None:
    """Store version 1 if this Job has none yet (old Jobs; Decks never edited). Needs the Job unedited."""
    if job.deck and job.deck_version == 1:
        _insert(c, job.id, initial(job))


def list_versions(org_id: UUID, job: Job) -> list[DeckVersion]:
    """Oldest first. Another Organization's rows are invisible (RLS), whatever the Job id."""
    with store.tenant(org_id) as c:
        rows = c.execute("select doc::text from deck_versions where job_id = %s order by version",
                         (job.id,)).fetchall()
    found = [v for (raw,) in rows if (v := store._load(DeckVersion, raw))]
    if not found and job.deck:  # never edited or approved (or from before versions): the pipeline's Deck
        found = [initial(job)]
    return found


# ---------------------------------------------------------------- pinning (approve)

def pin(job: Job, deck_version: int) -> int:
    """The version an approval pins: the one the Approver saw, which must still be the current one. Use the result
    for the Post's `deck_version` (#21)."""
    if deck_version != job.deck_version:
        raise HTTPException(409, f"you approved version {deck_version}, but the Deck is at version "
                                 f"{job.deck_version} now (someone edited it). Review the current version and "
                                 "approve again.")
    return deck_version


# ---------------------------------------------------------------- caption and hashtags

def split_caption(caption: str) -> tuple[str, list[str]]:
    """(text, hashtags): the hashtag lines at the end of a caption, separated from the text above them."""
    lines = caption.rstrip().split("\n")
    tags: list[str] = []
    while lines and lines[-1].strip() and not visual_qa.HASHTAG.sub("", lines[-1]).strip():
        tags = visual_qa.HASHTAG.findall(lines.pop()) + tags
    return "\n".join(lines).rstrip(), tags


def compose_caption(text: str, tags: list[str]) -> str:
    return text.rstrip() + ("\n" + " ".join(tags) if tags else "")


def _tag(raw: str) -> str:
    t = raw.strip()
    t = t if t.startswith("#") else f"#{t}"
    if not visual_qa.HASHTAG.fullmatch(t):
        raise HTTPException(422, f"{raw!r} is not a hashtag: letters, numbers and _ only, no spaces")
    return t


# ---------------------------------------------------------------- applying an edit

def apply_edit(deck: CardDeck, edit: DeckEdit) -> tuple[CardDeck, list[int], list[dict[str, Any]]]:
    """→ (new Deck, `order`: the base index of the slide now at each position, changes with before and after)."""
    new = deck.model_copy(deep=True)
    old = sorted(deck.slides, key=lambda s: s.index)
    order = list(range(len(old)))
    changes: list[dict[str, Any]] = []
    if edit.slides is not None:
        order = [s.from_index for s in edit.slides]
        if sorted(order) != list(range(len(old))):
            raise HTTPException(422, f"slides must list each of the {len(old)} slides once (from_index 0–"
                                     f"{len(old) - 1}); slides can be reordered, not added or removed")
        slides = []
        for pos, e in enumerate(edit.slides):
            s = old[e.from_index].model_copy(update={"index": pos, "heading": e.heading.strip(), "body": e.body.strip()})
            for f in ("heading", "body"):
                if getattr(s, f) != getattr(old[e.from_index], f):
                    changes.append({"field": f"slide.{f}", "slide": pos, "from_slide": e.from_index,
                                    "before": getattr(old[e.from_index], f), "after": getattr(s, f)})
            slides.append(s)
        new.slides = slides
        if order != sorted(order):
            changes.insert(0, {"field": "order", "before": [s.heading for s in old],
                               "after": [s.heading for s in slides], "order": order})
    if edit.caption is not None or edit.hashtags is not None:
        text0, tags0 = split_caption(deck.caption)
        text = (edit.caption if edit.caption is not None else text0).strip()
        tags = list(dict.fromkeys(_tag(t) for t in edit.hashtags)) if edit.hashtags is not None else tags0
        if not text:
            raise HTTPException(422, "the caption can't be empty")
        new.caption = compose_caption(text, tags)
        new.hashtags = tags[:5]
        if text != text0:
            changes.append({"field": "caption", "before": text0, "after": text})
        if tags != tags0:
            changes.append({"field": "hashtags", "before": tags0, "after": tags})
    if len(visual_qa.HASHTAG.findall(new.caption)) > 5:
        raise HTTPException(422, "Instagram allows at most 5 hashtags, counting any in the caption text")
    return new, order, changes


def remap_issues(issues: list[Issue], order: list[int], rerendered: set[int]) -> list[Issue]:
    """The Issues of the base version that still apply to the new one, at the slides' new positions. Host QA
    recomputes PII, credentials and the hashtag count for every edit, and overflow for each re-rendered slide, so
    those are dropped here. The reviewer's Issues (facts, tone, sensitive, links) stay: a person decides on them."""
    at = {old: new for new, old in enumerate(order)}
    out = []
    for i in issues:
        if i.category == "pii" or (i.category == "tone" and i.slide_index is None and "hashtags" in i.message):
            continue
        idx = i.slide_index if i.slide_index is None else at.get(i.slide_index)
        if i.slide_index is not None and idx is None:
            continue
        if i.category == "visual" and idx in rerendered:
            continue
        out.append(i.model_copy(update={"slide_index": idx}))
    return out


def edited_fields(before: dict[int, list[str]], order: list[int], changes: list[dict[str, Any]]) -> dict[int, list]:
    """Which text a person has changed on each slide, carried to the slides' new positions."""
    out = {pos: set(before.get(old, [])) for pos, old in enumerate(order)}
    for c in changes:
        if c["field"].startswith("slide."):
            out[c["slide"]].add(c["field"].removeprefix("slide."))
    return {pos: sorted(f) for pos, f in out.items() if f}


async def render_changed(job: Job, deck: CardDeck) -> tuple[list[str], list[RenderedSlide]]:
    """Render only the slides whose HTML differs from every slide of the current version; the rest keep their
    files. → (file names in slide order, the slides rendered now)."""
    folder = settings.output_dir / job.id
    renderer = HtmlRenderer(theme=settings.theme)
    briefs = {b.id: b for b in orchestrator._included(job)}
    before: dict[str, str] = {}  # HTML → the file rendered from it
    for name in (u.rsplit("/", 1)[-1] for u in job.slide_urls):
        html = folder / name.replace(".jpg", ".html")
        if html.is_file() and (folder / name).is_file():
            before.setdefault(html.read_text(encoding="utf-8"), name)
    token = secrets.token_hex(4)  # this save's files never overwrite another save's
    files, pages = [], []
    for s in sorted(deck.slides, key=lambda s: s.index):
        html = renderer.html(deck, s, briefs)
        if html in before:
            files.append(before[html])
        else:
            stem = f"slide-{s.index:02d}.{token}"
            files.append(f"{stem}.jpg")
            pages.append((s.index, stem, html))
    return files, await renderer.render_pages(pages, folder)


def _secrets() -> tuple[str, ...]:
    return tuple(x for x in (settings.supabase_service_key,) if x)  # as pipeline QA; account tokens: at Approve


# ---------------------------------------------------------------- routes

def _job_or_404(org_id: UUID, job_id: str) -> Job:
    if not (job := store.get_job(org_id, job_id)):
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/versions", response_model=list[DeckVersionOut])
def get_versions(job_id: str, ctx: auth.Viewer) -> list[DeckVersionOut]:
    """The Deck's version history, newest first."""
    job = _job_or_404(ctx.org_id, job_id)
    return [DeckVersionOut(**v.model_dump(exclude={"files"}), current=v.version == job.deck_version)
            for v in reversed(list_versions(ctx.org_id, job))]


@router.post("/jobs/{job_id}/versions", response_model=Job, status_code=201)
async def save_version(job_id: str, edit: DeckEdit, ctx: auth.Editor) -> Job:
    """Save an edit as the next Deck version (see the module docstring for the steps)."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    _editable(job, edit.base_version)
    deck, order, changes = apply_edit(job.deck, edit)
    if not changes:
        raise HTTPException(422, "nothing changed")

    # The Safeguard's host rules, now: a blocked phrase is never saved, rendered or sent to the model.
    if findings := safeguard.rule_findings(deck):
        policy = safeguard.load_policy()
        verdict = SafeguardVerdict(decision="block", summary=safeguard.merge(findings, None)[1], findings=findings,
                                   checked_by=["rules"], policy_version=policy.version,
                                   content_hash=safeguard.content_hash(deck), checked_at=datetime.now(UTC))
        await asyncio.to_thread(audit.record, ctx, "safeguard.edit_blocked", job.id, stage="edit",
                                base_version=edit.base_version, changes=changes,
                                **orchestrator.verdict_detail(verdict))
        raise HTTPException(422, {"code": "blocked", "message": "Not saved: " + verdict.summary,
                                  "findings": [f.model_dump() for f in findings]})
    text = [*(f"{s.heading}\n{s.body}" for s in deck.slides), deck.caption]
    if any(i.message.startswith("credential") for t in text for i in visual_qa.scan_text(t, "", None, _secrets())):
        raise HTTPException(422, "Not saved: the text contains a credential-like string. Remove it.")

    files, rendered = await render_changed(job, deck)
    qa = visual_qa.run_qa(rendered, deck, _secrets())
    overflow = [i for i in qa.issues if i.category == "visual" and i.severity == "block"]
    if overflow and not edit.allow_overflow:
        raise HTTPException(422, {"code": "overflow",
                                  "message": "Text overflows its box on " + ", ".join(
                                      sorted({f"slide {i.slide_index + 1}" for i in overflow})) +
                                  ". Shorten it, or save anyway (it stays a blocking Issue).",
                                  "issues": [i.model_dump() for i in overflow]})
    rerendered = {r.index for r in rendered}
    issues = remap_issues(job.issues, order, rerendered) + qa.issues
    edited_text = edited_fields(job.edited_text, order, changes)

    def commit() -> Job:
        with store.locked_job(ctx.org_id, job.id, user_id=ctx.user.id) as (c, cur):
            if cur is None:
                raise HTTPException(404, "job not found")
            _editable(cur, edit.base_version)  # nobody saved or approved it while this one rendered
            ensure_initial(c, cur)
            v = DeckVersion(version=cur.deck_version + 1, deck=deck, files=files, rerendered=sorted(rerendered),
                            changes=changes, edited_by=ctx.user.id, edited_by_email=ctx.user.email or None,
                            created_at=datetime.now(UTC))
            _insert(c, cur.id, v)
            cur.deck, cur.deck_version = deck, v.version
            cur.slide_urls = [f"/assets/{cur.id}/{f}" for f in files]
            cur.issues = issues
            cur.edited_text = edited_text
            cur.safeguard, cur.safeguard_pending = None, True
            cur.log.append(LogLine(time=datetime.now(orchestrator.KST).strftime("%H:%M:%S"), stage="edit",
                                   message=f"version {v.version} saved by {ctx.user.email or 'a Member'}: "
                                           f"{_describe(changes)} · {len(rerendered)} slide(s) re-rendered · "
                                           "safeguard checking"))
            if cur.status == JobStatus.SCHEDULED:
                _hold(c, ctx, cur, v.version)
            store.write_job(c, cur)
            audit.write(c, ctx.org_id, audit.entry(ctx, "deck.edited", cur.id, version=v.version,
                                                   base_version=edit.base_version, changes=changes,
                                                   rerendered=v.rerendered,
                                                   overflow_accepted=bool(overflow) or None))
            return cur
    saved = await asyncio.to_thread(commit)
    await start_check(ctx.org_id, saved.id, saved.deck_version, {"id": str(ctx.user.id), "email": ctx.user.email})
    out = assets.for_admin(ctx.org_id, saved)
    return out.model_copy(update={"slide_sources": slide_sources.for_job(out)})


@router.post("/jobs/{job_id}/fit", response_model=FitResult)
async def fit(job_id: str, req: FitCheck, ctx: auth.Editor) -> FitResult:
    """Does one slide's text fit, as typed? The Deck editor asks while someone edits ("Fits · 2 lines"), before
    anything is saved. Renders that slide's HTML with the edited Deck and measures it like visual QA does (auto-fit
    first), without a screenshot or any stored file."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    if not job.deck:
        raise HTTPException(409, "this Job has no Deck yet")
    if not 0 <= req.position < len(req.slides):
        raise HTTPException(422, "position is not one of the slides")
    deck, _, _ = apply_edit(job.deck, DeckEdit(base_version=req.base_version, slides=req.slides))
    slide = deck.slides[req.position]
    renderer = HtmlRenderer(theme=settings.theme)
    html = renderer.html(deck, slide, {b.id: b for b in orchestrator._included(job)})
    with tempfile.TemporaryDirectory(prefix="wok-fit-") as tmp:
        [measured] = await renderer.render_pages([(slide.index, "fit", html)], Path(tmp), screenshot=False)
    blocks = [FitBlock(selector=m.selector, lines=m.lines, font_px=m.font_px, overflow=m.overflow)
              for m in measured.measures if m.lines]
    return FitResult(position=req.position, fits=not any(m.overflow for m in measured.measures),
                     small_text=any(b.font_px < visual_qa.MIN_FONT_PX for b in blocks), blocks=blocks)


def _hold(c, ctx: auth.Ctx, job: Job, version: int) -> None:
    """Edited after approval (#26): the scheduled Post must not go out with what was approved before. Put it on hold,
    in the edit's own transaction, and hand the Deck back to Review; approving version `version` resumes it."""
    reason = f"the Deck was edited after approval (now version {version}); approve it again to post it"
    row = c.execute("update posts set state = 'on_hold', held_at = now(), held_reason = %s, updated_at = now() "
                    "where job_id = %s and state = 'scheduled' returning id, scheduled_at", (reason, job.id)).fetchone()
    job.status = JobStatus.READY_FOR_REVIEW
    for s in job.pipeline:
        if s.name == orchestrator.STEPS["publish"][0]:
            s.state, s.note = "pending", "On hold · edited after approval"
    if row:
        audit.write(c, ctx.org_id, audit.entry(ctx, "post.held", job.id, post=str(row[0]), reason=reason,
                                               scheduled_at=row[1].isoformat() if row[1] else None))


def _editable(job: Job, base_version: int) -> None:
    """A Deck in review, or one approved for later (#26: the edit puts its scheduled Post on hold)."""
    if job.status not in (JobStatus.READY_FOR_REVIEW, JobStatus.SCHEDULED) or not job.deck:
        raise HTTPException(409, f"only a Deck in review or scheduled can be edited; this one is {job.status}")
    if base_version != job.deck_version:
        raise HTTPException(409, {"code": "stale", "message": f"Not saved: someone saved version "
                                  f"{job.deck_version} while you were editing version {base_version}. "
                                  "Reload to see it, then make your change again."})


def _describe(changes: list[dict[str, Any]]) -> str:
    fields = dict.fromkeys(c["field"].replace("slide.", "") for c in changes)
    return ", ".join(fields)


# ---------------------------------------------------------------- the Safeguard re-check (durable)

def check_workflow_id(org_id: UUID | str, job_id: str, version: int) -> str:
    return f"deckcheck-{org_id}-{job_id}-v{version}"


async def _check_step(org: str, job_id: str, version: int, by: dict | None) -> str:
    """Run the full Safeguard on the Job's version `version` and record the verdict, unless the Deck has moved on
    (a newer edit starts its own check). Fails closed."""
    org_id = UUID(org)
    job = await asyncio.to_thread(store.get_job, org_id, job_id)
    if not job or job.deck_version != version or not job.safeguard_pending:
        return "stale"
    paths = assets.slide_files(job)
    try:
        verdict = await orchestrator.run_safeguard(org_id, job_id, job.deck, paths,
                                                   use_model=settings.demo_mode == "live")
    except Exception as e:  # noqa: BLE001 — fail closed
        verdict = SafeguardVerdict(decision="block", summary="Blocked because the safety check could not run.",
                                   model_error=str(e)[:300], content_hash=safeguard.content_hash(job.deck, paths))
    verdict = verdict.model_copy(update={"deck_version": version})

    def record() -> str:
        with store.locked_job(org_id, job_id) as (c, cur):
            if not cur or cur.deck_version != version or not cur.safeguard_pending:
                return "stale"
            cur.safeguard, cur.safeguard_pending = verdict, False
            nb = sum(f.severity == "block" for f in verdict.findings)
            cur.log.append(LogLine(
                time=datetime.now(orchestrator.KST).strftime("%H:%M:%S"), stage="safeguard",
                level="warn" if verdict.decision == "block" else "info",
                message=f"version {version}: {verdict.decision.upper()} · {nb} block · checked by "
                        f"{'+'.join(verdict.checked_by)} · policy {verdict.policy_version}"
                        + (" · edit it again or reject" if verdict.decision == "block" else "")))
            store.write_job(c, cur)
            return verdict.decision
    result = await asyncio.to_thread(record)
    if result == "block":
        await orchestrator._audit(org_id, by, "safeguard.blocked", job_id, stage="edit", deck_version=version,
                                  **orchestrator.verdict_detail(verdict))
    return result


@DBOS.workflow(name="wok.deck_check", max_recovery_attempts=5,
               serialization_type=WorkflowSerializationFormat.PORTABLE)
async def deck_check_workflow(org: str, job_id: str, version: int, by: dict | None = None) -> str:
    return await DBOS.run_step_async({"name": "safeguard"}, _check_step, org, job_id, version, by)


async def start_check(org_id: UUID, job_id: str, version: int, by: dict | None, *, suffix: str = "") -> None:
    """Queue the check. Once this returns it survives a restart (DBOS resumes it)."""
    queue = await DBOS.retrieve_queue_async(durable.DECK_CHECK_QUEUE)
    with SetWorkflowID(check_workflow_id(org_id, job_id, version) + suffix):
        await queue.enqueue_async(deck_check_workflow, str(org_id), job_id, version, by)


def recover_checks() -> None:
    """At startup: a Deck still waiting for its check whose workflow won't run (it ran out of attempts, or belongs
    to another WORKFLOW_VERSION) is checked again, so it can't stay "checking" forever. Blocking."""
    for org_id, job in store.jobs_in({JobStatus.READY_FOR_REVIEW}):
        if not job.safeguard_pending:
            continue
        wid = check_workflow_id(org_id, job.id, job.deck_version)
        st = DBOS.get_workflow_status(wid)
        alive = st is not None and st.status in ("PENDING", "ENQUEUED")
        if alive and st.app_version == settings.workflow_version:
            continue
        if alive:
            DBOS.cancel_workflow(wid)
        with SetWorkflowID(f"{wid}-r{int(time.time())}"):
            DBOS.retrieve_queue(durable.DECK_CHECK_QUEUE).enqueue(deck_check_workflow, str(org_id), job.id,
                                                                  job.deck_version, None)
