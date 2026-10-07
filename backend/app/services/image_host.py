"""Public hosting for rendered slides (BACKEND §7). Instagram's API only accepts image *URLs*: Meta's servers
download each JPEG, so slides must be reachable on the public internet before publishing.

IMAGE_HOST=supabase  upload to a public Supabase Storage bucket (works from laptop, Brev, Railway)
IMAGE_HOST=local     serve from this backend's /assets via PUBLIC_ASSET_BASE_URL (e.g. an ngrok tunnel)

The storage key is host-only: it is never passed to an agent sandbox.
"""

from collections.abc import Callable
from pathlib import Path

import httpx

from app.config import settings


class ImageHostError(RuntimeError):
    pass


async def _ensure_bucket(client: httpx.AsyncClient) -> None:
    b = settings.supabase_bucket
    r = await client.get(f"/storage/v1/bucket/{b}")
    if r.status_code == 200:
        if not r.json().get("public"):
            await client.put(f"/storage/v1/bucket/{b}", json={"id": b, "name": b, "public": True})
        return
    r = await client.post("/storage/v1/bucket", json={"id": b, "name": b, "public": True})
    if r.status_code not in (200, 201) and "already exists" not in r.text:
        raise ImageHostError(f"could not create bucket {b!r}: {r.status_code} {r.text[:200]}")


async def _supabase_upload(job_id: str, paths: list[Path], on_progress: Callable[[int, int], None]) -> list[str]:
    if not (settings.supabase_url and settings.supabase_service_key):
        raise ImageHostError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set in backend/.env")
    base = settings.supabase_url.rstrip("/")
    key = settings.supabase_service_key
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    urls = []
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30) as client:
        await _ensure_bucket(client)
        for p in paths:
            obj = f"{job_id}/{p.name}"
            r = await client.post(f"/storage/v1/object/{settings.supabase_bucket}/{obj}", content=p.read_bytes(),
                                  headers={"Content-Type": "image/jpeg", "x-upsert": "true"})
            if r.status_code not in (200, 201):
                raise ImageHostError(f"upload {obj} failed: {r.status_code} {r.text[:200]}")
            urls.append(f"{base}/storage/v1/object/public/{settings.supabase_bucket}/{obj}")
            on_progress(len(urls), len(paths))
        # Instagram must be able to fetch them: check one URL without credentials.
        chk = await httpx.AsyncClient(timeout=15).get(urls[0])
        if chk.status_code != 200 or not chk.headers.get("content-type", "").startswith("image/"):
            raise ImageHostError(f"public URL not reachable ({chk.status_code}): {urls[0]}")
    return urls


def public_url(obj: str) -> str:
    """Public URL of an object in the Supabase bucket ("" when Supabase is not configured)."""
    if not settings.supabase_url:
        return ""
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{settings.supabase_bucket}/{obj}"


async def upload_public(obj: str, data: bytes, content_type: str) -> str:
    """Upload one object to the public Supabase bucket and return its public URL."""
    if not (settings.supabase_url and settings.supabase_service_key):
        raise ImageHostError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set in backend/.env")
    key = settings.supabase_service_key
    async with httpx.AsyncClient(base_url=settings.supabase_url.rstrip("/"), timeout=30,
                                 headers={"Authorization": f"Bearer {key}", "apikey": key}) as client:
        await _ensure_bucket(client)
        r = await client.post(f"/storage/v1/object/{settings.supabase_bucket}/{obj}", content=data,
                              headers={"Content-Type": content_type, "x-upsert": "true"})
        if r.status_code not in (200, 201):
            raise ImageHostError(f"upload {obj} failed: {r.status_code} {r.text[:200]}")
    return public_url(obj)


def _local_urls(job_id: str, paths: list[Path]) -> list[str]:
    if not settings.public_asset_base_url:
        raise ImageHostError("PUBLIC_ASSET_BASE_URL is not set (needed for IMAGE_HOST=local)")
    base = settings.public_asset_base_url.rstrip("/")
    return [f"{base}/assets/{job_id}/{p.name}" for p in paths]


async def publish_images(job_id: str, paths: list[Path],
                         on_progress: Callable[[int, int], None] = lambda done, total: None) -> list[str]:
    """Make the job's slides publicly reachable and return their URLs (in slide order)."""
    paths = sorted(paths)
    if settings.image_host == "supabase":
        return await _supabase_upload(job_id, paths, on_progress)
    urls = _local_urls(job_id, paths)
    on_progress(len(urls), len(urls))
    return urls
