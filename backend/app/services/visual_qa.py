"""Deterministic checks on rendered slides: overflow, fallback fonts (tofu), PII (SPEC §7)."""

from app.renderer.base import RenderedSlide
from app.schemas import QAReport


def run_qa(slides: list[RenderedSlide], texts: dict[int, str]) -> QAReport:
    raise NotImplementedError
