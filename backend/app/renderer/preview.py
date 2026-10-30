"""Render a demo scenario for a quick visual check.

    uv run python -m app.renderer.preview                     # demo/scenarios/good
    uv run python -m app.renderer.preview --scenario ../demo/scenarios/guide   # a Guide Deck (#27)

Output: backend/.data/preview/slide-NN.{html,jpg}
"""

import argparse
import asyncio
import json
from pathlib import Path

from app import guides
from app.config import settings
from app.renderer.html import HtmlRenderer
from app.schemas import CardDeck, EventBrief, GuideBrief, VerificationReport


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, default=settings.scenarios_dir / "good")
    ap.add_argument("--out", type=Path, default=settings.output_dir.parent / "preview")
    a = ap.parse_args()

    deck = CardDeck.model_validate_json((a.scenario / "deck.json").read_text())
    guide = None
    if (a.scenario / "guide.json").exists():
        # Verify as the pipeline does, with the scenario's recorded link checks: only officially sourced points stay.
        guide = GuideBrief.model_validate_json((a.scenario / "guide.json").read_text())
        checks = VerificationReport.model_validate_json((a.scenario / "verification.json").read_text()).checks
        excluded, _, _ = guides.check(guide, {c.url: c for c in checks}, {})
        guide = guide.model_copy(update={"points": guides.included(guide, excluded)})
        briefs = []
    else:
        briefs = [EventBrief.model_validate(b) for b in json.loads((a.scenario / "briefs.json").read_text())]
    slides = await HtmlRenderer().render(deck, a.out, briefs, guide=guide)
    bad = [f"{s.index}:{m.selector}" for s in slides for m in s.measures if m.overflow or m.font_px < 30]
    print(f"{len(slides)} slides → {a.out}" + (f"  ⚠ QA: {bad}" if bad else "  ✓ no overflow"))


if __name__ == "__main__":
    asyncio.run(main())
