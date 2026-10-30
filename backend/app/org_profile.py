"""Organization profile (#16; GLOSSARY "Organization profile"): what the agents know about an Organization. It covers
what it does, its audience, home region, usual subjects and things never to say or promote.

The Admin fills it in during onboarding (right after creating the Organization, or later) and edits it on the
Profile page. Every Member can read it. Every Job stores a snapshot when it starts (`Job.org_profile`), so all of
its stages give the agents the same profile, and every Brainstorm message reads the current one. The reviewer
checks Decks against `never_say`, and the host flags any entry that appears word for word (`never_say_issues`)."""

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app import audit, store
from app.auth import Admin, Viewer
from app.schemas import CardDeck, Issue, OrgProfile

FIELDS = ("what_it_does", "audience", "home_region", "usual_subjects", "never_say")
NEVER_SAY = "Organization profile says never to say"  # how the host's own Issues start (recomputed on each edit)


def get(org_id: UUID) -> OrgProfile | None:
    """This Organization's profile, or None if no Admin has saved one yet."""
    with store.tenant(org_id) as c:
        row = c.execute("select doc::text from org_profiles").fetchone()  # RLS: only this Organization's row
    return store._load(OrgProfile, row[0]) if row else None


def for_agents(profile: OrgProfile | None) -> dict | None:
    """What a stage payload carries: the profile's fields, or None when there is nothing in it."""
    return None if profile is None or profile.is_empty() else profile.model_dump()


def save(org_id: UUID, profile: OrgProfile, entry: audit.Entry) -> OrgProfile:
    """Create or replace the profile and write its audit event in one transaction. Records only fields that changed."""
    with store.tenant(org_id, user_id=entry.actor) as c:
        row = c.execute("select doc::text from org_profiles for update").fetchone()
        before = (store._load(OrgProfile, row[0]) if row else None) or OrgProfile()
        changed = {k: {"before": getattr(before, k), "after": getattr(profile, k)}
                   for k in FIELDS if getattr(before, k) != getattr(profile, k)}
        c.execute("insert into org_profiles (doc, updated_by, updated_at) values (%s, %s, now()) "
                  "on conflict (org_id) do update set doc = excluded.doc, updated_by = excluded.updated_by, "
                  "updated_at = excluded.updated_at", (store._json(profile), entry.actor))
        if changed or not row:
            entry.detail = {**entry.detail, "changed": changed}
            audit.write(c, org_id, entry)
    return profile


def never_say_issues(deck: CardDeck, profile: OrgProfile | None) -> list[Issue]:
    """Host check: a `never_say` entry that appears word for word (ignoring case and spacing) in a slide, the caption
    or a hashtag. The reviewer also judges the meaning ("no alcohol promotions"); this only catches the literal ones.
    A blocking Issue: the Organization said never, so approving it anyway is an Override with a written reason."""
    if not profile or not profile.never_say:
        return []
    texts = [(s.index, f"{s.heading}\n{s.body}") for s in deck.slides] + [(None, deck.caption)]
    issues: list[Issue] = []
    for phrase in profile.never_say:
        words = phrase.split()
        rx = re.compile(r"(?<!\w)" + r"[\s#_-]*".join(map(re.escape, words)) + r"(?!\w)", re.IGNORECASE)
        for idx, text in texts:
            if rx.search(text):
                where = "the caption" if idx is None else f"slide {idx + 1}"
                issues.append(Issue(severity="block", category="sensitive", slide_index=idx,
                                    message=f'{NEVER_SAY} "{phrase}", and {where} '
                                            "does. Remove it, or approve with an Override reason."))
    return issues


# ---------------------------------------------------------------- routes

class ProfileOut(BaseModel):
    profile: OrgProfile
    complete: bool          # False until an Admin has saved a profile with something in it
    updated_at: datetime | None = None


router = APIRouter()


@router.get("/orgs/current/profile", response_model=ProfileOut)
def read(ctx: Viewer) -> ProfileOut:
    with store.tenant(ctx.org_id) as c:
        row = c.execute("select doc::text, updated_at from org_profiles").fetchone()
    profile = (store._load(OrgProfile, row[0]) if row else None) or OrgProfile()
    return ProfileOut(profile=profile, complete=not profile.is_empty(), updated_at=row[1] if row else None)


@router.put("/orgs/current/profile", response_model=ProfileOut)
def write(req: OrgProfile, ctx: Admin) -> ProfileOut:
    save(ctx.org_id, req, audit.entry(ctx, "org.profile_updated", str(ctx.org_id)))
    return read(ctx)
