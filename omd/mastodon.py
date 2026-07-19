#!/usr/bin/env python3
"""Convert a public Mastodon-compatible status URL to Markdown."""
from __future__ import annotations

import argparse
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

MASTODON_HOSTS = {
    "mastodon.social",
    "mastodon.online",
    "mastodon.world",
    "mstdn.social",
    "mas.to",
    "fosstodon.org",
    "hachyderm.io",
    "infosec.exchange",
    "techhub.social",
    "mozilla.social",
    "social.vivaldi.net",
    "universeodon.com",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
STATUS_ID_RE = re.compile(r"^\d+$")
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)


@dataclass(frozen=True)
class MastodonMedia:
    media_type: str
    url: str
    preview_url: str = ""
    description: str = ""


@dataclass(frozen=True)
class MastodonCard:
    title: str = ""
    url: str = ""
    description: str = ""
    provider_name: str = ""
    image: str = ""


@dataclass(frozen=True)
class MastodonPost:
    source_url: str
    api_url: str
    status_id: str
    uri: str
    author_name: str
    account: str
    author_url: str
    content: str
    created_at: str
    language: str = ""
    visibility: str = ""
    spoiler_text: str = ""
    favourites_count: int | None = None
    reblogs_count: int | None = None
    replies_count: int | None = None
    application: str = ""
    in_reply_to_id: str = ""
    media: list[MastodonMedia] = field(default_factory=list)
    card: MastodonCard | None = None
    mentions: list[tuple[str, str]] = field(default_factory=list)
    tags: list[tuple[str, str]] = field(default_factory=list)
    boosted_post: "MastodonPost | None" = None


def is_mastodon_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in MASTODON_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def parse_mastodon_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    status_id = ""
    if len(parts) >= 2 and parts[0].startswith("@") and STATUS_ID_RE.match(parts[1]):
        status_id = parts[1]
    elif len(parts) >= 4 and parts[0] == "users" and parts[2] == "statuses" and STATUS_ID_RE.match(parts[3]):
        status_id = parts[3]
    elif len(parts) >= 2 and parts[0] == "web" and parts[1] == "statuses":
        if len(parts) >= 3 and STATUS_ID_RE.match(parts[2]):
            status_id = parts[2]
    if host and status_id:
        return host, status_id
    from omd import _events

    _events.fatal("url_invalid", f"not a Mastodon status URL: {url}")


def api_url_for(url: str) -> str:
    host, status_id = parse_mastodon_url(url)
    return f"https://{host}/api/v1/statuses/{status_id}"


def fetch_status(url: str, *, timeout: int = 30) -> tuple[dict[str, Any], str]:
    payload, final_url = _fetch_json(api_url_for(url), timeout=timeout)
    if not isinstance(payload, dict):
        return {}, final_url
    return payload, final_url


def parse_status(payload: dict[str, Any], source_url: str, api_url: str) -> MastodonPost:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    card_payload = payload.get("card") if isinstance(payload.get("card"), dict) else None
    reblog_payload = payload.get("reblog") if isinstance(payload.get("reblog"), dict) else None
    return MastodonPost(
        source_url=source_url,
        api_url=api_url,
        status_id=str(payload.get("id") or ""),
        uri=str(payload.get("uri") or ""),
        author_name=str(account.get("display_name") or account.get("username") or ""),
        account=str(account.get("acct") or account.get("username") or ""),
        author_url=str(account.get("url") or ""),
        content=html_to_text(str(payload.get("content") or "")),
        created_at=str(payload.get("created_at") or ""),
        language=str(payload.get("language") or ""),
        visibility=str(payload.get("visibility") or ""),
        spoiler_text=html_to_text(str(payload.get("spoiler_text") or "")),
        favourites_count=_int_or_none(payload.get("favourites_count")),
        reblogs_count=_int_or_none(payload.get("reblogs_count")),
        replies_count=_int_or_none(payload.get("replies_count")),
        application=_application_name(payload.get("application")),
        in_reply_to_id=str(payload.get("in_reply_to_id") or ""),
        media=_parse_media(payload.get("media_attachments")),
        card=_parse_card(card_payload),
        mentions=_parse_people(payload.get("mentions")),
        tags=_parse_tags(payload.get("tags")),
        boosted_post=(
            parse_status(reblog_payload, str(reblog_payload.get("url") or source_url), "")
            if reblog_payload
            else None
        ),
    )


def markdown_for_post(post: MastodonPost) -> str:
    author = _format_author(post.author_name, post.account) or post.status_id
    lines = [f"# Mastodon post by {author}", "", f"- Source: {post.source_url}"]
    if post.api_url:
        lines.append(f"- API: {post.api_url}")
    if post.uri:
        lines.append(f"- URI: {post.uri}")
    if post.author_url:
        lines.append(f"- Author: [{author}]({post.author_url})")
    elif author:
        lines.append(f"- Author: {author}")
    if post.created_at:
        lines.append(f"- Created: {post.created_at}")
    if post.visibility:
        lines.append(f"- Visibility: {post.visibility}")
    if post.language:
        lines.append(f"- Language: {post.language}")
    if post.application:
        lines.append(f"- Application: {post.application}")
    if post.favourites_count is not None:
        lines.append(f"- Favorites: {post.favourites_count}")
    if post.reblogs_count is not None:
        lines.append(f"- Boosts: {post.reblogs_count}")
    if post.replies_count is not None:
        lines.append(f"- Replies: {post.replies_count}")
    if post.in_reply_to_id:
        lines.append(f"- In reply to: {post.in_reply_to_id}")

    if post.spoiler_text:
        lines.extend(["", "## Content warning", "", post.spoiler_text.strip(), ""])

    lines.extend(["", "## Post", "", post.content.strip() or "_No text body._", ""])
    if post.boosted_post:
        boosted_author = _format_author(post.boosted_post.author_name, post.boosted_post.account)
        lines.extend(["## Boosted post", ""])
        if boosted_author:
            lines.append(f"- Author: {boosted_author}")
        if post.boosted_post.source_url:
            lines.append(f"- Source: {post.boosted_post.source_url}")
        lines.extend(["", post.boosted_post.content.strip() or "_No text body._", ""])
    if post.media:
        lines.extend(["## Media", ""])
        for media in post.media:
            label = media.media_type or "media"
            target = media.url or media.preview_url
            if media.description:
                lines.append(f"- {label}: [{media.description}]({target})")
            else:
                lines.append(f"- {label}: {target}")
            if media.preview_url and media.preview_url != target:
                lines.append(f"  - Preview: {media.preview_url}")
        lines.append("")
    if post.card and (post.card.url or post.card.title):
        lines.extend(["## Card", ""])
        card_label = post.card.title or post.card.url
        if post.card.url:
            lines.append(f"- [{card_label}]({post.card.url})")
        else:
            lines.append(f"- {card_label}")
        if post.card.provider_name:
            lines.append(f"- Provider: {post.card.provider_name}")
        if post.card.description:
            lines.append(_indent_body(post.card.description, "  "))
        if post.card.image:
            lines.append(f"- Image: {post.card.image}")
        lines.append("")
    if post.mentions:
        lines.extend(["## Mentions", ""])
        for acct, url in _dedupe_pairs(post.mentions):
            lines.append(f"- [@{acct}]({url})" if url else f"- @{acct}")
        lines.append("")
    if post.tags:
        lines.extend(["## Tags", ""])
        for tag, url in _dedupe_pairs(post.tags):
            lines.append(f"- [#{tag}]({url})" if url else f"- #{tag}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_mastodon_url(url):
        _events.fatal("url_invalid", f"not a supported Mastodon-compatible URL: {url}")
    _progress.info("Fetching Mastodon status")
    payload, final_url = fetch_status(url)
    if not payload:
        _events.fatal("fetch_failed", "Mastodon returned no public status data.")
    post = parse_status(payload, canonical_url(url), final_url)
    md = markdown_for_post(post)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host, status_id = parse_mastodon_url(url)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].startswith("@"):
        return f"https://{host}/{parts[0]}/{status_id}"
    return f"https://{host}/web/statuses/{status_id}"


def html_to_text(raw_html: str) -> str:
    parser = _MastodonHTMLParser()
    parser.feed(raw_html)
    parser.close()
    return _collapse_blank_lines(html.unescape(parser.text()).strip())


def _parse_media(value: Any) -> list[MastodonMedia]:
    if not isinstance(value, list):
        return []
    media: list[MastodonMedia] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        target = str(item.get("url") or item.get("remote_url") or "")
        preview = str(item.get("preview_url") or "")
        if not target and not preview:
            continue
        media.append(
            MastodonMedia(
                media_type=str(item.get("type") or ""),
                url=target,
                preview_url=preview,
                description=html_to_text(str(item.get("description") or "")),
            )
        )
    return media


def _parse_card(value: dict[str, Any] | None) -> MastodonCard | None:
    if not value:
        return None
    return MastodonCard(
        title=html_to_text(str(value.get("title") or "")),
        url=str(value.get("url") or ""),
        description=html_to_text(str(value.get("description") or "")),
        provider_name=str(value.get("provider_name") or ""),
        image=str(value.get("image") or ""),
    )


def _parse_people(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    people: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            acct = str(item.get("acct") or item.get("username") or "").lstrip("@")
            url = str(item.get("url") or "")
            if acct:
                people.append((acct, url))
    return _dedupe_pairs(people)


def _parse_tags(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    tags: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").lstrip("#")
            url = str(item.get("url") or "")
            if name:
                tags.append((name, url))
    return _dedupe_pairs(tags)


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
                f"Mastodon JSON is {content_length} bytes, above limit {limit}. "
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
                    f"Mastodon JSON exceeded limit {limit} bytes. "
                    f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                )
            chunks.append(chunk)
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(b"".join(chunks).decode(charset, errors="replace")), response.geturl()


def _application_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return ""


def _format_author(name: str, account: str) -> str:
    name = name.strip()
    account = account.strip().lstrip("@")
    if name and account:
        return f"{name} (@{account})"
    if account:
        return f"@{account}"
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


def _collapse_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class _MastodonHTMLParser(HTMLParser):
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
    parser = argparse.ArgumentParser(description="Convert a public Mastodon-compatible status URL to Markdown.")
    parser.add_argument("url", help="Mastodon status URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
