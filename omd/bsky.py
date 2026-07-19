#!/usr/bin/env python3
"""Convert a public Bluesky post URL to Markdown."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omd import __version__

BSKY_HOSTS = {
    "bsky.app",
    "www.bsky.app",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)
PUBLIC_API = "https://public.api.bsky.app"


@dataclass(frozen=True)
class BlueskyImage:
    url: str
    alt: str = ""


@dataclass(frozen=True)
class BlueskyReply:
    author: str
    text: str
    url: str
    like_count: int | None = None
    reply_count: int | None = None


@dataclass(frozen=True)
class BlueskyPost:
    source_url: str
    uri: str
    cid: str
    author: str
    handle: str
    text: str
    created_at: str
    like_count: int | None = None
    repost_count: int | None = None
    reply_count: int | None = None
    quote_count: int | None = None
    languages: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    images: list[BlueskyImage] = field(default_factory=list)
    embedded_records: list[str] = field(default_factory=list)
    replies: list[BlueskyReply] = field(default_factory=list)


def is_bsky_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in BSKY_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def parse_bsky_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 4 and parts[0] == "profile" and parts[2] == "post":
        return parts[1].lstrip("@"), parts[3]
    from omd import _events

    _events.fatal("url_invalid", f"not a Bluesky post URL: {url}")


def at_uri_for(url: str) -> tuple[str, str, str]:
    actor, rkey = parse_bsky_url(url)
    did = actor if actor.startswith("did:") else resolve_handle(actor)
    return f"at://{did}/app.bsky.feed.post/{rkey}", actor, rkey


def resolve_handle(handle: str, *, timeout: int = 30) -> str:
    query = urllib.parse.urlencode({"handle": handle.lstrip("@")})
    payload, _ = _fetch_json(f"{PUBLIC_API}/xrpc/com.atproto.identity.resolveHandle?{query}", timeout=timeout)
    if not isinstance(payload, dict) or not payload.get("did"):
        from omd import _events

        _events.fatal("fetch_failed", f"Bluesky could not resolve handle: {handle}")
    return str(payload["did"])


def fetch_thread(uri: str, *, timeout: int = 30, depth: int = 1, parent_height: int = 1) -> tuple[dict[str, Any], str]:
    query = urllib.parse.urlencode(
        {
            "uri": uri,
            "depth": str(depth),
            "parentHeight": str(parent_height),
        }
    )
    payload, final_url = _fetch_json(f"{PUBLIC_API}/xrpc/app.bsky.feed.getPostThread?{query}", timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def parse_thread(payload: dict[str, Any], source_url: str, *, max_replies: int = 20) -> BlueskyPost:
    thread = payload.get("thread") if isinstance(payload.get("thread"), dict) else {}
    post_view = thread.get("post") if isinstance(thread.get("post"), dict) else {}
    if not post_view:
        from omd import _events

        _events.fatal("parse_failed", "Bluesky response did not contain a post.")
    replies = _parse_replies(thread.get("replies"), max_replies=max_replies)
    return _parse_post_view(post_view, source_url, replies=replies)


def markdown_for_post(post: BlueskyPost) -> str:
    author = _format_author(post.author, post.handle)
    lines = [f"# Bluesky post by {author or post.handle}", "", f"- Source: {post.source_url}"]
    if post.uri:
        lines.append(f"- AT URI: {post.uri}")
    if post.cid:
        lines.append(f"- CID: {post.cid}")
    if author:
        lines.append(f"- Author: {author}")
    if post.created_at:
        lines.append(f"- Created: {post.created_at}")
    if post.languages:
        lines.append(f"- Languages: {', '.join(post.languages)}")
    if post.like_count is not None:
        lines.append(f"- Likes: {post.like_count}")
    if post.repost_count is not None:
        lines.append(f"- Reposts: {post.repost_count}")
    if post.reply_count is not None:
        lines.append(f"- Replies: {post.reply_count}")
    if post.quote_count is not None:
        lines.append(f"- Quotes: {post.quote_count}")

    lines.extend(["", "## Post", "", post.text.strip() or "_No text body._", ""])
    if post.links:
        lines.extend(["## Links", ""])
        for label, url in _dedupe_pairs(post.links):
            lines.append(f"- [{label or url}]({url})")
        lines.append("")
    if post.images:
        lines.extend(["## Images", ""])
        for image in post.images:
            if image.alt:
                lines.append(f"- ![{image.alt}]({image.url})")
            else:
                lines.append(f"- {image.url}")
        lines.append("")
    if post.embedded_records:
        lines.extend(["## Embedded records", ""])
        for uri in post.embedded_records:
            lines.append(f"- {uri}")
        lines.append("")
    if post.replies:
        lines.extend(["## Replies", ""])
        for reply in post.replies:
            meta = f"- {reply.author}"
            counts = []
            if reply.like_count is not None:
                counts.append(f"{reply.like_count} likes")
            if reply.reply_count is not None:
                counts.append(f"{reply.reply_count} replies")
            if counts:
                meta += f" ({', '.join(counts)})"
            if reply.url:
                meta += f" - {reply.url}"
            lines.extend([meta, _indent_body(reply.text, "  "), ""])
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_bsky_url(url):
        _events.fatal("url_invalid", f"not a Bluesky URL: {url}")
    _progress.info("Fetching Bluesky post")
    uri, _actor, _rkey = at_uri_for(url)
    payload, _final_url = fetch_thread(uri)
    post = parse_thread(payload, canonical_url(url))
    md = markdown_for_post(post)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def canonical_url(url: str) -> str:
    actor, rkey = parse_bsky_url(url)
    return f"https://bsky.app/profile/{actor}/post/{rkey}"


def _parse_post_view(post_view: dict[str, Any], source_url: str, *, replies: list[BlueskyReply]) -> BlueskyPost:
    author = post_view.get("author") if isinstance(post_view.get("author"), dict) else {}
    record = post_view.get("record") if isinstance(post_view.get("record"), dict) else {}
    embed = post_view.get("embed") if isinstance(post_view.get("embed"), dict) else {}
    return BlueskyPost(
        source_url=source_url,
        uri=str(post_view.get("uri") or ""),
        cid=str(post_view.get("cid") or ""),
        author=str(author.get("displayName") or ""),
        handle=str(author.get("handle") or ""),
        text=str(record.get("text") or ""),
        created_at=str(record.get("createdAt") or post_view.get("indexedAt") or ""),
        like_count=_int_or_none(post_view.get("likeCount")),
        repost_count=_int_or_none(post_view.get("repostCount")),
        reply_count=_int_or_none(post_view.get("replyCount")),
        quote_count=_int_or_none(post_view.get("quoteCount")),
        languages=[str(lang) for lang in record.get("langs") or []],
        links=_extract_links(record, embed),
        images=_extract_images(embed),
        embedded_records=_extract_embedded_records(embed),
        replies=replies,
    )


def _parse_replies(value: Any, *, max_replies: int) -> list[BlueskyReply]:
    if not isinstance(value, list):
        return []
    replies: list[BlueskyReply] = []
    for item in value:
        if len(replies) >= max_replies:
            break
        if not isinstance(item, dict):
            continue
        post_view = item.get("post") if isinstance(item.get("post"), dict) else {}
        if not post_view:
            continue
        author = post_view.get("author") if isinstance(post_view.get("author"), dict) else {}
        record = post_view.get("record") if isinstance(post_view.get("record"), dict) else {}
        replies.append(
            BlueskyReply(
                author=_format_author(
                    str(author.get("displayName") or ""),
                    str(author.get("handle") or ""),
                ),
                text=str(record.get("text") or "").strip(),
                url=_url_from_at_uri(str(post_view.get("uri") or ""), str(author.get("handle") or "")),
                like_count=_int_or_none(post_view.get("likeCount")),
                reply_count=_int_or_none(post_view.get("replyCount")),
            )
        )
    return replies


def _extract_links(record: dict[str, Any], embed: dict[str, Any]) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for facet in record.get("facets") or []:
        if not isinstance(facet, dict):
            continue
        for feature in facet.get("features") or []:
            if not isinstance(feature, dict):
                continue
            if feature.get("$type") == "app.bsky.richtext.facet#link" and feature.get("uri"):
                uri = str(feature.get("uri"))
                links.append((uri, uri))
            elif feature.get("$type") == "app.bsky.richtext.facet#tag" and feature.get("tag"):
                tag = str(feature.get("tag"))
                links.append((f"#{tag}", f"https://bsky.app/hashtag/{urllib.parse.quote(tag)}"))
    if embed.get("$type") == "app.bsky.embed.external#view":
        external = embed.get("external") if isinstance(embed.get("external"), dict) else {}
        uri = str(external.get("uri") or "")
        title = str(external.get("title") or uri)
        if uri:
            links.append((title, uri))
    return _dedupe_pairs(links)


def _extract_images(embed: dict[str, Any]) -> list[BlueskyImage]:
    images: list[BlueskyImage] = []
    items = embed.get("images") or embed.get("items") or []
    if not isinstance(items, list):
        return images
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("fullsize") or item.get("thumb") or item.get("thumbnail") or "")
        alt = str(item.get("alt") or "")
        if url:
            images.append(BlueskyImage(url=url, alt=alt))
    return images


def _extract_embedded_records(embed: dict[str, Any]) -> list[str]:
    records: list[str] = []
    record = embed.get("record") if isinstance(embed.get("record"), dict) else {}
    uri = record.get("uri")
    if uri:
        records.append(str(uri))
    return records


def _url_from_at_uri(uri: str, handle: str) -> str:
    parts = uri.split("/")
    if len(parts) < 5 or parts[-2] != "app.bsky.feed.post":
        return ""
    actor = handle or parts[2].removeprefix("at://")
    return f"https://bsky.app/profile/{actor}/post/{parts[-1]}"


def _fetch_json(url: str, *, timeout: int) -> tuple[Any, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
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
                f"Bluesky JSON is {content_length} bytes, above limit {limit}. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError(
                    f"Bluesky JSON exceeded limit {limit} bytes. "
                    f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                )
            chunks.append(chunk)
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(b"".join(chunks).decode(charset, errors="replace")), response.geturl()


def _format_author(name: str, handle: str) -> str:
    name = name.strip()
    handle = handle.strip().lstrip("@")
    if name and handle:
        return f"{name} (@{handle})"
    if handle:
        return f"@{handle}"
    return name


def _indent_body(body: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in body.splitlines())


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a public Bluesky post URL to Markdown.")
    parser.add_argument("url", help="Bluesky post URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
