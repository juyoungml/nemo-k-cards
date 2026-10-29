"""Slide hosting for Instagram (BACKEND §7, #18 Private slides). Instagram's API only accepts image *URLs*:
Meta's servers download each JPEG while building the media container. Slides stay private: Instagram gets
links that expire PUBLISH_LINK_TTL_S after the upload (minutes), long after it has copied the images, and
the uploads are deleted as soon as the publish attempt ends.

IMAGE_HOST=supabase  upload to a PRIVATE Supabase Storage bucket under `<org_id>/<job_id>/slide-NN.jpg`
                     and hand out Storage signed URLs. A public bucket is refused (see docs/HARDENING.md SEC-3).
IMAGE_HOST=local     HMAC-signed links to this API's /assets route (app/assets.py) via PUBLIC_ASSET_BASE_URL
                     (e.g. a tunnel). They expire, and work only while the Job is PUBLISHING.

The storage key is host-only: it is never passed to an agent sandbox. Signed URLs are never logged.
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx

from app import assets
from app.config import settings

log = logging.getLogger("image_host")
_transport: httpx.AsyncBaseTransport | None = None  # tests swap in a fake Storage


class ImageHostError(RuntimeError):
    pass


def _client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=30, follow_redirects=False, **kw)


def _no_query(url: str) -> str:
    """For messages and logs: a signed URL without its token."""
    return url.split("?", 1)[0]


def object_key(org_id: UUID, job_id: str, name: str) -> str:
    """Storage layout: one folder per Organization, then per Job."""
    return f"{org_id}/{job_id}/{name}"


async def _ensure_private_bucket(client: httpx.AsyncClient) -> None:
    b = settings.supabase_bucket
    r = await client.get(f"/storage/v1/bucket/{b}")
    if r.status_code == 200:
        if r.json().get("public"):
            raise ImageHostError(
                f"Storage bucket {b!r} is public; slides must be private. Apply supabase/migrations/"
                "*_private_slides.sql (or switch the bucket to private in the Supabase dashboard) and try again.")
        return
    r = await client.post("/storage/v1/bucket", json={"id": b, "name": b, "public": False,
                                                       "allowed_mime_types": ["image/jpeg"]})
    if r.status_code not in (200, 201) and "already exists" not in r.text:
        raise ImageHostError(f"could not create bucket {b!r}: {r.status_code} {r.text[:200]}")


async def _supabase_delete(keys: list[str]) -> None:
    if not keys:
        return
    base, key = settings.supabase_url.rstrip("/"), settings.supabase_service_key
    async with _client(base_url=base, headers={"Authorization": f"Bearer {key}", "apikey": key}) as client:
        r = await client.request("DELETE", f"/storage/v1/object/{settings.supabase_bucket}",
                                 json={"prefixes": keys})
        if r.status_code not in (200, 204):
            raise ImageHostError(f"delete failed: {r.status_code} {r.text[:200]}")


async def _supabase_upload(org_id: UUID, job_id: str, paths: list[Path], ttl: int,
                           uploaded: list[str], on_progress: Callable[[int, int], None]) -> list[str]:
    """Upload to the private bucket (recording each key in `uploaded` so the caller can delete them even if this
    fails halfway) and return signed URLs that expire in `ttl` seconds."""
    if not (settings.supabase_url and settings.supabase_service_key):
        raise ImageHostError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set in backend/.env")
    base = settings.supabase_url.rstrip("/")
    key = settings.supabase_service_key
    bucket = settings.supabase_bucket
    async with _client(base_url=base, headers={"Authorization": f"Bearer {key}", "apikey": key}) as client:
        await _ensure_private_bucket(client)
        keys = []
        for p in paths:
            obj = object_key(org_id, job_id, p.name)
            r = await client.post(f"/storage/v1/object/{bucket}/{obj}", content=p.read_bytes(),
                                  headers={"Content-Type": "image/jpeg", "x-upsert": "true"})
            if r.status_code not in (200, 201):
                raise ImageHostError(f"upload {obj} failed: {r.status_code} {r.text[:200]}")
            uploaded.append(obj)
            keys.append(obj)
            on_progress(len(keys), len(paths))
        r = await client.post(f"/storage/v1/object/sign/{bucket}", json={"expiresIn": ttl, "paths": keys})
        if r.status_code != 200:
            raise ImageHostError(f"signing failed: {r.status_code} {r.text[:200]}")
        signed = {d.get("path"): d.get("signedURL") for d in r.json()}
        if any(not signed.get(k) for k in keys):
            raise ImageHostError("signing failed for some slides")
        urls = [f"{base}/storage/v1{signed[k]}" for k in keys]

    async with _client() as anon:  # what Instagram sees: no credentials at all
        chk = await anon.get(urls[0])
        if chk.status_code != 200 or not chk.headers.get("content-type", "").startswith("image/"):
            raise ImageHostError(f"signed URL not reachable ({chk.status_code}): {_no_query(urls[0])}")
        leak = await anon.get(f"{base}/storage/v1/object/public/{bucket}/{keys[0]}")
        if leak.status_code == 200:
            raise ImageHostError(f"bucket {bucket!r} serves slides without a signature; make it private")
    return urls


def _local_urls(org_id: UUID, job_id: str, paths: list[Path], ttl: int) -> list[str]:
    if not settings.public_asset_base_url:
        raise ImageHostError("PUBLIC_ASSET_BASE_URL is not set (needed for IMAGE_HOST=local)")
    exp = int(time.time()) + ttl
    return [assets.sign(org_id, job_id, p.name, exp, base=settings.public_asset_base_url, use="ig") for p in paths]


@asynccontextmanager
async def exposed(org_id: UUID, job_id: str, paths: list[Path],
                  on_progress: Callable[[int, int], None] = lambda done, total: None) -> AsyncIterator[list[str]]:
    """Make a Job's slides fetchable by Instagram for the duration of the `async with` block and yield their
    links in slide order. The links expire after PUBLISH_LINK_TTL_S whatever happens; on exit, uploaded copies
    are deleted (Instagram has its own copy by then: a FINISHED container no longer reads the URL)."""
    paths, ttl = sorted(paths), settings.publish_link_ttl_s
    uploaded: list[str] = []
    try:
        if settings.image_host == "supabase":
            urls = await _supabase_upload(org_id, job_id, paths, ttl, uploaded, on_progress)
        else:
            urls = _local_urls(org_id, job_id, paths, ttl)
            on_progress(len(urls), len(urls))
        yield urls
    finally:
        if uploaded:
            try:
                await _supabase_delete(uploaded)
            except (ImageHostError, httpx.HTTPError) as e:
                # Still private: the signed links expire on their own. Only storage is wasted.
                log.warning("could not delete %d uploaded slide(s) for job %s: %s", len(uploaded), job_id, e)
