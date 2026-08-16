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
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from omd._language import DEFAULT_OCR_LANGUAGE, MIXED_OCR_LANGUAGE_EXAMPLE
from omd._models import recommended_local_text_model
from omd.reel import (
    cookies_txt_to_string,
    polish_transcript,
    transcribe,
)

XHS_HOSTS = {
    "xiaohongshu.com", "www.xiaohongshu.com",
    "rednote.com", "www.rednote.com",
    "xhslink.com",
}

URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def is_xhs_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in XHS_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw
    m = URL_RE.search(raw)
    if not m:
        from omd import _events
        _events.fatal("url_not_found", "no http(s) URL found in input")
    return m.group(0).rstrip("/.,;)")


def require(cmd: str) -> str:
    p = shutil.which(cmd)
    if not p:
        from omd import _events
        _events.fatal("tool_missing", f"`{cmd}` not on PATH")
    return p


def _http_get(url: str, cookie_str: str = "", redirect: bool = True) -> tuple[int, dict, bytes, str]:
    """Stdlib GET. Returns (status, headers, body, final_url)."""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    req = urllib.request.Request(url, headers=headers)
    if not redirect:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_kw):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read(), r.geturl()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read() if hasattr(e, "read") else b"", e.geturl()
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, dict(r.headers), r.read(), r.geturl()


def expand_short_url(url: str) -> str:
    """Follow xhslink.com → xiaohongshu.com redirect without auth cookies."""
    if "xhslink.com" not in urlparse(url).netloc.lower():
        return url
    status, headers, _, final = _http_get(url, redirect=False)
    loc = headers.get("Location") or headers.get("location")
    if status in (301, 302, 303, 307, 308) and loc:
        return loc
    return final


NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-fA-F]+)")


def parse_note_id(url: str) -> str | None:
    m = NOTE_ID_RE.search(urlparse(url).path)
    return m.group(1) if m else None


STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL,
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
    raw = m.group(1)
    # XHS embeds JS literals (`undefined`, `NaN`) that are not valid JSON.
    raw = re.sub(r":\s*undefined\b", ": null", raw)
    raw = re.sub(r":\s*NaN\b", ": null", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        from omd import _events
        _events.fatal("parse_failed", f"state JSON parse failed at pos {e.pos}: {e.msg}")


def extract_note(state: dict, note_id: str | None) -> dict:
    """Pull normalized note dict out of __INITIAL_STATE__."""
    note_state = (state.get("note") or {})
    detail_map = note_state.get("noteDetailMap") or {}
    if note_id and note_id in detail_map:
        node = detail_map[note_id]
    elif detail_map:
        # fallback: take first entry
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
            images.append(url)

    video_url = ""
    video = note.get("video") or {}
    streams = ((video.get("media") or {}).get("stream") or {})
    for codec in ("h264", "h265", "av1"):
        seq = streams.get(codec) or []
        if seq and isinstance(seq, list):
            video_url = seq[0].get("masterUrl") or seq[0].get("backupUrls", [""])[0]
            if video_url:
                break

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
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        copy_response_bounded(r, dest, label="XHS media download")


def ocr_image(path: Path, lang: str) -> str:
    if not shutil.which("tesseract"):
        return ""
    proc = subprocess.run(
        ["tesseract", str(path), "-", "-l", lang],
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
) -> str:
    lines: list[str] = []
    title = note["title"] or note["desc"][:40] or note["id"] or "Xiaohongshu Note"
    lines.append(f"# {title}\n")

    lines.append("## Metadata\n")
    meta = [
        ("URL", url),
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
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="URL or share blob (e.g. '32 复制本条信息 ... http://xhslink.com/...').")
    p.add_argument("-o", "--output", help="Write markdown here (default: stdout).")
    p.add_argument("--cookies", dest="cookies_file", help="Netscape cookies.txt for xiaohongshu.com.")
    p.add_argument(
        "--lang",
        default=DEFAULT_OCR_LANGUAGE,
        help=(
            f"Tesseract OCR language (default {DEFAULT_OCR_LANGUAGE}; "
            f"Chinese + English example: {MIXED_OCR_LANGUAGE_EXAMPLE})."
        ),
    )
    p.add_argument("--whisper-lang", default="zh", help="Whisper language hint (default zh).")
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
    if args.polish:
        from omd._network_policy import validate_ollama_host

        try:
            validate_ollama_host(args.ollama_host, allow_remote=args.allow_remote_ollama)
        except ValueError as exc:
            _events.fatal("ollama_host_blocked", str(exc))
    from omd._language import choose_whisper_language
    whisper_lang = choose_whisper_language(
        None if args.whisper_lang == "zh" else args.whisper_lang,
        preferred=args.preferred_languages,
        default="zh",
    )

    url = extract_url(args.url)
    if not is_xhs_url(url):
        _events.fatal("url_invalid", f"not a Xiaohongshu URL: {url}")

    cookie_str = ""
    if args.cookies_file:
        cookie_str = cookies_txt_to_string(
            args.cookies_file, host_filter=("xiaohongshu", "rednote", "xhscdn"),
        )
        if not cookie_str:
            _progress.warn(f"no xiaohongshu cookies in {args.cookies_file}; trying anonymous")

    url = expand_short_url(url)
    note_id = parse_note_id(url)

    workdir_ctx = tempfile.TemporaryDirectory() if not args.keep else None
    workdir = Path(args.keep) if args.keep else Path(workdir_ctx.name)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        status, _, body, _ = _http_get(url, cookie_str)
        if status != 200:
            sys.exit(f"error: GET {url} returned {status}")
        html = body.decode("utf-8", errors="replace")
        state = parse_initial_state(html)
        note = extract_note(state, note_id)

        image_paths: list[Path] = []
        image_ocr: list[str] = []
        if note["images"] and note["type"] != "video":
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
                    image_ocr.append(ocr_image(p, args.lang))
                except Exception as e:
                    _progress.warn(f"image {i} download/OCR failed: {e}")

        transcript_text = ""
        polished = ""
        segments: list[dict] = []
        transcript_warnings: list[str] = []
        if note["type"] == "video" and note["video_url"]:
            video_path = workdir / "video.mp4"
            try:
                download_to(note["video_url"], video_path)
                audio = video_to_audio(video_path)
                tr = transcribe(audio, workdir, args.model, whisper_lang, args.whisper_backend)
                transcript_text = (tr.get("text") or "").strip()
                segments = tr.get("segments") or []
                from omd._audio import duration_seconds
                from omd._transcript import apply_transcript_quality, report_transcript_quality

                transcript_warnings = apply_transcript_quality(
                    tr,
                    expected_language=whisper_lang,
                    expected_duration=duration_seconds(audio),
                )
                report_transcript_quality(transcript_warnings, polish_requested=bool(args.polish))
                if args.polish and transcript_text and not transcript_warnings:
                    polished = polish_transcript(
                        transcript_text, args.polish, args.ollama_host, segments=segments,
                        **({"allow_remote": True} if args.allow_remote_ollama else {}),
                    )
            except Exception as e:
                _progress.warn(f"video transcription failed: {e}")

        md = compose_markdown(
            url, note, image_paths, image_ocr,
            transcript_text, polished, segments,
            include_comments=args.comments,
            transcript_warnings=transcript_warnings,
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
