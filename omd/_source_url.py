"""Shared HTTP URL extraction for URL-or-share-text entry points."""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
_PRIVATE_QUERY_SOURCE_DOMAINS = ("xiaohongshu.com", "rednote.com", "xhslink.com")


class SourceURLCountError(ValueError):
    """Shared text contained more than one URL and is unsafe to route."""


def extract_single_http_url(value: str) -> str | None:
    """Return one HTTP(S) URL, preserve zero-URL path inputs, reject multiples."""
    matches = [match.group(0) for match in URL_RE.finditer(value)]
    if len(matches) > 1:
        raise SourceURLCountError(
            "Shared text must contain exactly one HTTP(S) URL; multiple URLs "
            "were found. Remove the extra links, then retry."
        )
    if not matches:
        return None
    raw_url = matches[0]
    if value.strip() == raw_url:
        return raw_url
    return raw_url.rstrip("/.,;)")


def sanitize_persisted_source_url(url: str) -> str:
    """Remove access-bearing query data from supported private-context URLs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if any(host == domain or host.endswith(f".{domain}") for domain in _PRIVATE_QUERY_SOURCE_DOMAINS):
        return urlunparse(parsed._replace(query="", fragment=""))
    return url


def sanitize_persisted_source_text(value: str) -> str:
    """Preserve share text while sanitizing each embedded source URL."""
    return URL_RE.sub(lambda match: sanitize_persisted_source_url(match.group(0)), value)
