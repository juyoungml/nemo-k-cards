"""Deterministic link verification: dead / redirect / suspicious links (SPEC §7)."""

from app.schemas import EventBrief, LinkCheck, VerificationReport


async def check_url(url: str) -> LinkCheck:
    raise NotImplementedError  # TODO: HEAD->GET, redirect chain, shortener/TLD/lookalike checks


async def verify(briefs: list[EventBrief]) -> VerificationReport:
    raise NotImplementedError  # TODO: check all sources, exclude events without a healthy source
