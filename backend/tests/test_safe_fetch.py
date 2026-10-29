"""SEC-1: the host never fetches internal addresses, directly, through DNS or through a redirect. No network:
DNS is a dict and HTTP is an httpx.MockTransport that records every request it would have sent."""

import ipaddress

import httpx
import pytest

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


class Web:
    """Mock web: path → response; remembers every URL actually requested."""

    def __init__(self, routes: dict[str, httpx.Response]):
        self.routes, self.seen = routes, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(str(req.url))
        return self.routes.get(str(req.url), httpx.Response(200, text="ok"))

    def client(self, **_kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=False)


def moved(to: str, code: int = 302) -> httpx.Response:
    return httpx.Response(code, headers={"location": to})


# ---------------------------------------------------------------- address policy

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "224.0.0.1",
    "0.0.0.0", "255.255.255.255", "::1", "::", "fc00::1", "fd00:ec2::254", "fe80::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "2002:7f00:1::", "64:ff9b::a9fe:a9fe",
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
    web = Web({"https://public.example/big": httpx.Response(200, content=b"x" * 5000)})
    async with web.client() as c:
        with pytest.raises(safe_fetch.TooLarge):
            await safe_fetch.fetch(c, "GET", "https://public.example/big", max_bytes=1000)
        assert (await safe_fetch.fetch(c, "GET", "https://public.example/big", max_bytes=0)).content == b""


# ---------------------------------------------------------------- connection pinning (DNS rebinding)

async def test_client_pins_connections_to_public_addresses(monkeypatch):
    """Even if the pre-check were fooled (DNS answered differently a moment ago), the real client re-checks the
    address it actually connects to and refuses, without opening a connection to it."""
    async def fooled(url):
        return None
    monkeypatch.setattr(safe_fetch, "check_url", fooled)
    async with safe_fetch.client(timeout=2) as c:
        for url in ("http://127.0.0.1:9/", "http://[::1]:9/", "http://169.254.169.254/latest/meta-data/"):
            with pytest.raises(safe_fetch.BlockedURL):
                await safe_fetch.fetch(c, "GET", url)


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
