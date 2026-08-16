#!/usr/bin/env python3
"""Convert a public X/Twitter post URL to Markdown."""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from omd import __version__

X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
STATUS_RE = re.compile(r"/(?:i/web/)?status(?:es)?/(\d+)(?:\b|/)?", re.IGNORECASE)
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)


@dataclass(frozen=True)
class XPost:
    post_id: str
    source_url: str
    author_name: str = ""
    handle: str = ""
    text: str = ""
    created_at: str = ""
    likes: int | None = None
    reposts: int | None = None
    replies: int | None = None
    quotes: int | None = None
    links: list[tuple[str, str]] = field(default_factory=list)
    media: list[tuple[str, str]] = field(default_factory=list)
    quoted_text: str = ""
    quoted_author: str = ""


def is_x_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in X_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def post_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    match = STATUS_RE.search(parsed.path)
    if not match:
        from omd import _events

        _events.fatal("url_invalid", f"not an X/Twitter post URL: {url}")
    return match.group(1)


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    post_id = post_id_from_url(url)
    parts = [p for p in parsed.path.split("/") if p]
    handle = ""
    if len(parts) >= 3 and parts[0].lower() != "i" and parts[1].lower() in {"status", "statuses"}:
        handle = parts[0]
    if handle:
        return f"https://x.com/{handle}/status/{post_id}"
    return f"https://x.com/i/web/status/{post_id}"


def fetch_syndication(post_id: str, *, timeout: int = 30) -> tuple[dict[str, Any], str]:
    url = f"https://cdn.syndication.twimg.com/tweet-result?id={post_id}&lang=en"
    payload, final_url = _fetch_json(url, timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def fetch_oembed(url: str, *, timeout: int = 30) -> tuple[dict[str, Any], str]:
    query = urllib.parse.urlencode(
        {
            "url": canonical_url(url),
            "omit_script": "1",
            "dnt": "1",
        }
    )
    payload, final_url = _fetch_json(f"https://publish.twitter.com/oembed?{query}", timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def fetch_page(url: str, *, timeout: int = 30) -> tuple[str, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        canonical_url(url),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
    )
    limit = max_download_bytes()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        try:
            content_length = int(length or "0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            raise ValueError(
                f"X/Twitter page is {content_length} bytes, above limit {limit}. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(
                f"X/Twitter page exceeded limit {limit} bytes. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        charset = response.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace"), response.geturl()


def parse_syndication(payload: dict[str, Any], source_url: str) -> XPost | None:
    text = _primary_text(payload)
    if not text:
        return None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    quoted = payload.get("quoted_tweet") if isinstance(payload.get("quoted_tweet"), dict) else {}
    quoted_user = quoted.get("user") if isinstance(quoted.get("user"), dict) else {}
    post_id = str(payload.get("id_str") or payload.get("id") or post_id_from_url(source_url))
    return XPost(
        post_id=post_id,
        source_url=canonical_url(source_url),
        author_name=str(user.get("name") or ""),
        handle=str(user.get("screen_name") or ""),
        text=text,
        created_at=_format_created_at(payload.get("created_at")),
        likes=_int_or_none(payload.get("favorite_count")),
        reposts=_int_or_none(payload.get("retweet_count")),
        replies=_int_or_none(payload.get("conversation_count")),
        quotes=_int_or_none(payload.get("quote_count")),
        links=_extract_links(payload),
        media=_extract_media(payload),
        quoted_text=_clean_text(str(quoted.get("text") or "")),
        quoted_author=_format_author(
            str(quoted_user.get("name") or ""),
            str(quoted_user.get("screen_name") or ""),
        ),
    )


def parse_page(markup: str, source_url: str) -> XPost | None:
    text = _page_primary_text(markup)
    if not text:
        return None
    author_name, handle = _page_author(markup, source_url)
    counts = _page_counts(markup)
    post_id = post_id_from_url(source_url)
    return XPost(
        post_id=post_id,
        source_url=canonical_url(source_url),
        author_name=author_name,
        handle=handle,
        text=text,
        created_at=_page_created_at(markup),
        likes=counts.get("favorite_count"),
        reposts=counts.get("retweet_count"),
        replies=counts.get("reply_count"),
        quotes=counts.get("quote_count"),
    )


def parse_oembed(payload: dict[str, Any], source_url: str) -> XPost | None:
    parser = _OEmbedTweetParser()
    parser.feed(str(payload.get("html") or ""))
    parser.close()
    text = _clean_text(parser.post_text or parser.all_text)
    if not text:
        return None
    author_name = str(payload.get("author_name") or "")
    handle = parser.handle
    if not handle:
        author_url = str(payload.get("author_url") or "")
        handle = urllib.parse.urlparse(author_url).path.strip("/")
    return XPost(
        post_id=post_id_from_url(source_url),
        source_url=str(payload.get("url") or canonical_url(source_url)),
        author_name=author_name,
        handle=handle,
        text=text,
        created_at=parser.created_at,
        links=parser.links,
    )


def markdown_for_post(post: XPost) -> str:
    author = _format_author(post.author_name, post.handle) or post.post_id
    lines = [f"# X post by {author}", "", f"- Source: {post.source_url}"]
    if post.author_name or post.handle:
        lines.append(f"- Author: {author}")
    if post.created_at:
        lines.append(f"- Created: {post.created_at}")
    if post.likes is not None:
        lines.append(f"- Likes: {post.likes}")
    if post.reposts is not None:
        lines.append(f"- Reposts: {post.reposts}")
    if post.replies is not None:
        lines.append(f"- Replies: {post.replies}")
    if post.quotes is not None:
        lines.append(f"- Quotes: {post.quotes}")
    lines.extend(["", "## Post", "", post.text.strip(), ""])
    if post.links:
        lines.extend(["## Links", ""])
        for label, url in _dedupe_pairs(post.links):
            if label and label != url:
                lines.append(f"- [{label}]({url})")
            else:
                lines.append(f"- {url}")
        lines.append("")
    if post.media:
        lines.extend(["## Media", ""])
        for media_type, url in _dedupe_pairs(post.media):
            label = media_type or "media"
            lines.append(f"- {label}: {url}")
        lines.append("")
    if post.quoted_text:
        quoted_author = f" by {post.quoted_author}" if post.quoted_author else ""
        lines.extend(["## Quoted post", "", f"> {post.quoted_text.strip()}", ""])
        if quoted_author:
            lines.append(f"- Quoted author: {post.quoted_author}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_x_url(url):
        _events.fatal("url_invalid", f"not an X/Twitter URL: {url}")
    post_id = post_id_from_url(url)
    _progress.info("Fetching X post")
    post = None
    try:
        syndication_payload, _ = fetch_syndication(post_id)
        if syndication_payload:
            post = parse_syndication(syndication_payload, url)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        _progress.warn("X syndication fetch failed; trying public page fallback")
    if post is None or _looks_truncated(post.text):
        try:
            markup, _ = fetch_page(url)
        except Exception:
            markup = ""
        if markup:
            page_post = parse_page(markup, url)
            if page_post and (post is None or len(page_post.text) > len(post.text)):
                post = page_post
    if post is None or _looks_truncated(post.text):
        try:
            oembed_payload, _ = fetch_oembed(url)
            if oembed_payload:
                oembed_post = parse_oembed(oembed_payload, url)
                if oembed_post and (post is None or len(oembed_post.text) > len(post.text)):
                    post = oembed_post
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
    if post is None:
        _events.fatal(
            "fetch_failed",
            "X/Twitter returned no public post text. The post may be deleted, private, age-restricted, or login-gated.",
        )
    md = markdown_for_post(post)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def _fetch_json(url: str, *, timeout: int) -> tuple[Any, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    limit = max_download_bytes()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        try:
            content_length = int(length or "0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            raise ValueError(
                f"X/Twitter JSON is {content_length} bytes, above limit {limit}. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(
                f"X/Twitter JSON exceeded limit {limit} bytes. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(data.decode(charset, errors="replace")), response.geturl()


def _extract_links(payload: dict[str, Any]) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for entities in _entity_sources(payload):
        for item in entities.get("urls") or []:
            if not isinstance(item, dict):
                continue
            expanded = str(item.get("expanded_url") or item.get("url") or "")
            display = str(item.get("display_url") or expanded)
            if expanded:
                links.append((display, expanded))
    return links


def _extract_media(payload: dict[str, Any]) -> list[tuple[str, str]]:
    media: list[tuple[str, str]] = []
    for item in payload.get("mediaDetails") or payload.get("media_details") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("media_url_https") or item.get("url") or "")
        if url:
            media.append((str(item.get("type") or "media"), url))
    return media


def _page_primary_text(markup: str) -> str:
    for pattern in (
        r'__typename:"NoteTweet".{0,500}?text:"((?:\\.|[^"\\])*)"',
        r'\bfull_text:"((?:\\.|[^"\\])*)"',
    ):
        match = re.search(pattern, markup, flags=re.DOTALL)
        if match:
            text = _clean_text(_decode_js_string(match.group(1)))
            if text:
                return text
    return ""


def _page_author(markup: str, source_url: str) -> tuple[str, str]:
    author_name = ""
    handle = ""
    match = re.search(
        r'relevantPerson:\s*\{[^{}]*?name:"((?:\\.|[^"\\])*)",screenName:"((?:\\.|[^"\\])*)"',
        markup,
        flags=re.DOTALL,
    )
    if match:
        author_name = _decode_js_string(match.group(1))
        handle = _decode_js_string(match.group(2))
    if not author_name:
        title_match = re.search(
            r'<meta\s+name="title"\s+content="([^"]+?)\s+\(@([^")]+)\)\s+on X"',
            markup,
        )
        if title_match:
            author_name = html.unescape(title_match.group(1))
            handle = handle or html.unescape(title_match.group(2))
    if not handle:
        meta_handle = re.search(r'<meta\s+name="twitter:creator"\s+content="@?([^"]+)"', markup)
        if meta_handle:
            handle = html.unescape(meta_handle.group(1))
    if not handle:
        parts = [p for p in urllib.parse.urlparse(source_url).path.split("/") if p]
        if len(parts) >= 3 and parts[1].lower() in {"status", "statuses"}:
            handle = parts[0]
    return author_name.strip(), handle.strip().lstrip("@")


def _page_created_at(markup: str) -> str:
    match = re.search(r"\bcreated_at_ms:(\d+)", markup)
    if not match:
        return ""
    return _format_created_at(int(match.group(1)) / 1000)


def _page_counts(markup: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("reply_count", "favorite_count", "retweet_count", "quote_count"):
        match = re.search(rf"\b{key}:(\d+)", markup)
        if match:
            counts[key] = int(match.group(1))
    return counts


def _decode_js_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace(r"\/", "/").replace(r"\n", "\n").replace(r"\"", '"').replace(r"\\", "\\")


def _looks_truncated(text: str) -> bool:
    stripped = text.rstrip()
    return stripped.endswith("…") or stripped.endswith("...")


def _primary_text(payload: dict[str, Any]) -> str:
    candidates = [
        _nested_text(payload, ("note_tweet", "text")),
        _nested_text(payload, ("note_tweet", "full_text")),
        _nested_text(payload, ("note_tweet", "note_tweet_results", "result", "text")),
        str(payload.get("full_text") or ""),
        str(payload.get("text") or ""),
    ]
    cleaned = [_clean_text(value) for value in candidates if value]
    if not cleaned:
        return ""
    return max(cleaned, key=len)


def _nested_text(payload: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "")


def _entity_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for candidate in (
        payload.get("entities"),
        _nested_dict(payload, ("note_tweet", "entities")),
        _nested_dict(payload, ("note_tweet", "note_tweet_results", "result", "entity_set")),
        _nested_dict(payload, ("note_tweet", "note_tweet_results", "result", "entities")),
    ):
        if isinstance(candidate, dict):
            sources.append(candidate)
    return sources


def _nested_dict(payload: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


def _format_created_at(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _format_author(name: str, handle: str) -> str:
    name = name.strip()
    handle = handle.strip().lstrip("@")
    if name and handle:
        return f"{name} (@{handle})"
    if handle:
        return f"@{handle}"
    return name


def _clean_text(value: str) -> str:
    value = html.unescape(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_pairs(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, url in items:
        pair = (label, url)
        if pair not in seen:
            out.append(pair)
            seen.add(pair)
    return out


class _OEmbedTweetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.post_text = ""
        self.all_text = ""
        self.created_at = ""
        self.handle = ""
        self.links: list[tuple[str, str]] = []
        self._in_p = False
        self._in_a = False
        self._current_href = ""
        self._current_link_text: list[str] = []
        self._post_parts: list[str] = []
        self._all_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "p":
            self._in_p = True
        if tag.lower() == "br" and self._in_p:
            self._post_parts.append("\n")
            self._all_parts.append("\n")
        if tag.lower() == "a":
            self._in_a = True
            self._current_href = attr.get("href", "")
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "p":
            self._in_p = False
            self.post_text = "".join(self._post_parts).strip()
        if tag == "a" and self._in_a:
            text = "".join(self._current_link_text).strip()
            href = self._current_href
            if href:
                clean_href = href.split("?", 1)[0]
                is_status_link = bool(STATUS_RE.search(urllib.parse.urlparse(clean_href).path))
                if is_status_link and text:
                    self.created_at = text
                elif not is_status_link:
                    self.links.append((text or clean_href, clean_href))
            self._in_a = False
            self._current_href = ""
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._all_parts.append(data)
        self.all_text = "".join(self._all_parts).strip()
        if self._in_p:
            self._post_parts.append(data)
        if self._in_a:
            self._current_link_text.append(data)
        handle_match = re.search(r"\(@([A-Za-z0-9_]{1,15})\)", data)
        if handle_match:
            self.handle = handle_match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a public X/Twitter post URL to Markdown.")
    parser.add_argument("url", help="X/Twitter post URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
