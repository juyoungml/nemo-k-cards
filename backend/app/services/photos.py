"""Photo vetting on the host (DESIGN.md "Photo sourcing order").

The researcher proposes `EventBrief.images`; nothing it claims is trusted. For each candidate we check:
  - license allows a text overlay (KOGL type 3 forbids changes, so it never qualifies),
  - the image comes from the event's own verified official domain or a public tourism host,
    never from Instagram / blogs / news CDNs (other creators' photos),
  - it downloads as a real JPEG/PNG/WebP, 30 KB–15 MB, at least MIN_WIDTH px wide.
Survivors are saved under the job's output dir so rendering needs no network and the photo can't change
between review and publish. Events with no survivor get the Poster style.
"""

import asyncio
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.schemas import EventBrief, ImageAsset
from app.services.link_checker import UA, _registrable, suspicious_reason

OVERLAY_LICENSES = {"official_permission", "kogl_1", "cc0", "cc_by", "unsplash", "pexels"}
# Public tourism / government hosts whose photos are published for reuse with credit.
PUBLIC_HOSTS = {"visitkorea.or.kr", "visitseoul.net", "korea.net", "kogl.or.kr"}
# Other creators' photos: never.
BLOCKED_HOSTS = {"instagram.com", "cdninstagram.com", "fbcdn.net", "facebook.com", "pstatic.net", "naver.net",
                 "daumcdn.net", "kakaocdn.net", "pinimg.com", "pinterest.com", "twimg.com", "tiktokcdn.com",
                 "googleusercontent.com", "gstatic.com"}
MIN_WIDTH = 800
MIN_BYTES, MAX_BYTES = 30_000, 15_000_000
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@dataclass
class Vetted:
    event_id: str
    asset: ImageAsset          # url points at the local copy (file://)
    remote_url: str


def _is_public_host(reg: str) -> bool:
    return reg in PUBLIC_HOSTS or reg.endswith(".go.kr") or reg == "go.kr"


def reject_reason(img: ImageAsset, brief: EventBrief, ok_urls: set[str]) -> str | None:
    """Static checks before any download. None = worth fetching."""
    if img.license not in OVERLAY_LICENSES:
        return f"license {img.license} does not allow text overlay" if img.license != "ai_generated" else \
            "the researcher may not supply AI-generated images"
    if not img.allow_overlay:
        return "license marked no-overlay"
    if not img.credit.strip():
        return "no credit"
    host = (urlparse(img.url).hostname or "").lower()
    if urlparse(img.url).scheme != "https" or not host:
        return "not an https URL"
    if reason := suspicious_reason(img.url):
        return f"suspicious URL ({reason})"
    reg = _registrable(host)
    if reg in BLOCKED_HOSTS or any(host.endswith("." + b) for b in BLOCKED_HOSTS):
        return f"{reg} hosts other creators' photos"
    official = {_registrable(urlparse(str(s.url)).hostname or "") for s in brief.sources
                if str(s.url) in ok_urls and s.kind in ("official", "public_api", "ticketing")}
    if not (_is_public_host(reg) or reg in official):
        return f"{reg} is not the event's verified official domain or a public tourism host"
    if img.source_url:
        src_reg = _registrable(urlparse(img.source_url).hostname or "")
        if not (_is_public_host(src_reg) or src_reg in official):
            return f"source page {src_reg} is not official"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG/PNG/WebP header, without an imaging library."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


async def _download(img: ImageAsset, dest: Path, client: httpx.AsyncClient) -> str | None:
    """Fetch and validate; write to dest (extension added). Returns a reject reason or None."""
    try:
        resp = await client.get(img.url)
    except httpx.HTTPError as e:
        return f"download failed ({type(e).__name__})"
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"
    if _registrable(urlparse(str(resp.url)).hostname or "") != _registrable(urlparse(img.url).hostname or ""):
        return "redirected to another domain"
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in TYPES:
        return f"not a JPEG/PNG/WebP ({ctype or 'no content-type'})"
    data = resp.content
    if not MIN_BYTES <= len(data) <= MAX_BYTES:
        return f"{len(data) // 1000} KB is outside 30 KB–15 MB"
    size = image_size(data)
    if not size:
        return "unreadable image header"
    if size[0] < MIN_WIDTH:
        return f"too small ({size[0]}×{size[1]}, need ≥ {MIN_WIDTH}px wide)"
    dest.with_suffix("." + TYPES[ctype]).write_bytes(data)
    return None


async def collect(briefs: list[EventBrief], ok_urls: set[str], out_dir: Path) -> tuple[dict[str, Vetted], list[str]]:
    """First usable photo per event → {event_id: Vetted}, plus human-readable log lines."""
    photo_dir = out_dir / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Vetted] = {}
    log: list[str] = []

    async def one(brief: EventBrief, client: httpx.AsyncClient) -> None:
        for n, img in enumerate(brief.images[:3]):
            if reason := reject_reason(img, brief, ok_urls):
                log.append(f"{brief.id}: photo skipped, {reason}")
                continue
            dest = photo_dir / f"{brief.id}-{n}"
            if reason := await _download(img, dest, client):
                log.append(f"{brief.id}: photo skipped, {reason}")
                continue
            local = next(photo_dir.glob(f"{brief.id}-{n}.*"))
            found[brief.id] = Vetted(brief.id, img.model_copy(update={"url": local.as_uri()}), img.url)
            log.append(f"{brief.id}: photo ok ({img.license}, {img.credit})")
            return

    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=UA) as client:
        await asyncio.gather(*(one(b, client) for b in briefs))
    return found, log
