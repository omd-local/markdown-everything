#!/usr/bin/env python3
"""Convert an Apple Podcasts episode URL to Markdown.

Pipeline:
    1. Parse URL → collectionId (show) + trackId (?i=<n>) + slug.
    2. Hit iTunes Lookup (id=<show>&media=podcast) → feedUrl.
       Apple flakiness: entity=podcastEpisode often returns docs schema
       instead of data, so we go via RSS for episode-level info.
    3. Fetch RSS feed XML, locate <item> by:
         a. exact title slug match against URL slug, else
         b. <guid>/<link> contains trackId, else
         c. first item (newest).
    4. Extract <enclosure url="..."/> = direct audio (mp3/m4a).
    5. Download audio → mlx_whisper → optional Ollama polish.
    6. Compose Markdown: metadata, description, transcript.

Usage:
    python -m omd.podcast <apple-podcasts-url> [-o out.md]
                          [--polish [MODEL]]
                          [--whisper-lang en]
                          [--keep DIR]

Requires: ffmpeg + mlx_whisper. Same toolchain as omd.reel / omd.xhs.

Apple Podcasts+ paid subscriptions ARE NOT supported (DRM-gated).
Standard RSS-backed shows work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from omd._audio import duration_seconds
from omd._models import recommended_local_text_model
from omd.reel import polish_transcript, transcribe

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

APPLE_HOSTS = {"podcasts.apple.com", "podcast.apple.com"}

URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
ID_RE = re.compile(r"/id(\d+)")
SLUG_RE = re.compile(r"/podcast/([^/]+)/id\d+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def is_apple_podcast_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in APPLE_HOSTS)


def extract_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw
    m = URL_RE.search(raw)
    if not m:
        from omd import _events
        _events.fatal("url_not_found", "no http(s) URL found in input")
    return m.group(0).rstrip("/.,;)")


def parse_apple_url(url: str) -> tuple[str, str | None, str | None]:
    """Return (show_id, track_id_or_None, slug_or_None)."""
    parsed = urlparse(url)
    m = ID_RE.search(parsed.path)
    if not m:
        sys.exit(f"error: cannot find show id in URL path: {parsed.path}")
    show_id = m.group(1)
    track_id = parse_qs(parsed.query).get("i", [None])[0]
    slug_m = SLUG_RE.search(parsed.path)
    slug = slug_m.group(1).lower() if slug_m else None
    return show_id, track_id, slug


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def http_get(url: str, *, headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def lookup_feed_url(show_id: str, *, retries: int = 5) -> str:
    """iTunes lookup for show metadata. Returns feedUrl.

    Apple sometimes returns a JSON schema docstring instead of data
    (rate-limit signal). Retry until we get a real response.
    """
    url = f"https://itunes.apple.com/lookup?id={show_id}&media=podcast&limit=1"
    last_err = ""
    for i in range(retries):
        body = http_get(url).decode("utf-8", errors="replace")
        try:
            d = json.loads(body)
            if d.get("resultCount", 0) >= 1:
                feed = d["results"][0].get("feedUrl")
                if feed:
                    return feed
                last_err = "no feedUrl in result"
        except json.JSONDecodeError:
            last_err = "non-JSON response (Apple returned schema docs)"
        time.sleep(1 + i)
    sys.exit(f"error: iTunes lookup for show {show_id} failed: {last_err}")


_RSS_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out: list[dict] = []
    for it in root.findall(".//item"):
        enc = it.find("enclosure")
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "guid": (it.findtext("guid") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "pub_date": (it.findtext("pubDate") or "").strip(),
            "duration": (it.findtext("itunes:duration", namespaces=_RSS_NS) or "").strip(),
            "author": (it.findtext("itunes:author", namespaces=_RSS_NS) or "").strip(),
            "audio_url": (enc.get("url") if enc is not None else "") or "",
            "audio_type": (enc.get("type") if enc is not None else "") or "",
            "description": it.findtext("description") or "",
            "summary": it.findtext("itunes:summary", namespaces=_RSS_NS) or "",
        })
    return out


def find_episode(items: list[dict], track_id: str | None, slug: str | None) -> dict:
    if slug:
        for it in items:
            if slugify(it["title"]) == slug:
                return it
    if track_id:
        for it in items:
            if track_id in it.get("guid", "") or track_id in it.get("link", ""):
                return it
    if not items:
        sys.exit("error: feed has no items")
    sys.stderr.write("warn: episode not located in feed; defaulting to newest\n")
    return items[0]


def html_to_text(html: str) -> str:
    txt = TAG_RE.sub("", html)
    txt = unescape(txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def download_to(url: str, dest: Path) -> None:
    from omd._download import copy_response_bounded
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r:
        copy_response_bounded(r, dest, label="Podcast audio download")


def _episode_audio_path(workdir: Path, audio_url: str, audio_type: str) -> Path:
    ext = ".mp3" if "mp3" in audio_type.lower() or audio_url.lower().endswith(".mp3") else ".m4a"
    source_id = hashlib.sha256(audio_url.encode("utf-8")).hexdigest()[:12]
    return workdir / f"episode-{source_id}{ext}"


def _rss_duration_seconds(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if ":" not in raw:
            return float(raw)
        total = 0.0
        for part in raw.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None


def compose_markdown(
    page_url: str,
    show_name: str,
    item: dict,
    transcript_text: str,
    polished: str,
    segments: list[dict] | None,
    transcript_source: str = "",
    transcript_warnings: list[str] | None = None,
) -> str:
    title = item["title"] or "Podcast Episode"
    lines = [f"# {title}\n", "## Metadata\n"]
    meta = [
        ("URL", page_url),
        ("Platform", "Apple Podcasts"),
        ("Show", show_name),
        ("Author", item.get("author")),
        ("Published", item.get("pub_date")),
        ("Duration", item.get("duration")),
        ("Audio", item.get("audio_url")),
        ("Transcript source", transcript_source),
    ]
    for k, v in meta:
        if v:
            lines.append(f"- **{k}**: {v}")
    quality_warnings = [str(item) for item in (transcript_warnings or [])]
    if quality_warnings:
        lines.append("- **Transcript status**: Needs review")
        for warning in quality_warnings:
            lines.append(f"- **Transcript warning**: {warning}")
    lines.append("")

    desc_html = item.get("description") or item.get("summary") or ""
    desc = html_to_text(desc_html)
    if desc:
        lines += ["## Description\n", desc, ""]

    if quality_warnings:
        from omd._transcript import TRANSCRIPT_REVIEW_NOTE

        lines += ["## Transcript quality warning\n", TRANSCRIPT_REVIEW_NOTE, ""]

    if polished:
        lines += ["## Transcript (polished)\n", polished, ""]
    if transcript_text:
        lines.append("## Transcript (raw)\n" if polished else "## Transcript\n")
        lines += [transcript_text, ""]
        if segments:
            lines.append("### Timestamped\n")
            for seg in segments:
                m, s = divmod(int(seg.get("start", 0)), 60)
                m2, s2 = divmod(int(seg.get("end", 0)), 60)
                lines.append(f"- [{m:02d}:{s:02d} → {m2:02d}:{s2:02d}] Speaker unknown: {seg.get('text', '').strip()}")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="Apple Podcasts episode or show URL.")
    p.add_argument("-o", "--output", help="Write markdown here (default: stdout).")
    p.add_argument("--whisper-lang", default="en", help="Whisper language hint (default en).")
    p.add_argument("--preferred-languages", default=None,
                   help="Comma-separated common Whisper languages; first is used when --whisper-lang is omitted.")
    p.add_argument("--model", default="mlx-community/whisper-large-v3-turbo", help="Whisper model.")
    p.add_argument("--whisper-backend", default=os.environ.get("OMD_WHISPER_BACKEND", "mlx"),
                   help="Transcription backend (default: mlx; use faster-whisper on Linux hosts).")
    recommended_text_model = recommended_local_text_model()
    p.add_argument("--polish", nargs="?", const=recommended_text_model, default=None, metavar="MODEL",
                   help=f"Post-process transcript with Ollama (memory-sized default: {recommended_text_model}).")
    p.add_argument("--ollama-host", default="http://localhost:11434")
    p.add_argument("--allow-remote-ollama", action="store_true")
    p.add_argument("--no-transcript", action="store_true", help="Metadata only; skip download/transcribe.")
    p.add_argument("--keep", help="Keep intermediates here (default: tmpdir).")
    p.add_argument("--max-duration", type=int, default=None,
                   help="Reject episode audio longer than this many seconds before transcribing.")
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
        None if args.whisper_lang == "en" else args.whisper_lang,
        preferred=args.preferred_languages,
        default="en",
    )

    url = extract_url(args.url)
    if not is_apple_podcast_url(url):
        _events.fatal("url_invalid", f"not an Apple Podcasts URL: {url}")

    show_id, track_id, slug = parse_apple_url(url)
    _progress.info("Resolving Apple Podcasts feed")
    feed_url = lookup_feed_url(show_id)
    _progress.log(f"feed: {feed_url}")

    feed_xml = http_get(feed_url)
    items = parse_feed(feed_xml)
    if not items:
        sys.exit("error: feed parsed but contains no <item> elements")

    item = find_episode(items, track_id, slug)
    show_name = ""
    try:
        root = ET.fromstring(feed_xml)
        show_name = (root.findtext(".//channel/title") or "").strip()
    except ET.ParseError:
        pass

    workdir_ctx = tempfile.TemporaryDirectory() if not args.keep else None
    workdir = Path(args.keep) if args.keep else Path(workdir_ctx.name)
    workdir.mkdir(parents=True, exist_ok=True)

    transcript_text = ""
    polished = ""
    segments: list[dict] = []
    transcript_warnings: list[str] = []
    try:
        if not args.no_transcript and item["audio_url"]:
            audio = _episode_audio_path(workdir, item["audio_url"], item["audio_type"])
            _progress.log(f"downloading {item['audio_url']} → {audio}")
            download_to(item["audio_url"], audio)
            if args.max_duration:
                duration = duration_seconds(audio)
                if duration is None:
                    _events.fatal("duration_unknown", "could not verify episode duration with ffprobe")
                if duration > args.max_duration:
                    _events.fatal("duration_too_long", f"episode is longer than {args.max_duration} seconds")
            _progress.info("Transcribing audio (whisper)")
            tr = transcribe(audio, workdir, args.model, whisper_lang, args.whisper_backend)
            transcript_text = (tr.get("text") or "").strip()
            segments = tr.get("segments") or []
            from omd._transcript import apply_transcript_quality, report_transcript_quality

            transcript_warnings = apply_transcript_quality(
                tr,
                expected_language=whisper_lang,
                expected_duration=_rss_duration_seconds(item.get("duration", "")),
            )
            report_transcript_quality(transcript_warnings, polish_requested=bool(args.polish))
            if args.polish and transcript_text and not transcript_warnings:
                polished = polish_transcript(
                    transcript_text, args.polish, args.ollama_host, segments=segments,
                )

        transcript_source = ""
        if transcript_text:
            transcript_source = f"local Whisper ({args.whisper_backend}, {args.model}, language={whisper_lang})"
        elif args.no_transcript:
            transcript_source = "not requested (--no-transcript)"
        elif not item["audio_url"]:
            transcript_source = "not available in RSS feed"
        md = compose_markdown(
            url,
            show_name,
            item,
            transcript_text,
            polished,
            segments,
            transcript_source,
            transcript_warnings=transcript_warnings,
        )
        if args.output:
            from omd._io import write_atomic
            out = Path(args.output)
            write_atomic(out, md)
            _progress.done(f"wrote {out}")
        else:
            sys.stdout.write(md)
        return 0
    finally:
        if workdir_ctx is not None:
            workdir_ctx.cleanup()


if __name__ == "__main__":
    sys.exit(main())
