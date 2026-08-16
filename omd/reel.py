#!/usr/bin/env python3
"""Convert a Douyin / TikTok / Instagram Reel / YouTube Short URL to Markdown.

Pipeline:
    1. yt-dlp downloads audio + metadata + thumbnail.
    2. mlx_whisper transcribes audio by default (auto-detect language; default Chinese
       hint for Douyin domains).
    3. Optional ffmpeg frame extraction + tesseract OCR catches burned-in
       subtitles common on Douyin reels.
    4. Compose a Markdown document with metadata, transcript, OCR text.

Usage:
    python scripts/reel_to_markdown.py <url> [-o out.md] [--lang zh]
                                              [--model mlx-community/whisper-large-v3-mlx]
                                              [--ocr]

Requires: yt-dlp, ffmpeg, mlx_whisper on PATH by default; tesseract optional
for --ocr. Use --whisper-backend faster-whisper on Linux hosts with the
faster-whisper Python package installed.
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
from pathlib import Path
from urllib.parse import urlparse

from omd._language import DEFAULT_OCR_LANGUAGE
from omd._models import (
    LOCAL_TEXT_CONTEXT_TOKENS,
    bounded_edit_output_budget,
    estimated_text_tokens,
    local_text_model_issue,
    recommended_local_text_model,
)
from omd.ollama_runtime import ollama_keep_alive, request_ollama_json

DOUYIN_HOSTS = {"douyin.com", "v.douyin.com", "iesdouyin.com", "www.douyin.com"}
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_FAST_WHISPER_MODEL = "small"
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)


def is_douyin(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h == host or host.endswith("." + h) for h in {"douyin.com", "iesdouyin.com"})


def cookies_txt_to_string(path: str, host_filter="douyin") -> str:
    filters = (host_filter,) if isinstance(host_filter, str) else tuple(host_filter)
    cookie_path = Path(path).expanduser()
    if not cookie_path.is_file():
        return ""
    out = []
    with cookie_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, _, _, _, name, value = parts[:7]
            if any(f in domain for f in filters):
                out.append(f"{name}={value}")
    return "; ".join(out)


def extract_url(raw: str) -> str:
    """Pull first http(s) URL out of a share-blob like Douyin's
    `5.15 复制打开抖音… https://v.douyin.com/abc/ T@L.wS …`.
    Accepts a bare URL unchanged.
    """
    raw = raw.strip()
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw
    m = URL_RE.search(raw)
    if not m:
        from omd import _events
        _events.fatal("url_not_found", "no http(s) URL found in input")
    return m.group(0).rstrip("/.,;)")


def find_tool(cmd: str) -> str | None:
    path = shutil.which(cmd)
    if path:
        return path
    candidates = [
        Path(sys.executable).parent,
        Path(sys.executable).resolve().parent,
        Path.home() / ".local/share/omd/toolenv-py312/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    candidates[1:1] = [
        Path(p).expanduser()
        for p in os.environ.get("OMD_TOOL_PATH", "").split(os.pathsep)
        if p
    ]
    for directory in candidates:
        candidate = directory / cmd
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def require(cmd: str) -> str:
    path = find_tool(cmd)
    if path:
        return path
    from omd import _events
    _events.fatal("tool_missing", f"`{cmd}` not on PATH")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run subprocess. Capture stdout/stderr in default mode; surface them only
    on failure. Pass-through in --verbose so users can debug live."""
    from omd import _progress
    if not _progress.is_verbose() and "stdout" not in kw and "stderr" not in kw:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
        kw.setdefault("text", True)
    try:
        return subprocess.run(cmd, check=True, **kw)
    except subprocess.CalledProcessError as e:
        # On failure, dump captured output so the user sees the actual error.
        if e.stdout:
            sys.stderr.write(e.stdout if isinstance(e.stdout, str) else e.stdout.decode(errors="replace"))
        if e.stderr:
            sys.stderr.write(e.stderr if isinstance(e.stderr, str) else e.stderr.decode(errors="replace"))
        raise


def _usable_f2_media(path: Path) -> bool:
    """Ignore macOS AppleDouble sidecars and hidden artifacts from external drives."""
    name = path.name
    return path.is_file() and not name.startswith("._") and not name.startswith(".")


def detect_language_hint(url: str, override: str | None, preferred: str | None = None) -> str | None:
    from omd._language import choose_whisper_language
    if override:
        return override
    chosen = choose_whisper_language(None, preferred=preferred)
    if chosen:
        return chosen
    host = urlparse(url).netloc.lower()
    return "zh" if any(h in host for h in DOUYIN_HOSTS) else None


def download_ytdlp(
    url: str,
    workdir: Path,
    cookies_browser: str | None,
    cookies_file: str | None,
    max_duration: int | None = None,
) -> tuple[Path, dict, Path | None]:
    from omd._download import ytdlp_max_filesize_arg
    yt_dlp = require("yt-dlp")
    out_template = str(workdir / "reel.%(ext)s")
    cmd = [
        yt_dlp,
        "--no-playlist",
        "--max-filesize", ytdlp_max_filesize_arg(),
        "--write-info-json",
        "--write-thumbnail",
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", out_template,
    ]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    if max_duration:
        cmd += ["--match-filter", f"duration <= {int(max_duration)}"]
    cmd.append(url)
    run(cmd)
    audio = next(workdir.glob("reel.mp3"))
    info_path = next(workdir.glob("reel.info.json"))
    info = json.loads(info_path.read_text())
    thumbs = sorted([p for p in workdir.glob("reel.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])
    return audio, info, thumbs[0] if thumbs else None


def _yaml_double_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_f2_cookie_config(workdir: Path, cookie_str: str) -> Path:
    cfg = workdir / "f2-dy-cookie.yaml"
    cfg.write_text(
        "douyin:\n"
        f"  cookie: {_yaml_double_quote(cookie_str)}\n"
        "  interval: all\n"
        "  path: Download\n",
        encoding="utf-8",
    )
    cfg.chmod(0o600)
    return cfg


def _build_f2_douyin_command(f2: str, cfg: Path, url: str, workdir: Path) -> list[str]:
    return [
        f2, "dy", "-c", str(cfg), "-M", "one",
        "-u", url, "-p", str(workdir),
        "-m", "true", "-v", "true", "-d", "true",
    ]


def download_f2(url: str, workdir: Path, cookies_file: str | None) -> tuple[Path, dict, Path | None]:
    f2 = require("f2")
    if not cookies_file:
        from omd import _events
        _events.fatal(
            "cookies_missing",
            "Douyin requires --cookies <cookies.txt>. yt-dlp's Douyin "
            "extractor is broken; f2 fallback needs cookies.",
        )
    cookie_str = cookies_txt_to_string(cookies_file, host_filter="douyin")
    if not cookie_str:
        from omd import _events
        _events.fatal("cookies_invalid", f"no douyin cookies found in {cookies_file}")
    with tempfile.TemporaryDirectory() as cookie_cfg_dir:
        cookie_cfg = _write_f2_cookie_config(Path(cookie_cfg_dir), cookie_str)
        cmd = _build_f2_douyin_command(f2, cookie_cfg, url, workdir)
        from omd import _progress
        if _progress.is_verbose():
            proc = subprocess.run(cmd, input="n\n", text=True)
        else:
            proc = subprocess.run(cmd, input="n\n", text=True, capture_output=True)
    if proc.returncode != 0:
        if proc.stdout:
            sys.stderr.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        from omd import _events
        _events.fatal("f2_failed", f"f2 exited {proc.returncode}")
    mp4s = sorted(p for p in workdir.rglob("*_video.mp4") if _usable_f2_media(p))
    if mp4s:
        ffmpeg = require("ffmpeg")
        mp4 = mp4s[0]
        audio = mp4.with_name(mp4.stem.replace("_video", "_audio") + ".mp3")
        run([
            ffmpeg, "-y", "-i", str(mp4),
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            str(audio),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = audio.parent
    else:
        mp3s = sorted(p for p in workdir.rglob("*_music.mp3") if _usable_f2_media(p))
        if not mp3s:
            from omd import _events
            _events.fatal("f2_no_audio", f"f2 produced no audio under {workdir}; check cookies / URL.")
        audio = mp3s[0]
        base = audio.parent
    desc_path = next((p for p in base.glob("*_desc.txt") if _usable_f2_media(p)), None)
    cover = next(
        (
            p for p in base.glob("*_cover.*")
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and _usable_f2_media(p)
        ),
        None,
    )
    info: dict = {
        "extractor_key": "Douyin",
        "webpage_url": url,
        "uploader": base.name,
        "title": audio.stem.replace("_music", ""),
        "description": desc_path.read_text().strip() if desc_path else "",
    }
    return audio, info, cover


def download(
    url: str,
    workdir: Path,
    cookies_browser: str | None,
    cookies_file: str | None,
    max_duration: int | None = None,
) -> tuple[Path, dict, Path | None]:
    if is_douyin(url):
        return download_f2(url, workdir, cookies_file)
    return download_ytdlp(url, workdir, cookies_browser, cookies_file, max_duration=max_duration)


def _transcribe_mlx(audio: Path, workdir: Path, model: str, lang: str | None) -> dict:
    from omd._audio import run_with_estimated_progress
    mlx_whisper = require("mlx_whisper")
    whisper_root = workdir / "_whisper"
    whisper_root.mkdir(parents=True, exist_ok=True)
    out_dir = Path(tempfile.mkdtemp(prefix="run-", dir=whisper_root))
    audio_suffix = audio.suffix if audio.suffix else ".audio"
    short_audio = out_dir / f"audio{audio_suffix}"
    short_audio.symlink_to(audio.resolve(strict=True))
    cmd = [
        mlx_whisper,
        "--model", model,
        "--output-format", "json",
        "--output-dir", str(out_dir),
        "--task", "transcribe",
    ]
    if lang:
        cmd += ["--language", lang]
    cmd += [str(short_audio)]
    run_with_estimated_progress(cmd, audio, "Transcribe")
    result_path = out_dir / f"{short_audio.stem}.json"
    if not result_path.is_file():
        from omd import _events
        _events.fatal("transcribe_failed", f"mlx_whisper produced no JSON for this run in {out_dir}")
    return json.loads(result_path.read_text())


def _transcribe_faster_whisper(audio: Path, model: str, lang: str | None) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        from omd import _events
        _events.fatal("tool_missing", "`faster-whisper` not importable. Install: pip install faster-whisper")
    device = os.environ.get("OMD_FASTER_WHISPER_DEVICE", "auto")
    compute_type = os.environ.get("OMD_FASTER_WHISPER_COMPUTE_TYPE", "default")
    kwargs = {"device": device}
    if compute_type != "default":
        kwargs["compute_type"] = compute_type
    whisper = WhisperModel(model, **kwargs)
    segments_iter, info = whisper.transcribe(str(audio), language=lang)
    segments: list[dict] = []
    text_parts: list[str] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if text:
            text_parts.append(text)
        segment = {"start": float(seg.start), "end": float(seg.end), "text": text}
        compression_ratio = getattr(seg, "compression_ratio", None)
        if compression_ratio is not None:
            segment["compression_ratio"] = float(compression_ratio)
        segments.append(segment)
    return {
        "text": " ".join(text_parts).strip(),
        "segments": segments,
        "language": getattr(info, "language", None),
    }


def transcribe(audio: Path, workdir: Path, model: str, lang: str | None, backend: str | None = None) -> dict:
    selected = (backend or os.environ.get("OMD_WHISPER_BACKEND") or "mlx").strip().lower()
    if selected in {"mlx", "mlx-whisper", "mlx_whisper"}:
        result = _transcribe_mlx(audio, workdir, model, lang)
    elif selected in {"faster", "faster-whisper", "faster_whisper"}:
        result = _transcribe_faster_whisper(audio, model or DEFAULT_FAST_WHISPER_MODEL, lang)
    else:
        from omd import _events
        _events.fatal("flag_invalid", f"unsupported whisper backend: {backend}")
    from omd._transcript import apply_transcript_quality

    apply_transcript_quality(result, expected_language=lang)
    return result


POLISH_CHUNK_SIZE = 1500  # chars; above this, models tend to summarize instead of polish
POLISH_CHUNK_TIMEOUT_SECONDS = float(
    os.environ.get("OMD_POLISH_TIMEOUT", os.environ.get("OMD_POLISH_MD_TIMEOUT", "45"))
)


def _polish_chunk(text: str, model: str, host: str) -> str:
    import urllib.request
    n_chars = len(text)
    prompt = (
        "You are a transcript post-processor. Your task is to correct speech-to-text "
        "errors, punctuation, spacing, and paragraph breaks without changing the content.\n"
        "Strict rules:\n"
        "1. Do not summarize, rewrite, delete, expand, or invent content.\n"
        "2. The corrected text length must stay close to the original length "
        f"(within about +/-5%; original is about {n_chars} characters).\n"
        "3. Only fix likely transcription errors, punctuation, casing, spacing, and line breaks.\n"
        "4. Preserve the source language. English stays English, Chinese stays Chinese, "
        "and mixed-language input keeps the same language balance. Never translate.\n"
        "5. Preserve colloquial wording, filler words, terms, numbers, names, and URLs.\n"
        "6. Output only the corrected transcript text. No explanations, prefixes, "
        "Markdown headings, or bullet lists.\n\n"
        f"Original transcript ({n_chars} chars):\n{text}\n\nCorrected transcript:\n"
    )
    output_budget = bounded_edit_output_budget(text)
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": ollama_keep_alive(),
        "options": {
            "temperature": 0.1,
            "num_ctx": LOCAL_TEXT_CONTEXT_TOKENS,
            "num_predict": output_budget,
        },
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    payload = request_ollama_json(req, timeout=POLISH_CHUNK_TIMEOUT_SECONDS)
    if payload.get("done_reason") == "length":
        raise RuntimeError(
            f"model reached its output limit ({output_budget} tokens) before finishing"
        )
    out = re.sub(
        r"<think>.*?</think>",
        "",
        str(payload.get("response") or ""),
        flags=re.DOTALL,
    ).strip()
    if not out:
        raise RuntimeError("model returned empty transcript")
    source_chars = len(text.strip())
    if source_chars >= 80:
        if len(out) < source_chars * 0.45:
            raise RuntimeError("model output is unexpectedly short; refusing a partial rewrite")
        if len(out) > source_chars * 1.8 + 200:
            raise RuntimeError("model output expanded unexpectedly; refusing a likely explanation")
    return out


def _chunk_segments(segments: list[dict], max_chars: int) -> list[str]:
    """Group whisper segments into chunks of <= max_chars. Falls back to char-split if no segments."""
    chunks: list[str] = []
    cur = ""
    for seg in segments:
        t = (seg.get("text") or "").strip()
        if not t:
            continue
        if len(cur) + len(t) + 1 > max_chars and cur:
            chunks.append(cur)
            cur = t
        else:
            cur = (cur + " " + t).strip() if cur else t
    if cur:
        chunks.append(cur)
    return chunks


def polish_transcript(
    text: str,
    model: str,
    host: str = "http://localhost:11434",
    segments: list[dict] | None = None,
    *,
    allow_remote: bool = False,
) -> str:
    """Chunk-then-polish to keep models in 'edit' mode rather than 'summarize' mode."""
    from omd import _progress
    from omd._network_policy import validate_ollama_host

    validate_ollama_host(host, allow_remote=allow_remote)
    if issue := local_text_model_issue(model):
        _progress.warn(
            f"Ollama transcript polish skipped: {issue}. Keeping the raw transcript."
        )
        return text
    n_chars = len(text)
    if n_chars <= POLISH_CHUNK_SIZE:
        chunks = [text]
    elif segments:
        chunks = _chunk_segments(segments, POLISH_CHUNK_SIZE)
    else:
        chunks = [text[i:i + POLISH_CHUNK_SIZE] for i in range(0, n_chars, POLISH_CHUNK_SIZE)]

    out_parts: list[str] = []
    chunk_units = [estimated_text_tokens(chunk) for chunk in chunks]
    with _progress.ProgressBar(
        "Polish",
        total=sum(chunk_units),
        stage_id="polish",
        unit="tokens",
    ) as bar:
        for i, c in enumerate(chunks, 1):
            _progress.log(f"polish chunk {i}/{len(chunks)} ({len(c)} chars)...")
            try:
                out_parts.append(_polish_chunk(c, model, host))
            except Exception as e:
                remaining = len(chunks) - i
                skipped = (
                    f" Skipping the remaining {remaining} chunk(s) to avoid repeated timeouts."
                    if remaining
                    else " No further model calls will run."
                )
                _progress.warn(
                    f"polish chunk {i}/{len(chunks)} failed: {e}. Keeping this chunk and "
                    f"all remaining transcript text unchanged.{skipped}"
                )
                bar.update(sum(chunk_units[i - 1:]))
                if i == 1:
                    return text
                out_parts.append(c)
                out_parts.extend(chunks[i:])
                break
            bar.update(chunk_units[i - 1])
    out = "\n\n".join(p for p in out_parts if p)
    if len(out) < n_chars * 0.6:
        _progress.warn(
            f"polished output ({len(out)} chars) much shorter than raw ({n_chars})"
        )
    return out


def ocr_thumbnail(thumb: Path, lang: str = DEFAULT_OCR_LANGUAGE) -> str:
    tesseract = find_tool("tesseract")
    if not tesseract:
        return ""
    res = subprocess.run(
        [tesseract, str(thumb), "-", "-l", lang],
        capture_output=True, text=True,
    )
    return res.stdout.strip() if res.returncode == 0 else ""


def fmt_seconds(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m:02d}:{sec:02d}"


def compose_markdown(url: str, info: dict, transcript: dict, ocr_text: str, polished: str = "") -> str:
    lines: list[str] = []
    title = info.get("title") or info.get("description") or info.get("id") or "Reel"
    lines.append(f"# {title}\n")
    meta = [
        ("URL", url),
        ("Platform", info.get("extractor_key") or info.get("extractor")),
        ("Uploader", info.get("uploader") or info.get("creator")),
        ("Uploader ID", info.get("uploader_id")),
        ("Upload date", info.get("upload_date")),
        ("Duration", f"{info.get('duration')}s" if info.get("duration") else None),
        ("Views", info.get("view_count")),
        ("Likes", info.get("like_count")),
        ("Comments", info.get("comment_count")),
        ("Language", transcript.get("language")),
    ]
    lines.append("## Metadata\n")
    for k, v in meta:
        if v not in (None, ""):
            lines.append(f"- **{k}**: {v}")
    quality_warnings = [str(item) for item in transcript.get("quality_warnings", [])]
    if quality_warnings:
        lines.append("- **Transcript status**: Needs review")
        for warning in quality_warnings:
            lines.append(f"- **Transcript warning**: {warning}")
    tags = info.get("tags") or info.get("hashtags") or []
    if tags:
        lines.append(f"- **Tags**: {', '.join(str(t) for t in tags)}")
    lines.append("")

    desc = (info.get("description") or "").strip()
    if desc and desc != title:
        lines += ["## Description\n", desc, ""]

    if quality_warnings:
        from omd._transcript import TRANSCRIPT_REVIEW_NOTE

        lines += ["## Transcript quality warning\n", TRANSCRIPT_REVIEW_NOTE, ""]

    if polished:
        lines += ["## Transcript (polished)\n", polished, ""]

    lines.append("## Transcript (raw)\n" if polished else "## Transcript\n")
    full = (transcript.get("text") or "").strip()
    if full:
        lines.append(full)
        lines.append("")
    segments = transcript.get("segments") or []
    if segments:
        lines.append("### Timestamped\n")
        for seg in segments:
            ts = f"[{fmt_seconds(seg['start'])} → {fmt_seconds(seg['end'])}]"
            lines.append(f"- {ts} {seg['text'].strip()}")
        lines.append("")

    if ocr_text:
        lines += ["## On-screen text (OCR from thumbnail)\n", "```", ocr_text, "```", ""]

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="URL or full share blob (e.g. Douyin '5.15 复制打开抖音…' string).")
    p.add_argument("-o", "--output", help="Write markdown here (default: stdout).")
    p.add_argument("--lang", help="Whisper language hint (e.g. zh, en).")
    p.add_argument("--preferred-languages", default=None,
                   help="Comma-separated common Whisper languages; first is used when --lang is omitted.")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Whisper model.")
    p.add_argument(
        "--whisper-backend",
        default=os.environ.get("OMD_WHISPER_BACKEND", "mlx"),
        choices=["mlx", "mlx-whisper", "mlx_whisper", "faster", "faster-whisper", "faster_whisper"],
        help="Transcription backend (default: mlx; use faster-whisper on Linux hosts).",
    )
    p.add_argument("--ocr", action="store_true", help="OCR thumbnail for burned-in subtitles.")
    recommended_text_model = recommended_local_text_model()
    p.add_argument("--polish", nargs="?", const=recommended_text_model, default=None,
                   metavar="MODEL",
                   help=f"Post-process transcript with Ollama (memory-sized default: {recommended_text_model}).")
    p.add_argument("--ollama-host", default="http://localhost:11434",
                   help="Ollama HTTP host (default http://localhost:11434).")
    p.add_argument(
        "--allow-remote-ollama",
        action="store_true",
        help="Explicitly allow an HTTPS Ollama-compatible endpoint outside this machine.",
    )
    p.add_argument("--keep", help="Keep intermediates in this dir instead of tmpdir.")
    p.add_argument("--max-duration", type=int, default=None,
                   help="Reject media longer than this many seconds when supported by the downloader.")
    p.add_argument("--cookies-from-browser", dest="cookies_browser",
                   help="Read cookies from browser (chrome|safari|firefox|edge|brave). Use for region-locked Douyin.")
    p.add_argument("--cookies", dest="cookies_file", help="Path to Netscape-format cookies.txt for yt-dlp.")
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

    args.url = extract_url(args.url)
    lang = detect_language_hint(args.url, args.lang, args.preferred_languages)

    workdir_ctx = (
        tempfile.TemporaryDirectory()
        if not args.keep
        else None
    )
    workdir = Path(args.keep) if args.keep else Path(workdir_ctx.name)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        _progress.info("Downloading reel")
        audio, info, thumb = download(
            args.url,
            workdir,
            args.cookies_browser,
            args.cookies_file,
            max_duration=args.max_duration,
        )
        _progress.info("Transcribing audio (whisper)")
        transcript = transcribe(audio, workdir, args.model, lang, args.whisper_backend)
        from omd._transcript import apply_transcript_quality, report_transcript_quality

        quality_warnings = apply_transcript_quality(
            transcript,
            expected_language=lang,
            expected_duration=info.get("duration"),
        )
        report_transcript_quality(quality_warnings, polish_requested=bool(args.polish))
        ocr_text = ocr_thumbnail(thumb) if args.ocr and thumb else ""
        polished = ""
        if args.polish and not quality_warnings:
            raw_text = (transcript.get("text") or "").strip()
            if raw_text:
                polished = polish_transcript(
                    raw_text,
                    args.polish,
                    args.ollama_host,
                    segments=transcript.get("segments"),
                    **({"allow_remote": True} if args.allow_remote_ollama else {}),
                )
        md = compose_markdown(args.url, info, transcript, ocr_text, polished)
        if args.output:
            from omd._io import write_atomic
            write_atomic(Path(args.output), md)
            _progress.done(f"wrote {args.output}")
        else:
            sys.stdout.write(md)
        return 0
    finally:
        if workdir_ctx is not None:
            workdir_ctx.cleanup()


if __name__ == "__main__":
    sys.exit(main())
