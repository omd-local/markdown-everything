#!/usr/bin/env python3
"""Convert a public Threads post URL to Markdown."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from omd import __version__

THREADS_HOSTS = {
    "threads.com",
    "www.threads.com",
    "threads.net",
    "www.threads.net",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
POST_RE = re.compile(r"^[A-Za-z0-9_-]+$")
OEMBED_ENDPOINT = "https://graph.threads.net/v1.0/oembed"
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)


@dataclass(frozen=True)
class ThreadsEmbed:
    provider_name: str = ""
    provider_url: str = ""
    embed_type: str = ""
    width: int | None = None


@dataclass(frozen=True)
class ThreadsPost:
    post_id: str
    source_url: str
    page_url: str
    author_name: str = ""
    handle: str = ""
    text: str = ""
    title: str = ""
    image: str = ""
    embed: ThreadsEmbed | None = None


def is_threads_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in THREADS_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def parse_threads_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    handle = ""
    post_id = ""
    if len(parts) >= 3 and parts[0].startswith("@") and parts[1] == "post":
        handle, post_id = parts[0].lstrip("@"), parts[2]
    elif len(parts) >= 2 and parts[0] == "t":
        post_id = parts[1]
    if POST_RE.match(post_id or ""):
        return handle, post_id
    from omd import _events

    _events.fatal("url_invalid", f"not a public Threads post URL: {url}")


def canonical_url(handle: str, post_id: str) -> str:
    if handle:
        return f"https://www.threads.com/@{handle}/post/{post_id}"
    return f"https://www.threads.com/t/{post_id}"


def fetch_page(url: str, *, timeout: int = 30) -> tuple[str, str]:
    return _fetch_text(url, timeout=timeout)


def fetch_oembed(url: str, *, timeout: int = 30) -> tuple[dict[str, Any], str]:
    query = urllib.parse.urlencode({"url": url})
    payload, final_url = _fetch_json(f"{OEMBED_ENDPOINT}?{query}", timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def parse_page(html_text: str, *, source_url: str, page_url: str) -> ThreadsPost:
    parser = _MetadataParser()
    parser.feed(html_text)
    parser.close()
    meta = parser.meta
    canonical = parser.canonical or str(meta.get("og:url") or page_url)
    handle, post_id = parse_threads_url(canonical or source_url)
    title = _clean_text(str(meta.get("og:title") or meta.get("twitter:title") or ""))
    text = _clean_text(str(meta.get("og:description") or meta.get("twitter:description") or ""))
    author_name, meta_handle = _author_from_title(title)
    return ThreadsPost(
        post_id=post_id,
        source_url=source_url,
        page_url=canonical or page_url,
        author_name=author_name,
        handle=handle or meta_handle,
        text=text,
        title=title,
        image=str(meta.get("og:image") or meta.get("twitter:image") or ""),
    )


def parse_oembed(payload: dict[str, Any]) -> ThreadsEmbed | None:
    if not payload:
        return None
    width = _int_or_none(payload.get("width"))
    return ThreadsEmbed(
        provider_name=str(payload.get("provider_name") or ""),
        provider_url=str(payload.get("provider_url") or ""),
        embed_type=str(payload.get("type") or ""),
        width=width,
    )


def markdown_for_post(post: ThreadsPost) -> str:
    author = _format_author(post.author_name, post.handle) or post.post_id
    lines = [f"# Threads post by {author}", "", f"- Source: {post.source_url}"]
    if post.page_url and post.page_url != post.source_url:
        lines.append(f"- Canonical: {post.page_url}")
    if post.author_name or post.handle:
        lines.append(f"- Author: {author}")
    lines.append(f"- Post ID: {post.post_id}")
    if post.title:
        lines.append(f"- Page title: {post.title}")
    if post.embed:
        if post.embed.provider_name:
            lines.append(f"- Provider: {post.embed.provider_name}")
        if post.embed.embed_type:
            lines.append(f"- Embed type: {post.embed.embed_type}")
        if post.embed.width is not None:
            lines.append(f"- Embed width: {post.embed.width}")
    lines.extend(["", "## Post", "", post.text.strip() or "_No text body._", ""])
    if post.image:
        lines.extend(["## Media", "", f"- {post.image}", ""])
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_threads_url(url):
        _events.fatal("url_invalid", f"not a Threads URL: {url}")
    handle, post_id = parse_threads_url(url)
    source_url = canonical_url(handle, post_id)
    _progress.info("Fetching Threads post")
    body, final_url = fetch_page(source_url)
    post = parse_page(body, source_url=source_url, page_url=final_url)
    if not post.text:
        _events.fatal(
            "parse_failed",
            "Threads public page did not expose post text. The post may be private, deleted, or login-gated.",
        )
    try:
        payload, _ = fetch_oembed(post.page_url or source_url)
    except Exception:
        payload = {}
    embed = parse_oembed(payload)
    if embed:
        post = ThreadsPost(
            post_id=post.post_id,
            source_url=post.source_url,
            page_url=post.page_url,
            author_name=post.author_name,
            handle=post.handle,
            text=post.text,
            title=post.title,
            image=post.image,
            embed=embed,
        )
    md = markdown_for_post(post)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def _fetch_text(url: str, *, timeout: int) -> tuple[str, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    limit = max_download_bytes()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = _read_limited(response, limit, "Threads page", MAX_DOWNLOAD_MB_ENV)
        charset = response.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace"), response.geturl()


def _fetch_json(url: str, *, timeout: int) -> tuple[Any, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "Connection": "close",
        },
    )
    limit = max_download_bytes()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = _read_limited(response, limit, "Threads oEmbed JSON", MAX_DOWNLOAD_MB_ENV)
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(data.decode(charset, errors="replace")), response.geturl()


def _read_limited(response: Any, limit: int, label: str, env_name: str) -> bytes:
    length = response.headers.get("Content-Length")
    try:
        content_length = int(length or "0")
    except ValueError:
        content_length = 0
    if content_length > limit:
        raise ValueError(
            f"{label} is {content_length} bytes, above limit {limit}. "
            f"Set {env_name} to override for trusted workflows."
        )
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(
            f"{label} exceeded limit {limit} bytes. "
            f"Set {env_name} to override for trusted workflows."
        )
    return data


def _clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _author_from_title(title: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)\s+\(@([^)]+)\)\s+on Threads$", title)
    if not match:
        return "", ""
    return _clean_text(match.group(1)), _clean_text(match.group(2))


def _format_author(name: str, handle: str) -> str:
    if name and handle:
        return f"{name} (@{handle})"
    if handle:
        return f"@{handle}"
    return name


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, str | None]]) -> None:
        attrs = {name.lower(): value or "" for name, value in attrs_raw}
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            content = attrs.get("content")
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link":
            rel = attrs.get("rel", "")
            if "canonical" in rel.split() and attrs.get("href"):
                self.canonical = attrs["href"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a public Threads post to Markdown")
    parser.add_argument("url", help="Threads post URL or share blob")
    parser.add_argument("-o", "--output", help="Output .md path")
    args = parser.parse_args(argv)
    return convert_url(args.url, Path(args.output) if args.output else None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
