import struct
from pathlib import Path

import httpx
import pytest

from app.schemas import EventBrief, ImageAsset
from app.services import photos

BRIEF = EventBrief.model_validate({
    "id": "ev-jinju", "title_en": "Jinju Namgang Lantern Festival", "title_ko": "진주남강유등축제",
    "category": "festival", "start_date": "2026-10-01", "end_date": "2026-10-15", "venue_en": "Jinjuseong Fortress",
    "sources": [{"url": "https://www.yudeung.com/", "kind": "official"},
                {"url": "https://blog.naver.com/someone/1", "kind": "sns"}],
})
OK = {"https://www.yudeung.com/", "https://blog.naver.com/someone/1"}


def img(url: str, license: str = "kogl_1", **kw) -> ImageAsset:
    return ImageAsset(url=url, license=license, credit="© 진주시", **kw)


@pytest.mark.parametrize("asset, why", [
    (img("https://www.yudeung.com/photo/1.jpg"), None),                                  # event's official domain
    (img("https://tong.visitkorea.or.kr/cms/a.jpg"), None),                              # public tourism host
    (img("https://www.jinju.go.kr/img/a.jpg"), None),                                    # *.go.kr
    (img("https://www.yudeung.com/a.jpg", "kogl_3"), "does not allow text overlay"),     # KOGL 3 forbids changes
    (img("https://www.yudeung.com/a.jpg", "ai_generated"), "may not supply AI-generated"),
    (img("https://scontent.cdninstagram.com/a.jpg"), "other creators"),
    (img("https://blogfiles.pstatic.net/a.jpg"), "other creators"),
    (img("https://example-news.com/a.jpg"), "not the event's verified official domain"),
    (img("http://www.yudeung.com/a.jpg"), "not an https URL"),
    (img("https://www.yudeung.com/a.jpg", allow_overlay=False), "no-overlay"),
])
def test_reject_reason(asset, why):
    got = photos.reject_reason(asset, BRIEF, OK)
    assert (got is None) if why is None else (why in got)


def test_sns_source_domain_does_not_qualify():
    # blog.naver.com is a verified source here, but an sns one: its domain must not unlock photos.
    assert "not the event's verified official domain" in photos.reject_reason(img("https://blog.naver.com/a.jpg"), BRIEF, OK)


def _png(w: int, h: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\0" * 40_000


def _jpeg(w: int, h: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    sof = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, h, w) + b"\0" * 6
    return b"\xff\xd8" + app0 + sof + b"\0" * 40_000


def test_image_size():
    assert photos.image_size(_png(1200, 900)) == (1200, 900)
    assert photos.image_size(_jpeg(1080, 1350)) == (1080, 1350)
    assert photos.image_size(b"<html>") is None


async def test_collect_downloads_first_good_photo(tmp_path: Path, monkeypatch):
    files = {"https://www.yudeung.com/small.png": ("image/png", _png(400, 300)),
             "https://www.yudeung.com/page.html": ("text/html", b"<html>" * 10_000),
             "https://www.yudeung.com/big.jpg": ("image/jpeg", _jpeg(1600, 1200))}

    def handler(req: httpx.Request) -> httpx.Response:
        ctype, body = files[str(req.url)]
        return httpx.Response(200, headers={"content-type": ctype}, content=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(photos.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    brief = BRIEF.model_copy(update={"images": [img(u) for u in files]})
    found, log = await photos.collect([brief], OK, tmp_path)
    v = found["ev-jinju"]
    assert v.remote_url.endswith("big.jpg") and v.asset.url.startswith("file://")
    assert Path(v.asset.url.removeprefix("file://")).exists()
    assert any("too small" in line for line in log) and any("not a JPEG" in line for line in log)
