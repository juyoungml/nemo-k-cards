"""Guide Decks (#27): which points of a GuideBrief can go on the Deck.

A Guide Deck has no event to send readers to, so every claim on it must stand on an Official source (GLOSSARY). At
Verify the host checks each point's facts: a fact from this Job's Source material counts when its quote is really in
the material (ADR 0003); a fact from a link counts when that link is one of the guide's sources, passed the link
check, and is official (`app.official`). A point with no fact that counts is left off the Deck. A point that rests
on a source the researcher called official but we don't know raises a warning for a person to confirm.
"""

from urllib.parse import urlparse

from app import official
from app.materials import _norm
from app.schemas import GuideBrief, GuidePoint, Issue, Job, LinkCheck, Material

MIN_POINTS = 3  # fewer verified points than this is not a Deck (cover + 3 points + cta is the shortest)


def check(guide: GuideBrief, checks: dict[str, LinkCheck], material: dict[str, Material]
          ) -> tuple[list[str], list[Issue], list[str]]:
    """Set `verified` / `note` on every fact; return (excluded point ids, Issues, log lines)."""
    kinds = {str(s.url): s.kind for s in guide.sources}
    excluded: list[str] = []
    claimed: dict[str, list[str]] = {}  # host -> titles of points resting on it
    lines: list[str] = []
    for p in guide.points:
        hosts: set[str] = set()
        for f in p.facts:
            f.verified, f.note = False, None
            if f.origin == "material":
                m = material.get(f.material_id or "")
                if not m:
                    f.note = "not one of this request's Source material"
                elif f.quote and m.text and _norm(f.quote) not in _norm(m.text):
                    f.note = "the quoted words are not in the material"
                else:
                    f.verified = True
                continue
            url = f.url or ""
            c = checks.get(url)
            standing = official.standing(url, kinds.get(url))
            if url not in kinds:
                f.note = "not one of the guide's sources"
            elif not c or c.status != "ok":
                f.note = "the link did not pass the check"
            elif standing == "no":
                f.note = "not an official source (news, blogs and resellers can't carry a Guide fact alone)"
            else:
                f.verified = True
                if standing == "claimed":
                    hosts.add(urlparse(url).hostname or url)
        if not any(f.verified for f in p.facts):
            excluded.append(p.id)
            why = "; ".join(dict.fromkeys(f.note for f in p.facts if f.note)) or "no source recorded"
            lines.append(f"{p.id}: left off, no official source ({why})")
        else:
            # Only hosts this point actually needs: if an allowlisted or material fact also backs it, no question.
            if not any(f.verified and (f.origin == "material" or official.standing(f.url or "", kinds.get(
                    f.url or "")) == "allowlist") for f in p.facts):
                for h in hosts:
                    claimed.setdefault(h, []).append(p.title)
    issues = [Issue(severity="warn", category="fact", message=(
        f"{host} is used as an official source for “{'”, “'.join(titles)}”. Confirm it is the operator's or agency's "
        "own site before approving."))
        for host, titles in claimed.items()]
    return excluded, issues, lines


def verified(job: Job) -> GuideBrief | None:
    """A Guide Deck's brief with only the points that passed Verify: all that copy, render, review and the Admin see."""
    if job.kind != "guide" or not job.guide:
        return None
    kept = included(job.guide, job.verification.excluded_point_ids if job.verification else [])
    return job.guide.model_copy(update={"points": kept}) if kept else None


def included(guide: GuideBrief | None, excluded: list[str]) -> list[GuidePoint]:
    return [p for p in guide.points if p.id not in set(excluded)] if guide else []


def source_urls(guide: GuideBrief) -> list[str]:
    return [str(s.url) for s in guide.sources]
