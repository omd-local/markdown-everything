#!/usr/bin/env python3
"""Convert a Xiaohongshu (小红书) note URL to Markdown.

Handles all three note formats:
    - Image note (图文):  post body text + per-image OCR
    - Video note (视频):  post body + whisper transcript (+ optional polish)
    - Comments (评论):    inline comments embedded in __INITIAL_STATE__

Pipeline:
    1. Resolve xhslink.com short URL → www.xiaohongshu.com/explore/<id> via 302.
    2. Fetch HTML with cookies; parse `window.__INITIAL_STATE__ = {...}`.
    3. Walk state.note.noteDetailMap[<id>].note → normalized dict.
    4. For images: download each, run tesseract per image.
       For video : download stream, ffmpeg → mlx_whisper, optional Ollama polish.
    5. Compose Markdown: metadata, body, images+OCR or transcript, comments.

Usage:
    python -m omd.xhs <url-or-share-blob> [-o out.md]
                       [--cookies cookies.txt]
                       [--polish [MODEL]] [--comments]
                       [--lang eng]
                       [--keep DIR]

Requires: tesseract (image OCR), ffmpeg + mlx_whisper (video transcript).
Cookies: xhs API gates note detail behind login. Export Netscape cookies.txt
from a logged-in browser session for `xiaohongshu.com`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from omd._cookies import (
    XHS_COOKIE_DOMAINS,
    XHS_URL_DOMAINS,
    CookieFileError,
    host_matches,
    require_cookie_header,
)
from omd._language import DEFAULT_OCR_LANGUAGE, MIXED_OCR_LANGUAGE_EXAMPLE
from omd._models import recommended_local_text_model
from omd._source_url import (
    SourceURLCountError,
    extract_single_http_url,
    sanitize_persisted_source_url,
)
from omd.reel import (
    polish_transcript,
    transcribe,
)

XHS_HOSTS = set(XHS_URL_DOMAINS)
XHS_MEDIA_DOMAINS = (*XHS_URL_DOMAINS, "rednotecdn.com")
MAX_SHORTLINK_REDIRECTS = 5
MAX_XHS_PAGE_BYTES = 16 * 1024 * 1024

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def is_xhs_url(url: str) -> bool:
    return host_matches(urlparse(url).hostname or "", XHS_HOSTS)


def extract_url(raw: str) -> str:
    try:
        url = extract_single_http_url(raw.strip())
    except SourceURLCountError as exc:
        from omd import _events

        _events.fatal("source_url_count_invalid", str(exc))
    if not url:
        from omd import _events
        _events.fatal("url_not_found", "no http(s) URL found in input")
    return url


def require(cmd: str) -> str:
    p = shutil.which(cmd)
    if not p:
        from omd import _events
        _events.fatal("tool_missing", f"`{cmd}` not on PATH")
    return p


class XHSRedirectError(ValueError):
    """An XHS URL or redirect crossed the authenticated host boundary."""


class XHSFetchError(ValueError):
    """A safe XHS network or bounded-response failure."""


def _validate_xhs_request_url(url: str, *, carries_cookies: bool = False) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise XHSRedirectError(
            "XHS rejected a malformed URL. No cookies were sent."
        ) from exc
    host = (parsed.hostname or "").lower()
    default_port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, default_port}
        or not host_matches(host, XHS_HOSTS)
    ):
        raise XHSRedirectError(
            "XHS rejected a URL outside the approved Xiaohongshu/Rednote hosts. "
            "No cookies were sent."
        )
    if carries_cookies and (
        parsed.scheme != "https"
        or port not in {None, 443}
        or not host_matches(host, XHS_COOKIE_DOMAINS)
    ):
        raise XHSRedirectError(
            "XHS only sends cookies to approved Xiaohongshu/Rednote HTTPS page "
            "hosts on the default port. No cookies were sent."
        )
    return url


def _validate_xhs_media_url(url: str) -> str:
    """Allow HTTPS media downloads without ever widening the cookie boundary."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise XHSRedirectError(
            "XHS rejected a malformed media URL. No cookies were sent."
        ) from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not host_matches(host, XHS_MEDIA_DOMAINS)
    ):
        raise XHSRedirectError(
            "XHS rejected media outside approved HTTPS CDN hosts. No cookies were sent."
        )
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _open_no_redirect(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # HTTPError remains a bounded, closeable response. Returning it lets
        # callers inspect Location without urllib draining the response body.
        return exc
    except (urllib.error.URLError, OSError) as exc:
        raise XHSFetchError(
            "XHS / Rednote could not reach the platform. Check the network, then retry."
        ) from exc


def _response_status(response) -> int:
    return int(getattr(response, "status", getattr(response, "code", 200)))


def _response_url(response, fallback: str) -> str:
    geturl = getattr(response, "geturl", None)
    return str(geturl()) if callable(geturl) else fallback


def _bounded_page_body(response, *, read_body: bool) -> bytes:
    if not read_body:
        return b""
    try:
        content_length = int(response.headers.get("Content-Length") or 0)
    except (AttributeError, TypeError, ValueError):
        content_length = 0
    if content_length > MAX_XHS_PAGE_BYTES:
        raise XHSFetchError(
            "XHS / Rednote returned a page above the safe HTML size limit. "
            "Open the post in a browser to confirm it is still a normal note, then retry."
        )
    body = response.read(MAX_XHS_PAGE_BYTES + 1)
    if len(body) > MAX_XHS_PAGE_BYTES:
        raise XHSFetchError(
            "XHS / Rednote returned a page above the safe HTML size limit. "
            "Open the post in a browser to confirm it is still a normal note, then retry."
        )
    return body


def _http_get(
    url: str,
    cookie_str: str = "",
    redirect: bool = True,
    *,
    read_body: bool = True,
) -> tuple[int, dict, bytes, str]:
    """Bounded GET with explicit redirects and no inherited body draining."""
    current = url
    current_cookie = cookie_str
    max_requests = MAX_SHORTLINK_REDIRECTS + 1 if redirect else 1
    for _hop in range(max_requests):
        _validate_xhs_request_url(current, carries_cookies=bool(current_cookie))
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if current_cookie:
            headers["Cookie"] = current_cookie
        request = urllib.request.Request(current, headers=headers)
        response = _open_no_redirect(request, timeout=30)
        try:
            status = _response_status(response)
            response_headers = dict(getattr(response, "headers", {}) or {})
            final_url = _response_url(response, current)
            location = response_headers.get("Location") or response_headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                if not redirect:
                    return status, response_headers, b"", final_url
                target = urljoin(current, location)
                _validate_xhs_request_url(target)
                current = target
                # A raw header cannot be safely re-scoped after any redirect.
                # Authenticated page fetching uses _authenticated_http_get,
                # which requests each hop separately from the Netscape jar.
                current_cookie = ""
                continue
            body = _bounded_page_body(response, read_body=read_body)
            return status, response_headers, body, final_url
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise XHSRedirectError(
        "XHS rejected a response with too many redirects. No cookies were sent "
        "to an unapproved host."
    )


def expand_short_url(url: str) -> str:
    """Resolve a bounded XHS shortlink chain without sending auth cookies."""
    _validate_xhs_request_url(url)
    host = (urlparse(url).hostname or "").lower()
    if not host_matches(host, ("xhslink.com",)):
        return url
    current = url
    try:
        for _hop in range(MAX_SHORTLINK_REDIRECTS):
            status, headers, _, final = _http_get(
                current,
                redirect=False,
                read_body=False,
            )
            location = headers.get("Location") or headers.get("location")
            if status in (301, 302, 303, 307, 308) and location:
                target = urljoin(current, location)
                _validate_xhs_request_url(target)
                target_host = (urlparse(target).hostname or "").lower()
                if not host_matches(target_host, ("xhslink.com",)):
                    _validate_xhs_request_url(target, carries_cookies=True)
                    return target
                current = target
                continue
            _validate_xhs_request_url(final)
            final_host = (urlparse(final).hostname or "").lower()
            if host_matches(final_host, ("xhslink.com",)):
                raise XHSRedirectError(
                    "XHS did not resolve the shortlink to a page URL. No cookies were "
                    "sent; check the link and retry."
                )
            _validate_xhs_request_url(final, carries_cookies=True)
            return final
    except XHSFetchError as exc:
        raise XHSRedirectError(
            "XHS could not expand the shortlink anonymously. Check the network, then "
            "retry. No cookies were sent."
        ) from exc
    raise XHSRedirectError(
        "XHS rejected a shortlink with too many redirects. No cookies were sent."
    )


def _authenticated_http_get(
    url: str,
    cookies_file: str | None,
) -> tuple[int, dict, bytes, str]:
    """Follow bounded platform redirects with a freshly scoped Cookie header."""
    current = url
    for _hop in range(MAX_SHORTLINK_REDIRECTS + 1):
        _validate_xhs_request_url(current, carries_cookies=True)
        cookie_str = require_cookie_header(
            cookies_file,
            XHS_COOKIE_DOMAINS,
            platform="XHS / Rednote",
            request_url=current,
        )
        status, headers, body, final = _http_get(
            current,
            cookie_str,
            redirect=False,
        )
        location = headers.get("Location") or headers.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            target = urljoin(current, location)
            _validate_xhs_request_url(target, carries_cookies=True)
            current = target
            continue
        _validate_xhs_request_url(final, carries_cookies=True)
        return status, headers, body, final
    raise XHSRedirectError(
        "XHS rejected a page with too many redirects. No cookies were sent to "
        "an unapproved host."
    )


NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-fA-F]+)")


def parse_note_id(url: str) -> str | None:
    m = NOTE_ID_RE.search(urlparse(url).path)
    return m.group(1) if m else None


STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL,
)

_JS_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
_JS_STRING_PLACEHOLDER_RE = re.compile(r'"__OMD_XHS_STRING_(\d+)__"')


def _normalize_initial_state_literals(raw: str) -> str:
    """Convert the small set of JS literals used by Rednote into JSON.

    Masking strings first keeps note text such as ``new Set([])`` or
    ``undefined`` byte-for-byte intact while normalizing executable tokens.
    """
    strings: list[str] = []

    def mask_string(match: re.Match[str]) -> str:
        index = len(strings)
        strings.append(match.group(0))
        return f'"__OMD_XHS_STRING_{index}__"'

    normalized = _JS_STRING_RE.sub(mask_string, raw)
    normalized = re.sub(r"\b(?:undefined|NaN)\b", "null", normalized)
    normalized = re.sub(
        r"\bnew\s+Set\s*\(\s*\[\s*\]\s*\)",
        "[]",
        normalized,
    )
    return _JS_STRING_PLACEHOLDER_RE.sub(
        lambda match: strings[int(match.group(1))],
        normalized,
    )


def parse_initial_state(html: str) -> dict:
    m = STATE_RE.search(html)
    if not m:
        from omd import _events
        _events.fatal(
            "cookies_invalid",
            "__INITIAL_STATE__ not found in HTML. Likely cookies expired or "
            "note removed; pass --cookies <fresh.txt>.",
        )
    # Rednote embeds a bounded set of JavaScript literals that JSON rejects.
    raw = _normalize_initial_state_literals(m.group(1))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        from omd import _events
        _events.fatal("parse_failed", f"state JSON parse failed at pos {e.pos}: {e.msg}")


def extract_note(state: dict, note_id: str | None) -> dict:
    """Pull normalized note dict out of __INITIAL_STATE__."""
    note_state = (state.get("note") or {})
    detail_map = note_state.get("noteDetailMap") or {}
    if note_id:
        if note_id not in detail_map:
            from omd import _events
            _events.fatal(
                "xhs_note_unavailable",
                "XHS / Rednote did not include the requested post in its page data. "
                "Open the shared link to confirm it is available, then retry.",
            )
        node = detail_map[note_id]
    elif detail_map:
        # A resolved shortlink should normally provide an ID. Retain the
        # legacy fallback only for an ID-less approved page URL.
        note_id = next(iter(detail_map))
        node = detail_map[note_id]
    else:
        from omd import _events
        _events.fatal(
            "cookies_invalid",
            "note detail map empty in state. Cookies may be unauthenticated.",
        )

    note = node.get("note") or node
    comments_node = node.get("comments") or {}

    user = note.get("user") or {}
    interact = note.get("interactInfo") or {}
    images = []
    for img in note.get("imageList") or []:
        url = img.get("urlDefault") or img.get("url") or ""
        if not url and img.get("infoList"):
            url = img["infoList"][0].get("url", "")
        if url:
            images.append(_normalize_xhs_media_url(url))

    video_url = _extract_video_url(note.get("video") or {})

    tags = []
    for t in note.get("tagList") or []:
        name = t.get("name") if isinstance(t, dict) else str(t)
        if name:
            tags.append(name)

    return {
        "id": note_id,
        "type": note.get("type") or ("video" if video_url else "normal"),
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
        "uploader": user.get("nickname") or user.get("nickName") or "",
        "uploader_id": user.get("userId") or user.get("user_id") or "",
        "upload_time": note.get("time") or note.get("lastUpdateTime"),
        "tags": tags,
        "images": images,
        "video_url": video_url,
        "like_count": interact.get("likedCount"),
        "collect_count": interact.get("collectedCount"),
        "comment_count": interact.get("commentCount"),
        "share_count": interact.get("shareCount"),
        "comments": _extract_comments(comments_node),
    }


def _extract_video_url(video: dict) -> str:
    streams = ((video.get("media") or {}).get("stream") or {})
    if not isinstance(streams, dict):
        return ""
    preferred = ("h264", "h265", "av1", "EF4", "EF5", "EF6", "EF7")
    stream_keys = [*preferred, *(key for key in streams if key not in preferred)]
    for codec in stream_keys:
        entries = streams.get(codec)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            candidates = [entry.get("masterUrl")]
            backups = entry.get("backupUrls")
            if isinstance(backups, list):
                candidates.extend(backups)
            for candidate in candidates:
                if not isinstance(candidate, str) or not candidate.strip():
                    continue
                try:
                    normalized = _normalize_xhs_media_url(candidate.strip())
                    _validate_xhs_media_url(normalized)
                except (ValueError, XHSRedirectError):
                    continue
                return normalized
    return ""


def _normalize_xhs_media_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "http" and host_matches(
        parsed.hostname or "",
        XHS_MEDIA_DOMAINS,
    ):
        return urlunparse(parsed._replace(scheme="https"))
    return url


def _extract_comments(node: dict) -> list[dict]:
    out: list[dict] = []
    for c in node.get("list") or node.get("comments") or []:
        out.append({
            "user": (c.get("userInfo") or {}).get("nickname") or "",
            "content": (c.get("content") or "").strip(),
            "like_count": c.get("likeCount"),
            "time": c.get("createTime") or c.get("time"),
            "sub": [
                {
                    "user": (sc.get("userInfo") or {}).get("nickname") or "",
                    "content": (sc.get("content") or "").strip(),
                }
                for sc in (c.get("subComments") or [])
            ],
        })
    return out


def download_to(url: str, dest: Path) -> None:
    from omd._download import copy_response_bounded

    current = url
    for _hop in range(MAX_SHORTLINK_REDIRECTS + 1):
        _validate_xhs_media_url(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"},
        )
        response = _open_no_redirect(request, timeout=60)
        try:
            status = _response_status(response)
            response_headers = dict(getattr(response, "headers", {}) or {})
            location = response_headers.get("Location") or response_headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                target = urljoin(current, location)
                _validate_xhs_media_url(target)
                current = target
                continue
            if not 200 <= status < 300:
                raise XHSFetchError(
                    "XHS / Rednote could not download the post media. Check that "
                    "the post is still available, then retry."
                )
            copy_response_bounded(response, dest, label="XHS media download")
            return
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise XHSRedirectError(
        "XHS rejected media with too many redirects. No cookies were sent."
    )


def ocr_image(path: Path, lang: str, *, executable: str = "tesseract") -> str:
    proc = subprocess.run(
        [executable, str(path), "-", "-l", lang],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def video_to_audio(video: Path) -> Path:
    require("ffmpeg")
    audio = video.with_suffix(".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video),
         "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(audio)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return audio


def compose_markdown(
    url: str,
    note: dict,
    image_paths: list[Path],
    image_ocr: list[str],
    transcript_text: str,
    polished: str,
    transcript_segments: list[dict] | None,
    include_comments: bool,
    transcript_warnings: list[str] | None = None,
    transcript_language: str = "",
) -> str:
    lines: list[str] = []
    title = note["title"] or note["desc"][:40] or note["id"] or "Xiaohongshu Note"
    lines.append(f"# {title}\n")

    lines.append("## Metadata\n")
    meta = [
        ("URL", sanitize_persisted_source_url(url)),
        ("Platform", "Xiaohongshu (小红书)"),
        ("Note ID", note["id"]),
        ("Type", note["type"]),
        ("Uploader", note["uploader"]),
        ("Uploader ID", note["uploader_id"]),
        ("Upload time", note["upload_time"]),
        ("Likes", note["like_count"]),
        ("Collects", note["collect_count"]),
        ("Comments", note["comment_count"]),
        ("Shares", note["share_count"]),
        ("Language", transcript_language),
    ]
    for k, v in meta:
        if v not in (None, "", 0):
            lines.append(f"- **{k}**: {v}")
    if note["tags"]:
        lines.append(f"- **Tags**: {', '.join(note['tags'])}")
    quality_warnings = [str(item) for item in (transcript_warnings or [])]
    if quality_warnings:
        lines.append("- **Transcript status**: Needs review")
        for warning in quality_warnings:
            lines.append(f"- **Transcript warning**: {warning}")
    lines.append("")

    if note["desc"]:
        lines += ["## Body\n", note["desc"], ""]

    if quality_warnings:
        from omd._transcript import TRANSCRIPT_REVIEW_NOTE

        lines += ["## Transcript quality warning\n", TRANSCRIPT_REVIEW_NOTE, ""]

    if image_paths:
        lines.append("## Images\n")
        for i, (p, ocr) in enumerate(zip(image_paths, image_ocr), 1):
            lines.append(f"### Image {i}\n")
            lines.append(f"![image {i}]({p.name})\n")
            if ocr:
                lines += ["**OCR text:**\n", "```", ocr, "```", ""]
            else:
                lines.append("_(no text detected)_\n")

    if polished:
        lines += ["## Transcript (polished)\n", polished, ""]
    if transcript_text:
        lines.append("## Transcript (raw)\n" if polished else "## Transcript\n")
        lines += [transcript_text, ""]
        if transcript_segments:
            lines.append("### Timestamped\n")
            for seg in transcript_segments:
                m, s = divmod(int(seg.get("start", 0)), 60)
                m2, s2 = divmod(int(seg.get("end", 0)), 60)
                lines.append(f"- [{m:02d}:{s:02d} → {m2:02d}:{s2:02d}] {seg.get('text','').strip()}")
            lines.append("")

    if include_comments and note["comments"]:
        lines.append("## Comments\n")
        for c in note["comments"]:
            head = f"**{c['user']}**" if c["user"] else "**(anon)**"
            if c.get("like_count"):
                head += f"  · 👍 {c['like_count']}"
            lines.append(head)
            lines.append(c["content"] or "_(empty)_")
            for sc in c["sub"]:
                sub_head = f"  ↳ **{sc['user']}**: " if sc["user"] else "  ↳ "
                lines.append(sub_head + (sc["content"] or "_(empty)_"))
            lines.append("")
        if note.get("comment_count") and len(note["comments"]) < note["comment_count"]:
            lines.append(
                f"_(showing {len(note['comments'])} of {note['comment_count']} comments — "
                "full thread requires signed API; not yet supported)_\n"
            )

    return "\n".join(lines)


def main() -> int:
    from omd.config import SPOKEN_LANGUAGE_MODES

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="URL or share blob (e.g. '32 复制本条信息 ... http://xhslink.com/...').")
    p.add_argument("-o", "--output", help="Write markdown here (default: stdout).")
    p.add_argument("--cookies", dest="cookies_file", help="Netscape cookies.txt for xiaohongshu.com.")
    p.add_argument(
        "--lang",
        dest="legacy_ocr_language",
        default=None,
        help=(
            "Legacy OCR alias retained for compatibility. Prefer --ocr-language."
        ),
    )
    p.add_argument(
        "--ocr-lang",
        "--ocr-language",
        dest="ocr_language",
        default=None,
        help=(
            f"Tesseract OCR language set (configured default {DEFAULT_OCR_LANGUAGE}; "
            f"Chinese + English example: {MIXED_OCR_LANGUAGE_EXAMPLE})."
        ),
    )
    p.add_argument(
        "--whisper-lang",
        default=None,
        help="Whisper language hint such as zh/en, or auto; omit to inherit the XHS/config default.",
    )
    p.add_argument(
        "--spoken-language-mode",
        choices=SPOKEN_LANGUAGE_MODES,
        default=None,
        help="Spoken-language policy; explicit requires --whisper-lang.",
    )
    p.add_argument("--preferred-languages", default=None,
                   help="Comma-separated common Whisper languages; first is used when --whisper-lang is omitted.")
    p.add_argument("--model", default="mlx-community/whisper-large-v3-turbo", help="Whisper model.")
    p.add_argument("--whisper-backend", default=os.environ.get("OMD_WHISPER_BACKEND", "mlx"),
                   help="Transcription backend (default: mlx; use faster-whisper on Linux hosts).")
    recommended_text_model = recommended_local_text_model()
    p.add_argument("--polish", nargs="?", const=recommended_text_model, default=None, metavar="MODEL",
                   help=f"Post-process video transcript with Ollama (memory-sized default: {recommended_text_model}).")
    p.add_argument("--ollama-host", default="http://localhost:11434")
    p.add_argument("--allow-remote-ollama", action="store_true")
    p.add_argument("--comments", action="store_true",
                   help="Include comments embedded in initial state (does NOT fetch full thread).")
    p.add_argument("--keep", help="Keep intermediates here (default: tmpdir).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show subprocess output and chunk debug lines.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all stderr output except errors.")
    p.add_argument("--json-events", dest="json_events", action="store_true",
                   help="Emit JSON-Lines events on stderr (see docs/json-events.schema.md).")
    args = p.parse_args()

    if args.verbose and args.json_events:
        sys.exit("error: --json-events and --verbose are mutually exclusive")

    from omd import _events, _progress
    _events.configure(args.json_events)
    _progress.configure(verbose=args.verbose, quiet=args.quiet)
    try:
        from omd.cli import _coalesce_ocr_language_values, _normalize_whisper_language
        from omd._language import parse_preferred_languages
        from omd.config import config_path, load_config, resolve_capture_config

        requested_ocr_language = _coalesce_ocr_language_values(
            args.legacy_ocr_language,
            args.ocr_language,
        )
        runtime_config_path = config_path()
        configured = load_config(runtime_config_path) if runtime_config_path.exists() else None
        whisper_override = (
            _normalize_whisper_language(args.whisper_lang)
            if args.whisper_lang is not None
            else None
        )
        spoken_mode = args.spoken_language_mode
        spoken_hint = whisper_override
        if spoken_mode is None:
            if whisper_override == "auto":
                spoken_mode = "auto-detect"
                spoken_hint = None
            elif whisper_override is not None:
                spoken_mode = "explicit"
            elif args.preferred_languages is not None:
                preferred = parse_preferred_languages(args.preferred_languages)
                spoken_mode = "explicit" if preferred else "inherit-adapter-default"
                spoken_hint = preferred[0] if preferred else None
        elif spoken_mode != "explicit" and whisper_override is not None:
            raise ValueError("--whisper-lang may only accompany explicit spoken-language mode")

        resolved = resolve_capture_config(
            configured,
            explicit_ocr_language=requested_ocr_language,
            explicit_spoken_language_mode=spoken_mode,
            explicit_spoken_language_hint=spoken_hint,
        )
        ocr_language = resolved.ocr_language
        if resolved.spoken_language_mode == "explicit":
            whisper_lang = resolved.spoken_language_hint
        elif resolved.spoken_language_mode == "auto-detect":
            whisper_lang = None
        else:
            whisper_lang = "zh"
    except ValueError as exc:
        _events.fatal("language_config_invalid", str(exc))
    if args.polish:
        from omd._network_policy import validate_ollama_host

        try:
            validate_ollama_host(args.ollama_host, allow_remote=args.allow_remote_ollama)
        except ValueError as exc:
            _events.fatal("ollama_host_blocked", str(exc))
    url = extract_url(args.url)
    if not is_xhs_url(url):
        _events.fatal(
            "url_invalid",
            "The shared URL is not on an approved Xiaohongshu/Rednote host.",
        )

    try:
        url = expand_short_url(url)
    except XHSRedirectError as exc:
        _events.fatal("xhs_redirect_rejected", str(exc))

    workdir_ctx = tempfile.TemporaryDirectory() if not args.keep else None
    workdir = Path(args.keep) if args.keep else Path(workdir_ctx.name)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # Rednote occasionally returns an authenticated placeholder state on
        # the first request. Retry that exact approved URL once, but never use
        # an unrelated feed entry as the requested note.
        for attempt in range(2):
            try:
                status, _, body, final_url = _authenticated_http_get(
                    url,
                    args.cookies_file,
                )
            except CookieFileError as exc:
                _events.fatal(exc.kind, str(exc))
            except XHSRedirectError as exc:
                _events.fatal("xhs_redirect_rejected", str(exc))
            except XHSFetchError as exc:
                _events.fatal("xhs_fetch_failed", str(exc))
            if status in {401, 403, 412}:
                _events.fatal(
                    "cookies_rejected",
                    "XHS / Rednote rejected the supplied cookies. Sign in again, "
                    "export fresh cookies, then retry.",
                )
            if status != 200:
                _events.fatal(
                    "xhs_fetch_failed",
                    f"XHS / Rednote returned HTTP {status}. Check the shared link, then retry.",
                )
            candidate_note_id = parse_note_id(final_url)
            html = body.decode("utf-8", errors="replace")
            state = parse_initial_state(html)
            detail_map = (state.get("note") or {}).get("noteDetailMap") or {}
            if (
                candidate_note_id
                and candidate_note_id not in detail_map
                and attempt == 0
            ):
                continue
            break
        url = final_url
        note_id = candidate_note_id
        note = extract_note(state, note_id)

        image_paths: list[Path] = []
        image_ocr: list[str] = []
        if note["images"] and note["type"] != "video":
            from omd.cli import _prepare_ocr_execution

            ocr_language, tesseract = _prepare_ocr_execution(ocr_language)
            img_dir = workdir / "images"
            img_dir.mkdir(exist_ok=True)
            for i, img_url in enumerate(note["images"], 1):
                ext = ".jpg"
                for cand in (".webp", ".png", ".jpeg", ".jpg"):
                    if cand in img_url.lower():
                        ext = cand
                        break
                p = img_dir / f"image_{i:02d}{ext}"
                try:
                    download_to(img_url, p)
                    image_paths.append(p)
                    image_ocr.append(
                        ocr_image(p, ocr_language, executable=tesseract)
                    )
                except Exception:
                    _progress.warn(
                        f"image {i} download/OCR failed; check the post media and local OCR setup"
                    )

        transcript_text = ""
        polished = ""
        segments: list[dict] = []
        transcript_warnings: list[str] = []
        transcript_language = ""
        if note["type"] == "video" and not note["video_url"]:
            _events.fatal(
                "xhs_video_unavailable",
                "XHS / Rednote identified this as a video post but did not expose "
                "a supported video stream. Update OMD or export fresh cookies, then retry.",
            )
        if note["type"] == "video":
            video_path = workdir / "video.mp4"
            try:
                download_to(note["video_url"], video_path)
            except Exception:
                _events.fatal(
                    "xhs_media_download_failed",
                    "XHS / Rednote found the post, but its video could not be downloaded. "
                    "Check the post, network connection, and available local storage, "
                    "then retry.",
                )
            try:
                audio = video_to_audio(video_path)
            except Exception:
                _events.fatal(
                    "xhs_audio_extract_failed",
                    "The Rednote video downloaded, but OMD could not extract its audio. "
                    "Check ffmpeg with Check setup, then retry.",
                )
            try:
                tr = transcribe(audio, workdir, args.model, whisper_lang, args.whisper_backend)
                transcript_text = (tr.get("text") or "").strip()
                segments = tr.get("segments") or []
                transcript_language = str(tr.get("language") or "").strip()
                if not transcript_text:
                    _events.fatal(
                        "xhs_transcription_failed",
                        "The Rednote audio was prepared, but local transcription returned "
                        "no readable speech. Check speech language and Whisper, then retry.",
                    )
                from omd._audio import duration_seconds
                from omd._transcript import apply_transcript_quality, report_transcript_quality

                transcript_warnings = apply_transcript_quality(
                    tr,
                    expected_language=whisper_lang,
                    expected_duration=duration_seconds(audio),
                )
                report_transcript_quality(transcript_warnings, polish_requested=bool(args.polish))
            except Exception:
                _events.fatal(
                    "xhs_transcription_failed",
                    "The Rednote audio was prepared, but local transcription could not "
                    "finish. Check speech language and Whisper with Check setup, then retry.",
                )
            if args.polish and transcript_text and not transcript_warnings:
                try:
                    polished = polish_transcript(
                        transcript_text, args.polish, args.ollama_host, segments=segments,
                        **({"allow_remote": True} if args.allow_remote_ollama else {}),
                    )
                except Exception:
                    _events.fatal(
                        "xhs_polish_failed",
                        "The Rednote transcript was created, but Markdown polish could not "
                        "finish. Check the local writing model, then retry.",
                    )

        md = compose_markdown(
            url, note, image_paths, image_ocr,
            transcript_text, polished, segments,
            include_comments=args.comments,
            transcript_warnings=transcript_warnings,
            transcript_language=transcript_language,
        )
        if args.output:
            from omd._io import write_atomic
            out = Path(args.output)
            write_atomic(out, md)
            # copy images alongside output if user kept them
            if image_paths and out.parent.resolve() != workdir.resolve():
                for p in image_paths:
                    shutil.copy2(p, out.parent / p.name)
            _progress.done(f"wrote {out}")
        else:
            sys.stdout.write(md)
        return 0
    finally:
        if workdir_ctx is not None:
            workdir_ctx.cleanup()


if __name__ == "__main__":
    sys.exit(main())
