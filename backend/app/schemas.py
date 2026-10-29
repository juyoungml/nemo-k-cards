"""Shared data contracts between agent stages, host services, and the Admin UI.

Mirrors apps/web/src/lib/types.ts (frontend) and docs/BACKEND.md §3 (frozen v1.0).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator


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
    BLOCKED = "BLOCKED"  # the Safeguard refused the Deck: nobody can approve it (regenerate or reject)
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


TERMINAL = {JobStatus.READY_FOR_REVIEW, JobStatus.REJECTED, JobStatus.BLOCKED, JobStatus.PUBLISHED,
            JobStatus.FAILED}


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


# ---------------------------------------------------------------- source material (#20, ADR 0003)

MaterialKind = Literal["link", "file", "note"]


class MaterialRef(BaseModel):
    """What a Job or Brainstorm carries about one piece of its Source material (GLOSSARY): the Organization's own
    link, file or note. The extracted text stays in the `materials` table; the Admin shows these refs."""
    id: str
    kind: MaterialKind
    name: str                              # file name (cleaned), the link as given, or "Note"
    url: str | None = None                 # links only
    content_type: str | None = None        # files: application/pdf, image/jpeg, text/plain
    size: int | None = None                # bytes stored
    pages: int | None = None               # PDFs: pages in the file
    chars: int = 0                         # length of the extracted text the agents get
    truncated: bool = False                # the text was cut at the cap (pages or characters)
    image: bool = False                    # the agents also get the (re-encoded) image


class Material(MaterialRef):
    """One piece of Source material as stored (one row per Organization in `materials`)."""
    text: str = ""
    created_at: datetime
    created_by: str | None = None


class FactSource(BaseModel):
    """Where one fact of an EventBrief came from (ADR 0003): the Organization's Source material (counts as verified)
    or a link the host checks. `verified` is set by the host at Verify, never by an agent."""
    field: Literal["title", "dates", "time", "venue", "address", "station", "price", "booking", "access", "tips",
                   "why_go", "other"]
    value: str = ""
    origin: Literal["material", "link"]
    material_id: str | None = None
    url: str | None = None
    quote: str | None = Field(None, max_length=400)  # the words in the material that state it
    verified: bool | None = None
    note: str | None = None                # why the host could not count it as verified


class Conflict(BaseModel):
    """Source material and a public page disagree on a fact (ADR 0003): a person decides, so it becomes an Issue."""
    field: str
    material_id: str
    material_says: str
    url: str
    public_says: str


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
    # Checked links. May be empty only when the event comes from the Organization's Source material (`materials`).
    sources: list[Source] = Field(default_factory=list)
    access: Access | None = None
    images: list[ImageAsset] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)       # ids of the Source material this event is taken from
    facts: list[FactSource] = Field(default_factory=list)    # where each fact came from
    conflicts: list[Conflict] = Field(default_factory=list)  # material vs a public page

    @model_validator(mode="after")
    def _has_a_source(self) -> "EventBrief":
        if not self.sources and not self.materials:
            raise ValueError("an event needs at least one source link or Source material")
        return self


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
    stock_id: str | None = None  # copywriter's pick from renderer/stock/catalog.json; the host decides if it's used
    source_urls: list[str] = Field(default_factory=list)


class CardDeck(BaseModel):
    job_id: str = ""
    title: str
    caption: str
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    slides: list[Slide] = Field(min_length=6, max_length=8)


# ---------------------------------------------------------------- fact sources per slide (#25, story 2)

LinkStatus = Literal["ok", "redirect", "dead", "suspicious", "timeout", "unchecked"]


class SlideFactSource(BaseModel):
    """One source behind a fact on a slide: a link with the host's link-check result, or the Organization's own
    Source material (named, with the quoted words). Material is named only when it is this Job's own."""
    origin: Literal["material", "link"]
    url: str | None = None
    status: LinkStatus | None = None       # links: the host's check ("unchecked" when it never ran)
    http_code: int | None = None
    reason: str | None = None              # why a link is suspicious or dead
    material_id: str | None = None
    material_name: str | None = None
    quote: str | None = None               # the words in the material that state the fact
    verified: bool | None = None           # the host's verdict at Verify (FactSource.verified); None: not recorded
    note: str | None = None


class SlideFact(BaseModel):
    """A fact the slide shows (the renderer fills event and map slides from the event's brief) and its sources."""
    field: str                             # FactSource.field: title, dates, venue, station, price, …
    value: str                             # as the slide shows it
    recorded: bool = True                  # False: no per-fact source was recorded; these are the event's sources
    sources: list[SlideFactSource] = Field(default_factory=list)
    edited: bool = False                   # a person typed this in a Deck edit (#19): its old sources no longer apply


class SlideSources(BaseModel):
    """Where the facts on one slide came from. Computed by the API from the Job on the way out; never stored."""
    slide_index: int
    layout: Layout
    event_id: str | None = None
    event_title: str | None = None
    facts: list[SlideFact] = Field(default_factory=list)
    links: list[SlideFactSource] = Field(default_factory=list)  # links the copywriter attached to the slide
    unsourced: list[str] = Field(default_factory=list)          # claims in the slide's text no brief supports
    general: bool = False                  # tips: general know-how, not expected to trace to an event
    edited: list[str] = Field(default_factory=list)  # "heading" / "body" a person changed (#19): re-check sources
    note: str | None = None


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


# ---------------------------------------------------------------- safeguard (the one binding check, ADR 0004)

HarmCategory = Literal[
    "hate",                # slurs, demeaning a nationality, ethnicity, religion, gender, disability, region
    "harassment",          # targeting or mocking a real, identifiable person
    "sexual",              # sexual content, sexualised framing, anything sexual involving minors
    "violence",            # gore, glorified violence, weapons
    "self_harm",           # suicide or self-harm, incl. as a joke or a hook
    "dangerous",           # illegal drugs, drunk driving, unsafe or illegal activity presented as fun
    "historical_tragedy",  # Korean tragedies and atrocities as a hook, pun or sale; history distortion
    "political",           # partisan or election content, ballot numbers with party colours
    "territorial_cultural",  # Sea of Japan / Takeshima, rising sun imagery, cultural-origin claims
    "scam",                # fraud, phishing, fake giveaways, misleading money or safety claims
    "other",
]


class SafeguardFinding(BaseModel):
    """One reason the Safeguard objects, written for the Approver who reads it in Review."""
    category: HarmCategory
    severity: Literal["block", "warn"]
    location: Literal["slide", "caption", "hashtags", "image"]
    slide_index: int | None = None   # 0-based; None for caption / hashtags
    excerpt: str                     # the exact offending text, or a short description of the image part
    rule: str                        # the policy rule it breaks, e.g. "historical_tragedy · 05-18 date gate"
    explanation: str                 # why it is harmful, in one or two plain sentences for the Approver
    fix: str = ""                    # how to make it publishable


class SafeguardModelOutput(BaseModel):
    """What the safeguard subagent returns (its JSON schema)."""
    decision: Literal["allow", "block"]
    summary: str   # 1-3 sentences shown at the top of Review: what is wrong, or why the Deck is safe
    findings: list[SafeguardFinding] = Field(default_factory=list)


class SafeguardVerdict(SafeguardModelOutput):
    """The host's final decision: the model's output merged with the deterministic rules, tied to the content."""
    checked_by: list[Literal["rules", "model"]] = Field(default_factory=list)
    model_decision: Literal["allow", "block"] | None = None  # what the model said on its own; None if it didn't run
    model_error: str | None = None   # set when the model could not run; the gate then fails closed
    policy_version: str = ""         # hash of the policy files the Deck was checked against
    content_hash: str = ""           # hash of the slide text, caption and images that were checked
    checked_at: datetime | None = None
    deck_version: int | None = None  # the Deck version an edit re-check vouches for (#19); None: the pipeline's


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


class PostProgress(BaseModel):
    """Live sub-steps of a Post while it is prepared and published, for the Review progress card."""
    step: Literal["upload", "containers", "processing", "publish", "done", "failed"]
    label: str
    done: int = 0
    total: int = 0
    percent: int = 0


PostState = Literal["queued", "container_ready", "publishing", "published", "failed", "unknown"]
PublishMode = Literal["mock", "dryrun", "graph"]


class Post(BaseModel):
    """One publication of an approved Deck to one Instagram account (GLOSSARY "Post"), with an honest state
    (docs/spec/v1.md "Publish state machine"). Only `published` and `failed` are final for a real post; a dry run
    ends at `container_ready` (finished_at set): Instagram prepared the carousel and nothing was posted."""
    id: UUID
    job_id: str
    deck_version: int | None = None
    instagram_account_id: UUID | None = None
    handle: str | None = None                   # "@username" when approved; None for a mock Post without account
    mode: PublishMode
    state: PostState
    caption: str | None = None                  # the approved caption edit, if any
    creation_id: str | None = None
    media_id: str | None = None
    permalink: str | None = None
    attempts: int = 0
    last_error: str | None = None
    progress: PostProgress | None = None
    approved_by_email: str | None = None
    created_at: datetime
    updated_at: datetime
    publishing_started_at: datetime | None = None
    unknown_since: datetime | None = None
    next_check_at: datetime | None = None       # unknown: the reconciler's next look; None = a person decides
    alerted_at: datetime | None = None          # unknown for 30 minutes: a person was alerted
    published_at: datetime | None = None
    finished_at: datetime | None = None
    resolved_by: UUID | None = None
    legacy: bool = False                        # published before Posts existed (#21)

    @property
    def open(self) -> bool:
        return self.finished_at is None


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
    safeguard: SafeguardVerdict | None = None            # binding: "block" means nobody can approve it
    slide_urls: list[str] = Field(default_factory=list)  # /assets/{job_id}/slide-NN.jpg
    error: str | None = None
    # Publishing is a Post's (#21): `published_url` and `publish_progress` of older documents are ignored on load
    # (and removed by supabase/migrations/*_posts.sql).
    pipeline: list[PipelineStep] = Field(default_factory=list)
    log: list[LogLine] = Field(default_factory=list)
    # Stored before the glossary rename as "draft_id"; both keys load, new documents are written as brainstorm_id.
    brainstorm_id: str | None = Field(default=None, validation_alias=AliasChoices("brainstorm_id", "draft_id"))
    material: list[MaterialRef] = Field(default_factory=list)  # Source material (#20); empty on older Jobs
    # View only (#25): where each slide's facts came from, computed by the API on the way out, never stored.
    slide_sources: list[SlideSources] = Field(default_factory=list)
    # Deck versions (#19): the version `deck` and `slide_urls` show (Jobs from before versions are version 1), and
    # whether the Safeguard is still checking it after an edit (it can't be approved until the check passes).
    deck_version: int = 1
    safeguard_pending: bool = False
    # Which text a person has changed on each slide (current index → "heading" / "body"), so fact sources (#25) say
    # "edited, re-check sources" instead of vouching for words the research never saw.
    edited_text: dict[int, list[Literal["heading", "body"]]] = Field(default_factory=dict)


Scenario = Literal["good", "bad", "fail"]  # demo/scenarios/<name>/, same names as the Admin mock backend


MAX_MATERIALS = 10  # pieces of Source material per Job or Brainstorm


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3)
    material_ids: list[str] = Field(default_factory=list, max_length=MAX_MATERIALS)  # from POST /materials
    scenario: Scenario | None = None  # DEMO_MODE=fixture only; ignored in live mode


class ApproveRequest(BaseModel):
    """Approve one Deck version for posting. Nothing about the Deck is edited here: what is posted is exactly the
    pinned version (#19). Unknown fields are refused, so a client still sending a caption learns it is not used."""
    model_config = ConfigDict(extra="forbid")

    # The Deck version being approved: approval pins exactly this one. Refused (409) if the Deck has been edited
    # since the Approver loaded it.
    deck_version: int = Field(ge=1)
    mode: Literal["mock", "dryrun", "graph"] | None = None
    # The Instagram account to post to (one of the Organization's connected accounts). May be left out when the
    # Organization has exactly one, or none for a mock post.
    account_id: UUID | None = None
    # Required when the Deck has blocking Issues (an Override, GLOSSARY); recorded in the audit log.
    override_reason: str | None = Field(None, max_length=2000)


class ResolvePostRequest(BaseModel):
    """A person checked Instagram for an unknown Post."""
    posted: bool
    permalink: str | None = Field(None, max_length=300, pattern=r"^https://(www\.)?instagram\.com/\S+$")


class SlideEdit(BaseModel):
    """One slide in an edited Deck, in its new position. `from_index` is its index in the base version."""
    from_index: int = Field(ge=0)
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field("", max_length=2000)


class DeckEdit(BaseModel):
    """A save in the Deck editor (#19): every slide in its new order, the caption text and the hashtags.
    `base_version` is the version the editor started from; a save on top of a newer version is refused.
    Omitted parts stay as they are. `allow_overflow`: save even though text overflows (after the warning)."""
    base_version: int = Field(ge=1)
    slides: list[SlideEdit] | None = Field(None, min_length=6, max_length=8)
    caption: str | None = Field(None, max_length=2200)  # the caption without its hashtags
    hashtags: list[str] | None = Field(None, max_length=5)
    allow_overflow: bool = False


class FitCheck(BaseModel):
    """The Deck editor asks whether one slide's text fits, as typed (not saved): `slides` as in `DeckEdit`."""
    base_version: int = Field(ge=1)
    slides: list[SlideEdit] = Field(min_length=6, max_length=8)
    position: int = Field(ge=0)


class FitBlock(BaseModel):
    selector: str   # the template's text block, e.g. "event.heading"
    lines: int      # lines it takes after auto-fit
    font_px: float  # smallest font size after auto-fit
    overflow: bool


class FitResult(BaseModel):
    position: int
    fits: bool                       # no block overflows (the same rule as visual QA)
    small_text: bool                 # some text shrank below the 30px floor (QA warns)
    blocks: list[FitBlock] = Field(default_factory=list)


class RejectRequest(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------- admin

class Metric(BaseModel):
    label: str
    value: str
    delta: str


class InstagramAccount(BaseModel):
    """An Instagram account the Organization has connected (GLOSSARY). Never carries the token."""
    id: UUID
    handle: str                     # "@username"
    platform: Literal["Instagram"] = "Instagram"
    account_type: Literal["BUSINESS", "MEDIA_CREATOR"]
    status: Literal["connected", "expiring", "revoked"]
    token_expires_at: datetime | None
    connected_at: datetime
    followers: int | None = None    # not read yet: insights per Organization come later
    live: bool                      # status is connected (publishing can use it)


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
    material_id: str | None = None  # taken from this Source material (counts as verified, ADR 0003)


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
    material_ids: list[str] = Field(default_factory=list)  # Source material attached with this message


class Brainstorm(BaseModel):
    id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    angles: list[Angle] = Field(default_factory=list)
    selected_angle_id: str | None = None
    targets: list[str] = Field(default_factory=list)
    tones: list[str] = Field(default_factory=list)
    outline: list[OutlineItem] = Field(default_factory=list)
    job_id: str | None = None
    material: list[MaterialRef] = Field(default_factory=list)  # Source material attached in the chat (#20)


class BrainstormUpdate(BaseModel):
    """planner output; None = unchanged."""
    reply: str
    facts: list[Fact] | None = None
    angles: list[Angle] | None = None
    outline: list[OutlineItem] | None = None


class CreateBrainstormRequest(BaseModel):
    sample: bool = True


class BrainstormMessageRequest(BaseModel):
    text: str = ""
    urls: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list, max_length=MAX_MATERIALS)  # attached with this message


class BrainstormPatch(BaseModel):
    facts: list[Fact] | None = None
    angles: list[Angle] | None = None
    selected_angle_id: str | None = None
    targets: list[str] | None = None
    tones: list[str] | None = None
    outline: list[OutlineItem] | None = None
