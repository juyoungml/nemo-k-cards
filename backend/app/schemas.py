"""Shared data contracts between agent stages, host services, and the Admin UI (SPEC §5)."""

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


class Source(BaseModel):
    url: HttpUrl
    kind: Literal["official", "ticketing", "news", "sns", "public_api", "other"]
    fetched_at: datetime | None = None


class EventBrief(BaseModel):
    id: str
    title_en: str
    title_ko: str
    category: Literal["popup", "festival", "exhibition", "performance", "experience", "other"]
    start_date: date
    end_date: date
    venue_en: str
    venue_ko: str
    address_ko: str
    lat: float | None = None
    lng: float | None = None
    nearest_station: str | None = None
    price: str | None = None
    booking: str | None = None
    foreigner_tips: list[str] = Field(default_factory=list)
    why_go: str
    sources: list[Source] = Field(min_length=1)


class LinkCheck(BaseModel):
    url: str
    status: Literal["ok", "dead", "redirect", "suspicious", "timeout"]
    http_code: int | None = None
    final_url: str | None = None
    reason: str | None = None


class VerificationReport(BaseModel):
    checks: list[LinkCheck]
    excluded_event_ids: list[str] = Field(default_factory=list)


class Slide(BaseModel):
    index: int
    layout: Literal["cover", "event", "tips", "map", "cta"]
    heading: str
    body: str
    event_id: str | None = None
    image_url: str | None = None


class CardDeck(BaseModel):
    job_id: str
    title: str
    caption: str
    slides: list[Slide] = Field(min_length=6, max_length=8)


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


class Job(BaseModel):
    id: str
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime
    briefs: list[EventBrief] = Field(default_factory=list)
    verification: VerificationReport | None = None
    deck: CardDeck | None = None
    slide_paths: list[str] = Field(default_factory=list)
    qa: QAReport | None = None
    review: ReviewVerdict | None = None
    error: str | None = None
    published_url: str | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3)
