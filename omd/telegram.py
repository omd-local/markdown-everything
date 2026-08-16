#!/usr/bin/env python3
"""Convert a public Telegram channel post URL to Markdown."""
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from omd import __version__

TELEGRAM_HOSTS = {
    "t.me",
    "www.t.me",
    "telegram.me",
    "www.telegram.me",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MESSAGE_ID_RE = re.compile(r"^\d+$")
CSS_URL_RE = re.compile(r"url\((['\"]?)(.*?)\1\)")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)


@dataclass(frozen=True)
class TelegramPreview:
    url: str = ""
    site_name: str = ""
    title: str = ""
    description: str = ""


@dataclass(frozen=True)
class TelegramPost:
    source_url: str
    page_url: str
    channel: str
    message_id: int
    author: str = ""
    text: str = ""
    posted_at: str = ""
    views: str = ""
    links: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)
    preview: TelegramPreview | None = None


def is_telegram_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in TELEGRAM_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def parse_telegram_url(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    channel = ""
    message_id = ""
    if len(parts) >= 3 and parts[0] == "s":
        channel, message_id = parts[1], parts[2]
    elif len(parts) >= 2:
        channel, message_id = parts[0], parts[1]
    if CHANNEL_RE.match(channel or "") and MESSAGE_ID_RE.match(message_id or ""):
        return channel, int(message_id)
    from omd import _events

    _events.fatal("url_invalid", f"not a public Telegram channel post URL: {url}")


def canonical_url(channel: str, message_id: int) -> str:
    return f"https://t.me/{channel}/{message_id}"


def page_url_for(channel: str, message_id: int) -> str:
    return f"https://t.me/s/{channel}/{message_id}"


def fetch_page(channel: str, message_id: int, *, timeout: int = 30) -> tuple[str, str]:
    return _fetch_text(page_url_for(channel, message_id), timeout=timeout)


def parse_page(html_text: str, *, channel: str, message_id: int, source_url: str, page_url: str) -> TelegramPost:
    parser = _TelegramPageParser(f"{channel}/{message_id}")
    parser.feed(html_text)
    parser.close()
    if not parser.found:
        from omd import _events

        _events.fatal(
            "parse_failed",
            "Telegram public page did not contain the requested post. It may be private, deleted, or login-gated.",
        )
    preview = TelegramPreview(
        url=parser.preview_url,
        site_name=_clean_text(parser.preview_site_name),
        title=_clean_text(parser.preview_title),
        description=_clean_text(parser.preview_description),
    )
    return TelegramPost(
        source_url=source_url,
        page_url=page_url,
        channel=channel,
        message_id=message_id,
        author=_clean_text(parser.author),
        text=_clean_text(parser.text),
        posted_at=parser.posted_at,
        views=_clean_text(parser.views),
        links=_dedupe([link for link in parser.links if link != source_url]),
        media=_dedupe(parser.media),
        reactions=_dedupe([_clean_text(reaction) for reaction in parser.reactions if _clean_text(reaction)]),
        preview=preview if any((preview.url, preview.site_name, preview.title, preview.description)) else None,
    )


def markdown_for_post(post: TelegramPost) -> str:
    author = post.author or f"@{post.channel}"
    lines = [f"# Telegram post by {author}", "", f"- Source: {post.source_url}"]
    lines.append(f"- Public page: {post.page_url}")
    lines.append(f"- Channel: @{post.channel}")
    lines.append(f"- Message ID: {post.message_id}")
    if post.author:
        lines.append(f"- Author: {post.author}")
    if post.posted_at:
        lines.append(f"- Posted: {post.posted_at}")
    if post.views:
        lines.append(f"- Views: {post.views}")

    lines.extend(["", "## Post", "", post.text.strip() or "_No text body._", ""])
    if post.links:
        lines.extend(["## Links", ""])
        for link in post.links:
            lines.append(f"- {link}")
        lines.append("")
    if post.media:
        lines.extend(["## Media", ""])
        for media_url in post.media:
            lines.append(f"- {media_url}")
        lines.append("")
    if post.preview:
        lines.extend(["## Link Preview", ""])
        label = post.preview.title or post.preview.url
        if post.preview.url:
            lines.append(f"- [{label}]({post.preview.url})")
        elif label:
            lines.append(f"- {label}")
        if post.preview.site_name:
            lines.append(f"- Site: {post.preview.site_name}")
        if post.preview.description:
            lines.append(_indent_body(post.preview.description, "  "))
        lines.append("")
    if post.reactions:
        lines.extend(["## Reactions", ""])
        for reaction in post.reactions:
            lines.append(f"- {reaction}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_telegram_url(url):
        _events.fatal("url_invalid", f"not a Telegram URL: {url}")
    channel, message_id = parse_telegram_url(url)
    _progress.info("Fetching Telegram post")
    body, final_url = fetch_page(channel, message_id)
    post = parse_page(
        body,
        channel=channel,
        message_id=message_id,
        source_url=canonical_url(channel, message_id),
        page_url=final_url,
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
        length = response.headers.get("Content-Length")
        try:
            content_length = int(length or "0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            raise ValueError(
                f"Telegram page is {content_length} bytes, above limit {limit}. "
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
                    f"Telegram page exceeded limit {limit} bytes. "
                    f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                )
            chunks.append(chunk)
        charset = response.headers.get_content_charset() or "utf-8"
        return b"".join(chunks).decode(charset, errors="replace"), response.geturl()


def _clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _absolute_url(value: str) -> str:
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://t.me" + value
    return value


def _style_urls(style: str) -> list[str]:
    return [_absolute_url(match.group(2)) for match in CSS_URL_RE.finditer(style or "") if match.group(2)]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _indent_body(body: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in body.splitlines())


def _classes(attrs: dict[str, str]) -> set[str]:
    return set((attrs.get("class") or "").split())


def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name: value or "" for name, value in attrs}


class _TelegramPageParser(HTMLParser):
    def __init__(self, target_post: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target_post = target_post
        self.found = False
        self.in_message = False
        self.message_depth = 0
        self.author = ""
        self.text = ""
        self.views = ""
        self.posted_at = ""
        self.links: list[str] = []
        self.media: list[str] = []
        self.reactions: list[str] = []
        self.preview_url = ""
        self.preview_site_name = ""
        self.preview_title = ""
        self.preview_description = ""
        self._captures: dict[str, int] = {}
        self._reaction_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, str | None]]) -> None:
        attrs = _attrs(attrs_raw)
        classes = _classes(attrs)
        if not self.in_message:
            if tag == "div" and "tgme_widget_message" in classes and "js-widget_message" in classes:
                if attrs.get("data-post") == self.target_post:
                    self.found = True
                    self.in_message = True
                    self.message_depth = 1
            return

        if tag not in VOID_TAGS:
            self.message_depth += 1
            for key in list(self._captures):
                self._captures[key] += 1

        if tag == "br" and "text" in self._captures:
            self.text += "\n"

        href = _absolute_url(attrs.get("href") or "")
        if href and "text" in self._captures and not href.startswith(("tg://", "javascript:")):
            self.links.append(href)

        if tag == "time" and attrs.get("datetime") and not self.posted_at:
            self.posted_at = attrs["datetime"]
        if "tgme_widget_message_owner_name" in classes:
            self._captures["author"] = 1
        if "tgme_widget_message_user_photo" in classes:
            self._captures["user_photo"] = 1
        if "tgme_widget_message_text" in classes and "js-message_text" in classes:
            self._captures["text"] = 1
        if "tgme_widget_message_views" in classes:
            self._captures["views"] = 1
        if "tgme_reaction" in classes:
            self._captures["reaction"] = 1
            self._reaction_parts = []
        if "tgme_widget_message_link_preview" in classes:
            if href:
                self.preview_url = href
        if "link_preview_site_name" in classes:
            self._captures["preview_site_name"] = 1
        if "link_preview_title" in classes:
            self._captures["preview_title"] = 1
        if "link_preview_description" in classes:
            self._captures["preview_description"] = 1

        if tag == "img":
            src = _absolute_url(attrs.get("src") or "")
            if src and "emoji" not in src and "user_photo" not in self._captures:
                self.media.append(src)
        for media_url in _style_urls(attrs.get("style") or ""):
            if "emoji" not in media_url and "user_photo" not in self._captures:
                self.media.append(media_url)
        if tag == "source":
            src = _absolute_url(attrs.get("src") or "")
            if src:
                self.media.append(src)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_message:
            return
        if tag in VOID_TAGS:
            return
        for key in list(self._captures):
            self._captures[key] -= 1
            if self._captures[key] <= 0:
                if key == "reaction":
                    reaction = _clean_text("".join(self._reaction_parts))
                    if reaction:
                        self.reactions.append(reaction)
                    self._reaction_parts = []
                del self._captures[key]
        self.message_depth -= 1
        if self.message_depth <= 0:
            self.in_message = False

    def handle_data(self, data: str) -> None:
        if not self.in_message or not data:
            return
        if "author" in self._captures:
            self.author += data
        if "text" in self._captures:
            self.text += data
        if "views" in self._captures:
            self.views += data
        if "reaction" in self._captures:
            self._reaction_parts.append(data)
        if "preview_site_name" in self._captures:
            self.preview_site_name += data
        if "preview_title" in self._captures:
            self.preview_title += data
        if "preview_description" in self._captures:
            self.preview_description += data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a public Telegram channel post URL to Markdown.")
    parser.add_argument("url", help="Telegram public channel post URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
