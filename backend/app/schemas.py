"""Shared data contracts between agent stages, host services, and the Admin UI.

Mirrors apps/web/src/lib/types.ts (frontend) and docs/BACKEND.md §3 (frozen v1.0).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RESEARCHING = "RESEARCHING"
    VERIFYING = "VERIFYING"
    WRITING = "WRITING"
    RENDERING = "RENDERING"
    QA = "QA"
    REVIEWING = "REVIEWING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REJECTED = "REJECTED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


TERMINAL = {JobStatus.READY_FOR_REVIEW, JobStatus.REJECTED, JobStatus.PUBLISHED, JobStatus.FAILED}


# ---------------------------------------------------------------- sources & events

class Source(BaseModel):
    url: HttpUrl
    kind: Literal["official", "ticketing", "news", "sns", "public_api", "other"]
    fetched_at: datetime | None = None


class Access(BaseModel):
    """What a foreigner needs to know to actually get in (research R4)."""
    korean_phone: Literal["not_needed", "passport_ok", "required"] | None = None
    foreign_card: bool | None = None
    cash_only: bool | None = None
    english: bool | None = None
    entry: Literal["walk_in", "waitlist", "booking", "ticket"] | None = None
    booking_url: str | None = None


class ImageAsset(BaseModel):
    url: str
    license: Literal["official_permission", "kogl_1", "kogl_3", "cc0", "cc_by", "cc_by_sa",
                     "unsplash", "pexels", "ai_generated"]
    credit: str
    source_url: str | None = None
    allow_overlay: bool = True


class EventBrief(BaseModel):
    id: str
    title_en: str
    title_ko: str
    category: Literal["popup", "festival", "exhibition", "performance", "experience", "other"] = "other"
    start_date: date
    end_date: date
    venue_en: str
    venue_ko: str = ""
    address_ko: str = ""
    lat: float | None = None
    lng: float | None = None
    nearest_station: str | None = None   # keep short (≤ 60); renderer auto-fits, QA catches overflow
    price: str | None = None
    booking: str | None = None
    foreigner_tips: list[str] = Field(default_factory=list)
    why_go: str = ""
    sources: list[Source] = Field(min_length=1)
    access: Access | None = None
    images: list[ImageAsset] = Field(default_factory=list)


# ---------------------------------------------------------------- verification

class LinkCheck(BaseModel):
    url: str
    status: Literal["ok", "dead", "redirect", "suspicious", "timeout"]
    http_code: int | None = None
    final_url: str | None = None
    reason: str | None = None


class VerificationReport(BaseModel):
    checks: list[LinkCheck] = Field(default_factory=list)
    excluded_event_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- deck

Layout = Literal["cover", "event", "tips", "map", "cta"]


class Slide(BaseModel):
    index: int
    layout: Layout
    heading: str
    body: str = ""
    event_id: str | None = None
    image: ImageAsset | None = None
    source_urls: list[str] = Field(default_factory=list)


class CardDeck(BaseModel):
    job_id: str = ""
    title: str
    caption: str
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    slides: list[Slide] = Field(min_length=6, max_length=8)


# ---------------------------------------------------------------- checks

class Issue(BaseModel):
    severity: Literal["block", "warn"]
    category: Literal["fact", "link", "sensitive", "pii", "visual", "tone"]
    slide_index: int | None = None
    message: str


class QAReport(BaseModel):
    issues: list[Issue] = Field(default_factory=list)


class ReviewVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    issues: list[Issue] = Field(default_factory=list)


# ---------------------------------------------------------------- job (view model the Admin reads)

class PipelineStep(BaseModel):
    name: str
    where: str
    state: Literal["done", "running", "pending", "failed", "skipped"] = "pending"
    note: str | None = None


class LogLine(BaseModel):
    time: str
    stage: str
    message: str
    level: Literal["info", "warn"] = "info"


class Job(BaseModel):
    id: str
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime
    source: Literal["quick", "brainstorm"] = "quick"
    briefs: list[EventBrief] = Field(default_factory=list)
    verification: VerificationReport | None = None
    deck: CardDeck | None = None
    issues: list[Issue] = Field(default_factory=list)   # QA + review merged for display
    slide_urls: list[str] = Field(default_factory=list)  # /assets/{job_id}/slide-NN.jpg
    published_url: str | None = None
    error: str | None = None
    pipeline: list[PipelineStep] = Field(default_factory=list)
    log: list[LogLine] = Field(default_factory=list)
    draft_id: str | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3)
    scenario: str | None = None  # mock-backend QA hint; ignored here


class ApproveRequest(BaseModel):
    caption: str | None = None
    mode: Literal["mock", "dryrun", "graph"] | None = None


class RejectRequest(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------- admin

class Metric(BaseModel):
    label: str
    value: str
    delta: str


class Channel(BaseModel):
    handle: str
    platform: str
    followers: int
    live: bool


class PolicyEvent(BaseModel):
    time: str
    sandbox: str
    binary: str
    host: str
    request: str
    result: Literal["policy_denied", "fs_denied", "audit", "allowed"]
    note: str | None = None
    job_id: str | None = None


# ---------------------------------------------------------------- brainstorm (SPEC §4-1)

class Fact(BaseModel):
    key: Literal["event", "dates", "venue", "price", "booking", "other"]
    value: str
    verified: bool
    source_url: str | None = None


class Angle(BaseModel):
    id: str
    title: str
    hook: str


class OutlineItem(BaseModel):
    layout: Layout
    heading: str
    updated_from_chat: bool = False


class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    text: str
    urls: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    angles: list[Angle] = Field(default_factory=list)
    selected_angle_id: str | None = None
    targets: list[str] = Field(default_factory=list)
    tones: list[str] = Field(default_factory=list)
    outline: list[OutlineItem] = Field(default_factory=list)
    job_id: str | None = None


class DraftUpdate(BaseModel):
    """planner output; None = unchanged."""
    reply: str
    facts: list[Fact] | None = None
    angles: list[Angle] | None = None
    outline: list[OutlineItem] | None = None


class CreateDraftRequest(BaseModel):
    sample: bool = True


class DraftMessageRequest(BaseModel):
    text: str = ""
    urls: list[str] = Field(default_factory=list)


class DraftPatch(BaseModel):
    facts: list[Fact] | None = None
    angles: list[Angle] | None = None
    selected_angle_id: str | None = None
    targets: list[str] | None = None
    tones: list[str] | None = None
    outline: list[OutlineItem] | None = None
