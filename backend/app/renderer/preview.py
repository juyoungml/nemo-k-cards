"""Render a demo scenario in every theme for a quick visual check.

    uv run python -m app.renderer.preview                     # demo/scenarios/good, all themes
    uv run python -m app.renderer.preview --theme pop --mood night

Output: backend/.data/preview/<theme>/slide-NN.{html,jpg}
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.renderer.html import MOODS, THEMES, HtmlRenderer
from app.schemas import CardDeck, EventBrief


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, default=settings.scenarios_dir / "good")
    ap.add_argument("--theme", choices=[*THEMES, "all"], default="all")
    ap.add_argument("--mood", choices=list(MOODS), default="autumn")
    ap.add_argument("--out", type=Path, default=settings.output_dir.parent / "preview")
    a = ap.parse_args()

    deck = CardDeck.model_validate_json((a.scenario / "deck.json").read_text())
    briefs = [EventBrief.model_validate(b) for b in json.loads((a.scenario / "briefs.json").read_text())]
    for theme in THEMES if a.theme == "all" else (a.theme,):
        slides = await HtmlRenderer(theme=theme, mood=a.mood).render(deck, a.out / theme, briefs)
        bad = [f"{s.index}:{m.selector}" for s in slides for m in s.measures if m.overflow or m.font_px < 30]
        print(f"{theme}: {len(slides)} slides → {a.out / theme}" + (f"  ⚠ QA: {bad}" if bad else "  ✓ no overflow"))


if __name__ == "__main__":
    asyncio.run(main())
