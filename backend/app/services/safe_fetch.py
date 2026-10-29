"""Safe outbound fetching for the trusted host (HARDENING SEC-1).

Every fetch of a URL that came from an agent or a user goes through `fetch()` with a client from `client()`:
  - http/https only;
  - the hostname is resolved first and *every* address must be public: no loopback, private (RFC 1918, ULA),
    link-local (incl. the 169.254.169.254 metadata endpoint), CGNAT, multicast, unspecified or reserved
    addresses, IPv4 or IPv6, including IPv4 embedded in IPv6 (mapped, 6to4, Teredo, NAT64);
  - redirects are followed by hand (at most MAX_REDIRECTS) and every hop is checked again;
  - the body is size-capped and the whole fetch time-capped.

The client also pins each connection to the address it vetted (`httpx-secure`, 0BSD): the address is checked
again at connect time, and the socket goes to that numeric IP, so a DNS answer that changes between our check
and the connect (DNS rebinding) cannot reach an internal address.

Fixed, operator-configured endpoints (Instagram Graph API, SUPABASE_URL) don't use this: no agent or user
input picks their host, and a self-hosted Supabase may legitimately be on a private address.
"""

import asyncio
import ipaddress
import socket
from typing import Any

import httpx
from httpx_secure import SSRFProtectionError, httpx_ssrf_protection

MAX_REDIRECTS = 3
SCHEMES = {"http", "https"}
UA = {"User-Agent": "Mozilla/5.0 (WhatsOnKorea link checker; +https://github.com/juyoungml/nemo-k-cards)"}
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedURL(httpx.RequestError):
    """The URL points somewhere the host must never fetch. `reason` is human-readable."""

    def __init__(self, reason: str, *, request: httpx.Request | None = None) -> None:
        super().__init__(reason, request=request)
        self.reason = reason


class TooLarge(httpx.RequestError):
    """The response body is larger than the caller's cap."""


def blocked_ip_reason(ip: IPAddress) -> str | None:
    """None if `ip` is a public unicast address the host may connect to, else why not."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and any(ip in n for n in _NAT64):
            inner = ipaddress.IPv4Address(ip.packed[-4:])
        if inner is not None and (why := blocked_ip_reason(inner)):
            return f"{ip} embeds {why}"
    if ip.is_unspecified:
        return f"{ip} is unspecified"
    if ip.is_loopback:
        return f"{ip} is loopback"
    if ip.is_link_local:
        return f"{ip} is link-local (cloud metadata lives here)"
    if ip.is_multicast:
        return f"{ip} is multicast"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return f"{ip} is carrier-grade NAT"
    if ip.is_private:
        return f"{ip} is private"
    if ip.is_reserved or not ip.is_global:
        return f"{ip} is not a public address"
    return None


async def resolve(host: str, port: int) -> list[str]:
    """All addresses `host` resolves to. Module-level so tests can replace it."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def check_url(url: str | httpx.URL) -> None:
    """Raise BlockedURL unless `url` is http(s) and its host resolves only to public addresses."""
    u = httpx.URL(url)
    if u.scheme not in SCHEMES:
        raise BlockedURL(f"scheme {u.scheme or '(none)'!r} is not http(s)")
    host = u.host
    if not host:
        raise BlockedURL("no host")
    try:
        addrs = [host] if _is_ip(host) else await resolve(host, u.port or (443 if u.scheme == "https" else 80))
    except (OSError, UnicodeError) as e:
        raise httpx.ConnectError(f"cannot resolve {host}: {e}") from e
    if not addrs:
        raise httpx.ConnectError(f"cannot resolve {host}")
    for a in addrs:
        why = blocked_ip_reason(ipaddress.ip_address(a.split("%", 1)[0]))  # drop an IPv6 zone id
        if why:
            raise BlockedURL(why if a == host else f"{host} resolves to {why}")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def client(timeout: float = 8, **kw: Any) -> httpx.AsyncClient:
    """An AsyncClient for `fetch()`: no automatic redirects, no env proxies, connections pinned to vetted IPs."""
    c = httpx.AsyncClient(timeout=timeout, headers=UA, follow_redirects=False, trust_env=False, **kw)
    return httpx_ssrf_protection(c, custom_validator=lambda _host, ip, _port: blocked_ip_reason(ip) is None)


async def fetch(c: httpx.AsyncClient, method: str, url: str, *, max_bytes: int = 2_000_000,
                max_redirects: int = MAX_REDIRECTS, total_timeout: float = 30) -> httpx.Response:
    """Fetch `url`, checking it and every redirect hop. Returns the final response with its body read
    (at most `max_bytes`; 0 = don't read the body). `response.url` is the final URL.

    Raises BlockedURL, TooLarge, httpx.TooManyRedirects or any other httpx.HTTPError.
    """
    request = c.build_request(method, url)
    try:
        async with asyncio.timeout(total_timeout):
            for hop in range(max_redirects + 1):
                await check_url(request.url)
                try:
                    resp = await c.send(request, stream=True, follow_redirects=False)
                except SSRFProtectionError as e:  # connect-time re-check (e.g. DNS changed since check_url)
                    raise BlockedURL(f"{request.url.host} resolves to a non-public address",
                                     request=request) from e
                try:
                    if resp.next_request is not None:
                        if hop == max_redirects:
                            raise httpx.TooManyRedirects(f"more than {max_redirects} redirects", request=request)
                        request = resp.next_request
                        continue
                    body = await _read(resp, max_bytes) if max_bytes else b""
                finally:
                    await resp.aclose()
                headers = [(k, v) for k, v in resp.headers.multi_items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                return httpx.Response(resp.status_code, headers=headers, content=body, request=request)
    except TimeoutError as e:
        raise httpx.ReadTimeout(f"no complete response in {total_timeout}s", request=request) from e
    raise AssertionError("unreachable")


async def _read(resp: httpx.Response, max_bytes: int) -> bytes:
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise TooLarge(f"body is {int(declared)} bytes (cap {max_bytes})", request=resp.request)
    chunks, n = [], 0
    async for chunk in resp.aiter_bytes():
        n += len(chunk)
        if n > max_bytes:
            raise TooLarge(f"body exceeds {max_bytes} bytes", request=resp.request)
        chunks.append(chunk)
    return b"".join(chunks)
