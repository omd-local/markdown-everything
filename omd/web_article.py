"""Public, bounded fallbacks for web pages that reject generic HTTP clients."""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from omd._download import max_download_bytes

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
WEBPAGE_LIMIT_BYTES = 20 * 1024 * 1024
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(frozen=True)
class WebFallbackDocument:
    html: str
    mode: str
    partial: bool


class WebFallbackUnavailable(RuntimeError):
    pass


def fetch_public_fallback(
    url: str,
    *,
    timeout: int = 30,
    _open=None,
) -> WebFallbackDocument:
    """Fetch public HTML, then an exact same-origin RSS item if HTML is blocked."""
    opener = _open or urllib.request.urlopen
    direct_error: Exception | None = None
    try:
        source_html, final_url = _fetch_text(url, timeout=timeout, opener=opener)
        return WebFallbackDocument(
            html=_inject_base(source_html, final_url),
            mode="browser_html",
            partial=False,
        )
    except Exception as exc:  # noqa: BLE001 - the RSS path is the intended fallback
        direct_error = exc

    feed_url = _feed_url(url)
    try:
        feed_xml, _ = _fetch_text(feed_url, timeout=timeout, opener=opener, allow_xml=True)
        return _document_from_feed(feed_xml, url)
    except WebFallbackUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to one concise boundary error
        raise WebFallbackUnavailable(
            f"public HTML failed ({_error_summary(direct_error)}); "
            f"same-origin RSS failed ({_error_summary(exc)})"
        ) from exc


def _fetch_text(url: str, *, timeout: int, opener, allow_xml: bool = False) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5"
                if allow_xml
                else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
            ),
            "Accept-Language": "en-US,en;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    with opener(request, timeout=timeout) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if allow_xml:
            accepted = not content_type or any(
                marker in content_type for marker in ("xml", "rss", "text/plain")
            )
        else:
            accepted = not content_type or any(
                marker in content_type for marker in ("text/html", "application/xhtml+xml")
            )
        if not accepted:
            raise WebFallbackUnavailable(f"unsupported response type: {content_type}")
        raw = _read_bounded(response)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), response.geturl()


def _read_bounded(response) -> bytes:
    limit = min(max_download_bytes(), WEBPAGE_LIMIT_BYTES)
    try:
        content_length = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > limit:
        raise WebFallbackUnavailable(f"web response is larger than the {limit}-byte limit")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise WebFallbackUnavailable(f"web response exceeded the {limit}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _document_from_feed(feed_xml: str, source_url: str) -> WebFallbackDocument:
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as exc:
        raise WebFallbackUnavailable(f"same-origin feed was not valid XML: {exc}") from exc

    wanted = _normalized_url(source_url)
    for item in root.findall(".//item"):
        link = _element_text(item.find("link"))
        if not link or _normalized_url(link) != wanted:
            continue
        title = _element_text(item.find("title")) or "Web article"
        author = _element_text(item.find(f"{{{DC_NS}}}creator"))
        published = _element_text(item.find("pubDate"))
        full = _element_text(item.find(f"{{{CONTENT_NS}}}encoded"))
        excerpt = _element_text(item.find("description"))
        body = full or excerpt
        if not body:
            raise WebFallbackUnavailable("matching RSS item did not contain article text")
        partial = not bool(full)
        return WebFallbackDocument(
            html=_feed_html(
                title=title,
                author=author,
                published=published,
                source_url=link,
                body=body,
                partial=partial,
            ),
            mode="rss_excerpt" if partial else "rss_full",
            partial=partial,
        )
    raise WebFallbackUnavailable("same-origin RSS did not contain a matching article")


def _feed_html(
    *,
    title: str,
    author: str,
    published: str,
    source_url: str,
    body: str,
    partial: bool,
) -> str:
    metadata = [f"<li>Source: <a href=\"{html.escape(source_url, quote=True)}\">{html.escape(source_url)}</a></li>"]
    if author:
        metadata.append(f"<li>Author: {html.escape(author)}</li>")
    if published:
        metadata.append(f"<li>Published: {html.escape(published)}</li>")
    notice = ""
    if partial:
        notice = (
            "<blockquote><strong>Partial capture:</strong> Only the public RSS excerpt was "
            "available because the article page rejected automated access. Save the page "
            "as HTML or PDF in your browser and import that file for the full article.</blockquote>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        f"<base href=\"{html.escape(source_url, quote=True)}\"></head><body><article>"
        f"<h1>{html.escape(title)}</h1><ul>{''.join(metadata)}</ul>{notice}{body}"
        "</article></body></html>"
    )


def _inject_base(source_html: str, source_url: str) -> str:
    base = f"<base href=\"{html.escape(source_url, quote=True)}\">"
    head = re.search(r"<head\b[^>]*>", source_html, flags=re.IGNORECASE)
    if head:
        return source_html[: head.end()] + base + source_html[head.end() :]
    html_tag = re.search(r"<html\b[^>]*>", source_html, flags=re.IGNORECASE)
    if html_tag:
        return source_html[: html_tag.end()] + f"<head>{base}</head>" + source_html[html_tag.end() :]
    return f"<!doctype html><html><head>{base}</head><body>{source_html}</body></html>"


def _feed_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/feed/", "", ""))


def _normalized_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _element_text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _error_summary(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    return str(exc).strip() or exc.__class__.__name__
