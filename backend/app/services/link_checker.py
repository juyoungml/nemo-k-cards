"""Deterministic link verification: dead / redirect / suspicious links (SPEC §7, BACKEND §7)."""

import asyncio
import ipaddress
import re
from urllib.parse import urlparse

import httpx

from app.schemas import EventBrief, LinkCheck, VerificationReport
from app.services import safe_fetch
from app.services.safe_fetch import UA  # noqa: F401  (re-exported for callers)

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "han.gl", "me2.do", "url.kr", "vo.la", "is.gd"}
RISKY_TLDS = {"xyz", "top", "click", "zip", "icu", "rest", "cam", "monster", "gq", "tk"}
# Known official/organizer domains; a near-miss of one of these is a lookalike.
OFFICIAL = {"visitkorea.or.kr", "seoul.go.kr", "visitseoul.net", "visitbusan.net", "korea.net", "go.kr", "or.kr", "interpark.com", "yes24.com",
            "popply.co.kr", "popga.co.kr", "naver.com", "kakao.com", "instagram.com"}


def _registrable(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "or", "go", "ac", "ne", "re"} and parts[-1] == "kr":
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suspicious_reason(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:  # e.g. an unclosed IPv6 bracket
        return "invalid URL"
    if not host:
        return "no host"
    try:
        ipaddress.ip_address(host)
        return "raw IP address"
    except ValueError:
        pass
    if host in SHORTENERS:
        return "URL shortener"
    if host.rsplit(".", 1)[-1] in RISKY_TLDS:
        return "lookalike / risky TLD"
    reg = _registrable(host)
    if reg not in OFFICIAL:
        label = reg.split(".")[0]
        for off in OFFICIAL:
            name = off.split(".")[0]
            # brand inside a different domain with a hyphen/odd TLD, e.g. visitkorea-tickets.xyz
            if len(name) > 4 and name in label and reg != off and "-" in label:
                return f"lookalike of {off}"
            if len(name) > 4 and 0 < _lev(label, name) <= 2:
                return f"lookalike of {off}"
    if re.search(r"xn--", host):
        return "punycode / homoglyph"
    return None


async def check_url(url: str, client: httpx.AsyncClient | None = None) -> LinkCheck:
    if reason := suspicious_reason(url):
        return LinkCheck(url=url, status="suspicious", reason=reason)
    own = client is None
    client = client or safe_fetch.client(timeout=5)
    try:
        # Status only: redirects are followed by hand and every hop is re-checked (SEC-1).
        resp = await safe_fetch.fetch(client, "HEAD", url, max_bytes=0)
        if resp.status_code in (403, 405) or resp.status_code >= 500:
            resp = await safe_fetch.fetch(client, "GET", url, max_bytes=0)
        final = str(resp.url)
        if resp.status_code in (401, 403, 429):
            return LinkCheck(url=url, status="ok", http_code=resp.status_code, final_url=final,
                             reason="reachable; blocks automated checks")
        if resp.status_code >= 400:
            return LinkCheck(url=url, status="dead", http_code=resp.status_code, final_url=final)
        if _registrable(urlparse(final).hostname or "") != _registrable(urlparse(url).hostname or ""):
            return LinkCheck(url=url, status="redirect", http_code=resp.status_code, final_url=final,
                             reason="redirects to another domain")
        return LinkCheck(url=url, status="ok", http_code=resp.status_code, final_url=final)
    except safe_fetch.BlockedURL as e:
        return LinkCheck(url=url, status="suspicious", reason=f"blocked: {e.reason}")
    except httpx.TimeoutException:
        return LinkCheck(url=url, status="timeout", reason="no response in 5s")
    except httpx.HTTPError as e:
        return LinkCheck(url=url, status="dead", reason=type(e).__name__)
    finally:
        if own:
            await client.aclose()


async def check_urls(urls: list[str], known: dict[str, LinkCheck] | None = None) -> list[LinkCheck]:
    """Check each URL once, in order; `known` (demo fixtures) are not fetched again."""
    known = known or {}
    urls = list(dict.fromkeys(urls))
    todo = [u for u in urls if u not in known]
    async with safe_fetch.client(timeout=5) as client:
        fresh = await asyncio.gather(*(check_url(u, client) for u in todo))
    by_url = {**{u: known[u] for u in urls if u in known}, **{c.url: c for c in fresh}}
    return [by_url[u] for u in urls]


async def verify(briefs: list[EventBrief], known: dict[str, LinkCheck] | None = None,
                 materials: set[str] | frozenset[str] = frozenset()) -> VerificationReport:
    """Check every source; exclude events without at least one healthy source, unless the event is taken from
    Source material: `materials` holds the ids of the request's own material, which counts as verified (ADR 0003).

    `known` holds precomputed checks (demo fixtures) — those URLs are not fetched again.
    """
    urls = list(dict.fromkeys(str(s.url) for b in briefs for s in b.sources))
    checks = await check_urls(urls, known)
    by_url = {c.url: c for c in checks}
    excluded = [b.id for b in briefs if not any(by_url[str(s.url)].status == "ok" for s in b.sources)
                and not any(m in materials for m in b.materials)]
    return VerificationReport(checks=list(checks), excluded_event_ids=excluded)
