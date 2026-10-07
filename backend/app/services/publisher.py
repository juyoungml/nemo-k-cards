"""Instagram carousel publishing via Graph API. Host-only: the sandbox never sees the token."""


async def publish_carousel(image_urls: list[str], caption: str) -> str:
    # TODO: child containers -> CAROUSEL container -> media_publish -> return permalink
    raise NotImplementedError
