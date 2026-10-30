"""Source material (#20, ADR 0003): attaching links, files and notes; limits and type checks; Organization isolation of
the rows, the files and what an agent sandbox receives; links fetched only through safe_fetch; facts recording where
they came from; material-backed facts counting as verified; contradictions raising an Issue; older Jobs still loading.
No network and no real agents: DNS is a dict, HTTP a MockTransport, agents a fake runner."""

import io
import json
import uuid
from datetime import UTC, date, datetime

import httpx
import pytest
from PIL import Image

from app import brainstorms, materials, store
from app.config import settings
from app.pipeline import agent_runner, orchestrator
from app.schemas import (
    Brainstorm,
    BrainstormUpdate,
    EventBrief,
    Fact,
    Job,
    LinkCheck,
    Material,
    MaterialRef,
)
from app.services import link_checker, safe_fetch

# ---------------------------------------------------------------- helpers


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_image(fmt="JPEG", size=(2400, 1200), exif_gps=False) -> bytes:
    im = Image.new("RGB", size, (200, 30, 40))
    out = io.BytesIO()
    kw = {}
    if exif_gps:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0)}  # GPSInfo
        exif[0x010F] = "SecretCam"                       # Make
        kw["exif"] = exif
    im.save(out, fmt, **kw)
    return out.getvalue()


def upload(c, name: str, data: bytes, ctype="application/octet-stream"):
    return c.post("/materials/files", files={"file": (name, data, ctype)})


def note(c, text="Lantern Walk 2026 runs Nov 7–9 at Cheonggyecheon. Entry ₩0 for everyone."):
    r = c.post("/materials", json={"kind": "note", "text": text})
    assert r.status_code == 201, r.text
    return r.json()


DNS = {"public.example": ["93.184.215.14"], "internal.example": ["10.0.0.5"]}


@pytest.fixture
def fake_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in DNS:
            raise OSError("NXDOMAIN")
        return DNS[host]
    monkeypatch.setattr(safe_fetch, "resolve", resolve)


class Web:
    def __init__(self, routes: dict[str, tuple[int, dict, bytes]]):
        self.routes, self.seen = routes, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(str(req.url))
        status, headers, body = self.routes.get(str(req.url), (404, {}, b""))
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))

    def client(self, **_kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=False)


# ---------------------------------------------------------------- attaching: types, limits, names

def test_note_is_stored_with_its_text_and_audited(client):
    ref = note(client)
    assert ref["kind"] == "note" and ref["id"].startswith("m-") and ref["chars"] > 0 and "text" not in ref
    full = client.get(f"/materials/{ref['id']}").json()
    assert "Cheonggyecheon" in full["text"] and full["created_by"] == str(client.user_id)
    actions = [e["action"] for e in client.get("/audit").json()]
    assert "material.added" in actions


def test_note_limits(client):
    assert client.post("/materials", json={"kind": "note", "text": "   "}).status_code == 422
    long = "x" * (materials.MAX_NOTE_CHARS + 1)
    r = client.post("/materials", json={"kind": "note", "text": long})
    assert r.status_code == 422 and "longer than" in r.json()["detail"]


def test_pdf_text_is_extracted_and_capped_by_pages(client):
    pages = [f"Page {i} Lantern Walk" for i in range(materials.MAX_PDF_PAGES + 5)]
    r = upload(client, "press release.pdf", make_pdf(pages), "application/pdf")
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["content_type"] == "application/pdf" and ref["pages"] == len(pages) and ref["truncated"]
    text = client.get(f"/materials/{ref['id']}").json()["text"]
    assert "[page 1] Page 0 Lantern Walk" in text and f"Page {materials.MAX_PDF_PAGES} " not in text


def test_pdf_text_is_capped_by_characters(client, monkeypatch):
    monkeypatch.setattr(materials, "MAX_TEXT_CHARS", 50)
    r = upload(client, "a.pdf", make_pdf(["A" * 40, "B" * 40, "C" * 40]))
    assert r.status_code == 201 and r.json()["truncated"] and r.json()["chars"] <= 50


def test_bad_pdfs_are_refused(client):
    assert "no text" in upload(client, "scan.pdf", make_pdf([""])).json()["detail"]
    r = upload(client, "broken.pdf", b"%PDF-1.4\n garbage without objects")
    assert r.status_code == 422


def test_pdf_parsing_is_time_limited(client, monkeypatch, tmp_path):
    """A PDF that makes the parser hang is killed after PDF_TIMEOUT_S (the worker runs in its own process)."""
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(materials, "_PDF_WORKER", slow)
    monkeypatch.setattr(materials, "PDF_TIMEOUT_S", 0.5)
    r = upload(client, "loop.pdf", make_pdf(["x"]))
    assert r.status_code == 422 and "longer than" in r.json()["detail"]


def test_type_is_decided_by_content_not_name(client):
    assert upload(client, "tool.exe", b"MZ\x90\x00binary").status_code == 422
    assert upload(client, "fake.pdf", b"not a pdf at all").status_code == 422     # a PDF name, no PDF inside
    assert upload(client, "fake.jpg", b"<svg onload=alert(1)>").status_code == 422  # SVG is never accepted
    assert upload(client, "notes.txt", b"bin\x00ary").status_code == 422
    r = upload(client, "poster.txt", make_image())  # an image named .txt is handled as an image
    assert r.status_code == 201 and r.json()["content_type"] == "image/jpeg"


def test_size_limits(client, monkeypatch):
    assert upload(client, "empty.pdf", b"").status_code == 422
    monkeypatch.setattr(materials, "MAX_FILE_BYTES", 1000)
    r = upload(client, "big.pdf", make_pdf(["x" * 2000]))
    assert r.status_code in (413, 422) and "larger than" in r.json()["detail"]
    monkeypatch.setattr(materials, "MAX_FILE_BYTES", 10_000_000)
    monkeypatch.setattr(materials, "MAX_TEXT_FILE_BYTES", 10)
    assert upload(client, "a.txt", b"0123456789ABC").status_code == 422


def test_text_files_decode_utf8_and_cp949(client):
    r = upload(client, "공지.txt", "휴관 안내: 11월 3일".encode("cp949"))
    assert r.status_code == 201 and r.json()["name"] == "공지.txt"
    assert "휴관 안내" in client.get(f"/materials/{r.json()['id']}").json()["text"]


def test_images_are_reencoded_without_exif_and_pixel_capped(client, monkeypatch):
    r = upload(client, "poster.png", make_image("JPEG", (3000, 1500), exif_gps=True))
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["image"] and ref["content_type"] == "image/jpeg"
    stored = settings.materials_root / str(client.org_id) / ref["id"] / "image.jpg"
    with Image.open(stored) as im:
        assert max(im.size) == materials.IMAGE_MAX_SIDE and not im.getexif()
    assert b"SecretCam" not in stored.read_bytes()
    monkeypatch.setattr(materials, "MAX_IMAGE_PIXELS", 1000)
    assert "too large" in upload(client, "huge.png", make_image("PNG", (100, 100))).json()["detail"]
    assert upload(client, "a.gif", b"GIF89a....").status_code == 422


def test_file_names_are_cleaned_and_never_used_as_paths(client):
    r = upload(client, "../../etc/" + chr(0x202E) + "passwd.txt", b"hello")
    assert r.status_code == 201
    ref = r.json()
    assert ref["name"] == "passwd.txt"
    assert (settings.materials_root / str(client.org_id) / ref["id"] / "original.txt").read_bytes() == b"hello"
    assert materials.clean_name("..\\..\\x.pdf") == "x.pdf" and materials.clean_name("") == "file"


def test_too_many_pieces_for_one_job(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "start_job", _no_run)
    ids = [note(client, f"note {i}")["id"] for i in range(11)]
    assert client.post("/jobs", json={"prompt": "Lanterns", "material_ids": ids}).status_code == 422


# ---------------------------------------------------------------- links: only through safe_fetch

def test_link_is_fetched_through_safe_fetch(client, monkeypatch, fake_dns):
    web = Web({"https://public.example/notice": (200, {"content-type": "text/html; charset=utf-8"},
                                                 b"<html><script>evil()</script><p>Closed Nov 3&amp;4</p></html>")})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    calls = []
    real = safe_fetch.fetch

    async def spy(*a, **kw):
        calls.append(a[2])
        return await real(*a, **kw)
    monkeypatch.setattr(safe_fetch, "fetch", spy)
    r = client.post("/materials", json={"kind": "link", "url": "https://public.example/notice"})
    assert r.status_code == 201, r.text
    assert calls == ["https://public.example/notice"] and web.seen == ["https://public.example/notice"]
    text = client.get(f"/materials/{r.json()['id']}").json()["text"]
    assert "Closed Nov 3&4" in text and "evil" not in text
    assert r.json()["url"] == "https://public.example/notice"


def test_link_to_an_internal_address_is_refused_without_a_request(client, monkeypatch, fake_dns):
    web = Web({})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    for url in ("https://internal.example/admin", "http://127.0.0.1/", "file:///etc/passwd",
                "http://169.254.169.254/latest/meta-data"):
        r = client.post("/materials", json={"kind": "link", "url": url})
        assert r.status_code == 422 and "can't be used" in r.json()["detail"], url
    assert web.seen == []


def test_linked_pdf_and_bad_links(client, monkeypatch, fake_dns):
    web = Web({"https://public.example/a.pdf": (200, {"content-type": "application/pdf"}, make_pdf(["Hours 10-18"])),
               "https://public.example/gone": (404, {}, b""),
               "https://public.example/zip": (200, {"content-type": "application/zip"}, b"PK..")})
    monkeypatch.setattr(safe_fetch, "client", web.client)
    r = client.post("/materials", json={"kind": "link", "url": "https://public.example/a.pdf"})
    assert r.status_code == 201 and r.json()["pages"] == 1
    assert "HTTP 404" in client.post("/materials", json={"kind": "link", "url": "https://public.example/gone"}
                                     ).json()["detail"]
    assert client.post("/materials", json={"kind": "link", "url": "https://public.example/zip"}).status_code == 422


# ---------------------------------------------------------------- Organization isolation

async def _no_run(*a, **kw):
    return None


def test_another_organizations_material_is_invisible(sign_in, monkeypatch):
    monkeypatch.setattr(orchestrator, "start_job", _no_run)
    a, b = sign_in(org="A"), sign_in(org="B")
    mine = note(a, "A-ONLY secret press release")["id"]
    assert b.get(f"/materials/{mine}").status_code == 404
    assert store.get_materials(b.org_id, [mine]) == []
    r = b.post("/jobs", json={"prompt": "Lanterns", "material_ids": [mine]})
    assert r.status_code == 422 and r.json()["detail"] == "unknown Source material"
    board = b.post("/brainstorms", json={"sample": False}).json()["id"]
    assert b.post(f"/brainstorms/{board}/messages", json={"text": "hi", "material_ids": [mine]}).status_code == 422
    assert a.post("/jobs", json={"prompt": "Lanterns", "material_ids": [mine]}).status_code == 201


def test_files_are_stored_under_their_organization(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    ra = upload(a, "a.txt", b"A file").json()
    rb = upload(b, "b.txt", b"B file").json()
    assert (settings.materials_root / str(a.org_id) / ra["id"] / "original.txt").exists()
    assert not (settings.materials_root / str(b.org_id) / ra["id"]).exists()
    assert (settings.materials_root / str(b.org_id) / rb["id"] / "original.txt").exists()


async def test_agent_payload_holds_only_the_jobs_own_material(sign_in, tmp_path):
    """`for_agent` loads under the Job's Organization: another Organization's id yields nothing, even if a payload
    were built with it; images are copied into the Job's own directory."""
    a, b = sign_in(org="A"), sign_in(org="B")
    secret = note(a, "A-ONLY secret")
    img = upload(b, "p.jpg", make_image()).json()
    refs = [MaterialRef.model_validate(secret), MaterialRef.model_validate(img)]
    items, files = await materials.for_agent(b.org_id, refs, tmp_path / "job-b")
    assert [i["id"] for i in items] == [img["id"]] and "A-ONLY" not in json.dumps(items)
    assert files == [tmp_path / "job-b" / f"{img['id']}.jpg"] and items[0]["image_path"] == str(files[0])


async def test_agent_payload_is_capped(org, monkeypatch, tmp_path):
    monkeypatch.setattr(materials, "MAX_AGENT_CHARS", 30)
    ms = [store.save_material(org, materials.from_note("x" * 25)) for _ in range(3)]
    items, _ = await materials.for_agent(org, [MaterialRef.model_validate(m.model_dump()) for m in ms], tmp_path)
    assert sum(len(i["text"]) for i in items) == 30 and items[1]["truncated"] and items[2]["text"] == ""


def test_material_rows_are_append_only_and_rls_scoped(org):
    m = store.save_material(org, materials.from_note("hello"))
    other = uuid.uuid4()
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.tenant(org) as c:
        c.execute("update materials set doc = doc where id = %s", (m.id,))
    with store.tenant(other) as c:
        assert c.execute("select count(*) from materials").fetchone()[0] == 0


class FakeStorage:
    def __init__(self, public=False):
        self.public, self.objects = public, {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, admin = req.url.path, req.headers.get("authorization") == "Bearer service-key"
        if not admin:
            return httpx.Response(403)
        if p == "/storage/v1/bucket/materials":
            return httpx.Response(200, json={"id": "materials", "public": self.public})
        if p.startswith("/storage/v1/object/materials/") and req.method == "POST":
            self.objects[p.removeprefix("/storage/v1/object/materials/")] = req.content
            return httpx.Response(200, json={})
        if p.startswith("/storage/v1/object/authenticated/materials/"):
            key = p.removeprefix("/storage/v1/object/authenticated/materials/")
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        return httpx.Response(400)


async def test_supabase_storage_keys_are_per_organization_and_bucket_must_be_private(org, monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(materials, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(settings, "material_store", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://proj.supabase.test")
    monkeypatch.setattr(settings, "supabase_service_key", "service-key")
    m = await materials.from_file(org, "p.png", make_image("PNG"))
    assert list(fake.objects) == [f"{org}/{m.id}/image.jpg"]
    assert await materials.get_file(org, m.id, "image.jpg") == fake.objects[f"{org}/{m.id}/image.jpg"]
    with pytest.raises(FileNotFoundError):
        await materials.get_file(uuid.uuid4(), m.id, "image.jpg")  # another Organization's prefix has nothing
    fake.public = True
    with pytest.raises(RuntimeError, match="public"):
        await materials.put_file(org, materials.new_id(), "image.jpg", b"x", "image/jpeg")
    with pytest.raises(ValueError):
        materials._key(org, "../other-org", "image.jpg")
    with pytest.raises(ValueError):
        materials._key(org, materials.new_id(), "../../x")


# ---------------------------------------------------------------- research and verify (ADR 0003)

class FakeRunner:
    def __init__(self, outputs: dict):
        self.outputs, self.calls = outputs, []

    async def run(self, agent, task, payload, schema, files, job_id, org_id):
        self.calls.append({"agent": agent, "task": task, "payload": payload, "files": files, "org": org_id})
        out = self.outputs[agent]
        if agent == "researcher" and isinstance(out, list):  # events: the researcher's Research shape (#27)
            return {"kind": "event", "events": out}
        return {"items": out} if isinstance(out, list) else out


def _brief(**kw) -> dict:
    return {"id": "lantern", "title_en": "Lantern Walk", "title_ko": "등불 산책", "category": "festival",
            "start_date": "2026-11-07", "end_date": "2026-11-09", "venue_en": "Cheonggyecheon", **kw}


async def _job_with(org, refs) -> Job:
    job = orchestrator.new_job(uuid.uuid4().hex[:16], "Lantern Walk this weekend")
    job.material = refs
    return await __import__("asyncio").to_thread(store.save_job, org, job)


def _ok_links(monkeypatch, ok=("https://public.example/event",)):
    async def check(url, client=None):
        return LinkCheck(url=url, status="ok" if url in ok else "dead", http_code=200 if url in ok else 404)
    monkeypatch.setattr(link_checker, "check_url", check)


async def _research_and_verify(org, job, runner, monkeypatch):
    monkeypatch.setattr(agent_runner, "get_runner", lambda: runner)
    monkeypatch.setattr(settings, "demo_mode", "live")
    out = await orchestrator._run_stage_step(str(org), job.id, "research", None, "good", {})
    assert out["ok"], store.get_job(org, job.id).error
    out = await orchestrator._run_stage_step(str(org), job.id, "verify", None, "good", {})
    return store.get_job(org, job.id), out


async def test_material_is_the_researchers_primary_input_and_facts_keep_their_origin(org, monkeypatch):
    m = store.save_material(org, materials.from_note("Lantern Walk 2026: Nov 7-9, Cheonggyecheon. Free entry."))
    img = store.save_material(org, Material(id=materials.new_id(), kind="file", name="poster.jpg", image=True,
                                            content_type="image/jpeg", created_at=datetime.now(UTC)))
    await materials.put_file(org, img.id, "image.jpg", make_image(), "image/jpeg")
    refs = [MaterialRef.model_validate(x.model_dump()) for x in (m, img)]
    job = await _job_with(org, refs)
    brief = _brief(materials=[m.id], sources=[{"url": "https://public.example/event", "kind": "official"}],
                   price="Free", nearest_station="Jonggak Stn. Exit 5",
                   facts=[{"field": "dates", "value": "Nov 7–9", "origin": "material", "material_id": m.id,
                           "quote": "Nov 7-9"},
                          {"field": "price", "value": "Free", "origin": "material", "material_id": m.id,
                           "quote": "Free entry"},
                          {"field": "station", "value": "Jonggak Stn. Exit 5", "origin": "link",
                           "url": "https://public.example/event"}],
                   conflicts=[{"field": "price", "material_id": m.id, "material_says": "Free",
                               "url": "https://public.example/event", "public_says": "₩5,000"}])
    runner = FakeRunner({"researcher": [brief]})
    _ok_links(monkeypatch)
    job, out = await _research_and_verify(org, job, runner, monkeypatch)

    [call] = runner.calls
    assert call["agent"] == "researcher" and "primary source" in call["task"]
    sent = {x["id"]: x for x in call["payload"]["material"]}
    assert "Free entry" in sent[m.id]["text"] and sent[img.id]["image_path"].endswith(f"{img.id}.jpg")
    assert [p.name for p in call["files"]] == [f"{img.id}.jpg"]
    assert all("url" not in x or x["url"] is None for x in sent.values())  # nothing for the agent to fetch itself

    [b] = job.briefs
    origins = {f.field: (f.origin, f.verified) for f in b.facts}
    assert origins == {"dates": ("material", True), "price": ("material", True), "station": ("link", True)}
    issues = out["carry"]["host_issues"]
    [conflict] = [i for i in issues if "₩5,000" in i["message"]]
    assert conflict["severity"] == "warn" and conflict["category"] == "fact"
    assert "Free" in conflict["message"] and "public.example" in conflict["message"]
    assert job.verification.excluded_event_ids == []


async def test_event_only_in_material_counts_as_verified(org, monkeypatch):
    m = store.save_material(org, materials.from_note("Members-only exhibition, Nov 10, Room 3. Not online yet."))
    job = await _job_with(org, [MaterialRef.model_validate(m.model_dump())])
    brief = _brief(materials=[m.id], sources=[],
                   facts=[{"field": "dates", "value": "Nov 10", "origin": "material", "material_id": m.id,
                           "quote": "Nov 10"}])
    _ok_links(monkeypatch)
    job, out = await _research_and_verify(org, job, FakeRunner({"researcher": [brief]}), monkeypatch)
    assert out["ok"] and job.verification.excluded_event_ids == [] and job.verification.checks == []
    assert job.briefs[0].facts[0].verified is True and not out["carry"]["host_issues"]


async def test_material_claims_are_checked_not_trusted(sign_in, monkeypatch):
    """A fact said to come from material must cite this Job's own material and, for text, really be in it; an event
    that cites only another Organization's material id is not kept on that basis."""
    a, b = sign_in(org="A"), sign_in(org="B")
    theirs = store.save_material(a.org_id, materials.from_note("A-ONLY"))
    mine = store.save_material(b.org_id, materials.from_note("Opening hours 10:00-18:00."))
    job = await _job_with(b.org_id, [MaterialRef.model_validate(mine.model_dump())])
    briefs = [
        _brief(id="real", materials=[mine.id], facts=[
            {"field": "price", "value": "Free", "origin": "material", "material_id": mine.id, "quote": "Free entry"},
            {"field": "time", "value": "10–18", "origin": "material", "material_id": mine.id,
             "quote": "Opening hours 10:00-18:00"}]),
        _brief(id="stolen", materials=[theirs.id], sources=[{"url": "https://dead.example/", "kind": "other"}]),
    ]
    _ok_links(monkeypatch, ok=())
    job, out = await _research_and_verify(b.org_id, job, FakeRunner({"researcher": briefs}), monkeypatch)
    assert job.verification.excluded_event_ids == ["stolen"]
    facts = {f.field: f for f in job.briefs[0].facts}
    assert facts["time"].verified and not facts["price"].verified and "not in the material" in facts["price"].note
    [issue] = out["carry"]["host_issues"]
    assert issue["severity"] == "warn" and "price" in issue["message"]
    assert any("not attached to this request" in line.message for line in job.log)


def test_check_facts_link_origins_and_excluded_events():
    m = Material(id="m-" + "1" * 16, kind="note", name="n", text="Free", created_at=datetime.now(UTC))
    b = EventBrief.model_validate(_brief(
        sources=[{"url": "https://public.example/event", "kind": "official"}],
        facts=[{"field": "venue", "value": "x", "origin": "link", "url": "https://elsewhere.example/"},
               {"field": "dates", "value": "x", "origin": "link", "url": "https://public.example/event"}],
        conflicts=[{"field": "price", "material_id": m.id, "material_says": "Free", "url": "https://public.example/x",
                    "public_says": "₩1"}]))
    issues, lines = materials.check_facts([b], {m.id: m}, set(), included=set())
    assert issues == []  # not on the Deck: logged, not raised
    assert "not on the Deck" in lines[0]
    assert [f.note for f in b.facts] == ["not one of the event's sources", "the link did not pass the check"]
    issues, _ = materials.check_facts([b], {m.id: m}, set(), included={b.id})
    assert len(issues) == 1 and "did not pass the link check" in issues[0].message


# ---------------------------------------------------------------- Brainstorm

def test_brainstorm_material_reaches_the_planner_and_the_job(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "start_job", _no_run)
    ref = note(client, "Night market Oct 17-19 at Mangwon Hangang Park, free entry.")
    board = client.post("/brainstorms", json={"sample": False}).json()["id"]
    runner = FakeRunner({"planner": BrainstormUpdate(reply="ok").model_dump()})
    monkeypatch.setattr(agent_runner, "get_runner", lambda: runner)
    monkeypatch.setattr(settings, "demo_mode", "live")
    r = client.post(f"/brainstorms/{board}/messages", json={"text": "use this", "material_ids": [ref["id"]]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [m["id"] for m in d["material"]] == [ref["id"]] and d["messages"][0]["material_ids"] == [ref["id"]]
    assert "Mangwon Hangang Park" in json.dumps(runner.calls[0]["payload"]["material"], ensure_ascii=False)
    client.patch(f"/brainstorms/{board}", json={"angles": [{"id": "a", "title": "t", "hook": "h"}],
                                                 "selected_angle_id": "a"})
    job = client.post(f"/brainstorms/{board}/generate").json()
    assert [m["id"] for m in job["material"]] == [ref["id"]]


def test_board_facts_from_material_need_no_link_and_stay_verified():
    mid = "m-" + "a" * 16
    d = Brainstorm(id="d-1", facts=[
        Fact(key="event", value="Night Market (야시장)", verified=True, material_id=mid),
        Fact(key="dates", value="Oct 17–19", verified=True, material_id=mid),
        Fact(key="price", value="Free", verified=True, source_url="https://public.example/p"),
        Fact(key="venue", value="Somewhere", verified=True, material_id="m-" + "b" * 16),  # not this Job's
    ])
    brief, problems = brainstorms.brief_from_brainstorm(d, date(2026, 10, 1), {mid})
    assert brief.materials == [mid] and [str(s.url) for s in brief.sources] == ["https://public.example/p"]
    assert [(f.field, f.origin) for f in brief.facts] == [("title", "material"), ("dates", "material"),
                                                          ("price", "link")]
    assert problems == []
    board, demoted = brainstorms.demote_unverified(d, set(), {mid})
    assert [f.verified for f in board.facts] == [True, True, False, False] and demoted == 2
    none, problems = brainstorms.brief_from_brainstorm(d, date(2026, 10, 1), set())
    assert none is not None and none.materials == []  # without the material, only the link fact remains


# ---------------------------------------------------------------- stored documents from before #20

def test_old_jobs_and_briefs_still_load(org):
    old = {"id": "oldjob1", "prompt": "Seongsu", "status": "READY_FOR_REVIEW", "created_at": "2026-10-20T00:00:00Z",
           "draft_id": "d-1",
           "briefs": [{"id": "e1", "title_en": "Pop-up", "title_ko": "팝업", "start_date": "2026-10-20",
                       "end_date": "2026-10-21", "venue_en": "Seongsu",
                       "sources": [{"url": "https://public.example/e", "kind": "official"}]}]}
    with store.tenant(org) as c:
        c.execute("insert into jobs (id, created_at, doc) values (%s, now(), %s)", ("oldjob1", json.dumps(old)))
    job = store.get_job(org, "oldjob1")
    assert job and job.material == [] and job.briefs[0].facts == [] and job.briefs[0].materials == []
    assert job.brainstorm_id == "d-1"
    b = Brainstorm.model_validate({"id": "d-x", "facts": [{"key": "event", "value": "x", "verified": True}],
                                   "messages": [{"role": "user", "text": "hi"}]})
    assert b.material == [] and b.facts[0].material_id is None


def test_an_event_needs_a_link_or_material():
    with pytest.raises(ValueError, match="at least one source"):
        EventBrief.model_validate(_brief(sources=[]))
