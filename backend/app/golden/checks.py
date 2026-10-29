"""Checks run on one recorded Deck. Deterministic unless marked `judge="llm"`.

The two `llm` checks don't call a model here: they read the Issues the pipeline's reviewer agent (an LLM)
raised during the recorded run. They are reported separately so a rule failure and a judge failure are never
confused, and so replay stays free and deterministic.

Rule checks:
  outcome       the job ended as the case expects (a Deck in review, or no Deck when nothing should be found)
  schema        briefs are EventBriefs, the Deck is a CardDeck (6–8 slides, ≤ 5 hashtags in the list)
  slides        indices 0..n-1, cover first, cta last, 1–5 event slides, each naming a verified event
  hashtags      at most 5 hashtags in the caption (the same regex host QA uses)
  links         every shown event kept a source that passed the link check; no excluded event, unchecked or
                suspicious URL appears on a slide (link_checker.suspicious_reason, offline)
  facts         prices, percentages, times, dates and "free" claims on slides and caption appear in the briefs,
                compared as values (see "fact extraction"); event headings name their event (title_en /
                title_ko). The Prompt is never evidence. Warn only: Hangul names missing from the briefs, and
                claims on the tips slide (general know-how such as last-train times is not in any brief)
  photos        AI stock photos come from the catalog, match the event's category and are used once; other photos
                carry an overlay-safe licence and a credit; a stock photo whose `never_for` names the event warns
  expectations  event count, categories, place and date window from the case
  safeguard     the Safeguard's decision on the Deck, as the host would make it today: the blocked-phrase rules
                run again on the recorded Deck and are merged with the recorded model output (if any); it must
                match the case (`expect.safeguard`, else allow for ready and block for blocked). Every blocking
                finding explains itself (excerpt, explanation, fix)
LLM checks (from the recorded agent output):
  facts·llm      no blocking `fact` Issue
  photos·llm     no Issue saying a photo doesn't match its event (one sentence names the photo and a mismatch)
  safeguard·llm  the safeguard agent's own decision matches the case (not needed for a block the rules already
                 make), it didn't fail, and each finding's excerpt is in the text it points at (warn)

Safeguard cases (`kind: "safeguard"`) check outcome, schema, safeguard and safeguard·llm only: they are about the
verdict, and a blocked Deck is never posted, so the Deck quality checks don't apply.
"""

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from pydantic import ValidationError

from app.golden.cases import Case, Recording
from app.schemas import CardDeck, EventBrief, JobStatus, SafeguardVerdict, Slide, VerificationReport
from app.services import safeguard
from app.services.link_checker import suspicious_reason
from app.services.photos import OVERLAY_LICENSES, stock_catalog
from app.services.visual_qa import HASHTAG

Status = Literal["pass", "fail", "warn", "skip"]


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    judge: Literal["rule", "llm"] = "rule"


def _result(name: str, fails: list[str], warns: list[str] = (), ok: str = "", judge: str = "rule") -> CheckResult:
    if fails:
        return CheckResult(name, "fail", "; ".join(fails + list(warns)), judge)
    if warns:
        return CheckResult(name, "warn", "; ".join(warns), judge)
    return CheckResult(name, "pass", ok, judge)


# ---------------------------------------------------------------- fact extraction
# Claims are pulled out of slide text and out of the briefs with the same patterns, normalised, and compared as
# sets: (month, day) for dates, (hour, minute) for times, numbers for prices and percentages. No substring matching,
# and the Prompt is never evidence (a request for "free things" doesn't make anything free).

_MONTHS = ["jan(?:uary)?", "feb(?:ruary)?", "mar(?:ch)?", "apr(?:il)?", "may", "june?", "july?", "aug(?:ust)?",
           "sep(?:t(?:ember)?)?", "oct(?:ober)?", "nov(?:ember)?", "dec(?:ember)?"]
_MON = r"(" + "|".join(_MONTHS) + r")\.?(?![a-z])"
_DASH = r"\s*[–—~-]\s*"
DATE_MD = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?!\d)(?:{_DASH}(?:{_MON}\s+)?(\d{{1,2}})(?!\d))?", re.IGNORECASE)
DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:{_DASH}(\d{{1,2}}))?\s+{_MON}", re.IGNORECASE)
DATE_ISO = re.compile(r"(?<!\d)(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
DATE_SLASH = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])")
DATE_KO = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
MONEY = re.compile(r"₩\s?(\d[\d,]*)|(?:KRW|won)\s?(\d[\d,]*)|(\d[\d,]*)\s?(?:won\b|KRW\b|원)", re.IGNORECASE)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s?([ap])\.?m\b\.?", re.IGNORECASE)
CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
FREE = re.compile(r"\bfree\b|무료", re.IGNORECASE)
HANGUL = re.compile(r"[가-힣]{2,}")
URL = re.compile(r"https?://[^\s)>\]]+")
WORD = re.compile(r"[a-z0-9]{3,}")
# Words that don't identify an event on their own.
GENERIC = {"the", "and", "for", "with", "seoul", "korea", "korean", "festival", "exhibition", "popup", "pop",
           "store", "market", "show", "week", "weekend", "night", "day", "2026", "2027", "edition", "special"}


def _month(word: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if re.fullmatch(m, word.lower().rstrip(".")))


def dates_in(text: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """Every date written in `text` → [(as written, [(month, day), …])]; impossible dates are not dates."""
    found: list[tuple[str, list[tuple[int, int]]]] = []
    for m in DATE_MD.finditer(text):
        mon1, d1, mon2, d2 = m.groups()
        found.append((m.group(0), [(_month(mon1), int(d1))] + ([(_month(mon2 or mon1), int(d2))] if d2 else [])))
    for m in DATE_DM.finditer(text):
        d1, d2, mon = m.groups()
        found.append((m.group(0), [(_month(mon), int(d1))] + ([(_month(mon), int(d2))] if d2 else [])))
    for m in DATE_ISO.finditer(text):
        found.append((m.group(0), [(int(m.group(2)), int(m.group(3)))]))
    for m in DATE_SLASH.finditer(text):
        found.append((m.group(0), [(int(m.group(1)), int(m.group(2)))]))
    for m in DATE_KO.finditer(text):
        found.append((m.group(0), [(int(m.group(1)), int(m.group(2)))]))
    return [(s, mds) for s, mds in found if all(1 <= mo <= 12 and 1 <= d <= 31 for mo, d in mds)]


def times_in(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Clock times → [(as written, (hour, minute) on a 24-hour clock)]."""
    out = []
    for m in AMPM.finditer(text):
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
        out.append((m.group(0), (h, int(m.group(2) or 0))))
    rest = AMPM.sub(" ", text)
    out += [(m.group(0), (int(m.group(1)), int(m.group(2)))) for m in CLOCK.finditer(rest)]
    return out


def money_in(text: str) -> list[tuple[str, int]]:
    return [(m.group(0).strip(), int(next(g for g in m.groups() if g).replace(",", ""))) for m in MONEY.finditer(text)]


def percents_in(text: str) -> list[tuple[str, float]]:
    return [(m.group(0), float(m.group(1))) for m in PERCENT.finditer(text)]


def _days(briefs: list[EventBrief]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for b in briefs:
        d, n = b.start_date, 0
        while d <= b.end_date and n < 400:
            out.add((d.month, d.day))
            d, n = d + timedelta(days=1), n + 1
    return out


def _claims_text(b: EventBrief) -> str:
    """The brief fields that state prices and offers (not ids, addresses or station exits)."""
    return "\n".join([b.price or "", b.why_go, *b.foreigner_tips])


def unsupported_facts(text: str, briefs: list[EventBrief]) -> tuple[list[str], list[str]]:
    """Claims in `text` that the briefs don't support → (fails, warns). Pure rules, no model."""
    full = "\n".join(b.model_dump_json() for b in briefs)
    claims = "\n".join(_claims_text(b) for b in briefs)
    days = _days(briefs) | {md for _, mds in dates_in(full) for md in mds}
    times = {t for _, t in times_in(full)}
    amounts = {n for _, n in money_in(claims)} | {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", claims)}
    percents = {p for _, p in percents_in(full)}
    fails: list[str] = []
    fails += [f"date '{s}' is outside every event's dates" for s, mds in dates_in(text)
              if any(md not in days for md in mds)]
    fails += [f"price '{s}' is not in the briefs" for s, n in money_in(text) if n not in amounts]
    fails += [f"'{s}' is not in the briefs" for s, p in percents_in(text) if p not in percents]
    fails += [f"time '{s}' is not in the briefs" for s, t in times_in(text) if t not in times]
    if FREE.search(text) and not FREE.search(claims):
        fails.append("says 'free' but no brief's price or offer does")
    warns = [f"Korean name '{w}' is not in the briefs" for w in dict.fromkeys(HANGUL.findall(text))
             if w not in full]
    return fails, warns


def _names_event(heading: str, b: EventBrief) -> bool:
    if b.title_ko and b.title_ko in heading:
        return True
    words = lambda s: set(WORD.findall(s.lower())) - GENERIC
    return bool(words(heading) & words(b.title_en))


# ---------------------------------------------------------------- context


@dataclass
class Ctx:
    case: Case
    rec: Recording
    briefs: list[EventBrief]
    deck: CardDeck | None
    report: VerificationReport | None

    @property
    def ok_urls(self) -> set[str]:
        return {c.url for c in self.report.checks if c.status == "ok"} if self.report else set()

    @property
    def included(self) -> list[EventBrief]:
        ok = self.ok_urls
        excluded = set(self.report.excluded_event_ids) if self.report else set()
        # An event taken from Source material (#20, ADR 0003) needs no checked link; the host kept it only if its
        # material ids were the request's own.
        return [b for b in self.briefs if any(str(s.url) in ok for s in b.sources)
                or (b.materials and b.id not in excluded)]

    @property
    def by_id(self) -> dict[str, EventBrief]:
        return {b.id: b for b in self.included}


# ---------------------------------------------------------------- checks


def check_outcome(c: Ctx) -> CheckResult:
    want, rec = c.case.expect.outcome, c.rec
    if want == "ready":
        ok = rec.status == JobStatus.READY_FOR_REVIEW and rec.deck is not None
    elif want == "blocked":
        ok = rec.status == JobStatus.BLOCKED and rec.deck is not None
    else:
        ok = rec.deck is None and rec.status == JobStatus.FAILED and any(
            s in (rec.error or "") for s in ("found no events", "failed link verification"))
    got = rec.status.value + (f" ({rec.error})" if rec.error else "")
    return CheckResult("outcome", "pass" if ok else "fail", f"expected {want}, got {got}")


def check_schema(c: Ctx, errors: list[str]) -> CheckResult:
    return _result("schema", errors, ok=f"{len(c.briefs)} briefs" + (f", {len(c.deck.slides)} slides"
                                                                      if c.deck else ""))


def check_slides(c: Ctx) -> CheckResult:
    s, fails = sorted(c.deck.slides, key=lambda x: x.index), []
    if [x.index for x in s] != list(range(len(s))):
        fails.append(f"slide indices are {[x.index for x in s]}, not 0..{len(s) - 1}")
    if s[0].layout != "cover":
        fails.append(f"first slide is {s[0].layout}, not cover")
    if s[-1].layout != "cta":
        fails.append(f"last slide is {s[-1].layout}, not cta")
    events = [x for x in s if x.layout == "event"]
    if not 1 <= len(events) <= 5:
        fails.append(f"{len(events)} event slides (want 1–5)")
    for x in events:
        if x.event_id not in c.by_id:
            fails.append(f"slide {x.index}: event_id {x.event_id!r} is not a verified event")
    return _result("slides", fails, ok=f"{len(s)} slides, {len(events)} events")


def check_hashtags(c: Ctx) -> CheckResult:
    tags = HASHTAG.findall(c.deck.caption)
    fails = [f"{len(tags)} hashtags in the caption"] if len(tags) > 5 else []
    if not tags:
        return CheckResult("hashtags", "warn", "no hashtags in the caption")
    return _result("hashtags", fails, ok=f"{len(tags)} in caption")


def check_links(c: Ctx) -> CheckResult:
    if c.report is None:
        return CheckResult("links", "fail", "no link verification recorded")
    by_url = {ch.url: ch for ch in c.report.checks}
    fails = []
    for b in c.briefs:
        for src in b.sources:
            if str(src.url) not in by_url:
                fails.append(f"{b.id}: source {src.url} was never checked")
    shown = {sl.event_id for sl in c.deck.slides if sl.event_id}
    for b in c.briefs:
        if b.id in shown and b not in c.included:
            fails.append(f"{b.id} is on a slide but none of its sources passed the link check")
    text_urls = URL.findall("\n".join([c.deck.caption] + [f"{sl.heading}\n{sl.body}" for sl in c.deck.slides]))
    for url in [u for sl in c.deck.slides for u in sl.source_urls] + text_urls:
        url = url.rstrip(".,;")
        if url not in c.ok_urls:
            fails.append(f"{url} is on the Deck but did not pass the link check")
    for b in c.included:
        for src in b.sources:
            if str(src.url) in c.ok_urls and (why := suspicious_reason(str(src.url))):
                fails.append(f"{b.id}: {src.url} is suspicious ({why})")
    n_ok = sum(len([s for s in b.sources if str(s.url) in c.ok_urls]) for b in c.included)
    return _result("links", fails, ok=f"{n_ok} verified sources behind {len(c.included)} events")


def _slide_corpus(c: Ctx, sl: Slide) -> list[EventBrief]:
    b = c.by_id.get(sl.event_id or "")
    return [b] if b and sl.layout == "event" else c.included


def check_facts(c: Ctx) -> CheckResult:
    fails, warns = [], []
    for sl in c.deck.slides:
        f, w = unsupported_facts(f"{sl.heading}\n{sl.body}", _slide_corpus(c, sl))
        if sl.layout == "tips":  # general know-how (last trains, T-money), not event claims: flag, don't fail
            f, w = [], [*f, *w]
        fails += [f"slide {sl.index}: {x}" for x in f]
        warns += [f"slide {sl.index}: {x}" for x in w]
        if sl.layout == "event" and (b := c.by_id.get(sl.event_id or "")) and not _names_event(sl.heading, b):
            fails.append(f"slide {sl.index}: heading {sl.heading!r} doesn't name {b.title_en!r}")
    f, w = unsupported_facts(f"{c.deck.title}\n{c.deck.caption}", c.included)
    fails += [f"caption: {x}" for x in f]
    warns += [f"caption: {x}" for x in w]
    return _result("facts", fails, warns, ok="every price, date, time and 'free' traces to a brief")


def _never_for_hit(entry: dict, b: EventBrief) -> str | None:
    text = f" {b.title_en} {b.why_go} ".lower()
    for phrase in entry.get("never_for", []):
        for alt in re.split(r"\s*/\s*|\s+or\s+|,\s*", phrase.lower()):
            alt = alt.strip(" '")
            if len(alt) >= 4 and re.search(rf"\b{re.escape(alt)}", text):
                return phrase
    return None


def check_photos(c: Ctx) -> CheckResult:
    catalog = {e["id"]: e for e in stock_catalog()}
    fails, warns, used, n = [], [], set(), 0
    for sl in c.deck.slides:
        if not sl.image or sl.layout != "event":
            continue
        n += 1
        b = c.by_id.get(sl.event_id or "")
        if sl.image.license == "ai_generated":
            entry = catalog.get(sl.stock_id or "")
            if not entry:
                fails.append(f"slide {sl.index}: AI image {sl.stock_id!r} is not in the stock catalog")
                continue
            if sl.stock_id in used:
                fails.append(f"slide {sl.index}: stock photo {sl.stock_id!r} used twice")
            used.add(sl.stock_id)
            if b and entry["category"] != b.category:
                fails.append(f"slide {sl.index}: {entry['category']} stock photo on a {b.category} event")
            if b and (hit := _never_for_hit(entry, b)):
                warns.append(f"slide {sl.index}: stock photo {sl.stock_id!r} is never for '{hit}'")
        else:
            if sl.image.license not in OVERLAY_LICENSES or not sl.image.allow_overlay:
                fails.append(f"slide {sl.index}: licence {sl.image.license} doesn't allow a text overlay")
            if not sl.image.credit.strip():
                fails.append(f"slide {sl.index}: photo has no credit")
    return _result("photos", fails, warns, ok=f"{n} photo(s) on event slides" if n else "no photos (posters)")


PHOTO_WORDS = re.compile(r"photo|image|picture|사진", re.IGNORECASE)
# The reviewer writes prose, so a photo Issue counts as "doesn't match its event" only when one sentence names the
# photo and says it doesn't fit; contrast or layout notes about a photo slide don't count.
MISMATCH = re.compile(r"\b(?:doesn't|does not|don't|do not|isn't|is not|not)\s+(?:\w+\s+)?(?:match|show|fit|depict|"
                      r"relevant|related)|mismatch|unrelated|irrelevant|wrong (?:place|event|venue|season|festival)|"
                      r"(?:a |another )?different (?:event|place|venue|festival|season)|misleading", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def photo_mismatch(message: str) -> bool:
    return any(PHOTO_WORDS.search(s) and MISMATCH.search(s) for s in SENTENCE.split(message))


def check_facts_llm(c: Ctx) -> CheckResult:
    found = [f"slide {i.slide_index}: {i.message}" for i in c.rec.issues
             if i.category == "fact" and i.severity == "block"]
    return _result("facts·llm", found, ok="reviewer raised no blocking fact issue", judge="llm")


def check_photos_llm(c: Ctx) -> CheckResult:
    found = [f"slide {i.slide_index}: {i.message}" for i in c.rec.issues
             if i.category == "visual" and photo_mismatch(i.message)]
    return _result("photos·llm", found, ok="reviewer flagged no photo as not matching its event", judge="llm")


def check_expectations(c: Ctx) -> CheckResult:
    e, inc, fails = c.case.expect, c.included, []
    if not e.min_events <= len(inc) <= e.max_events:
        fails.append(f"{len(inc)} verified events (want {e.min_events}–{e.max_events})")
    if e.categories_any and not any(b.category in e.categories_any for b in inc):
        fails.append(f"no event is a {' / '.join(e.categories_any)} (got {sorted({b.category for b in inc})})")
    for b in inc:
        place = f"{b.title_en} {b.title_ko} {b.venue_en} {b.venue_ko} {b.address_ko} {b.nearest_station or ''}"
        if e.every_event_mentions_any and not any(k.lower() in place.lower() for k in e.every_event_mentions_any):
            fails.append(f"{b.id} is not in {' / '.join(e.every_event_mentions_any)} ({b.venue_en})")
        if hit := [k for k in e.no_event_mentions if k.lower() in place.lower()]:
            fails.append(f"{b.id} mentions {hit[0]} ({b.venue_en})")
        if b.end_date < c.rec.today:
            fails.append(f"{b.id} ended {b.end_date} (today {c.rec.today})")
        if e.window_days is not None and b.start_date > c.rec.today + timedelta(days=e.window_days):
            fails.append(f"{b.id} starts {b.start_date}, after the {e.window_days}-day window")
    return _result("expectations", fails, ok=f"{len(inc)} events match the case")


def _want_safeguard(c: Ctx) -> str | None:
    e = c.case.expect
    return e.safeguard or {"ready": "allow", "blocked": "block"}.get(e.outcome)


def _recorded_verdict(c: Ctx) -> tuple[SafeguardVerdict | None, str]:
    if c.rec.safeguard is None:
        return None, ""
    try:
        return SafeguardVerdict.model_validate(c.rec.safeguard), ""
    except ValidationError as e:
        return None, f"recorded verdict fails its schema: {str(e).splitlines()[1]}"


def check_safeguard(c: Ctx) -> CheckResult:
    want = _want_safeguard(c)
    if want is None:
        return CheckResult("safeguard", "skip", "the case expects no Deck")
    verdict, err = _recorded_verdict(c)
    if err:
        return CheckResult("safeguard", "fail", err)
    rules = safeguard.rule_findings(c.deck)
    model = safeguard.model_part(verdict) if verdict else None
    decision, summary, findings = safeguard.merge(rules, model, verdict.model_error if verdict else None)
    by = "rules + recorded model" if model else "rules only (no model verdict recorded)"
    fails = [] if decision == want else [f"expected {want}, got {decision} ({by})"
                                         + (f": {summary}" if decision == "block" else "")]
    fails += [f"block finding {f.excerpt!r} doesn't explain itself (explanation and fix)"
              for f in findings if f.severity == "block" and not (f.excerpt and f.explanation and f.fix)]
    return _result("safeguard", fails, ok=f"{decision} · {len(rules)} rule hit(s) · {by}")


def check_safeguard_llm(c: Ctx) -> CheckResult:
    want = _want_safeguard(c)
    verdict, err = _recorded_verdict(c)
    if want is None or err:
        return CheckResult("safeguard·llm", "skip", err or "the case expects no Deck", "llm")
    if verdict and verdict.model_error:
        return CheckResult("safeguard·llm", "fail", f"the model failed: {verdict.model_error}", "llm")
    model = safeguard.model_part(verdict) if verdict else None
    if model is None:
        return CheckResult("safeguard·llm", "skip", "no model verdict recorded", "llm")
    said, _, _ = safeguard.merge([], model)  # the model on its own (its block findings count as a block)
    fails, ok = [], f"the safeguard agent said {said}"
    if said != want:
        if want == "block" and safeguard.rule_findings(c.deck):
            ok += "; the host rules block it regardless"
        else:
            fails.append(f"the safeguard agent said {said}, expected {want}")
    texts = {(loc, idx): t.casefold() for loc, idx, t in safeguard.texts(c.deck)}
    warns = [f"{f.location} {f.slide_index if f.slide_index is not None else ''}: excerpt {f.excerpt!r} is not in "
             "that text" for f in model.findings
             if f.location != "image" and (key := (f.location, f.slide_index if f.location == "slide" else None))
             in texts and f.excerpt.casefold() not in texts[key]]
    return _result("safeguard·llm", fails, warns, ok=ok, judge="llm")


DECK_CHECKS = [check_slides, check_hashtags, check_links, check_facts, check_photos, check_facts_llm,
               check_photos_llm, check_safeguard, check_safeguard_llm]
SAFEGUARD_CHECKS = [check_safeguard, check_safeguard_llm]


def run_checks(case: Case, rec: Recording) -> list[CheckResult]:
    """All checks for one recording, in report order."""
    errors: list[str] = []
    briefs: list[EventBrief] = []
    for i, raw in enumerate(rec.briefs):
        try:
            briefs.append(EventBrief.model_validate(raw))
        except ValidationError as e:
            errors.append(f"brief {i}: {e.error_count()} schema error(s): {str(e).splitlines()[1]}")
    deck = report = None
    if rec.deck is not None:
        try:
            deck = CardDeck.model_validate(rec.deck)
        except ValidationError as e:
            errors.append(f"deck: {e.error_count()} schema error(s): {str(e).splitlines()[1]}")
    if rec.verification is not None:
        report = VerificationReport.model_validate(rec.verification)
    c = Ctx(case, rec, briefs, deck, report)

    results = [check_outcome(c)]
    if case.kind == "guide":  # Guide Decks (#27): add their checks here once the pipeline produces them
        return [*results, CheckResult("guide", "skip", "Guide checks arrive with #27")]
    results.append(check_schema(c, errors))
    checks = SAFEGUARD_CHECKS if case.kind == "safeguard" else [*DECK_CHECKS, check_expectations]
    if deck is None:
        skipped = "no Deck" if rec.deck is None else "Deck failed its schema"
        return results + [CheckResult(f.__name__.removeprefix("check_").replace("_llm", "·llm"), "skip", skipped,
                                      "llm" if f.__name__.endswith("_llm") else "rule")
                          for f in checks]
    return results + [f(c) for f in checks]

