"""Official sources for Guide Decks (#27; GLOSSARY "Official source").

Every fact in a Guide Deck must trace to an Official source: a government or agency site, an operator's own site, or
the Organization's Source material (ADR 0003). The researcher labels each source's `kind`, but a label is only a
claim, so the host decides:

- `allowlist`: the domain is a known government, agency or operator domain. Counts with no further question.
- `claimed`: the researcher says `official` (or `public_api`) for a domain we don't know, e.g. a museum's or an
  operator's own site. It counts, but the Deck gets a warning so a person confirms it is really that operator's site.
- `no`: news, blogs, SNS, ticket resellers and anything else. It can lead to a fact, never be its only source.
"""

from typing import Literal
from urllib.parse import urlparse

Standing = Literal["allowlist", "claimed", "no"]

# Suffixes: any host ending in one of these (gov sites of every level, public institutions, the tourism board).
OFFICIAL_SUFFIXES = ("go.kr", "korea.kr", "gov", "gov.uk", "europa.eu", "mil.kr", "police.go.kr")
# Registrable domains of agencies and public operators with a non-gov domain (transport, tourism, culture, health).
OFFICIAL_DOMAINS = {
    "visitkorea.or.kr", "korean.visitkorea.or.kr", "english.visitkorea.or.kr", "knto.or.kr", "visitseoul.net",
    "visitbusan.net", "visitjeju.net", "korea.net", "hikorea.go.kr", "immigration.go.kr", "k-eta.go.kr",
    "airport.kr", "airport.co.kr", "letskorail.com", "korail.com", "seoulmetro.co.kr", "bustago.or.kr",
    "kobus.co.kr", "tmoney.co.kr", "t-money.co.kr", "nts.go.kr", "customs.go.kr", "mofa.go.kr", "0404.go.kr",
    "kma.go.kr", "weather.go.kr", "safekorea.go.kr", "119.go.kr", "nhis.or.kr", "hira.or.kr", "kdca.go.kr",
    "museum.go.kr", "royalpalace.go.kr", "cha.go.kr", "khs.go.kr", "sejongpac.or.kr", "arko.or.kr",
    "kocis.go.kr", "1330.visitkorea.or.kr", "knps.or.kr", "sto.or.kr", "ktourtop.or.kr",
}
# Never official, whatever the label says: news, blogs, SNS, communities, resellers, travel agencies, wikis.
NEVER = {
    "naver.com", "blog.naver.com", "tistory.com", "brunch.co.kr", "daum.net", "kakao.com", "instagram.com",
    "facebook.com", "x.com", "twitter.com", "youtube.com", "tiktok.com", "reddit.com", "tripadvisor.com",
    "klook.com", "kkday.com", "trip.com", "agoda.com", "booking.com", "expedia.com", "viator.com",
    "getyourguide.com", "wikipedia.org", "namu.wiki", "medium.com", "quora.com", "pinterest.com",
    "koreaherald.com", "koreatimes.co.kr", "koreajoongangdaily.joins.com", "joongang.co.kr", "chosun.com",
    "donga.com", "hani.co.kr", "yna.co.kr", "yonhapnews.co.kr", "kbs.co.kr", "mbc.co.kr", "sbs.co.kr",
    "arirang.com", "cnn.com", "bbc.com", "bbc.co.uk", "nytimes.com", "timeout.com", "lonelyplanet.com",
    "popply.co.kr", "popga.co.kr", "interpark.com", "yes24.com", "melon.com",
}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _matches(host: str, domains: set[str] | tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def standing(url: str, kind: str | None) -> Standing:
    """Whether a source can carry a Guide fact on its own (see module doc). `kind` is the researcher's label."""
    host = _host(url)
    if not host or _matches(host, NEVER):
        return "no"
    if _matches(host, OFFICIAL_SUFFIXES) or _matches(host, OFFICIAL_DOMAINS):
        return "allowlist"
    return "claimed" if kind in ("official", "public_api") else "no"
