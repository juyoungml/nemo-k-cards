"""Deterministic link verification: dead / redirect / suspicious links (SPEC §7, BACKEND §7)."""

import asyncio
import ipaddress
import re
from urllib.parse import urlparse

import httpx

from app.schemas import EventBrief, LinkCheck, VerificationReport

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "han.gl", "me2.do", "url.kr", "vo.la", "is.gd"}
RISKY_TLDS = {"xyz", "top", "click", "zip", "icu", "rest", "cam", "monster", "gq", "tk"}
# Known official/organizer domains; a near-miss of one of these is a lookalike.
OFFICIAL = {"visitkorea.or.kr", "seoul.go.kr", "visitseoul.net", "visitbusan.net", "korea.net", "go.kr", "or.kr", "interpark.com", "yes24.com",
            "popply.co.kr", "popga.co.kr", "naver.com", "kakao.com", "instagram.com"}
UA = {"User-Agent": "Mozilla/5.0 (WhatsOnKorea link checker; +https://github.com/juyoungml/nemo-k-cards)"}


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
    host = (urlparse(url).hostname or "").lower()
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
    client = client or httpx.AsyncClient(timeout=5, follow_redirects=True, headers=UA)
    try:
        resp = await client.head(url)
        if resp.status_code in (403, 405) or resp.status_code >= 500:
            resp = await client.get(url)
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
    except httpx.TimeoutException:
        return LinkCheck(url=url, status="timeout", reason="no response in 5s")
    except httpx.HTTPError as e:
        return LinkCheck(url=url, status="dead", reason=type(e).__name__)
    finally:
        if own:
            await client.aclose()


async def verify(briefs: list[EventBrief]) -> VerificationReport:
    """Check every source; exclude events without at least one healthy source."""
    urls = list(dict.fromkeys(str(s.url) for b in briefs for s in b.sources))
    async with httpx.AsyncClient(timeout=5, follow_redirects=True, headers=UA) as client:
        checks = await asyncio.gather(*(check_url(u, client) for u in urls))
    by_url = {c.url: c for c in checks}
    excluded = [b.id for b in briefs if not any(by_url[str(s.url)].status == "ok" for s in b.sources)]
    return VerificationReport(checks=list(checks), excluded_event_ids=excluded)
