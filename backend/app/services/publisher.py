"""Instagram carousel publishing via Graph API. Host-only: the sandbox never sees the token.

Instagram fetches images itself, so image_urls must be public JPEG URLs
(PUBLIC_ASSET_BASE_URL + path under output_dir, served by main.py at /assets).
"""

import asyncio
from collections.abc import Callable

import httpx

from app.config import settings

GRAPH = "https://graph.instagram.com/v24.0"


async def _call(client: httpx.AsyncClient, method: str, path: str, **params) -> dict:
    resp = await client.request(method, path, params=params)
    body = resp.json()
    if resp.is_error or "error" in body:
        raise RuntimeError(f"{method} {path}: {body.get('error', {}).get('message', resp.text)}")
    return body


async def _wait_ready(client: httpx.AsyncClient, container_id: str) -> None:
    for _ in range(30):
        body = await _call(client, "GET", f"/{container_id}", fields="status_code")
        if body["status_code"] == "FINISHED":
            return
        if body["status_code"] in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id}: {body['status_code']}")
        await asyncio.sleep(2)
    raise RuntimeError(f"container {container_id}: not ready in time")


async def publish_carousel(image_urls: list[str], caption: str, publish: bool = True,
                           on_progress: Callable[[str, int, int], None] = lambda step, done, total: None) -> str:
    """child containers -> CAROUSEL container -> media_publish -> return permalink.

    publish=False is a dry run: everything up to a FINISHED carousel container, then stop (nothing goes public).
    Returns the permalink, or "dryrun:<container_id>".
    """
    if not 2 <= len(image_urls) <= 10:
        raise ValueError(f"carousel needs 2-10 images, got {len(image_urls)}")
    user = settings.ig_user_id
    async with httpx.AsyncClient(
        base_url=GRAPH, timeout=30,
        headers={"Authorization": f"Bearer {settings.ig_access_token}"},
    ) as client:
        children = []
        for i, url in enumerate(image_urls, 1):
            children.append((await _call(client, "POST", f"/{user}/media",
                                         image_url=url, is_carousel_item="true"))["id"])
            on_progress("containers", i, len(image_urls))
        for i, child in enumerate(children, 1):
            await _wait_ready(client, child)
            on_progress("processing", i, len(children))

        carousel = await _call(client, "POST", f"/{user}/media", media_type="CAROUSEL",
                               children=",".join(children), caption=caption)
        await _wait_ready(client, carousel["id"])
        if not publish:
            return f"dryrun:{carousel['id']}"
        on_progress("publish", 0, 1)

        media = await _call(client, "POST", f"/{user}/media_publish", creation_id=carousel["id"])
        return (await _call(client, "GET", f"/{media['id']}", fields="permalink"))["permalink"]
