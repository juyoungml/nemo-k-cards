"""Private slides (#18): rendered slide files are served only through short-lived signed links.

The renderer writes `OUTPUT_DIR/<job_id>/slide-NN.jpg` and the Job stores the canonical, unsigned path
`/assets/<job_id>/slide-NN.jpg`. Nothing serves that path. The API turns it into a signed link on the way out:

    /assets/<org_id>/<job_id>/slide-NN.jpg?exp=<unix>&sig=<HMAC(org_id/job_id/name, exp)>

`GET /assets/...` is anonymous (an <img> tag can't send a Bearer token, and Instagram fetches with no
credentials), so the link itself is the credential. It works only if the signature matches, `exp` hasn't passed,
the file is a slide, and the Job still belongs to that Organization (checked under RLS). Another Organization's
path, a forged or edited link and an expired one all get the same 404.

Two kinds of link, told apart by a signed `use`:
- Admin (`use` absent): ADMIN_TTL_S, re-signed on every API response.
- Instagram (`use=ig`, IMAGE_HOST=local): PUBLISH_LINK_TTL_S, and only while the Job is PUBLISHING, so the
  links stop working as soon as the publish attempt ends, even before they expire.

The signing key is ASSET_SIGNING_KEY, else derived from SUPABASE_SERVICE_KEY, else random per process (links
then die on restart; the Admin polls and picks up new ones). It never leaves the host."""

import asyncio
import base64
import hashlib
import hmac
import math
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app import store
from app.config import settings
from app.schemas import Job, JobStatus

router = APIRouter()

# The only files served; photos and anything else stay host-only. `slide-NN.jpg` is what the pipeline renders;
# `slide-NN.<8 hex>.jpg` is a slide re-rendered by a Deck edit (#19), a new name so no version's file is overwritten.
SLIDE = re.compile(r"slide-\d{1,3}(?:\.[0-9a-f]{8})?\.jpg")
PIPELINE_SLIDE = re.compile(r"slide-\d{1,3}\.jpg")
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# A photo the host vetted for an event (#23, `photos/<event id>-<n>.<ext>`): shown to the Admin as a swap candidate,
# never to Instagram (a slide is rendered from it instead).
PHOTO = re.compile(r"[A-Za-z0-9_.-]{1,96}-\d\.(?:jpg|png|webp)")
PHOTO_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
ADMIN_TTL_S = 15 * 60
# Admin links are rounded to this window so an <img> src stays the same between polls (the browser caches it)
# and a link is valid for ADMIN_TTL_S to ADMIN_TTL_S + WINDOW_S.
WINDOW_S = 5 * 60
_random_key = secrets.token_bytes(32)


def _key() -> bytes:
    if settings.asset_signing_key:
        return settings.asset_signing_key.encode()
    if settings.supabase_service_key:  # one-way: the link reveals nothing about the service key
        return hmac.new(settings.supabase_service_key.encode(), b"wok-asset-links-v1", hashlib.sha256).digest()
    return _random_key


def _sig(org_id: UUID, job_id: str, name: str, exp: int, use: str) -> str:
    msg = f"{org_id}/{job_id}/{name}\n{exp}\n{use}".encode()
    return base64.urlsafe_b64encode(hmac.new(_key(), msg, hashlib.sha256).digest()).rstrip(b"=").decode()


def sign(org_id: UUID, job_id: str, name: str, exp: int, base: str = "", use: str = "") -> str:
    """A link to one slide that works until `exp` (unix seconds). `base` is empty for API-relative Admin links,
    or PUBLIC_ASSET_BASE_URL with `use="ig"` for Instagram."""
    q = f"exp={exp}" + (f"&use={use}" if use else "") + f"&sig={_sig(org_id, job_id, name, exp, use)}"
    return f"{base.rstrip('/')}/assets/{org_id}/{job_id}/{name}?{q}"


def valid(org_id: UUID, job_id: str, name: str, exp: int, sig: str, use: str = "",
          now: float | None = None) -> bool:
    if exp < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig.encode(), _sig(org_id, job_id, name, exp, use).encode())


def admin_exp(now: float | None = None) -> int:
    now = time.time() if now is None else now
    return int(math.ceil((now + ADMIN_TTL_S) / WINDOW_S) * WINDOW_S)


def for_admin(org_id: UUID, job: Job, now: float | None = None) -> Job:
    """The Job as the Admin sees it: its stored `/assets/<job>/<file>` paths replaced by signed links."""
    if not job.slide_urls:
        return job
    exp = admin_exp(now)
    urls = [sign(org_id, job.id, path.rsplit("/", 1)[-1], exp) for path in job.slide_urls]
    return job.model_copy(update={"slide_urls": urls})


def slide_files(job: Job) -> list[Path]:
    """The rendered files of the Job's current Deck version, in slide order: what the Safeguard checks and what is
    posted. From `slide_urls` (each version names its own files); a Job without them falls back to the pipeline's
    `slide-NN.jpg` files in its folder."""
    folder = settings.output_dir / job.id
    if job.slide_urls:
        return [folder / u.rsplit("/", 1)[-1] for u in job.slide_urls]
    return sorted(p for p in folder.glob("slide-*.jpg") if PIPELINE_SLIDE.fullmatch(p.name))


def photo_link(org_id: UUID, job_id: str, file_url: str, now: float | None = None) -> str | None:
    """An Admin link to one of the Job's vetted photos (a `file://` path under its `photos/` folder), or None."""
    path = Path(url2pathname(urlparse(file_url).path)).resolve()
    if path.parent != (settings.output_dir / job_id / "photos").resolve() or not PHOTO.fullmatch(path.name):
        return None
    return sign(org_id, job_id, f"photos/{path.name}", admin_exp(now))


@router.get("/assets/{org_id}/{job_id}/photos/{name}", include_in_schema=False)
async def photo(org_id: UUID, job_id: str, name: str, exp: int = 0, sig: str = "") -> FileResponse:
    """A vetted photo for the Admin's swap picker (#23). Admin links only (no `use=ig`); same single 404 as slides."""
    if not (PHOTO.fullmatch(name) and JOB_ID.fullmatch(job_id) and valid(org_id, job_id, f"photos/{name}", exp, sig)):
        raise HTTPException(404, "not found")
    if await asyncio.to_thread(store.get_job, org_id, job_id) is None:
        raise HTTPException(404, "not found")
    path = settings.output_dir / job_id / "photos" / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type=PHOTO_TYPES[name.rsplit(".", 1)[1]],
                        headers={"cache-control": f"private, max-age={max(0, min(exp - int(time.time()), ADMIN_TTL_S))}",
                                 "x-content-type-options": "nosniff", "referrer-policy": "no-referrer"})


@router.get("/assets/{org_id}/{job_id}/{name}", include_in_schema=False)
async def slide(org_id: UUID, job_id: str, name: str, exp: int = 0, sig: str = "", use: str = "") -> FileResponse:
    # One answer for every refusal, so a probe can't tell a forged link from another Organization's real Job.
    if not (SLIDE.fullmatch(name) and JOB_ID.fullmatch(job_id) and use in ("", "ig")
            and valid(org_id, job_id, name, exp, sig, use)):
        raise HTTPException(404, "not found")
    job = await asyncio.to_thread(store.get_job, org_id, job_id)  # None: deleted, or not this Organization's
    if job is None or (use == "ig" and job.status != JobStatus.PUBLISHING):
        raise HTTPException(404, "not found")
    path = settings.output_dir / job_id / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    max_age = max(0, min(exp - int(time.time()), ADMIN_TTL_S))
    return FileResponse(path, media_type="image/jpeg",
                        headers={"cache-control": f"private, max-age={max_age}",
                                 "x-content-type-options": "nosniff", "referrer-policy": "no-referrer"})
