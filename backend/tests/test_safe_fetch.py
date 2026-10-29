"""SEC-1: the host never fetches internal addresses, directly, through DNS or through a redirect. No network:
DNS is a dict and HTTP is an httpx.MockTransport that records every request it would have sent."""

import gzip
import ipaddress
import zlib

import httpx
import pytest
from httpx_secure import SSRFProtectionError

from app import drafts
from app.schemas import CardDeck, ImageAsset
from app.services import link_checker, photos, safe_fetch

DNS = {
    "localhost": ["127.0.0.1", "::1"],
    "public.example": ["93.184.215.14"],
    "other.example": ["93.184.215.15"],
    "internal.example": ["10.0.0.5"],
    "metadata.example": ["169.254.169.254"],
    "v6-loopback.example": ["::1"],
    "mixed.example": ["93.184.215.14", "192.168.1.10"],   # one bad answer is enough to refuse
    "mapped.example": ["::ffff:127.0.0.1"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in DNS:
            raise OSError("NXDOMAIN")
        return DNS[host]
    monkeypatch.setattr(safe_fetch, "resolve", resolve)


def raw(status: int = 200, headers=None, body: bytes = b"ok") -> tuple:
    """A route: (status, headers, body). The body is sent as-is, like a real server's (still compressed)."""
    return status, headers or {}, body


class Web:
    """Mock web: URL → raw response; remembers every URL actually requested."""

    def __init__(self, routes: dict[str, tuple]):
        self.routes, self.seen = routes, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(str(req.url))
        status, headers, body = self.routes.get(str(req.url), raw())
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))

    def client(self, **_kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=False)


def moved(to: str, code: int = 302) -> tuple:
    return raw(code, [(b"location", to.encode())], b"")


# ---------------------------------------------------------------- address policy

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "224.0.0.1",
    "0.0.0.0", "255.255.255.255", "::1", "::", "fc00::1", "fd00:ec2::254", "fe80::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "2002:7f00:1::", "64:ff9b::a9fe:a9fe",
    # explicit denylist: not blocked by `ipaddress` flags on every Python version
    "192.0.0.192", "0.1.2.3", "fec0::1", "100.100.100.200", "168.63.129.16", "198.18.0.1", "240.0.0.1",
])
def test_non_public_addresses_are_blocked(ip):
    assert safe_fetch.blocked_ip_reason(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["93.184.215.14", "8.8.8.8", "2001:4860:4860::8888"])
def test_public_addresses_are_allowed(ip):
    assert safe_fetch.blocked_ip_reason(ipaddress.ip_address(ip)) is None


# ---------------------------------------------------------------- fetch refuses before sending anything

@pytest.mark.parametrize("url, why", [
    ("http://localhost/", "loopback"),
    ("http://127.0.0.1:8000/jobs", "loopback"),
    ("http://[::1]/", "loopback"),
    ("http://internal.example/admin", "private"),
    ("http://metadata.example/latest/meta-data/", "link-local"),
    ("http://169.254.169.254/latest/meta-data/", "link-local"),
    ("http://v6-loopback.example/", "loopback"),
    ("http://mixed.example/", "private"),
    ("http://mapped.example/", "loopback"),
    ("http://100.64.0.1/", "carrier-grade NAT"),
    ("file:///etc/passwd", "not http(s)"),
    ("ftp://public.example/x", "not http(s)"),
    ("gopher://public.example/", "not http(s)"),
])
async def test_refused(url, why):
    web = Web({})
    async with web.client() as c:
        with pytest.raises(safe_fetch.BlockedURL) as e:
            await safe_fetch.fetch(c, "GET", url)
    assert why in e.value.reason and web.seen == []


async def test_redirect_to_metadata_is_refused():
    web = Web({"https://public.example/go": moved("http://169.254.169.254/latest/meta-data/iam")})
    async with web.client() as c:
        with pytest.raises(safe_fetch.BlockedURL, match="link-local"):
            await safe_fetch.fetch(c, "GET", "https://public.example/go")
    assert web.seen == ["https://public.example/go"]


async def test_redirect_to_name_resolving_internal_is_refused():
    web = Web({"https://public.example/a": moved("https://other.example/b"),
               "https://other.example/b": moved("http://internal.example/c", 301)})
    async with web.client() as c:
        with pytest.raises(safe_fetch.BlockedURL, match="internal.example resolves to 10.0.0.5"):
            await safe_fetch.fetch(c, "GET", "https://public.example/a")
    assert web.seen == ["https://public.example/a", "https://other.example/b"]


async def test_redirect_to_other_scheme_is_refused():
    web = Web({"https://public.example/a": moved("file:///etc/passwd")})
    async with web.client() as c:
        with pytest.raises(httpx.HTTPError):
            await safe_fetch.fetch(c, "GET", "https://public.example/a")
    assert web.seen == ["https://public.example/a"]


async def test_redirects_are_followed_and_capped():
    chain = {f"https://public.example/{i}": moved(f"https://public.example/{i + 1}") for i in range(5)}
    web = Web(chain)
    async with web.client() as c:
        resp = await safe_fetch.fetch(c, "GET", "https://public.example/2")      # 2 → 3 → 4 → 5: 3 hops
        assert resp.status_code == 200 and str(resp.url) == "https://public.example/5" and resp.text == "ok"
        with pytest.raises(httpx.TooManyRedirects):
            await safe_fetch.fetch(c, "GET", "https://public.example/0")         # 4 hops


async def test_body_is_capped():
    web = Web({"https://public.example/big": raw(body=b"x" * 5000)})
    async with web.client() as c:
        with pytest.raises(safe_fetch.TooLarge):
            await safe_fetch.fetch(c, "GET", "https://public.example/big", max_bytes=1000)
        assert (await safe_fetch.fetch(c, "GET", "https://public.example/big", max_bytes=0)).content == b""


def _gz(body: bytes, layers: int = 1) -> bytes:
    for _ in range(layers):
        body = gzip.compress(body, compresslevel=9)
    return body


async def test_compressed_body_is_capped_while_decoding():
    """A tiny gzip body can't expand past the cap (no gigabyte allocation), stacked encodings are refused,
    and an honest gzip response is decoded."""
    web = Web({
        "https://public.example/bomb": raw(200, {"content-encoding": "gzip"}, _gz(b"\0" * 50_000_000)),
        "https://public.example/nested": raw(200, {"content-encoding": "gzip, gzip"}, _gz(b"\0" * 50_000_000, 2)),
        "https://public.example/br": raw(200, {"content-encoding": "br"}, b"x"),
        "https://public.example/ok": raw(200, {"content-encoding": "gzip"}, _gz(b"hello" * 100)),
        "https://public.example/deflate": raw(200, {"content-encoding": "deflate"}, zlib.compress(b"hi" * 10)),
        "https://public.example/raw-deflate": raw(200, {"content-encoding": "deflate"},
                                                  zlib.compress(b"hi" * 10)[2:-4]),
    })
    assert len(web.routes["https://public.example/nested"][2]) < 2_000
    async with web.client() as c:
        for url in ("https://public.example/bomb", "https://public.example/nested", "https://public.example/br"):
            with pytest.raises(safe_fetch.TooLarge):
                await safe_fetch.fetch(c, "GET", url, max_bytes=1_000_000)
        ok = await safe_fetch.fetch(c, "GET", "https://public.example/ok")
        assert ok.content == b"hello" * 100 and "content-encoding" not in ok.headers
        for url in ("https://public.example/deflate", "https://public.example/raw-deflate"):
            assert (await safe_fetch.fetch(c, "GET", url)).content == b"hi" * 10


async def test_asks_for_uncompressed_bodies():
    sent = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: sent.append(r.headers.get("accept-encoding")) or httpx.Response(200, stream=httpx.ByteStream(b""))
    )) as c:
        await safe_fetch.fetch(c, "GET", "https://public.example/")
    assert sent == ["identity"] and safe_fetch.client().headers["accept-encoding"] == "identity"


# ---------------------------------------------------------------- invalid URLs fail closed, never crash

@pytest.mark.parametrize("url", ["https://１.visitkorea.or.kr/a.jpg", "https://[::1/x"])
async def test_invalid_urls_are_blocked(url):
    web = Web({})
    async with web.client() as c:
        with pytest.raises(safe_fetch.BlockedURL, match="invalid URL"):
            await safe_fetch.fetch(c, "GET", url)
    assert web.seen == []


async def test_redirect_to_invalid_url_is_blocked():
    web = Web({"https://public.example/a": moved("https://１.visitkorea.or.kr/a.jpg")})
    async with web.client() as c:
        with pytest.raises(httpx.HTTPError):
            await safe_fetch.fetch(c, "GET", "https://public.example/a")
    assert web.seen == ["https://public.example/a"]


def test_invalid_urls_in_static_checks():
    assert link_checker.suspicious_reason("https://[::1/x") == "invalid URL"
    brief = photos.EventBrief.model_validate({
        "id": "ev", "title_en": "t", "title_ko": "t", "start_date": "2026-10-01", "end_date": "2026-10-02",
        "venue_en": "v", "sources": [{"url": "https://www.visitkorea.or.kr/", "kind": "official"}]})
    assert photos.reject_reason(ImageAsset(url="https://[::1/x", license="kogl_1", credit="c"), brief, set()) \
        == "invalid URL"


async def test_invalid_photo_url_does_not_fail_collect(tmp_path, monkeypatch):
    """A URL that passes the static checks but httpx can't parse is skipped, not raised out of gather()."""
    web = Web({})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    brief = photos.EventBrief.model_validate({
        "id": "ev", "title_en": "t", "title_ko": "t", "start_date": "2026-10-01", "end_date": "2026-10-02",
        "venue_en": "v", "sources": [{"url": "https://www.visitkorea.or.kr/", "kind": "official"}],
        "images": [{"url": "https://１.visitkorea.or.kr/a.jpg", "license": "kogl_1", "credit": "© KTO"}]})
    found, log = await photos.collect([brief], {"https://www.visitkorea.or.kr/"}, tmp_path)
    assert found == {} and "invalid URL" in log[0] and web.seen == []


# ---------------------------------------------------------------- connection pinning (DNS rebinding)

async def test_client_pins_connections_to_public_addresses(monkeypatch):
    """Even if the pre-check were fooled (DNS answered differently a moment ago), the real client re-checks the
    address it actually connects to and refuses, without opening a connection to it. The refusal looks exactly
    like an unreachable host, so it can't be used to probe internal ports."""
    async def fooled(url):
        return None
    monkeypatch.setattr(safe_fetch, "check_url", fooled)
    async with safe_fetch.client(timeout=2) as c:
        for url in ("http://127.0.0.1:9/", "http://[::1]:9/", "http://169.254.169.254/latest/meta-data/",
                    "http://192.0.0.192/opc/v1/instance/", "http://[fec0::1]:9/"):
            with pytest.raises(httpx.ConnectError, match="^connection failed$") as e:
                await safe_fetch.fetch(c, "GET", url)
            assert isinstance(e.value.__cause__, SSRFProtectionError), url


def test_client_ignores_env_proxies_and_redirects():
    c = safe_fetch.client()
    assert not c.follow_redirects and not c.trust_env


# ---------------------------------------------------------------- callers

async def test_link_checker_marks_internal_links_suspicious(monkeypatch):
    web = Web({"https://public.example/event": moved("http://localhost:8000/health")})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    async with web.client() as c:
        chk = await link_checker.check_url("https://public.example/event", c)
        assert chk.status == "suspicious" and "loopback" in chk.reason
        chk = await link_checker.check_url("https://internal.example/", c)
        assert chk.status == "suspicious" and "private" in chk.reason
    assert web.seen == ["https://public.example/event"]


async def test_photo_download_refuses_internal_redirect(tmp_path):
    web = Web({"https://public.example/a.jpg": moved("http://metadata.example/a.jpg")})
    async with web.client() as c:
        why = await photos._download(ImageAsset(url="https://public.example/a.jpg", license="kogl_1", credit="c"),
                                     tmp_path / "a", c)
    assert why.startswith("blocked") and not list(tmp_path.iterdir())


async def test_brainstorm_page_fetch_refuses_internal(monkeypatch):
    web = Web({})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    texts, blocked = await drafts.fetch_pages(["https://internal.example/secret"])
    assert texts == [] and "internal.example" in blocked[0] and web.seen == []


# ---------------------------------------------------------------- slide images (only host-vetted photos)

def _deck(**event_slide) -> CardDeck:
    slides = [{"index": 0, "layout": "cover", "heading": "c",
               "image": {"url": "http://169.254.169.254/x.jpg", "license": "cc0", "credit": "x"}},
              {"index": 1, "layout": "event", "heading": "e", "event_id": "ev1", **event_slide}]
    slides += [{"index": i, "layout": "tips", "heading": "t"} for i in range(2, 6)]
    return CardDeck.model_validate({"job_id": "t", "title": "t", "slides": slides, "caption": "x"})


def test_agent_supplied_slide_images_are_dropped():
    agent = {"url": "https://evil.example/x.jpg", "license": "kogl_1", "credit": "x"}
    d = _deck(image=agent)
    log = photos.attach_vetted(d, {})
    assert all(s.image is None for s in d.slides) and len(log) == 2


def test_vetted_photo_replaces_agent_image():
    local = ImageAsset(url="file:///tmp/out/photos/ev1-0.jpg", license="kogl_1", credit="© KTO")
    d = _deck(image={"url": "https://evil.example/x.jpg", "license": "kogl_1", "credit": "x"})
    photos.attach_vetted(d, {"ev1": photos.Vetted("ev1", local, "https://www.visitkorea.or.kr/a.jpg")})
    assert d.slides[1].image == local and d.slides[0].image is None


def test_vetted_photo_and_stock_still_flow():
    d = _deck(stock_id="popup-paper-goods")
    photos.attach_vetted(d, {})
    photos.attach_stock(d, [{"id": "popup-paper-goods", "file": "popup.jpg"}])
    assert d.slides[1].image.url.startswith("file:") and d.slides[1].image.license == "ai_generated"


def test_renderer_never_loads_remote_images():
    from app.renderer.html import HtmlRenderer
    d = _deck()
    ctx = HtmlRenderer().context(d, d.slides[0], {})
    assert ctx["photo"] is None


async def test_renderer_aborts_every_network_request():
    from types import SimpleNamespace

    from app.renderer.html import offline

    done = []

    class Route:
        def __init__(self, url):
            self.request = SimpleNamespace(url=url)

        async def continue_(self):
            done.append(("continue", self.request.url))

        async def abort(self):
            done.append(("abort", self.request.url))

    for url in ("file:///x/slide.html", "data:image/svg+xml,x", "http://169.254.169.254/", "https://a.example/x.jpg"):
        await offline(Route(url))
    assert [d[0] for d in done] == ["continue", "continue", "abort", "abort"]
