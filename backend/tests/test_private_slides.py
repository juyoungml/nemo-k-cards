"""Private slides (#18): slide files are reachable only through short-lived signed links, never across
Organizations, and the links handed to Instagram stop working once the publish attempt ends.
No network: the local host is exercised in-process, Supabase Storage through a fake transport."""

import asyncio
import json
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from factories import connect_account
from fastapi.testclient import TestClient

from app import assets, store
from app.config import settings
from app.main import app
from app.pipeline import orchestrator, publishing
from app.schemas import CardDeck, JobStatus
from app.services import image_host, publisher

anon = TestClient(app)
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
DECK = json.loads((settings.scenarios_dir / "good/deck.json").read_text())


def _job_with_slides(org_id, n=2, status=JobStatus.READY_FOR_REVIEW):
    job = orchestrator.new_job(secrets.token_hex(3), "private plan")
    job.status, job.deck = status, CardDeck.model_validate({**DECK, "job_id": job.id})
    out = settings.output_dir / job.id
    out.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (out / f"slide-{i:02d}.jpg").write_bytes(JPEG + bytes([i]))
    (out / "photos").mkdir(exist_ok=True)
    (out / "photos/e1-0").write_bytes(b"official photo")
    job.slide_urls = [f"/assets/{job.id}/slide-{i:02d}.jpg" for i in range(n)]
    return store.save_job(org_id, job)


def _path(url: str) -> str:
    u = urlsplit(url)
    return f"{u.path}?{u.query}"


def _with(url: str, **changes) -> str:
    u = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()} | {k: str(v) for k, v in changes.items()}
    return f"{u.path}?" + "&".join(f"{k}={v}" for k, v in q.items())


@pytest.fixture
def two(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    return a, b, _job_with_slides(a.org_id)


# ---------------------------------------------------------------- Admin links

def test_owner_gets_signed_links_that_work(two):
    a, _, job = two
    urls = a.get(f"/jobs/{job.id}").json()["slide_urls"]
    assert len(urls) == 2 and all(u.startswith(f"/assets/{a.org_id}/{job.id}/slide-") for u in urls)
    exp = int(parse_qs(urlsplit(urls[0]).query)["exp"][0])
    assert time.time() + assets.ADMIN_TTL_S <= exp <= time.time() + assets.ADMIN_TTL_S + assets.WINDOW_S + 1
    r = anon.get(urls[1])  # an <img> tag: no Authorization header
    assert r.status_code == 200 and r.content == JPEG + b"\x01" and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"].startswith("private")
    assert a.get("/jobs").json()[0]["slide_urls"] == urls  # stable within a signing window (browser cache)
    assert store.get_job(a.org_id, job.id).slide_urls[0] == f"/assets/{job.id}/slide-00.jpg"  # stored unsigned


def test_links_are_signed_on_every_job_response(two):
    a, _, job = two
    with a.stream("GET", f"/jobs/{job.id}/events") as r:
        snap = json.loads(next(line for line in r.iter_lines() if line.startswith("data:"))[5:])
    assert anon.get(snap["slide_urls"][0]).status_code == 200
    assert anon.get(a.post(f"/jobs/{job.id}/reject", json={}).json()["slide_urls"][0]).status_code == 200


def test_unsigned_paths_serve_nothing(two):
    a, _, job = two
    for path in (f"/assets/{job.id}/slide-00.jpg", f"/assets/{a.org_id}/{job.id}/slide-00.jpg",
                 f"/assets/{a.org_id}/{job.id}/slide-00.jpg?exp=9999999999"):
        assert anon.get(path).status_code == 404, path


def test_another_organization_cannot_fetch_a_slide(two):
    a, b, job = two
    assert b.get(f"/jobs/{job.id}").status_code == 404
    good = a.get(f"/jobs/{job.id}").json()["slide_urls"][0]
    # A's link with B's Organization swapped in: the signature no longer matches.
    assert anon.get(good.replace(str(a.org_id), str(b.org_id))).status_code == 404
    # A link correctly signed for B but pointing at A's Job (e.g. B guessed the id): RLS says it's not B's.
    exp = assets.admin_exp()
    assert anon.get(assets.sign(b.org_id, job.id, "slide-00.jpg", exp)).status_code == 404
    assert anon.get(assets.sign(a.org_id, job.id, "slide-00.jpg", exp)).status_code == 200  # control


@pytest.mark.parametrize("tamper", ["sig", "exp", "name", "use"])
def test_forged_links_are_refused(two, tamper):
    a, _, job = two
    good = a.get(f"/jobs/{job.id}").json()["slide_urls"][0]
    exp = int(parse_qs(urlsplit(good).query)["exp"][0])
    bad = {"sig": _with(good, sig="A" * 43), "exp": _with(good, exp=exp + 3600), "use": _with(good, use="ig"),
           "name": good.replace("slide-00.jpg", "slide-01.jpg")}[tamper]
    assert anon.get(bad).status_code == 404


def test_expired_links_are_refused(two):
    a, _, job = two
    old = assets.sign(a.org_id, job.id, "slide-00.jpg", int(time.time()) - 1)
    assert anon.get(old).status_code == 404
    assert not assets.valid(a.org_id, job.id, "slide-00.jpg", 1000, "x", now=2000)


def test_only_slides_are_served(two):
    a, _, job = two
    exp = assets.admin_exp()
    for name in ("e1-0", "..%2F..%2Fapp.db", "slide-00.png", "deck.json"):
        assert anon.get(assets.sign(a.org_id, job.id, name, exp)).status_code == 404, name
    assert anon.get(assets.sign(a.org_id, "..", "slide-00.jpg", exp)).status_code == 404


# ---------------------------------------------------------------- publishing (IMAGE_HOST=local)

async def test_dryrun_publish_gives_instagram_links_that_expire_and_die_after(sign_in, monkeypatch):
    a = sign_in(org="A")
    job = _job_with_slides(a.org_id, n=3)
    monkeypatch.setattr(settings, "public_asset_base_url", "https://tunnel.example")
    seen: dict = {}
    asgi = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")

    async def instagram(ig, urls, caption, on_progress=lambda *a: None):
        seen["urls"] = urls
        for _ in range(50):  # the publish step has saved PUBLISHING before Instagram is ever called
            if store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHING:
                break
            await asyncio.sleep(0.02)
        seen["during"] = [(await asgi.get(_path(u))).status_code for u in urls]
        return "17890000000000123"
    monkeypatch.setattr(publisher, "build_carousel", instagram)

    post = await publishing.publish_now(a.org_id, job.id, None, "dryrun", account=connect_account(a.org_id))

    assert seen["during"] == [200, 200, 200]
    assert (post.state, post.creation_id, post.attempts) == ("container_ready", "17890000000000123", 0)
    for u in seen["urls"]:
        q = parse_qs(urlsplit(u).query)
        assert u.startswith(f"https://tunnel.example/assets/{a.org_id}/{job.id}/slide-") and q["use"] == ["ig"]
        assert int(q["exp"][0]) <= time.time() + settings.publish_link_ttl_s
    assert store.get_job(a.org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    assert [(await asgi.get(_path(u))).status_code for u in seen["urls"]] == [404] * 3  # publish over: dead
    await asgi.aclose()


async def test_mock_publish_exposes_nothing(client, monkeypatch):
    job = _job_with_slides(client.org_id)
    called = []
    monkeypatch.setattr(image_host, "exposed", lambda *a, **k: called.append(a))
    await publishing.publish_now(client.org_id, job.id, None, "mock")
    assert called == [] and store.get_job(client.org_id, job.id).status == JobStatus.PUBLISHED


# ---------------------------------------------------------------- publishing (IMAGE_HOST=supabase, fake Storage)

class FakeStorage:
    """Enough of the Supabase Storage API: private/public buckets, upload, batch sign, anonymous fetch, delete."""

    def __init__(self, public=False):
        self.bucket = {"id": "slides", "public": public}
        self.objects: dict[str, bytes] = {}
        self.tokens: dict[str, tuple[str, float]] = {}
        self.signed_ttls: list[int] = []
        self.deleted: list[str] = []
        self.fail_upload_at: int | None = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, admin = req.url.path, req.headers.get("authorization") == "Bearer service-key"
        if p == "/storage/v1/bucket/slides" and req.method == "GET" and admin:
            return httpx.Response(200, json=self.bucket)
        if p.startswith("/storage/v1/object/slides/") and req.method == "POST" and admin:
            if self.fail_upload_at is not None and len(self.objects) == self.fail_upload_at:
                return httpx.Response(500, text="boom")
            self.objects[p.removeprefix("/storage/v1/object/slides/")] = req.content
            return httpx.Response(200, json={"Key": p})
        if p == "/storage/v1/object/sign/slides" and admin:
            body = json.loads(req.content)
            self.signed_ttls.append(body["expiresIn"])
            out = []
            for k in body["paths"]:
                t = secrets.token_urlsafe(8)
                self.tokens[t] = (k, time.time() + body["expiresIn"])
                out.append({"path": k, "signedURL": f"/object/sign/slides/{k}?token={t}", "error": None})
            return httpx.Response(200, json=out)
        if p.startswith("/storage/v1/object/sign/slides/") and req.method == "GET":
            key, until = self.tokens.get(req.url.params.get("token", ""), ("", 0))
            if key == p.removeprefix("/storage/v1/object/sign/slides/") and time.time() < until \
                    and key in self.objects:
                return httpx.Response(200, content=self.objects[key], headers={"content-type": "image/jpeg"})
            return httpx.Response(400, json={"error": "InvalidSignature"})
        if p.startswith("/storage/v1/object/public/slides/"):
            key = p.removeprefix("/storage/v1/object/public/slides/")
            if self.bucket["public"] and key in self.objects:
                return httpx.Response(200, content=self.objects[key], headers={"content-type": "image/jpeg"})
            return httpx.Response(400, json={"error": "not found"})
        if p == "/storage/v1/object/slides" and req.method == "DELETE" and admin:
            for k in json.loads(req.content)["prefixes"]:
                self.objects.pop(k, None)
                self.deleted.append(k)
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"error": "unauthorized"})


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(image_host, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(settings, "image_host", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://proj.supabase.test")
    monkeypatch.setattr(settings, "supabase_service_key", "service-key")
    return fake


async def test_supabase_publish_uses_org_prefixed_private_objects_and_cleans_up(storage):
    org, other = uuid.uuid4(), uuid.uuid4()
    jid = secrets.token_hex(3)
    (settings.output_dir / jid).mkdir(parents=True)
    for i in range(2):
        (settings.output_dir / jid / f"slide-{i:02d}.jpg").write_bytes(JPEG)
    paths = sorted((settings.output_dir / jid).glob("slide-*.jpg"))
    async with image_host.exposed(org, jid, paths) as urls:
        assert sorted(storage.objects) == [f"{org}/{jid}/slide-00.jpg", f"{org}/{jid}/slide-01.jpg"]
        assert storage.signed_ttls == [settings.publish_link_ttl_s]
        async with httpx.AsyncClient(transport=image_host._transport) as instagram:  # anonymous
            assert [(await instagram.get(u)).status_code for u in urls] == [200, 200]
            assert (await instagram.get(f"https://proj.supabase.test/storage/v1/object/public/slides/"
                                        f"{org}/{jid}/slide-00.jpg")).status_code == 400
            # Another Organization's path with this token: refused.
            assert (await instagram.get(urls[0].replace(str(org), str(other)))).status_code == 400
    assert storage.objects == {} and len(storage.deleted) == 2
    async with httpx.AsyncClient(transport=image_host._transport) as instagram:
        assert (await instagram.get(urls[0])).status_code == 400  # after publish: gone


async def test_supabase_refuses_a_public_bucket(storage):
    storage.bucket["public"] = True
    jid = secrets.token_hex(3)
    (settings.output_dir / jid).mkdir(parents=True)
    (settings.output_dir / jid / "slide-00.jpg").write_bytes(JPEG)
    with pytest.raises(image_host.ImageHostError, match="public"):
        async with image_host.exposed(uuid.uuid4(), jid, [settings.output_dir / jid / "slide-00.jpg"]):
            pass
    assert storage.objects == {}


async def test_supabase_deletes_partial_uploads_when_publishing_fails(storage):
    storage.fail_upload_at = 1
    jid = secrets.token_hex(3)
    (settings.output_dir / jid).mkdir(parents=True)
    for i in range(3):
        (settings.output_dir / jid / f"slide-{i:02d}.jpg").write_bytes(JPEG)
    with pytest.raises(image_host.ImageHostError, match="upload"):
        async with image_host.exposed(uuid.uuid4(), jid, sorted((settings.output_dir / jid).glob("*.jpg"))):
            pass
    assert storage.objects == {} and len(storage.deleted) == 1


async def test_supabase_failure_message_never_carries_the_token(storage, monkeypatch):
    jid = secrets.token_hex(3)
    (settings.output_dir / jid).mkdir(parents=True)
    (settings.output_dir / jid / "slide-00.jpg").write_bytes(JPEG)
    monkeypatch.setattr(storage, "tokens", {})  # every signed fetch fails
    real = storage.handler

    def no_tokens(req):
        if req.url.path.startswith("/storage/v1/object/sign/slides/") and req.method == "GET":
            return httpx.Response(400)
        return real(req)
    monkeypatch.setattr(image_host, "_transport", httpx.MockTransport(no_tokens))
    with pytest.raises(image_host.ImageHostError) as e:
        async with image_host.exposed(uuid.uuid4(), jid, [settings.output_dir / jid / "slide-00.jpg"]):
            pass
    assert "token=" not in str(e.value) and "not reachable" in str(e.value)
