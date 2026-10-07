from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.schemas import CardDeck


class TextMeasure(BaseModel):
    selector: str
    overflow: bool
    fallback_fonts: list[str] = []
    font_px: float


class RenderedSlide(BaseModel):
    index: int
    path: Path
    measures: list[TextMeasure] = []


class Renderer(Protocol):
    """Default: HtmlRenderer (Jinja2 + Playwright, 1080x1350). Optional: CanvaRenderer (SPEC §9)."""

    async def render(self, deck: CardDeck, out_dir: Path) -> list[RenderedSlide]: ...
