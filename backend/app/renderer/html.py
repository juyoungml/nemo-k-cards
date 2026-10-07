"""HtmlRenderer: Jinja2 card templates -> Playwright (Chromium) -> 1080x1350 JPEG (SPEC §9, BACKEND §5).

Fonts are bundled in renderer/fonts and loaded via file://, so rendering needs no network.
Each slide's HTML is written next to its JPEG for debugging; text marked with data-qa is measured
for visual QA (overflow, font size).
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.renderer.base import RenderedSlide, TextMeasure
from app.schemas import CardDeck, EventBrief, Slide

HERE = Path(__file__).parent
FONT_DIR = HERE / "fonts"

Theme = Literal["bold", "clean", "pop"]  # kept for config compatibility; one brand look (DESIGN.md)
THEMES: tuple[Theme, ...] = ("clean",)

# Category -> (label, Seoul Metro line no., line color, poster text, poster title). DESIGN.md "Category colors".
CATEGORY = {
    "popup": ("Pop-up", "8", "#E6186C", "#111418", "#FFFFFF"),
    "exhibition": ("Exhibition", "1", "#0052A4", "#FFFFFF", "#FFFFFF"),
    "festival": ("Festival", "3", "#EF7C1C", "#111418", "#FFFFFF"),
    "performance": ("Performance", "5", "#996CAC", "#FFFFFF", "#FFFFFF"),
    "experience": ("Experience", "2", "#00A84D", "#FFFFFF", "#FFFFFF"),
    "other": ("Event", "9", "#BDB092", "#111418", "#111418"),
}
# Neighborhood -> Hangul set huge on the cover. Falls back to 서울.
HANGUL = {"seongsu": "성수", "hongdae": "홍대", "itaewon": "이태원", "myeongdong": "명동", "jongno": "종로",
          "gangnam": "강남", "insadong": "인사동", "mangwon": "망원", "euljiro": "을지로", "yeonnam": "연남",
          "hannam": "한남", "jamsil": "잠실", "bukchon": "북촌", "ikseon": "익선", "yeouido": "여의도", "busan": "부산"}

# Auto-fit: if the column overflows, shrink text blocks together (5% steps) down to a readable floor.
# Photos already shrink first (flex: 1 1 0). Anything still overflowing at the floor is caught by QA.
FIT_JS = """
() => {
  const pad = document.querySelector('.pad');
  const blocks = [...pad.querySelectorAll('.title, .lead, .facts, ul.tips, .why, .src, .taxi, .route, .ko, .row, .num')];
  const floor = el => el.classList.contains('title') ? 56 : el.classList.contains('num') ? 120 : 30;
  const overflowing = () => pad.scrollHeight > pad.clientHeight + 1;
  let steps = 0;
  while (overflowing() && steps < 30) {
    let shrunk = false;
    for (const el of blocks) {
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs > floor(el)) { el.style.fontSize = Math.max(floor(el), fs * 0.95) + 'px'; shrunk = true; }
      el.querySelectorAll('dd, dt, li, .addr, .chip, .stop').forEach(c => {
        const f = parseFloat(getComputedStyle(c).fontSize);
        if (f > 30) { c.style.fontSize = Math.max(30, f * 0.95) + 'px'; shrunk = true; }
      });
    }
    if (!shrunk) break;
    steps++;
  }
  return steps;
}
"""

MEASURE_JS = """
() => [...document.querySelectorAll('[data-qa]')].map(el => {
  const cs = getComputedStyle(el), r = el.getBoundingClientRect();
  const hasText = [...el.querySelectorAll('*'), el].some(n =>
    [...n.childNodes].some(c => c.nodeType === 3 && c.textContent.trim()));
  // smallest font actually used by text inside this element
  const sizes = [...el.querySelectorAll('*'), el]
    .filter(n => [...n.childNodes].some(c => c.nodeType === 3 && c.textContent.trim()))
    .map(n => parseFloat(getComputedStyle(n).fontSize));
  return { selector: el.dataset.qa,
           overflow: el.scrollHeight > el.clientHeight + 8 || el.scrollWidth > el.clientWidth + 8
                     || r.bottom > 1350 - 40 || r.right > 1080,
           font_px: hasText ? Math.min(...sizes) : 999 };
})
"""


def daterange(start: date | str, end: date | str) -> str:
    """'Oct 9–11', 'Oct 30 – Nov 2', 'Oct 9'."""
    s = date.fromisoformat(start) if isinstance(start, str) else start
    e = date.fromisoformat(end) if isinstance(end, str) else end
    if s == e:
        return f"{s:%b} {s.day}"
    if (s.year, s.month) == (e.year, e.month):
        return f"{s:%b} {s.day}–{e.day}"
    return f"{s:%b} {s.day} – {e:%b} {e.day}"


def weekend_label(today: date) -> str:
    """'Oct 10–11' for the coming weekend (this weekend if today is Sat/Sun)."""
    sat = today + timedelta(days=(5 - today.weekday()) % 7) if today.weekday() < 5 else today - timedelta(
        days=today.weekday() - 5)
    return daterange(sat, sat + timedelta(days=1))


def access_badges(event: EventBrief | None) -> list[dict]:
    """Foreigner access chips from EventBrief.access (BACKEND §3). Empty until the schema has it."""
    acc = getattr(event, "access", None) if event else None
    if not acc:
        return []
    get = acc.get if isinstance(acc, dict) else lambda k: getattr(acc, k, None)
    out: list[dict] = []
    phone = get("korean_phone")
    if phone == "not_needed":
        out.append({"text": "No Korean phone needed", "warn": False})
    elif phone == "passport_ok":
        out.append({"text": "Passport OK on site", "warn": False})
    elif phone == "required":
        out.append({"text": "Korean phone needed", "warn": True})
    if get("foreign_card") is True:
        out.append({"text": "Foreign card OK", "warn": False})
    if get("cash_only") is True:
        out.append({"text": "Cash only", "warn": True})
    if get("english") is True:
        out.append({"text": "English OK", "warn": False})
    return out


def slide_image(slide: Slide) -> dict | None:
    """Supports the frozen `Slide.image: ImageAsset` and the legacy `image_url` field."""
    img = getattr(slide, "image", None)
    if img:
        d = img if isinstance(img, dict) else img.model_dump()
        return {"url": d.get("url"), "credit": d.get("credit"), "allow_overlay": d.get("allow_overlay", True)}
    url = getattr(slide, "image_url", None)
    return {"url": url, "credit": None, "allow_overlay": True} if url else None


class HtmlRenderer:
    def __init__(self, theme: Theme = "clean",
                 account: str = "@whatsonkorea", as_of: date | None = None) -> None:
        self.theme, self.account = theme, account
        self.today = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
        self.as_of = self.today.strftime("%Y.%m.%d")
        self.env = Environment(loader=FileSystemLoader(HERE / "templates"),
                               autoescape=select_autoescape(["html"]), undefined=StrictUndefined)
        self.env.filters["daterange"] = daterange

    def context(self, deck: CardDeck, slide: Slide, briefs: dict[str, EventBrief]) -> dict:
        event = briefs.get(slide.event_id) if slide.event_id else None
        event_slides = [s for s in deck.slides if s.layout == "event"]
        event_ids = [s.event_id for s in event_slides]
        # Sources = only events that made it into the deck (excluded/lookalike links never appear on a card).
        in_deck = [briefs[i] for i in dict.fromkeys(s.event_id for s in deck.slides if s.event_id) if i in briefs]
        hosts = sorted({urlparse(str(src.url)).hostname or "" for b in in_deck for src in b.sources} - {""})
        credits = sorted({c for s in deck.slides if (c := (slide_image(s) or {}).get("credit"))})
        img = slide_image(slide)
        sat = self.today + timedelta(days=(5 - self.today.weekday()) % 7) if self.today.weekday() < 5 else (
            self.today - timedelta(days=self.today.weekday() - 5))
        cat = event.category if event and event.category in CATEGORY else "other"
        label, line, color, poster_fg, poster_title = CATEGORY[cat]
        ends_soon = event and (event.end_date - self.today).days <= 14
        photo = img if img and img.get("url") and img.get("allow_overlay") else None
        # Cover Hangul: the place the headline names, else a neighborhood every event shares, else 서울.
        def place(text: str) -> str | None:
            return next((ko for en, ko in HANGUL.items() if en in text.lower()), None)
        venues = {place(b.venue_en or "") for b in in_deck}
        hangul = place(deck.slides[0].heading) or (venues.pop() if len(venues) == 1 and None not in venues else "서울")
        if slide.layout in ("cover", "cta"):
            slide_class = "dark" + (" has-photo" if photo else "")
        elif slide.layout == "event" and event:
            slide_class = "kf" if photo else "poster"
        else:
            slide_class = ""
        return {
            "deck": deck, "slide": slide, "event": event, "theme": self.theme,
            "total": len(deck.slides), "account": self.account, "as_of": self.as_of,
            "font_dir": FONT_DIR.as_uri(), "slide_class": slide_class,
            "palette_css": f"--cat:{color};--poster-fg:{poster_fg};--poster-title:{poster_title}",
            # Only licensed images that allow text overlay are used; never a placeholder.
            "photo": photo,
            "weekend": weekend_label(self.today), "issue_no": f"{sat.isocalendar().week:02d}", "hangul": hangul,
            "event_no": event_ids.index(slide.event_id) + 1 if slide.event_id in event_ids else 1,
            "area": (event.venue_en or "").split(",")[0].split("·")[0].strip()[:18] if event else "",
            "cat_label": label, "cat_line": line, "cat_color": color,
            "until": f"{event.end_date:%b} {event.end_date.day}" if ends_soon else None,
            "badges": access_badges(event),
            "source_hosts": hosts, "credits": credits,
        }

    def html(self, deck: CardDeck, slide: Slide, briefs: dict[str, EventBrief]) -> str:
        return self.env.get_template(f"{slide.layout}.html").render(**self.context(deck, slide, briefs))

    async def render(self, deck: CardDeck, out_dir: Path,
                     briefs: list[EventBrief] | None = None) -> list[RenderedSlide]:
        from playwright.async_api import async_playwright

        by_id = {b.id: b for b in (briefs or [])}
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[RenderedSlide] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            for slide in sorted(deck.slides, key=lambda s: s.index):
                html_path = out_dir / f"slide-{slide.index:02d}.html"
                html_path.write_text(self.html(deck, slide, by_id), encoding="utf-8")
                await page.goto(html_path.as_uri())
                await page.evaluate("document.fonts.ready")
                await page.evaluate(FIT_JS)
                measures = [TextMeasure(**m) for m in await page.evaluate(MEASURE_JS)]
                jpg = out_dir / f"slide-{slide.index:02d}.jpg"
                await page.screenshot(path=str(jpg), type="jpeg", quality=90)
                rendered.append(RenderedSlide(index=slide.index, path=jpg, measures=measures))
            await browser.close()
        return rendered
