#!/usr/bin/env python3
"""Convert a public Hacker News item URL to Markdown."""
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

HN_HOSTS = {
    "news.ycombinator.com",
    "www.news.ycombinator.com",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
ITEM_ID_RE = re.compile(r"^\d+$")
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)
API_ROOT = "https://hacker-news.firebaseio.com/v0"
DEFAULT_MAX_COMMENTS = 20
DEFAULT_MAX_DEPTH = 2


@dataclass(frozen=True)
class HNComment:
    item_id: int
    author: str
    text: str
    created_at: str
    deleted: bool = False
    dead: bool = False
    children: list["HNComment"] = field(default_factory=list)


@dataclass(frozen=True)
class HNItem:
    item_id: int
    item_type: str
    source_url: str
    api_url: str
    title: str = ""
    author: str = ""
    text: str = ""
    url: str = ""
    score: int | None = None
    created_at: str = ""
    descendants: int | None = None
    parent: int | None = None
    deleted: bool = False
    dead: bool = False
    comments: list[HNComment] = field(default_factory=list)
    fetched_comments: int = 0
    skipped_comment_ids: int = 0


def is_hn_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in HN_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def item_id_from_url(url: str) -> int:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    item_id = (query.get("id") or [""])[0]
    if parsed.path.strip("/") == "item" and ITEM_ID_RE.match(item_id):
        return int(item_id)
    from omd import _events

    _events.fatal("url_invalid", f"not a Hacker News item URL: {url}")


def canonical_url(item_id: int) -> str:
    return f"https://news.ycombinator.com/item?id={item_id}"


def api_url_for(item_id: int) -> str:
    return f"{API_ROOT}/item/{item_id}.json"


def fetch_item(item_id: int, *, timeout: int = 30) -> tuple[dict[str, Any], str]:
    payload, final_url = _fetch_json(api_url_for(item_id), timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def fetch_hn_item(
    item_id: int,
    *,
    max_comments: int = DEFAULT_MAX_COMMENTS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    timeout: int = 30,
) -> HNItem:
    payload, final_url = fetch_item(item_id, timeout=timeout)
    if not payload:
        from omd import _events

        _events.fatal("fetch_failed", f"Hacker News returned no item data for id {item_id}.")
    counter = _CommentCounter(max_comments=max_comments)
    comments, skipped = _fetch_comments(
        payload.get("kids"),
        counter=counter,
        depth=1,
        max_depth=max_depth,
        timeout=timeout,
    )
    return parse_item(
        payload,
        source_url=canonical_url(item_id),
        api_url=final_url,
        comments=comments,
        fetched_comments=counter.count,
        skipped_comment_ids=skipped,
    )


def parse_item(
    payload: dict[str, Any],
    *,
    source_url: str,
    api_url: str,
    comments: list[HNComment] | None = None,
    fetched_comments: int = 0,
    skipped_comment_ids: int = 0,
) -> HNItem:
    return HNItem(
        item_id=int(payload.get("id") or 0),
        item_type=str(payload.get("type") or "item"),
        source_url=source_url,
        api_url=api_url,
        title=html_to_text(str(payload.get("title") or "")),
        author=str(payload.get("by") or ""),
        text=html_to_text(str(payload.get("text") or "")),
        url=str(payload.get("url") or ""),
        score=_int_or_none(payload.get("score")),
        created_at=_format_time(payload.get("time")),
        descendants=_int_or_none(payload.get("descendants")),
        parent=_int_or_none(payload.get("parent")),
        deleted=bool(payload.get("deleted")),
        dead=bool(payload.get("dead")),
        comments=comments or [],
        fetched_comments=fetched_comments,
        skipped_comment_ids=skipped_comment_ids,
    )


def markdown_for_item(item: HNItem) -> str:
    title = item.title or f"Hacker News {item.item_type} {item.item_id}"
    lines = [f"# Hacker News {item.item_type}: {title}", "", f"- Source: {item.source_url}"]
    if item.api_url:
        lines.append(f"- API: {item.api_url}")
    if item.author:
        lines.append(f"- Author: {item.author}")
    if item.created_at:
        lines.append(f"- Posted: {item.created_at}")
    if item.score is not None:
        lines.append(f"- Score: {item.score}")
    if item.descendants is not None:
        lines.append(f"- Comments: {item.descendants}")
    if item.parent is not None:
        lines.append(f"- Parent: {canonical_url(item.parent)}")
    if item.deleted:
        lines.append("- Deleted: true")
    if item.dead:
        lines.append("- Dead: true")
    if item.url:
        lines.append(f"- Link: {item.url}")

    if item.text:
        lines.extend(["", "## Text", "", item.text.strip(), ""])
    if item.url:
        lines.extend(["", "## Link", "", f"- {item.url}", ""])
    if item.comments:
        lines.extend(["## Comments", ""])
        for comment in item.comments:
            _append_comment(lines, comment, depth=0)
        if item.skipped_comment_ids:
            lines.append(f"_Skipped {item.skipped_comment_ids} comment ids because of the configured limit._")
            lines.append("")
    elif item.descendants:
        lines.extend(["## Comments", "", "_No comments fetched._", ""])
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_hn_url(url):
        _events.fatal("url_invalid", f"not a Hacker News URL: {url}")
    item_id = item_id_from_url(url)
    _progress.info("Fetching Hacker News item")
    item = fetch_hn_item(item_id)
    md = markdown_for_item(item)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def html_to_text(raw_html: str) -> str:
    parser = _HNHTMLParser()
    parser.feed(raw_html)
    parser.close()
    return _collapse_blank_lines(html.unescape(parser.text()).strip())


def _fetch_comments(
    kids: Any,
    *,
    counter: "_CommentCounter",
    depth: int,
    max_depth: int,
    timeout: int,
) -> tuple[list[HNComment], int]:
    if not isinstance(kids, list) or depth > max_depth or counter.exhausted:
        return [], len(kids) if isinstance(kids, list) else 0
    comments: list[HNComment] = []
    skipped = 0
    for kid in kids:
        if counter.exhausted:
            skipped += 1
            continue
        try:
            kid_id = int(kid)
        except (TypeError, ValueError):
            skipped += 1
            continue
        payload, _ = fetch_item(kid_id, timeout=timeout)
        if not payload:
            skipped += 1
            continue
        counter.count += 1
        child_comments, child_skipped = _fetch_comments(
            payload.get("kids"),
            counter=counter,
            depth=depth + 1,
            max_depth=max_depth,
            timeout=timeout,
        )
        skipped += child_skipped
        comments.append(_parse_comment(payload, child_comments))
    return comments, skipped


def _parse_comment(payload: dict[str, Any], children: list[HNComment]) -> HNComment:
    return HNComment(
        item_id=int(payload.get("id") or 0),
        author=str(payload.get("by") or ""),
        text=html_to_text(str(payload.get("text") or "")),
        created_at=_format_time(payload.get("time")),
        deleted=bool(payload.get("deleted")),
        dead=bool(payload.get("dead")),
        children=children,
    )


def _append_comment(lines: list[str], comment: HNComment, *, depth: int) -> None:
    prefix = "  " * depth
    label = comment.author or f"item {comment.item_id}"
    meta = f"{prefix}- {label} - {canonical_url(comment.item_id)}"
    details = []
    if comment.created_at:
        details.append(comment.created_at)
    if comment.deleted:
        details.append("deleted")
    if comment.dead:
        details.append("dead")
    if details:
        meta += f" ({', '.join(details)})"
    lines.append(meta)
    body = comment.text.strip() or "_No text body._"
    lines.append(_indent_body(body, prefix + "  "))
    lines.append("")
    for child in comment.children:
        _append_comment(lines, child, depth=depth + 1)


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
                f"Hacker News JSON is {content_length} bytes, above limit {limit}. "
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
                    f"Hacker News JSON exceeded limit {limit} bytes. "
                    f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                )
            chunks.append(chunk)
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(b"".join(chunks).decode(charset, errors="replace")), response.geturl()


def _format_time(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    return _dt.datetime.fromtimestamp(timestamp, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _indent_body(body: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in body.splitlines())


def _collapse_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class _CommentCounter:
    def __init__(self, *, max_comments: int) -> None:
        self.max_comments = max(0, max_comments)
        self.count = 0

    @property
    def exhausted(self) -> bool:
        return self.count >= self.max_comments


class _HNHTMLParser(HTMLParser):
    PARAGRAPH_TAGS = {"p", "div", "blockquote"}
    LINE_TAGS = {"br", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.LINE_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.PARAGRAPH_TAGS:
            self._paragraph()
        elif tag in self.LINE_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def _paragraph(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n\n"):
            self._newline()
            self.parts.append("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a public Hacker News item URL to Markdown.")
    parser.add_argument("url", help="Hacker News item URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
