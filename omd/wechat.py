#!/usr/bin/env python3
"""Convert a WeChat Official Account article URL to Markdown.

WeChat articles need a small dedicated backend because the readable article
body lives in `#js_content`, images use `data-src` instead of `src`, and generic
HTML converters often keep too much page chrome.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from omd._wechat import (
    WECHAT_VERIFICATION_REQUIRED_MESSAGE,
    is_wechat_article_url,
    is_wechat_url,
    is_wechat_verification_url,
    looks_like_verification_page,
)

URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


@dataclass(frozen=True)
class WeChatArticle:
    title: str
    account: str
    body: str
    image_count: int


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def fetch_html(url: str, *, timeout: int = 60) -> tuple[str, str]:
    """Fetch article HTML with a browser-like request and bounded memory use."""
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    req = urllib.request.Request(url, headers=headers)
    limit = max_download_bytes()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        try:
            content_length = int(length or "0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            raise ValueError(
                f"WeChat HTML is {content_length} bytes, above limit {limit}. "
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
                    f"WeChat HTML exceeded limit {limit} bytes. "
                    f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                )
            chunks.append(chunk)
        charset = response.headers.get_content_charset() or "utf-8"
        return b"".join(chunks).decode(charset, errors="replace"), response.geturl()


def html_to_markdown(source_html: str, source_url: str) -> str:
    article = parse_article(source_html)
    title = article.title or "WeChat Article"
    lines = [f"# {title}", "", f"- Source: {source_url}"]
    if article.account:
        lines.append(f"- Account: {article.account}")
    if article.image_count:
        lines.append(f"- Images: {article.image_count}")
    lines.extend(["", article.body.rstrip(), ""])
    return "\n".join(lines)


def parse_article(source_html: str) -> WeChatArticle:
    parser = _WeChatParser()
    parser.feed(source_html)
    parser.close()
    body = parser.markdown()
    if not body:
        from omd import _events

        if looks_like_verification_page(source_html):
            _fatal_verification_required()
        _events.fatal(
            "parse_failed",
            "WeChat article body `#js_content` was not found or was empty. "
            "The fetched page may be login-gated, expired, tokenized, or not the article HTML. "
            "Open the article in a browser, copy the final `mp.weixin.qq.com/s/...` URL, "
            "and retry; if it still fails, the account may be blocking automated fetches.",
        )
    return WeChatArticle(
        title=parser.title.strip(),
        account=parser.account.strip(),
        body=body,
        image_count=parser.image_count,
    )


def convert_url(url_or_blob: str, output: Path | None = None) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_wechat_url(url):
        _events.fatal("url_invalid", f"not a WeChat article URL: {url}")
    if not is_wechat_article_url(url):
        _events.fatal(
            "unsupported_shape",
            "Unsupported WeChat URL shape. OMD supports Official Account article URLs "
            "such as `https://mp.weixin.qq.com/s/...` or `https://mp.weixin.qq.com/s?...`. "
            "Album pages, profile/history pages, and other `mp.weixin.qq.com/mp/...` pages "
            "do not contain the article body that OMD extracts.",
        )
    _progress.info("Fetching WeChat article")
    source_html, final_url = fetch_html(url)
    if is_wechat_verification_url(final_url) or looks_like_verification_page(source_html):
        _fatal_verification_required()
    md = html_to_markdown(source_html, final_url)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def _fatal_verification_required() -> None:
    from omd import _events

    _events.fatal("verification_required", WECHAT_VERIFICATION_REQUIRED_MESSAGE)


class _WeChatParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "aside", "blockquote", "div", "figcaption", "figure",
        "footer", "header", "li", "main", "p", "section", "table", "tr",
    }
    SKIP_TAGS = {"script", "style", "svg", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.account = ""
        self.image_count = 0
        self._parts: list[str] = []
        self._in_content = False
        self._content_depth = 0
        self._skip_depth = 0
        self._capture_title_depth = 0
        self._capture_account_depth = 0
        self._title_parts: list[str] = []
        self._account_parts: list[str] = []
        self._link_stack: list[str | None] = []
        self._heading_section_depths: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {name.lower(): value or "" for name, value in attrs}

        if tag == "meta":
            self._handle_meta(attr)
            return

        if attr.get("id") == "activity-name":
            self._capture_title_depth = 1
        elif attr.get("id") in {"profileBt", "js_name"}:
            self._capture_account_depth = 1
        elif self._capture_title_depth:
            self._capture_title_depth += 1
        elif self._capture_account_depth:
            self._capture_account_depth += 1

        if attr.get("id") == "js_content" and not self._in_content:
            self._in_content = True
            self._content_depth = 1
            return

        if not self._in_content:
            return

        self._content_depth += 1
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._blankline()
            level = int(tag[1])
            self._append("#" * level + " ")
            return
        if tag == "section" and _looks_like_heading(attr.get("style", "")):
            self._blankline()
            self._append("## ")
            self._heading_section_depths.append(self._content_depth)
            return
        if tag in self.BLOCK_TAGS:
            self._blankline()
            if tag == "li":
                self._append("- ")
            elif tag == "blockquote":
                self._append("> ")
            return
        if tag == "br":
            self._newline()
            return
        if tag == "img":
            src = attr.get("data-src") or attr.get("src")
            if src:
                self.image_count += 1
                alt = _escape_alt(attr.get("alt") or f"image {self.image_count}")
                self._blankline()
                self._append(f"![{alt}]({src})")
                self._blankline()
            return
        if tag in {"strong", "b"}:
            self._append("**")
            return
        if tag in {"em", "i"}:
            self._append("*")
            return
        if tag == "a":
            href = attr.get("href") or None
            self._link_stack.append(href)
            if href:
                self._append("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._capture_title_depth:
            self._capture_title_depth -= 1
            if self._capture_title_depth == 0 and not self.title:
                self.title = _clean_inline("".join(self._title_parts))
        if self._capture_account_depth:
            self._capture_account_depth -= 1
            if self._capture_account_depth == 0 and not self.account:
                self.account = _clean_inline("".join(self._account_parts))

        if not self._in_content:
            return

        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth:
            if tag in {"strong", "b"}:
                self._append("**")
            elif tag in {"em", "i"}:
                self._append("*")
            elif tag == "a":
                href = self._link_stack.pop() if self._link_stack else None
                if href:
                    self._append(f"]({href})")
            elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._blankline()
            elif self._heading_section_depths and self._heading_section_depths[-1] == self._content_depth:
                self._heading_section_depths.pop()
                self._blankline()
            elif tag in self.BLOCK_TAGS:
                self._blankline()

        self._content_depth -= 1
        if self._content_depth <= 0:
            self._in_content = False

    def handle_data(self, data: str) -> None:
        if self._capture_title_depth:
            self._title_parts.append(data)
        if self._capture_account_depth:
            self._account_parts.append(data)
        if not self._in_content or self._skip_depth:
            return
        self._append_text(data)

    def markdown(self) -> str:
        text = "".join(self._parts)
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(?m)^##\s*$\n*", "", text)
        return text.strip()

    def _handle_meta(self, attr: dict[str, str]) -> None:
        key = (attr.get("property") or attr.get("name") or "").lower()
        content = html.unescape(attr.get("content") or "").strip()
        if key == "og:title" and content and not self.title:
            self.title = content

    def _append_text(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        leading_space = text.startswith(" ")
        trailing_space = text.endswith(" ")
        text = text.strip()
        if (
            self._parts
            and not self._parts[-1].endswith((" ", "\n", "[", "*", "> "))
            and leading_space
        ):
            self._parts.append(" ")
        elif (
            self._parts
            and not self._parts[-1].endswith((" ", "\n", "[", "*", "> "))
            and text[:1].isascii()
            and text[:1].isalnum()
            and self._parts[-1][-1:].isascii()
            and self._parts[-1][-1:].isalnum()
        ):
            self._parts.append(" ")
        self._parts.append(text)
        if trailing_space:
            self._parts.append(" ")

    def _append(self, text: str) -> None:
        self._parts.append(text)

    def _newline(self) -> None:
        if not self._parts or self._parts[-1].endswith("\n"):
            return
        self._parts.append("\n")

    def _blankline(self) -> None:
        if not self._parts:
            return
        current = "".join(self._parts)
        if current.endswith("\n\n"):
            return
        if current.endswith("\n"):
            self._parts.append("\n")
        else:
            self._parts.append("\n\n")


def _looks_like_heading(style: str) -> bool:
    style = style.lower().replace(" ", "")
    if "font-weight:bold" in style or "font-weight:600" in style or "font-weight:700" in style:
        return True
    match = re.search(r"font-size:(\d+)px", style)
    return bool(match and int(match.group(1)) >= 18)


def _escape_alt(value: str) -> str:
    return _clean_inline(value).replace("[", "\\[").replace("]", "\\]")


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a WeChat Official Account article to Markdown.")
    parser.add_argument("url", help="WeChat article URL or share blob containing one.")
    parser.add_argument("-o", "--output", help="Write Markdown to this path instead of stdout.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--json-events", dest="json_events", action="store_true")
    args = parser.parse_args(argv)

    if args.verbose and args.json_events:
        sys.exit("error: --json-events and --verbose are mutually exclusive")

    from omd import _events, _progress

    _events.configure(args.json_events)
    _progress.configure(verbose=args.verbose, quiet=args.quiet)
    return convert_url(args.url, Path(args.output) if args.output else None)


if __name__ == "__main__":
    sys.exit(main())
