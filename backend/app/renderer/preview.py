"""Render a demo scenario for a quick visual check.

    uv run python -m app.renderer.preview                     # demo/scenarios/good

Output: backend/.data/preview/slide-NN.{html,jpg}
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.renderer.html import HtmlRenderer
from app.schemas import CardDeck, EventBrief


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, default=settings.scenarios_dir / "good")
    ap.add_argument("--out", type=Path, default=settings.output_dir.parent / "preview")
    a = ap.parse_args()

    deck = CardDeck.model_validate_json((a.scenario / "deck.json").read_text())
    briefs = [EventBrief.model_validate(b) for b in json.loads((a.scenario / "briefs.json").read_text())]
    slides = await HtmlRenderer().render(deck, a.out, briefs)
    bad = [f"{s.index}:{m.selector}" for s in slides for m in s.measures if m.overflow or m.font_px < 30]
    print(f"{len(slides)} slides → {a.out}" + (f"  ⚠ QA: {bad}" if bad else "  ✓ no overflow"))


if __name__ == "__main__":
    asyncio.run(main())
