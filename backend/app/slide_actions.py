"""Swap a photo, regenerate one slide (#23; docs/spec/v1.md W4 "Light editing", pseudocode 6).

From Review, without rerunning the Job:

- **Swap a photo** on an event slide: another photo the host vetted for that event at research time
  (`Job.photo_candidates`, local copies), a stock photo (same rules as the pipeline: the event's category, each
  stock photo at most once per Deck, labeled AI-generated on the card), or no photo (the slide becomes a Poster).
  Uploading the Organization's own photos is the photo library's (#24).
- **Regenerate one slide**: the copywriter rewrites that slide's heading and body from the same research, with an
  optional instruction ("shorter", "mention the night market"). Every other slide stays as it is. The host checks
  the new text against the research like the golden set does (an invented price, date or "free" is a blocking
  Issue) and drops the reviewer's earlier fact and tone notes on that slide, which were about the old text.

Both go through the same save as a text edit (`deck_versions.save`): refused unless the Deck is in review (or
scheduled, which puts its Post on hold) at the version the person saw, the Safeguard's host rules at once, only the
slides that changed re-rendered, a new Deck version, and the full Safeguard check of that version before it can be
approved. Neither counts against the Deck quota (GLOSSARY "Deck quota"); agent rewrites have a fair-use cap per
Deck (`REGENERATIONS_PER_DECK`, default 10) because each one is an agent call.
"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import assets, auth, guides, org_profile
from app.config import settings
from app.deck_versions import _editable, _job_or_404, list_versions, save
from app.golden.checks import unsupported_facts
from app.pipeline import orchestrator
from app.pipeline.agent_runner import run_stage
from app.schemas import EventBrief, ImageAsset, Issue, Job, Slide
from app.services import photos

router = APIRouter()


# ---------------------------------------------------------------- shapes

class PhotoChoice(BaseModel):
    """What to put on the slide: one of the event's vetted photos (`candidate`: its index), a stock photo
    (`stock_id`), or none (a Poster)."""
    base_version: int = Field(ge=1)
    source: Literal["candidate", "stock", "none"]
    candidate: int | None = Field(None, ge=0)
    stock_id: str | None = Field(None, max_length=80)


class PhotoOption(BaseModel):
    source: Literal["candidate", "stock", "none"]
    candidate: int | None = None
    stock_id: str | None = None
    url: str | None = None          # Admin link (signed for vetted photos; /stock/<file> for stock); None: no photo
    label: str                      # what it shows / where it is from
    credit: str = ""
    license: str = ""
    current: bool = False
    allowed: bool = True
    why_not: str | None = None      # e.g. "used on slide 3", "a festival photo on a pop-up"


class PhotoOptions(BaseModel):
    slide_index: int
    event_id: str
    event_title: str
    options: list[PhotoOption]


class RegenerateRequest(BaseModel):
    base_version: int = Field(ge=1)
    instruction: str = Field("", max_length=300)  # optional: what to change ("shorter", "lead with the price")


class SlideRewrite(BaseModel):
    """The copywriter's new text for one slide; layout, event, point and photo stay as they are."""
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field("", max_length=2000)


# ---------------------------------------------------------------- helpers

def _slide(job: Job, n: int) -> Slide:
    if not job.deck:
        raise HTTPException(409, "this Job has no Deck yet")
    slides = sorted(job.deck.slides, key=lambda s: s.index)
    if not 0 <= n < len(slides):
        raise HTTPException(404, f"slide {n} is not in the Deck (0–{len(slides) - 1})")
    return slides[n]


def _event(job: Job, slide: Slide) -> EventBrief:
    if slide.layout != "event":
        raise HTTPException(409, "only event slides carry a photo")
    b = next((b for b in orchestrator._included(job) if b.id == slide.event_id), None)
    if not b:
        raise HTTPException(409, "this slide's event is not in the verified research")
    return b


def _describe(img: ImageAsset | None, stock_id: str | None) -> str:
    if img is None:
        return "no photo (poster)"
    if stock_id or img.license == "ai_generated":
        return f"stock photo {stock_id or ''} (AI-generated)".replace("  ", " ")
    return f"official photo · {img.credit}"


def _stock_users(job: Job, n: int) -> dict[str, int]:
    """Stock ids already on other slides → that slide's index."""
    return {s.stock_id: s.index for s in job.deck.slides if s.stock_id and s.index != n}


def _same(a: ImageAsset | None, b: ImageAsset | None) -> bool:
    return (a.url if a else None) == (b.url if b else None)


# ---------------------------------------------------------------- swap a photo

@router.get("/jobs/{job_id}/slides/{n}/photos", response_model=PhotoOptions)
def photo_options(job_id: str, n: int, ctx: auth.Viewer) -> PhotoOptions:
    """What the slide's photo can be swapped to, with the reason when an option isn't allowed."""
    job = _job_or_404(ctx.org_id, job_id)
    slide = _slide(job, n)
    b = _event(job, slide)
    opts: list[PhotoOption] = []
    for i, img in enumerate(job.photo_candidates.get(b.id, [])):
        opts.append(PhotoOption(source="candidate", candidate=i, url=assets.photo_link(ctx.org_id, job.id, img.url),
                                label="Official photo", credit=img.credit, license=img.license,
                                current=_same(slide.image, img)))
    used = _stock_users(job, n)
    for e in photos.stock_catalog():
        why = (f"a {e['category']} photo on a {b.category} event" if e["category"] != b.category
               else f"already on slide {used[e['id']] + 1}" if e["id"] in used else None)
        opts.append(PhotoOption(source="stock", stock_id=e["id"], url=f"/stock/{e['file']}", label=e["shows"],
                                credit="Image · AI-generated", license="ai_generated",
                                current=slide.stock_id == e["id"], allowed=why is None, why_not=why))
    opts.append(PhotoOption(source="none", label="No photo: a color poster", current=slide.image is None))
    return PhotoOptions(slide_index=n, event_id=b.id, event_title=b.title_en, options=opts)


@router.post("/jobs/{job_id}/slides/{n}/photo", response_model=Job, status_code=201)
async def swap_photo(job_id: str, n: int, choice: PhotoChoice, ctx: auth.Editor) -> Job:
    """Put another photo on an event slide: a new Deck version, that slide re-rendered."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    _editable(job, choice.base_version)
    slide = _slide(job, n)
    b = _event(job, slide)
    image, stock_id = None, None
    if choice.source == "candidate":
        found = job.photo_candidates.get(b.id, [])
        if choice.candidate is None or choice.candidate >= len(found):
            raise HTTPException(422, f"{b.title_en} has {len(found)} vetted photo(s); pick one of them")
        image = found[choice.candidate]
        if assets.photo_link(ctx.org_id, job.id, image.url) is None:
            raise HTTPException(409, "that photo's file is no longer on this server")
    elif choice.source == "stock":
        entry = next((e for e in photos.stock_catalog() if e["id"] == choice.stock_id), None)
        if entry is None:
            raise HTTPException(422, "not one of the stock photos")
        if entry["category"] != b.category:
            raise HTTPException(422, f"a {entry['category']} stock photo can't stand for a {b.category} event: "
                                     "readers would take it for the real thing")
        if (other := _stock_users(job, n).get(entry["id"])) is not None:
            raise HTTPException(422, f"that stock photo is already on slide {other + 1}; each is used once per Deck")
        image, stock_id = photos.stock_asset(entry), entry["id"]
    if _same(slide.image, image):
        raise HTTPException(422, "that is already the slide's photo")
    deck = job.deck.model_copy(deep=True)
    for s in deck.slides:
        if s.index == slide.index:
            s.image, s.stock_id = image, stock_id
    changes = [{"field": "slide.photo", "slide": n, "before": _describe(slide.image, slide.stock_id),
                "after": _describe(image, stock_id)}]
    # A photo changes the card's style (poster ↔ photo), so text can stop fitting: saved anyway, the overflow stays a
    # blocking Issue on the slide, as with a rewrite.
    return await save(ctx, job, deck, list(range(len(deck.slides))), changes, choice.base_version,
                      allow_overflow=True, edited_text=job.edited_text, action="photo")


@router.get("/stock/{name}", include_in_schema=False)
def stock_file(name: str) -> FileResponse:
    """The stock photos' own files (our AI-generated set, nobody's data), for the Admin's swap picker."""
    files = {e["file"] for e in photos.stock_catalog()}
    if name not in files:
        raise HTTPException(404, "not found")
    return FileResponse(photos.STOCK_DIR / name, media_type="image/jpeg", headers={"cache-control": "max-age=3600"})


# ---------------------------------------------------------------- regenerate one slide

REWRITE_TASK = (
    "Use the copywriter subagent. Rewrite ONE slide of this CardDeck: the slide at `slide_index` in `deck`. Keep its "
    "layout and what it is about (the same event or guide point); return only its new `heading` and `body`, under "
    "the same length rules. Use only facts in `briefs` (events) or `guide` (verified guide points); no new prices, "
    "dates or claims. Follow `instruction` when it is given, unless it asks for something the rules forbid. "
    "The other slides are context: don't repeat them. ")


def _fixture_rewrite(job: Job, slide: Slide) -> SlideRewrite:
    """Fixture mode: a fixed, honest rewrite from the research itself (no agent)."""
    b = next((b for b in job.briefs if b.id == slide.event_id), None)
    if slide.layout == "event" and b:
        shown = slide.body or b.why_go  # the card shows the why-go line when the body is empty
        if line := next((t for t in [b.why_go, *b.foreigner_tips] if t and t != shown), None):
            return SlideRewrite(heading=slide.heading, body=line)
    g = guides.verified(job)
    p = next((p for p in (g.points if g else []) if p.id == slide.point_id), None)
    if p and p.detail and p.detail != slide.body:
        return SlideRewrite(heading=p.title, body=p.detail)
    first = slide.body.split(". ")[0].rstrip(".")
    return SlideRewrite(heading=slide.heading, body=f"{first}." if first else slide.body)  # shorter: one sentence


def _uses(org_id, job: Job) -> int:
    return sum(v.action == "regenerate" for v in list_versions(org_id, job))


def fact_issues(job: Job, slide: Slide, n: int) -> list[Issue]:
    """The rewritten text against the research (same rule as the golden set's facts check): blocking Issues."""
    b = next((b for b in orchestrator._included(job) if b.id == slide.event_id), None)
    corpus = [b] if b and slide.layout == "event" else orchestrator._included(job)
    fails, _ = unsupported_facts(f"{slide.heading}\n{slide.body}", corpus, guides.verified(job))
    return [Issue(severity="block", category="fact", slide_index=n,
                  message=f"Regenerated text: {f}. Edit it or regenerate again.") for f in fails]


@router.post("/jobs/{job_id}/slides/{n}/regenerate", response_model=Job, status_code=201)
async def regenerate(job_id: str, n: int, req: RegenerateRequest, ctx: auth.Editor) -> Job:
    """The copywriter rewrites one slide; the result is a new Deck version with only that slide re-rendered."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    _editable(job, req.base_version)
    slide = _slide(job, n)
    used = await asyncio.to_thread(_uses, ctx.org_id, job)
    if used >= settings.regenerations_per_deck:
        raise HTTPException(429, f"fair use: this Deck has had {used} slide rewrites, the most one Deck gets. Edit "
                                 "the text yourself, or start a new Deck.")
    instruction = req.instruction.strip()
    if settings.demo_mode == "live":
        guide = guides.verified(job)
        rewrite = await run_stage(
            "copywriter", REWRITE_TASK + orchestrator.PROFILE_TASK,
            {"kind": job.kind, "deck": job.deck.model_dump(mode="json"), "slide_index": n,
             "instruction": instruction or None,
             "briefs": [b.model_dump(mode="json") for b in orchestrator._included(job)],
             "guide": guide.model_dump(mode="json") if guide else None,
             "org_profile": org_profile.for_agents(job.org_profile)},
            SlideRewrite, job_id=job.id, org_id=ctx.org_id)
    else:
        rewrite = _fixture_rewrite(job, slide)
    heading, body = rewrite.heading.strip(), rewrite.body.strip()
    if (heading, body) == (slide.heading, slide.body):
        raise HTTPException(422, "the rewrite came back the same as the slide; try again with an instruction")

    deck = job.deck.model_copy(deep=True)
    new = next(s for s in deck.slides if s.index == slide.index)
    new.heading, new.body = heading, body
    changes: list[dict[str, Any]] = [
        {"field": f"slide.{f}", "slide": n, "from_slide": n, "before": getattr(slide, f), "after": getattr(new, f),
         "by": "agent"} for f in ("heading", "body") if getattr(slide, f) != getattr(new, f)]
    if instruction:
        changes[0]["instruction"] = instruction
    # The reviewer's fact and tone notes on this slide were about the old text; the host checks the new text here,
    # and the Safeguard checks the whole version. What a person typed on it before is gone, so it's no longer "edited".
    base = job.model_copy(update={"issues": [i for i in job.issues if not (
        i.slide_index == n and i.category in ("fact", "tone"))]})
    edited = {k: v for k, v in job.edited_text.items() if k != n}
    return await save(ctx, base, deck, list(range(len(deck.slides))), changes, req.base_version,
                      allow_overflow=True, edited_text=edited, action="regenerate",
                      log_note=f" (agent rewrite of slide {n + 1}{': ' + instruction if instruction else ''})",
                      extra_issues=fact_issues(job, new, n))

