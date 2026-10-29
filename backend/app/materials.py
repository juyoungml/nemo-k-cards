"""Source material (#20; GLOSSARY "Source material", ADR 0003): links, files (PDF, images, text) and notes an
Organization attaches when it starts a Job or in a Brainstorm. The agents treat it as the primary source; facts
taken from it count as verified; research only fills the gaps; a contradiction with a checked public page becomes an
Issue for a person to decide.

Trust boundary. Everything is vetted on the host when it is attached, once:
  - links are fetched only through `safe_fetch` (after the same suspicious-URL check as event sources);
  - files are typed by their bytes, not their name or the browser's content type, and size-capped;
  - PDFs are parsed in a separate, time- and CPU-limited process (app/services/pdf_text.py, pypdf);
  - images are decoded with a pixel cap and re-encoded as a plain JPEG (EXIF, GPS and any trailing payload gone);
  - extracted text is capped per piece (MAX_TEXT_CHARS) and per agent run (MAX_AGENT_CHARS).
An agent sandbox gets only that extracted text and the re-encoded images of the one Job's or Brainstorm's own
material, loaded under its Organization (RLS): never a URL to fetch itself, never another Organization's material.

Storage. The row (`materials`, RLS like `jobs`) holds the metadata and the capped text. The file is private to the
Organization: `MATERIAL_DIR/<org_id>/<material_id>/<file>` on this host, or the private Storage bucket
`materials` under the same key (MATERIAL_STORE=supabase). Stored file names are fixed (`original.pdf`,
`image.jpg`, `original.txt`); the uploader's file name is only a cleaned display name."""

import asyncio
import html
import io
import logging
import re
import secrets
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app import audit, auth, store
from app.config import settings
from app.schemas import MAX_MATERIALS, EventBrief, Issue, Material, MaterialRef
from app.services import link_checker, safe_fetch

log = logging.getLogger("materials")
router = APIRouter()

MAX_FILE_BYTES = 10_000_000       # any uploaded file
MAX_TEXT_FILE_BYTES = 1_000_000   # .txt / .md / .csv
MAX_LINK_BYTES = 5_000_000        # a linked page or PDF
MAX_NOTE_CHARS = 20_000
MAX_TEXT_CHARS = 20_000           # extracted text kept per piece (what the agents can read)
MAX_PDF_PAGES = 40
PDF_TIMEOUT_S = 20
MAX_IMAGE_PIXELS = 40_000_000     # decoded size; bigger images are refused before decoding
IMAGE_MAX_SIDE = 1600             # what the agents get
MAX_AGENT_CHARS = 60_000          # all material text in one agent run
MAX_AGENT_IMAGES = 5
ID = re.compile(r"m-[0-9a-f]{16}")
FILE_NAMES = {"original.pdf", "image.jpg", "original.txt"}
TEXT_EXT = {".txt", ".md", ".csv"}
_PDF_WORKER = Path(__file__).resolve().parent / "services" / "pdf_text.py"


class MaterialError(ValueError):
    """The material can't be used; the message says why, for the person attaching it (HTTP 422)."""


def new_id() -> str:
    return f"m-{secrets.token_hex(8)}"


# ---------------------------------------------------------------- vetting

def clean_name(name: str | None, default: str = "file") -> str:
    """A display name: the last path segment, no control or bidi-override characters, at most 120 characters."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFC", base) if unicodedata.category(ch)[0] != "C")
    base = re.sub(r"\s+", " ", base).strip().lstrip(".")
    return base[:120] or default


def sniff(data: bytes, filename: str) -> Literal["pdf", "jpeg", "png", "webp", "text"] | None:
    """The file's real type from its first bytes (text only by extension, and only if it decodes)."""
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if Path(filename.lower()).suffix in TEXT_EXT:
        return "text"
    return None


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), else CP949 (Korean Windows); binary is refused."""
    if b"\x00" in data:
        raise MaterialError("this text file contains binary data")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise MaterialError("this text file is not UTF-8 or CP949 text")


def html_text(page: str) -> str:
    page = re.sub(r"<(script|style|noscript|template)[^>]*>.*?</\1>", " ", page, flags=re.DOTALL | re.IGNORECASE)
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.DOTALL)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def cap(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return (text[:limit], True) if len(text) > limit else (text, False)


async def pdf_text(data: bytes) -> dict:
    """{"pages", "text", "truncated"} from a PDF, parsed in a child process that is killed after PDF_TIMEOUT_S."""
    import json
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(_PDF_WORKER), str(MAX_PDF_PAGES), str(MAX_TEXT_CHARS),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(data), timeout=PDF_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MaterialError(f"reading this PDF took longer than {PDF_TIMEOUT_S}s") from None
    try:
        res = json.loads(out)
    except ValueError:
        raise MaterialError("this PDF could not be read") from None
    if res.get("error"):
        raise MaterialError(res["error"])
    if not res.get("text", "").strip():
        raise MaterialError("no text found in this PDF (a scan?): attach the pages as images or paste the text "
                            "as a note")
    return res


def reencode_image(data: bytes) -> tuple[bytes, tuple[int, int]]:
    """Decode with a pixel cap and save as a plain JPEG at most IMAGE_MAX_SIDE wide: what the agents see. Drops
    EXIF (GPS), colour profiles and anything appended to the file."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP"):
                raise MaterialError(f"{im.format} images are not supported: use JPEG, PNG or WebP")
            w, h = im.size
            if w * h > MAX_IMAGE_PIXELS:
                raise MaterialError(f"the image is too large ({w}×{h}); at most {MAX_IMAGE_PIXELS // 1_000_000} MP")
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
            out = io.BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue(), im.size
    except MaterialError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise MaterialError(f"the image could not be read ({type(e).__name__})") from None


# ---------------------------------------------------------------- private storage

def _key(org_id: UUID, material_id: str, name: str) -> str:
    if not ID.fullmatch(material_id) or name not in FILE_NAMES:
        raise ValueError("bad material key")
    return f"{org_id}/{material_id}/{name}"


_transport: httpx.AsyncBaseTransport | None = None  # tests swap in a fake Storage


def _storage() -> httpx.AsyncClient:
    if not (settings.supabase_url and settings.supabase_service_key):
        raise MaterialError("file storage is not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY)")
    key = settings.supabase_service_key
    return httpx.AsyncClient(transport=_transport, base_url=settings.supabase_url.rstrip("/"), timeout=30,
                             follow_redirects=False, headers={"Authorization": f"Bearer {key}", "apikey": key})


async def _ensure_private_bucket(c: httpx.AsyncClient) -> None:
    b = settings.material_bucket
    r = await c.get(f"/storage/v1/bucket/{b}")
    if r.status_code == 200:
        if r.json().get("public"):
            raise RuntimeError(f"Storage bucket {b!r} is public; Source material must be private. Apply "
                               "supabase/migrations/*_source_material.sql or switch it to private.")
        return
    r = await c.post("/storage/v1/bucket", json={"id": b, "name": b, "public": False})
    if r.status_code not in (200, 201) and "already exists" not in r.text:
        raise RuntimeError(f"could not create bucket {b!r}: {r.status_code}")


async def put_file(org_id: UUID, material_id: str, name: str, data: bytes, content_type: str) -> None:
    key = _key(org_id, material_id, name)
    if settings.material_store == "supabase":
        async with _storage() as c:
            await _ensure_private_bucket(c)
            r = await c.post(f"/storage/v1/object/{settings.material_bucket}/{key}", content=data,
                             headers={"Content-Type": content_type})
            if r.status_code not in (200, 201):
                raise RuntimeError(f"storing material failed: {r.status_code}")
        return
    path = settings.materials_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)


async def get_file(org_id: UUID, material_id: str, name: str) -> bytes:
    """A stored file of this Organization's material. The caller must have loaded the material under `org_id`."""
    key = _key(org_id, material_id, name)
    if settings.material_store == "supabase":
        async with _storage() as c:
            r = await c.get(f"/storage/v1/object/authenticated/{settings.material_bucket}/{key}")
            if r.status_code != 200:
                raise FileNotFoundError(key)
            return r.content
    return await asyncio.to_thread((settings.materials_root / key).read_bytes)


# ---------------------------------------------------------------- making material

def _material(kind: str, name: str, text: str, *, truncated: bool = False, **kw) -> Material:
    text, cut = cap(text)
    return Material(id=new_id(), kind=kind, name=name, text=text, chars=len(text), truncated=truncated or cut,
                    created_at=datetime.now(UTC), **kw)


def from_note(text: str) -> Material:
    text = text.strip()
    if not text:
        raise MaterialError("the note is empty")
    if len(text) > MAX_NOTE_CHARS:
        raise MaterialError(f"the note is longer than {MAX_NOTE_CHARS:,} characters: attach it as a file")
    first = text.splitlines()[0][:60]
    return _material("note", clean_name(first, "Note"), text)


async def from_file(org_id: UUID, filename: str, data: bytes) -> Material:
    """Vet an uploaded file and store it under the Organization. Raises MaterialError for the uploader."""
    name = clean_name(filename)
    if not data:
        raise MaterialError("the file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise MaterialError(f"the file is larger than {MAX_FILE_BYTES // 1_000_000} MB")
    kind = sniff(data, name)
    if kind is None:
        raise MaterialError("only PDF, JPEG, PNG, WebP and text (.txt, .md, .csv) files can be attached")
    if kind == "pdf":
        res = await pdf_text(data)
        m = _material("file", name, res["text"], content_type="application/pdf", size=len(data),
                      pages=res["pages"], truncated=res["truncated"])
        await put_file(org_id, m.id, "original.pdf", data, "application/pdf")
        return m
    if kind == "text":
        if len(data) > MAX_TEXT_FILE_BYTES:
            raise MaterialError(f"text files can be at most {MAX_TEXT_FILE_BYTES // 1_000_000} MB")
        text = decode_text(data)
        if not text.strip():
            raise MaterialError("the file is empty")
        m = _material("file", name, text, content_type="text/plain", size=len(data))
        await put_file(org_id, m.id, "original.txt", data, "text/plain")
        return m
    jpeg, _ = await asyncio.to_thread(reencode_image, data)
    m = _material("file", name, "", content_type="image/jpeg", size=len(jpeg), image=True)
    await put_file(org_id, m.id, "image.jpg", jpeg, "image/jpeg")
    return m


async def from_link(org_id: UUID, url: str) -> Material:
    """Fetch the Organization's own page (or PDF) once, through safe_fetch, and keep its text."""
    url = url.strip()
    if reason := link_checker.suspicious_reason(url):
        raise MaterialError(f"this link can't be used ({reason})")
    try:
        async with safe_fetch.client(timeout=10) as c:
            resp = await safe_fetch.fetch(c, "GET", url, max_bytes=MAX_LINK_BYTES)
    except safe_fetch.BlockedURL as e:
        raise MaterialError(f"this link can't be used ({e.reason})") from None
    except safe_fetch.TooLarge:
        raise MaterialError(f"the linked page is larger than {MAX_LINK_BYTES // 1_000_000} MB") from None
    except httpx.HTTPError as e:
        raise MaterialError(f"the link could not be opened ({type(e).__name__})") from None
    if resp.status_code >= 400:
        raise MaterialError(f"the link answered HTTP {resp.status_code}: paste the text as a note instead")
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    body, name = resp.content, clean_name(urlparse(url).netloc + urlparse(url).path, "Link")
    if ctype == "application/pdf" or body[:5] == b"%PDF-":
        res = await pdf_text(body)
        return _material("link", name, res["text"], url=url, content_type="application/pdf", pages=res["pages"],
                         truncated=res["truncated"])
    if ctype not in ("text/html", "application/xhtml+xml", "text/plain", ""):
        raise MaterialError(f"the link is not a web page or PDF ({ctype})")
    text = resp.text
    text = html_text(text) if ctype != "text/plain" else text
    if not text.strip():
        raise MaterialError("no text found on this page (it may need JavaScript): paste the text as a note")
    return _material("link", name, text, url=url, content_type=ctype or "text/html")


def _save(ctx: auth.Ctx, m: Material) -> MaterialRef:
    m = m.model_copy(update={"created_by": str(ctx.user.id)})
    store.save_material(ctx.org_id, m, audit=audit.entry(ctx, "material.added", m.id, kind=m.kind, name=m.name,
                                                          size=m.size, url=m.url))
    return MaterialRef.model_validate(m.model_dump(include=set(MaterialRef.model_fields)))


# ---------------------------------------------------------------- routes (Editor attaches, Viewer reads)

class MaterialCreate(BaseModel):
    kind: Literal["link", "note"]
    url: str | None = Field(None, max_length=2000)
    text: str | None = Field(None, max_length=MAX_NOTE_CHARS + 1000)


@router.post("/materials", response_model=MaterialRef, status_code=201)
async def create_material(req: MaterialCreate, ctx: auth.Editor) -> MaterialRef:
    """Attach a link (fetched now, on the host) or a note."""
    try:
        m = await from_link(ctx.org_id, req.url or "") if req.kind == "link" else from_note(req.text or "")
    except MaterialError as e:
        raise HTTPException(422, str(e)) from None
    return await asyncio.to_thread(_save, ctx, m)


@router.post("/materials/files", response_model=MaterialRef, status_code=201)
async def upload_material(request: Request, file: UploadFile, ctx: auth.Editor) -> MaterialRef:
    """Attach one file (multipart field `file`): PDF, JPEG, PNG, WebP or text, at most MAX_FILE_BYTES."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_FILE_BYTES + 64_000:
        raise HTTPException(413, f"the file is larger than {MAX_FILE_BYTES // 1_000_000} MB")
    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        m = await from_file(ctx.org_id, file.filename or "file", data)
    except MaterialError as e:
        raise HTTPException(422, str(e)) from None
    finally:
        await file.close()
    return await asyncio.to_thread(_save, ctx, m)


@router.get("/materials/{material_id}", response_model=Material)
def get_material(material_id: str, ctx: auth.Viewer) -> Material:
    found = store.get_materials(ctx.org_id, [material_id]) if ID.fullmatch(material_id) else []
    if not found:
        raise HTTPException(404, "material not found")
    return found[0]


def refs(org_id: UUID, ids: list[str]) -> list[MaterialRef]:
    """The refs a Job or Brainstorm keeps, for ids the caller sent. Another Organization's id looks unknown (RLS):
    422 without saying whether it exists."""
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_MATERIALS:
        raise HTTPException(422, f"at most {MAX_MATERIALS} pieces of Source material")
    found = store.get_materials(org_id, [i for i in ids if ID.fullmatch(i)])
    if len(found) != len(ids):
        raise HTTPException(422, "unknown Source material")
    return [MaterialRef.model_validate(m.model_dump(include=set(MaterialRef.model_fields))) for m in found]


# ---------------------------------------------------------------- what an agent gets

async def for_agent(org_id: UUID, material: list[MaterialRef], dest: Path) -> tuple[list[dict], list[Path]]:
    """The Job's (or Brainstorm's) own material for an agent payload: extracted text within MAX_AGENT_CHARS, and the
    re-encoded images copied into `dest` (a directory of this Job only, so a sandbox upload holds nothing else).
    Loaded under `org_id`, so only that Organization's material can ever appear."""
    if not material:
        return [], []
    found = await asyncio.to_thread(store.get_materials, org_id, [m.id for m in material])
    budget, n_img, items, files = MAX_AGENT_CHARS, 0, [], []
    for m in found:
        text, cut = m.text[:budget], len(m.text) > budget
        budget -= len(text)
        item = {"id": m.id, "kind": m.kind, "name": m.name, "url": m.url, "text": text,
                "truncated": m.truncated or cut}
        if m.image and n_img < MAX_AGENT_IMAGES:
            try:
                data = await get_file(org_id, m.id, "image.jpg")
            except (OSError, RuntimeError, MaterialError) as e:
                log.warning("material %s image unavailable: %s", m.id, e)
            else:
                dest.mkdir(parents=True, exist_ok=True)
                path = dest / f"{m.id}.jpg"
                await asyncio.to_thread(path.write_bytes, data)
                item["image_path"] = str(path)
                files.append(path)
                n_img += 1
        items.append(item)
    return items, files


# ---------------------------------------------------------------- Verify: where facts came from (ADR 0003)

def _norm(s: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", s).lower())


def check_facts(briefs: list[EventBrief], material: dict[str, Material], ok_urls: set[str],
                included: set[str]) -> tuple[list[Issue], list[str]]:
    """Set `verified` on every recorded fact source and turn contradictions into Issues. A fact from Source material
    counts as verified when it names this Job's material and, for text material, its quote is really in it; a fact
    from a link when that link is one of the event's sources and passed the link check. Contradictions between the
    material and a public page are never resolved here: each becomes a warning for the person reviewing.
    Returns (issues, log lines). Only events in `included` (on the Deck) raise Issues; the rest are logged."""
    issues: list[Issue] = []
    lines: list[str] = []
    for b in briefs:
        srcs = {str(s.url) for s in b.sources}
        unproven: list[str] = []
        for f in b.facts:
            f.verified, f.note = False, None
            if f.origin == "material":
                m = material.get(f.material_id or "")
                if not m:
                    f.note = "not one of this request's Source material"
                elif f.quote and m.text and _norm(f.quote) not in _norm(m.text):
                    f.note = "the quoted words are not in the material"
                else:
                    f.verified = True
                if not f.verified:
                    unproven.append(f.field)
            else:
                if f.url not in srcs:
                    f.note = "not one of the event's sources"
                elif f.url not in ok_urls:
                    f.note = "the link did not pass the check"
                else:
                    f.verified = True
        unknown = [i for i in b.materials if i not in material]
        if unknown:
            lines.append(f"{b.id}: ignored {len(unknown)} Source material reference(s) not attached to this request")
        if unproven and b.id in included:
            issues.append(Issue(severity="warn", category="fact", message=(
                f"{b.title_en}: {', '.join(dict.fromkeys(unproven))} said to come from your Source material, but "
                "it isn't there. Check before approving.")))
        for c in b.conflicts:
            m = material.get(c.material_id)
            where = urlparse(c.url).hostname or c.url
            msg = (f"{b.title_en}: your Source material{f' “{m.name}”' if m else ''} says {c.field} is "
                   f"“{c.material_says}”, but {where} says “{c.public_says}”. The Deck follows your material; "
                   "decide which is right before approving.")
            if c.url not in ok_urls:
                msg += " (That page did not pass the link check.)"
            if b.id in included:
                issues.append(Issue(severity="warn", category="fact", message=msg))
            lines.append(f"{b.id}: Source material and {where} disagree on {c.field}"
                         + ("" if b.id in included else " (event not on the Deck)"))
    return issues, lines
