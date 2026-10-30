"""Fact sources on slides (#25, story 2): for each slide, the facts it shows and where each came from.

Deterministic and host-side, from what the Job already holds; no agent is asked:

- Event and map slides show their event's brief (the renderer fills them from `EventBrief`, see
  renderer/templates/event.html and map.html), so their facts are the brief fields those templates show. Each one
  gets the sources the researcher recorded for that field (`EventBrief.facts`, #20); a field with no recorded
  source, and every field on an older Job, falls back to the event's own links and Source material
  (`recorded=False`), so the reviewer still sees something checkable.
- Links come with the host's link-check result (ok, redirect, dead, suspicious, timeout, or unchecked when it never
  ran). Source material is named only when it is this Job's own (`Job.material`, which the API checked belongs to
  the caller's Organization when the Job was created); a reference to anything else shows no name and no quote.
- Claims in the copywriter's own text (heading and body: prices, dates, times, percentages, "free") that no brief
  supports are listed as `unsourced`, using the golden set's rule checker (`app.golden.checks.unsupported_facts`),
  so the review page and the golden set agree on what counts as unsupported. Tips slides hold general know-how
  (last trains, T-money), so theirs are marked `general` instead of being treated as a problem.

Text a person changed in a Deck edit (#19, `Job.edited_text`) is flagged `edited` (slide) and, for an event
slide's heading, the title fact loses the research's sources: the reviewer re-checks it.

Computed on the way out of the API, never stored: it always matches the Deck's current text and the latest link
check, and Jobs stored before #20 or #25 get it too.
"""

from app import guides
from app.golden.checks import unsupported_facts
from app.renderer.html import access_badges, daterange
from app.schemas import (
    EventBrief,
    FactSource,
    Job,
    LinkCheck,
    MaterialRef,
    Slide,
    SlideFact,
    SlideFactSource,
    SlideSources,
)

# Which brief fields each layout shows, in the order a reader sees them (renderer templates).
SHOWN: dict[str, tuple[str, ...]] = {
    "event": ("title", "dates", "venue", "station", "price", "booking", "access", "why_go"),
    "map": ("station", "venue", "address"),
}
NOT_YOURS = "not one of this request's Source material"


def _value(b: EventBrief, field: str, slide: Slide) -> str:
    match field:
        case "title":
            return f"{b.title_en} ({b.title_ko})" if b.title_ko else b.title_en
        case "dates":
            return daterange(b.start_date, b.end_date)
        case "venue":
            return b.venue_en + (f" ({b.venue_ko})" if b.venue_ko else "")
        case "station":
            return b.nearest_station or ""
        case "price":
            return b.price or ""
        case "booking":
            return b.booking or ""
        case "address":
            return b.address_ko
        case "access":
            return " · ".join(x["text"] for x in access_badges(b))
        case "why_go":
            return "" if slide.body else b.why_go  # the template shows the slide's own body instead, if any
    return ""


class _Lookup:
    def __init__(self, job: Job):
        self.checks: dict[str, LinkCheck] = {c.url: c for c in job.verification.checks} if job.verification else {}
        self.material: dict[str, MaterialRef] = {m.id: m for m in job.material}

    def link(self, url: str, **kw) -> SlideFactSource:
        c = self.checks.get(url)
        if not c:
            return SlideFactSource(origin="link", url=url, status="unchecked", **kw)
        return SlideFactSource(origin="link", url=url, status=c.status, http_code=c.http_code, reason=c.reason, **kw)

    def material_source(self, material_id: str | None, **kw) -> SlideFactSource:
        m = self.material.get(material_id or "")
        if not m:  # another Organization's id, or an id the agent made up: say nothing about it
            return SlideFactSource(origin="material", verified=False, note=NOT_YOURS)
        return SlideFactSource(origin="material", material_id=m.id, material_name=m.name, **kw)

    def recorded(self, f: FactSource) -> SlideFactSource:
        if f.origin == "material":
            return self.material_source(f.material_id, quote=f.quote, verified=f.verified, note=f.note)
        if not f.url:
            return SlideFactSource(origin="link", status="unchecked", verified=f.verified, note=f.note)
        return self.link(f.url, verified=f.verified, note=f.note)

    def event_sources(self, b: EventBrief) -> list[SlideFactSource]:
        return [self.link(str(s.url)) for s in b.sources] + [self.material_source(i) for i in b.materials]


def _facts(look: _Lookup, b: EventBrief, slide: Slide) -> list[SlideFact]:
    out = []
    for field in SHOWN.get(slide.layout, ()):
        value = _value(b, field, slide)
        if not value:
            continue
        recorded = [look.recorded(f) for f in b.facts if f.field == field]
        out.append(SlideFact(field=field, value=value, recorded=bool(recorded),
                             sources=recorded or look.event_sources(b)))
    return out


def for_job(job: Job) -> list[SlideSources]:
    """Where the facts on each slide of the Job's Deck came from (empty when there is no Deck yet)."""
    if not job.deck:
        return []
    look = _Lookup(job)
    guide = guides.verified(job)  # a Guide Deck's verified points (#27)
    all_points = {p.id: p for p in job.guide.points} if job.guide else {}
    kept_points = {p.id for p in guide.points} if guide else set()
    excluded = set(job.verification.excluded_event_ids) if job.verification else set()
    briefs = {b.id: b for b in job.briefs}
    included = [b for b in job.briefs if b.id not in excluded]
    out = []
    for slide in sorted(job.deck.slides, key=lambda s: s.index):
        b = briefs.get(slide.event_id or "")
        point = all_points.get(slide.point_id or "")
        s = SlideSources(slide_index=slide.index, layout=slide.layout, event_id=slide.event_id,
                         event_title=b.title_en if b else (point.title if point else None),
                         links=[look.link(u) for u in dict.fromkeys(slide.source_urls)],
                         general=slide.layout in ("tips", "checklist"))
        if slide.layout == "point":
            if not point:
                s.note = "This slide names a guide point that is not in the research."
            elif point.id not in kept_points:
                s.note = "This slide's point was left off at Verify: it has no official source."
            else:
                s.facts = [SlideFact(field="other", value=point.title, recorded=True,
                                     sources=[look.recorded(f) for f in point.facts])]
        if slide.event_id and not b:
            s.note = "This slide names an event that is not in the research."
        elif b and b.id in excluded:
            s.note = "This slide's event was excluded at Verify: none of its links passed the check."
        if b and slide.layout in SHOWN:
            s.facts = _facts(look, b, slide)
        # Text a person typed in a Deck edit (#19) has no research behind it: never show the old sources for it.
        s.edited = list(job.edited_text.get(slide.index, []))
        if "heading" in s.edited and slide.layout == "event":  # the card shows the slide's heading as its title
            s.facts = [SlideFact(field="title", value=slide.heading, recorded=False, edited=True) if f.field == "title"
                       else f for f in s.facts]
        # The same corpus the golden set uses: an event slide answers to its own event, others to every event kept.
        corpus = [b] if b and slide.layout == "event" else included
        fails, _ = unsupported_facts(f"{slide.heading}\n{slide.body}", corpus, guide)
        s.unsourced = fails
        out.append(s)
    return out
