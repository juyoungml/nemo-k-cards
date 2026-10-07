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

Theme = Literal["bold", "clean", "pop"]
Mood = Literal["autumn", "night", "bright"]
THEMES: tuple[Theme, ...] = ("bold", "clean", "pop")

# Theme backgrounds that change with the season/mood. Everything else lives in _cards.css.
MOODS: dict[str, dict[str, str]] = {
    "autumn": {"bold-bg": "#0B2F6B", "pop-bg": "#FFE2C7", "blob1": "#FF9F43", "blob2": "#E85D4A"},
    "night": {"bold-bg": "#14123A", "pop-bg": "#E3DBFF", "blob1": "#7B3FE4", "blob2": "#00C2A8"},
    "bright": {"bold-bg": "#0047A0", "pop-bg": "#DDF4FF", "blob1": "#FFD166", "blob2": "#4CC9F0"},
}
# Photo placeholder gradient per event category (used when a slide has no licensed image).
PLACEHOLDER = {
    "popup": ("#FF8FB1", "#FFE3EC"), "festival": ("#1B1F4B", "#F28C38"),
    "exhibition": ("#E9DFC8", "#8A6A3B"), "performance": ("#2D0F3F", "#00E0B8"),
    "experience": ("#0F2A3D", "#E8B04B"), "other": ("#24324A", "#7FA7D9"),
}

# Auto-fit: if the column overflows, shrink text blocks together (5% steps) down to a readable floor.
# Photos already shrink first (flex: 1 1 0). Anything still overflowing at the floor is caught by QA.
FIT_JS = """
() => {
  const pad = document.querySelector('.pad');
  const blocks = [...pad.querySelectorAll('.ttl, .sub, .kv, ul.list, .src, .place, .chips, .ko')];
  const floor = el => el.classList.contains('ttl') ? 52 : 30;
  const overflowing = () => pad.scrollHeight > pad.clientHeight + 1;
  let steps = 0;
  while (overflowing() && steps < 30) {
    let shrunk = false;
    for (const el of blocks) {
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs > floor(el)) { el.style.fontSize = Math.max(floor(el), fs * 0.95) + 'px'; shrunk = true; }
      el.querySelectorAll('dd, li, .addr, .chip').forEach(c => {
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
           overflow: el.scrollHeight > el.clientHeight + 4 || el.scrollWidth > el.clientWidth + 4
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
    def __init__(self, theme: Theme = "clean", mood: Mood = "autumn",
                 account: str = "@whatsonkorea", as_of: date | None = None) -> None:
        self.theme, self.mood, self.account = theme, mood, account
        self.today = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
        self.as_of = self.today.strftime("%Y.%m.%d")
        self.env = Environment(loader=FileSystemLoader(HERE / "templates"),
                               autoescape=select_autoescape(["html"]), undefined=StrictUndefined)
        self.env.filters["daterange"] = daterange

    def context(self, deck: CardDeck, slide: Slide, briefs: dict[str, EventBrief]) -> dict:
        event = briefs.get(slide.event_id) if slide.event_id else None
        cover_event = event or next(iter(briefs.values()), None)
        ph = PLACEHOLDER.get((cover_event.category if cover_event else "other"), PLACEHOLDER["other"])
        palette = {**MOODS[self.mood], "ph1": ph[0], "ph2": ph[1]}
        event_ids = [s.event_id for s in deck.slides if s.layout == "event"]
        hosts = sorted({urlparse(str(src.url)).hostname or "" for b in briefs.values() for src in b.sources} - {""})
        credits = sorted({c for s in deck.slides if (c := (slide_image(s) or {}).get("credit"))})
        return {
            "deck": deck, "slide": slide, "event": event, "theme": self.theme,
            "total": len(deck.slides), "account": self.account, "as_of": self.as_of,
            "font_dir": FONT_DIR.as_uri(),
            "palette_css": ";".join(f"--{k}:{v}" for k, v in palette.items()),
            "img": slide_image(slide),
            "kicker": f"{weekend_label(self.today)} · {self.account}",
            "event_no": event_ids.index(slide.event_id) + 1 if slide.event_id in event_ids else "",
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
