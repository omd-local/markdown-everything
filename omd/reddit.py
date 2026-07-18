#!/usr/bin/env python3
"""Convert a Reddit post URL to Markdown.

Reddit has a stable public JSON representation for post pages. This backend
uses that endpoint instead of generic HTML extraction so comments, scores, and
post metadata survive the conversion.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from omd import __version__

REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "m.reddit.com",
    "redd.it",
}
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
UA = (
    f"omd/{__version__} (+https://github.com/omd-local/markdown-everything) "
    "python-urllib"
)
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)
THING_TAG_RE = re.compile(r"<div\b(?=[^>]*\bclass=\"[^\"]*\bthing\b)(?=[^>]*\bid=\"thing_(t[13]_[^\"]+)\")(?P<tag>[^>]*)>", re.I)
DATA_ATTR_RE = re.compile(r"\s(data-[\w-]+|id|class)=\"([^\"]*)\"", re.I)
TITLE_LINK_RE = re.compile(r"<a\b(?=[^>]*\bclass=\"[^\"]*\btitle\b)[^>]*>(.*?)</a>", re.I | re.S)
OG_TITLE_RE = re.compile(r"<meta\b(?=[^>]*\bproperty=\"og:title\")(?=[^>]*\bcontent=\"([^\"]*)\")[^>]*>", re.I | re.S)
HTML_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
SHARE_PATH_RE = re.compile(r"^/r/[^/]+/s/[^/]+/?$", re.I)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class RedditComment:
    author: str
    body: str
    score: int | None
    created_utc: float | None
    edited: bool
    depth: int
    permalink: str


@dataclass(frozen=True)
class RedditPost:
    title: str
    author: str
    subreddit: str
    selftext: str
    score: int | None
    num_comments: int | None
    created_utc: float | None
    edited: bool
    permalink: str
    url: str
    comments: list[RedditComment]


def is_reddit_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in REDDIT_HOSTS)


def is_share_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return is_reddit_url(url) and bool(SHARE_PATH_RE.fullmatch(parsed.path))


def _request_share_redirect(url: str, *, method: str, timeout: int) -> str | None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method=method,
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            location = response.headers.get("Location")
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code not in REDIRECT_STATUSES:
            raise
        status = exc.code
        location = exc.headers.get("Location") if exc.headers else None
        final_url = exc.geturl()

    if status in REDIRECT_STATUSES and location:
        return urllib.parse.urljoin(url, location)
    return final_url if final_url != url else None


def resolve_share_url(url: str, *, timeout: int = 30) -> str:
    """Resolve Reddit's /r/<subreddit>/s/<token> links to post URLs."""
    if not is_share_url(url):
        return url

    head_error: OSError | None = None
    try:
        target = _request_share_redirect(url, method="HEAD", timeout=timeout)
    except OSError as exc:
        head_error = exc
        target = None
    if target is None:
        try:
            target = _request_share_redirect(url, method="GET", timeout=timeout)
        except OSError as exc:
            if head_error is not None:
                raise exc from head_error
            raise
    if target is None:
        raise ValueError("Reddit share URL did not return a redirect")

    parsed = urllib.parse.urlparse(target)
    if not is_reddit_url(target) or "/comments/" not in parsed.path:
        raise ValueError(f"Reddit share URL redirected to an unsupported target: {target}")
    return urllib.parse.urlunparse(("https", parsed.netloc.lower(), parsed.path, "", "", ""))


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw.rstrip("/.,;)")
    match = URL_RE.search(raw)
    if not match:
        from omd import _events

        _events.fatal("url_not_found", "no http(s) URL found in input")
    return match.group(0).rstrip("/.,;)")


def json_url_for(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if host == "redd.it" or host.endswith(".redd.it"):
        post_id = path.strip("/").split("/", 1)[0]
        if not post_id:
            from omd import _events

            _events.fatal("url_invalid", f"not a Reddit post URL: {url}")
        return f"https://www.reddit.com/comments/{post_id}.json?limit=50&raw_json=1"
    if not is_reddit_url(url) or "/comments/" not in path:
        from omd import _events

        _events.fatal("url_invalid", f"not a Reddit post URL: {url}")
    if path.endswith(".json"):
        json_path = path
    else:
        json_path = path + ".json"
    query = urllib.parse.parse_qs(parsed.query)
    query.setdefault("limit", ["50"])
    query.setdefault("raw_json", ["1"])
    return urllib.parse.urlunparse(
        ("https", "www.reddit.com", json_path, "", urllib.parse.urlencode(query, doseq=True), "")
    )


def old_reddit_url_for(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    if host == "redd.it" or host.endswith(".redd.it"):
        post_id = path.strip("/").split("/", 1)[0]
        if not post_id:
            from omd import _events

            _events.fatal("url_invalid", f"not a Reddit post URL: {url}")
        path = f"/comments/{post_id}/"
    elif not is_reddit_url(url) or "/comments/" not in path:
        from omd import _events

        _events.fatal("url_invalid", f"not a Reddit post URL: {url}")
    path = re.sub(r"\.json/?$", "/", path.rstrip("/"))
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunparse(("https", "old.reddit.com", path, "", "", ""))


def fetch_json(url: str, *, timeout: int = 60) -> tuple[Any, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        json_url_for(url),
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
                f"Reddit JSON is {content_length} bytes, above limit {limit}. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(
                f"Reddit JSON exceeded limit {limit} bytes. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(data.decode(charset, errors="replace")), response.geturl()


def fetch_old_html(url: str, *, timeout: int = 60) -> tuple[str, str]:
    from omd._download import MAX_DOWNLOAD_MB_ENV, max_download_bytes

    req = urllib.request.Request(
        old_reddit_url_for(url),
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
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
                f"Reddit HTML is {content_length} bytes, above limit {limit}. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(
                f"Reddit HTML exceeded limit {limit} bytes. "
                f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
            )
        charset = response.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace"), response.geturl()


def parse_listing(payload: Any) -> RedditPost:
    if isinstance(payload, dict) and payload.get("error"):
        from omd import _events

        _events.fatal("fetch_failed", f"Reddit returned error {payload.get('error')}: {payload.get('message', '')}")
    if not isinstance(payload, list) or len(payload) < 2:
        from omd import _events

        _events.fatal("parse_failed", "Reddit JSON did not contain post + comments listings.")
    post_children = _children(payload[0])
    if not post_children:
        from omd import _events

        _events.fatal("parse_failed", "Reddit JSON contained no post data.")
    post_data = post_children[0].get("data") or {}
    comments = _parse_comments(_children(payload[1]), depth=0)
    return RedditPost(
        title=str(post_data.get("title") or "Reddit Post"),
        author=str(post_data.get("author") or "[deleted]"),
        subreddit=str(post_data.get("subreddit_name_prefixed") or post_data.get("subreddit") or ""),
        selftext=str(post_data.get("selftext") or ""),
        score=_int_or_none(post_data.get("score")),
        num_comments=_int_or_none(post_data.get("num_comments")),
        created_utc=_float_or_none(post_data.get("created_utc")),
        edited=_bool_edited(post_data.get("edited")),
        permalink=_absolute_reddit_url(str(post_data.get("permalink") or "")),
        url=str(post_data.get("url") or ""),
        comments=comments,
    )


def parse_old_html(markup: str, source_url: str) -> RedditPost:
    """Parse old.reddit.com post HTML when Reddit blocks anonymous JSON."""
    things = list(_iter_old_reddit_things(markup))
    post_item = next((item for item in things if item["fullname"].startswith("t3_")), None)
    if post_item is None:
        from omd import _events

        _events.fatal("parse_failed", "old.reddit HTML contained no post data.")
    post_segment = str(post_item["segment"])
    post_attrs = post_item["attrs"]
    title = (
        _first_title_text(post_segment)
        or _meta_title(markup)
        or "Reddit Post"
    )
    timestamp = _float_or_none(post_attrs.get("data-timestamp"))
    if timestamp and timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    comments: list[RedditComment] = []
    for item in things:
        if not item["fullname"].startswith("t1_"):
            continue
        attrs = item["attrs"]
        segment = str(item["segment"])
        body = _html_fragment_to_markdown(_first_md_fragment(segment))
        if not body:
            continue
        comments.append(
            RedditComment(
                author=str(attrs.get("data-author") or "[deleted]"),
                body=body,
                score=_score_from_old_comment(segment),
                created_utc=None,
                edited=False,
                depth=0,
                permalink=_absolute_reddit_url(str(attrs.get("data-permalink") or "")),
            )
        )
    return RedditPost(
        title=title,
        author=str(post_attrs.get("data-author") or "[deleted]"),
        subreddit=str(post_attrs.get("data-subreddit-prefixed") or post_attrs.get("data-subreddit") or ""),
        selftext=_html_fragment_to_markdown(_first_md_fragment(post_segment)),
        score=_int_or_none(post_attrs.get("data-score")),
        num_comments=_int_or_none(post_attrs.get("data-comments-count")),
        created_utc=timestamp,
        edited=False,
        permalink=_absolute_reddit_url(str(post_attrs.get("data-permalink") or source_url)),
        url=_absolute_reddit_url(str(post_attrs.get("data-url") or source_url)),
        comments=comments,
    )


def markdown_for_post(
    post: RedditPost,
    source_url: str,
    *,
    include_comments: bool = False,
    max_comments: int = 50,
) -> str:
    lines = [f"# {post.title}", ""]
    lines.append(f"- Source: {source_url}")
    if post.permalink and post.permalink != source_url:
        lines.append(f"- Permalink: {post.permalink}")
    if post.subreddit:
        lines.append(f"- Subreddit: {post.subreddit}")
    if post.author:
        lines.append(f"- Author: u/{post.author}")
    if post.score is not None:
        lines.append(f"- Score: {post.score}")
    if post.num_comments is not None:
        lines.append(f"- Comments: {post.num_comments}")
    if post.created_utc is not None:
        lines.append(f"- Created: {_format_utc(post.created_utc)}")
    if post.edited:
        lines.append("- Edited: yes")
    if _is_deleted_author(post.author) or _is_deleted_body(post.selftext):
        lines.append("- Deleted/removed: yes")
    if post.url and post.url not in {source_url, post.permalink}:
        lines.append(f"- Linked URL: {post.url}")
    lines.append("")
    if post.selftext.strip():
        lines.extend(["## Post", "", post.selftext.strip(), ""])
    if not include_comments:
        if post.num_comments:
            lines.extend(["## Comments", "", "_Skipped by default. Choose OP + Top comments to include comment structure._", ""])
        return "\n".join(lines).rstrip() + "\n"
    comments = post.comments[:max_comments]
    if comments:
        lines.extend(["## Top comments", ""])
        for comment in comments:
            indent = "  " * min(comment.depth, 3)
            body = _indent_body(comment.body.strip(), indent + "  ")
            meta = f"{indent}- u/{comment.author}"
            if comment.score is not None:
                meta += f" ({comment.score} points)"
            if comment.created_utc is not None:
                meta += f" - {_format_utc(comment.created_utc)}"
            markers = _comment_markers(comment)
            if markers:
                meta += f" - {', '.join(markers)}"
            if comment.permalink:
                meta += f" - {comment.permalink}"
            lines.extend([meta, body, ""])
    return "\n".join(lines).rstrip() + "\n"


def convert_url(url_or_blob: str, output: Path | None = None, *, include_comments: bool = False) -> int:
    from omd import _events, _progress

    url = extract_url(url_or_blob)
    if not is_reddit_url(url):
        _events.fatal("url_invalid", f"not a Reddit URL: {url}")
    try:
        resolved_url = resolve_share_url(url)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        _events.fatal("fetch_failed", f"Reddit share link could not be resolved: {_error_summary(exc)}")
    if resolved_url != url:
        _progress.info("Resolved Reddit share link")
        url = resolved_url
    _progress.info("Fetching Reddit post")
    try:
        payload, final_url = fetch_json(url)
        post = parse_listing(payload)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as json_exc:
        _progress.warn(f"Reddit JSON fetch failed ({_error_summary(json_exc)}); trying old.reddit HTML fallback")
        try:
            markup, final_url = fetch_old_html(url)
            post = parse_old_html(markup, final_url)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as html_exc:
            _events.fatal(
                "fetch_failed",
                "Reddit fetch failed. JSON endpoint error: "
                f"{_error_summary(json_exc)}; old.reddit fallback error: {_error_summary(html_exc)}. "
                "Reddit may be rate-limiting this network or requiring browser authentication.",
            )
    md = markdown_for_post(post, final_url, include_comments=include_comments)
    if output:
        from omd._io import write_atomic

        write_atomic(output, md)
        _progress.done(f"wrote {output}")
    else:
        sys.stdout.write(md)
    return 0


def _children(listing: Any) -> list[dict[str, Any]]:
    if not isinstance(listing, dict):
        return []
    data = listing.get("data")
    if not isinstance(data, dict):
        return []
    children = data.get("children")
    return children if isinstance(children, list) else []


def _parse_comments(children: list[dict[str, Any]], *, depth: int) -> list[RedditComment]:
    out: list[RedditComment] = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = child.get("data") or {}
        body = str(data.get("body") or "").strip()
        if not body:
            continue
        out.append(
            RedditComment(
                author=str(data.get("author") or "[deleted]"),
                body=body,
                score=_int_or_none(data.get("score")),
                created_utc=_float_or_none(data.get("created_utc")),
                edited=_bool_edited(data.get("edited")),
                depth=depth,
                permalink=_absolute_reddit_url(str(data.get("permalink") or "")),
            )
        )
        replies = data.get("replies")
        if isinstance(replies, dict) and depth < 3:
            out.extend(_parse_comments(_children(replies), depth=depth + 1))
    return out


def _absolute_reddit_url(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    if path_or_url.startswith("/"):
        return "https://www.reddit.com" + path_or_url
    return path_or_url


def _format_utc(value: float) -> str:
    return _dt.datetime.fromtimestamp(value, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _indent_body(body: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in body.splitlines())


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_edited(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "0", "none", "no"}
    return False


def _is_deleted_author(author: str) -> bool:
    return author.strip().lower() in {"[deleted]", "[removed]", "deleted", "removed"}


def _is_deleted_body(body: str) -> bool:
    return body.strip().lower() in {"[deleted]", "[removed]", "deleted", "removed"}


def _comment_markers(comment: RedditComment) -> list[str]:
    markers: list[str] = []
    if comment.edited:
        markers.append("edited")
    if _is_deleted_author(comment.author) or _is_deleted_body(comment.body):
        markers.append("deleted/removed")
    return markers


def _iter_old_reddit_things(markup: str):
    matches = list(THING_TAG_RE.finditer(markup))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markup)
        tag = match.group(0)
        yield {
            "fullname": match.group(1),
            "attrs": _tag_attrs(tag),
            "segment": markup[start:end],
        }


def _tag_attrs(tag: str) -> dict[str, str]:
    return {
        name.lower(): html.unescape(value)
        for name, value in DATA_ATTR_RE.findall(tag)
    }


def _first_md_fragment(segment: str) -> str:
    match = re.search(r"<div\b[^>]*\bclass=\"md\"[^>]*>", segment, flags=re.I)
    if not match:
        return ""
    start = match.end()
    depth = 1
    for token in re.finditer(r"</?div\b[^>]*>", segment[start:], flags=re.I):
        raw = token.group(0).lower()
        if raw.startswith("</"):
            depth -= 1
            if depth == 0:
                return segment[start:start + token.start()]
        else:
            depth += 1
    return segment[start:]


def _first_title_text(segment: str) -> str:
    match = TITLE_LINK_RE.search(segment)
    if not match:
        return ""
    return _html_fragment_to_text(match.group(1))


def _meta_title(markup: str) -> str:
    match = OG_TITLE_RE.search(markup)
    if match:
        return _html_fragment_to_text(match.group(1))
    match = HTML_TITLE_RE.search(markup)
    if not match:
        return ""
    title = _html_fragment_to_text(match.group(1))
    return title.split(" : ", 1)[0].strip()


def _score_from_old_comment(segment: str) -> int | None:
    match = re.search(r"<span\b[^>]*\bclass=\"[^\"]*\bscore\s+unvoted\b[^\"]*\"[^>]*\btitle=\"(-?\d+)\"", segment, re.I)
    if match:
        return _int_or_none(match.group(1))
    match = re.search(r"<span\b[^>]*\bclass=\"[^\"]*\bscore\s+unvoted\b[^\"]*\"[^>]*>(-?\d+)\s+points?", segment, re.I)
    return _int_or_none(match.group(1)) if match else None


def _html_fragment_to_text(fragment: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _html_fragment_to_markdown(fragment: str) -> str:
    parser = _MarkdownHTMLParser()
    parser.feed(fragment)
    parser.close()
    return parser.markdown()


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


class _MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {name.lower(): value or "" for name, value in attrs}
        if tag in {"p", "blockquote", "ul", "ol"}:
            self._blankline()
        elif tag == "br":
            self._newline()
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._blankline()
            self.parts.append("### ")
        elif tag == "li":
            self._newline()
            self.parts.append("- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            href = attr.get("href", "")
            self.link_stack.append(href)
            if href:
                self.parts.append("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "blockquote", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._blankline()
        elif tag == "li":
            self._newline()
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            if href:
                self.parts.append(f"]({href})")

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self.parts.append(data)

    def markdown(self) -> str:
        text = "".join(self.parts).replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(?m)^-\s*$\n?", "", text)
        return text.strip()

    def _newline(self) -> None:
        if not self.parts or self.parts[-1].endswith("\n"):
            return
        self.parts.append("\n")

    def _blankline(self) -> None:
        if not self.parts:
            return
        current = "".join(self.parts)
        if current.endswith("\n\n"):
            return
        if current.endswith("\n"):
            self.parts.append("\n")
        else:
            self.parts.append("\n\n")


def _error_summary(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    return str(exc) or exc.__class__.__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a Reddit post URL to Markdown.")
    parser.add_argument("url", help="Reddit post URL or share blob.")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown here.")
    parser.add_argument(
        "--comments",
        choices=["op", "top"],
        default="op",
        help="Reddit content scope: op saves only the original post; top also includes top comments.",
    )
    args = parser.parse_args(argv)
    return convert_url(args.url, args.output, include_comments=args.comments == "top")


if __name__ == "__main__":
    raise SystemExit(main())
