"""Instagram content publishing (Graph API with Instagram Login), host only: the sandbox never sees a token.

Every call is made for one connected Instagram account (#15) with that account's own token, read from Vault by the
publisher (app/pipeline/publishing.py) and passed in here. The token travels in the Authorization header, never in
a URL, so it can't end up in httpx's or a proxy's request log. Errors never carry the token or a signed slide URL.

Instagram fetches images itself while it builds each container, so image_urls are the short-lived signed links from
image_host.exposed() (#18 Private slides). Never log them: the signature is the credential.

What an error means for a publish (HARDENING PUB-1; docs/spec/v1.md "Publish state machine"):
- `ambiguous`: the call may or may not have taken effect. Rate limits (Meta codes 4, 17, 32, 613 and the
  business-use-case family 80001-80014), `is_transient`, unknown errors (1, 2), HTTP 5xx, timeouts, dropped
  connections, an answer we can't read. On `media_publish` this means "maybe posted": the Post goes to unknown.
- `rejected`: Instagram clearly refused (invalid parameter, permissions, media errors, expired container). Nothing
  happened. `token` marks the token itself as invalid (code 190): the account needs reconnecting.
"""

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Self

import httpx

from app.config import settings

AMBIGUOUS_CODES = {1, 2, 4, 17, 32, 613, *range(80001, 80015)}  # rate limits and "try again" (maybe it happened)
TOKEN_CODES = {190}                                              # invalid or revoked token
CONTAINER_WAIT_BUDGET_S = 300                                    # one container may take this long to process

# Tests swap in an httpx.MockTransport; nothing in the test suite reaches Meta.
transport: httpx.AsyncBaseTransport | None = None
sleep = asyncio.sleep  # tests make polling instant


def graph_base() -> str:
    return f"https://graph.instagram.com/{settings.instagram_api_version}"


class GraphError(Exception):
    """A failed Graph call. `kind`: ambiguous | rejected. The message is safe to log and show."""

    def __init__(self, step: str, kind: str, detail: str, *, code: int | None = None, status: int | None = None,
                 token_invalid: bool = False):
        super().__init__(f"{step}: {detail}")
        self.step, self.kind, self.code, self.status, self.token_invalid = step, kind, code, status, token_invalid

    @property
    def ambiguous(self) -> bool:
        return self.kind == "ambiguous"


def classify(step: str, status: int, body: object) -> GraphError:
    """Turn an error answer into a GraphError of the right kind."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        kind = "ambiguous" if status >= 500 or status == 429 or status < 400 else "rejected"
        return GraphError(step, kind, f"Instagram answered HTTP {status}", status=status)
    code = err.get("code") if isinstance(err.get("code"), int) else None
    sub = err.get("error_subcode")
    msg = str(err.get("message") or "")[:200]
    detail = f"{msg} (code {code}{f', subcode {sub}' if sub else ''}, HTTP {status})"
    if code in TOKEN_CODES:
        return GraphError(step, "rejected", detail, code=code, status=status, token_invalid=True)
    if code in AMBIGUOUS_CODES or err.get("is_transient") is True or status >= 500 or status == 429:
        return GraphError(step, "ambiguous", detail, code=code, status=status)
    return GraphError(step, "rejected", detail, code=code, status=status)


@dataclass(frozen=True)
class Media:
    id: str
    permalink: str | None
    timestamp: datetime | None
    caption: str | None


class Instagram:
    """The publishing calls for one account. Use as `async with Instagram(ig_user_id, token) as ig:`."""

    def __init__(self, ig_user_id: str, token: str):
        self.user = ig_user_id
        self._client = httpx.AsyncClient(base_url=graph_base(), timeout=30, transport=transport,
                                         follow_redirects=False, headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never print the token by accident
        return f"Instagram(user={self.user})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _call(self, step: str, method: str, path: str, **params) -> dict:
        try:
            resp = await self._client.request(method, path, params=params)
        except httpx.HTTPError as e:  # timeouts, dropped connections: the request may have arrived
            raise GraphError(step, "ambiguous", f"no answer from Instagram ({type(e).__name__})") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.is_error or not isinstance(body, dict) or "error" in body:
            raise classify(step, resp.status_code, body)
        return body

    async def create_child(self, image_url: str) -> str:
        body = await self._call("create slide container", "POST", f"/{self.user}/media", image_url=image_url,
                                is_carousel_item="true")
        return _id(body, "create slide container")

    async def create_carousel(self, children: list[str], caption: str) -> str:
        body = await self._call("create carousel container", "POST", f"/{self.user}/media", media_type="CAROUSEL",
                                children=",".join(children), caption=caption)
        return _id(body, "create carousel container")

    async def status(self, container_id: str) -> str:
        """IN_PROGRESS | FINISHED | PUBLISHED | ERROR | EXPIRED."""
        body = await self._call("container status", "GET", f"/{container_id}", fields="status_code")
        return str(body.get("status_code") or "")

    async def wait_finished(self, container_id: str, budget_s: float = CONTAINER_WAIT_BUDGET_S) -> None:
        """Poll with exponential backoff and jitter (PUB-4), not a fixed 2 s."""
        waited, delay = 0.0, 1.0
        while True:
            status = await self.status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise GraphError("container processing", "rejected", f"Instagram reports the container {status}")
            if waited >= budget_s:
                raise GraphError("container processing", "rejected", "Instagram did not finish processing in time")
            d = min(delay, 15.0) * (0.75 + random.random() / 2)
            await sleep(d)
            waited, delay = waited + d, delay * 2

    async def media_publish(self, creation_id: str) -> str:
        """The one call that makes a post public. Any answer but an id leaves the outcome to the caller."""
        body = await self._call("media_publish", "POST", f"/{self.user}/media_publish", creation_id=creation_id)
        if not isinstance(body.get("id"), str | int):
            raise GraphError("media_publish", "ambiguous", "Instagram's answer had no media id")
        return str(body["id"])

    async def permalink(self, media_id: str) -> str | None:
        body = await self._call("permalink", "GET", f"/{media_id}", fields="permalink")
        link = body.get("permalink")
        return link if isinstance(link, str) else None

    async def recent_media(self, limit: int = 10) -> list[Media]:
        body = await self._call("recent media", "GET", f"/{self.user}/media",
                                fields="id,permalink,timestamp,caption", limit=str(limit))
        out = []
        for m in body.get("data") or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            try:
                ts = datetime.fromisoformat(str(m["timestamp"]).replace("+0000", "+00:00")) if m.get("timestamp") \
                    else None
            except ValueError:
                ts = None
            out.append(Media(str(m["id"]), m.get("permalink"), ts, m.get("caption")))
        return out

    async def account(self) -> dict:
        """Followers and media count, for the dashboard."""
        return await self._call("account", "GET", "/me", fields="username,followers_count,media_count")

    async def get(self, step: str, path: str, **params) -> dict:
        return await self._call(step, "GET", path, **params)


def _id(body: dict, step: str) -> str:
    if not isinstance(body.get("id"), str | int):
        raise GraphError(step, "rejected", "Instagram's answer had no container id")
    return str(body["id"])


async def build_carousel(ig: Instagram, image_urls: list[str], caption: str,
                         on_progress: Callable[[str, int, int], None] = lambda step, done, total: None) -> str:
    """Child containers → a FINISHED carousel container. Returns its creation_id. Nothing is public yet."""
    if not 2 <= len(image_urls) <= 10:
        raise ValueError(f"carousel needs 2-10 images, got {len(image_urls)}")
    children = []
    for i, url in enumerate(image_urls, 1):
        children.append(await ig.create_child(url))
        on_progress("containers", i, len(image_urls))
    for i, child in enumerate(children, 1):
        await ig.wait_finished(child)
        on_progress("processing", i, len(children))
    creation_id = await ig.create_carousel(children, caption)
    await ig.wait_finished(creation_id)
    return creation_id
