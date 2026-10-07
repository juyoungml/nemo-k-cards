"""Deterministic checks on rendered slides: overflow, font floor, PII, hashtags, secrets (SPEC §7, BACKEND §7)."""

import re

from app.renderer.base import RenderedSlide
from app.schemas import CardDeck, Issue, QAReport

MIN_FONT_PX = 30
PII = {
    "phone number": re.compile(r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"),
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "card number": re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"),
    "resident registration number": re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
}
SECRET_PATTERNS = re.compile(r"(sk-ant-[\w-]{10,}|IGAA[\w]{20,}|AKIA[0-9A-Z]{16}|eyJ[\w-]{20,}\.[\w-]{10,})")
HASHTAG = re.compile(r"#[\w가-힣]+")


def scan_text(text: str, where: str, slide_index: int | None = None,
              secrets: tuple[str, ...] = ()) -> list[Issue]:
    issues = [Issue(severity="block", category="pii", slide_index=slide_index,
                    message=f"{label} found in {where}") for label, rx in PII.items() if rx.search(text)]
    if SECRET_PATTERNS.search(text) or any(s and s in text for s in secrets):
        issues.append(Issue(severity="block", category="pii", slide_index=slide_index,
                            message=f"credential-like string found in {where}"))
    return issues


def run_qa(slides: list[RenderedSlide], deck: CardDeck, secrets: tuple[str, ...] = ()) -> QAReport:
    issues: list[Issue] = []
    for s in slides:
        for m in s.measures:
            if m.overflow:
                issues.append(Issue(severity="block", category="visual", slide_index=s.index,
                                    message=f"text overflows its box ({m.selector})"))
            elif m.font_px < MIN_FONT_PX:
                issues.append(Issue(severity="warn", category="visual", slide_index=s.index,
                                    message=f"text smaller than {MIN_FONT_PX}px ({m.selector}: {m.font_px:.0f}px)"))
    for sl in deck.slides:
        issues += scan_text(f"{sl.heading}\n{sl.body}", "slide text", sl.index, secrets)
    issues += scan_text(deck.caption, "caption", None, secrets)
    tags = HASHTAG.findall(deck.caption)
    if len(tags) > 5:
        issues.append(Issue(severity="block", category="tone",
                            message=f"{len(tags)} hashtags in caption; Instagram allows at most 5"))
    return QAReport(issues=issues)
