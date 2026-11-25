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
import contextlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Iterator
from typing import NoReturn
from urllib.parse import urljoin, urlparse

from omd._cookies import (
    DOUYIN_COOKIE_DOMAINS,
    CookieFileError,
    host_matches,
    write_scoped_cookie_jar,
)
from omd._language import DEFAULT_OCR_LANGUAGE
from omd._models import (
    LOCAL_TEXT_CONTEXT_TOKENS,
    bounded_edit_output_budget,
    estimated_text_tokens,
    local_text_model_issue,
    recommended_local_text_model,
)
from omd._source_url import SourceURLCountError, extract_single_http_url
from omd.ollama_runtime import ollama_keep_alive, request_ollama_json

DOUYIN_HOSTS = {"douyin.com", "v.douyin.com", "iesdouyin.com", "www.douyin.com"}
DOUYIN_SHORTLINK_HOSTS = ("v.douyin.com",)
MAX_DOUYIN_REDIRECTS = 5
DOUYIN_PRIVATE_DIR_PREFIX = "omd-reel-cookies-"
_DOUYIN_PRIVATE_DIR_RE = re.compile(
    rf"^{re.escape(DOUYIN_PRIVATE_DIR_PREFIX)}(?P<pid>[0-9]+)-[A-Za-z0-9_-]+$"
)
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_FAST_WHISPER_MODEL = "small"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class DouyinRedirectError(ValueError):
    """Safe shortlink rejection without exposing the submitted URL."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _prune_stale_douyin_cookie_dirs(temp_root: Path | None = None) -> None:
    """Remove private jars left by an uncatchable process termination."""
    root = Path(tempfile.gettempdir()) if temp_root is None else Path(temp_root)
    try:
        root_resolved = root.resolve(strict=True)
        entries = list(root.iterdir())
    except OSError:
        return
    current_pid = os.getpid()
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    for entry in entries:
        match = _DOUYIN_PRIVATE_DIR_RE.fullmatch(entry.name)
        if match is None:
            continue
        pid = int(match.group("pid"))
        if pid == current_pid or _pid_is_running(pid):
            continue
        try:
            entry_stat = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
                continue
            if current_uid is not None and entry_stat.st_uid != current_uid:
                continue
            resolved = entry.resolve(strict=True)
            if resolved.parent != root_resolved:
                continue
            shutil.rmtree(resolved)
        except OSError:
            # Fail closed: an uncertain entry is never followed or removed.
            continue


@contextlib.contextmanager
def _private_douyin_cookie_dir(temp_root: Path | None = None) -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) if temp_root is None else Path(temp_root)
    _prune_stale_douyin_cookie_dirs(root)
    directory = Path(
        tempfile.mkdtemp(
            prefix=f"{DOUYIN_PRIVATE_DIR_PREFIX}{os.getpid()}-",
            dir=str(root),
        )
    )
    directory.chmod(0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextlib.contextmanager
def _sigterm_as_system_exit() -> Iterator[None]:
    """Make process-group cancellation unwind Python cleanup handlers."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def is_douyin(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(h == host or host.endswith("." + h) for h in {"douyin.com", "iesdouyin.com"})


def extract_url(raw: str) -> str:
    """Pull first http(s) URL out of a share-blob like Douyin's
    `5.15 复制打开抖音… https://v.douyin.com/abc/ T@L.wS …`.
    Accepts a bare URL unchanged.
    """
    try:
        url = extract_single_http_url(raw.strip())
    except SourceURLCountError as exc:
        from omd import _events

        _events.fatal("source_url_count_invalid", str(exc))
    if not url:
        from omd import _events
        _events.fatal("url_not_found", "no http(s) URL found in input")
    return url


def _validate_douyin_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise DouyinRedirectError(
            "Douyin rejected a malformed shortlink destination. No cookies were sent."
        ) from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not host_matches(host, DOUYIN_COOKIE_DOMAINS)
    ):
        raise DouyinRedirectError(
            "Douyin rejected a shortlink destination outside approved HTTPS hosts. "
            "No cookies were sent."
        )
    return host


def _douyin_http_get(url: str) -> tuple[int, dict, str]:
    """Fetch one redirect hop without cookies or automatic redirects."""
    _validate_douyin_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.geturl()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, dict(exc.headers or {}), exc.geturl()
        finally:
            exc.close()


def expand_douyin_short_url(url: str) -> str:
    """Resolve a bounded Douyin shortlink chain before any cookie use."""
    host = _validate_douyin_url(url)
    if not host_matches(host, DOUYIN_SHORTLINK_HOSTS):
        return url
    current = url
    try:
        for _hop in range(MAX_DOUYIN_REDIRECTS):
            status, headers, final = _douyin_http_get(current)
            location = headers.get("Location") or headers.get("location")
            if status in (301, 302, 303, 307, 308) and location:
                target = urljoin(current, location)
                target_host = _validate_douyin_url(target)
                if not host_matches(target_host, DOUYIN_SHORTLINK_HOSTS):
                    return target
                current = target
                continue
            final_host = _validate_douyin_url(final)
            if host_matches(final_host, DOUYIN_SHORTLINK_HOSTS):
                raise DouyinRedirectError(
                    "Douyin did not resolve the shortlink to a content URL. No cookies "
                    "were sent; check the link and retry."
                )
            return final
    except DouyinRedirectError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise DouyinRedirectError(
            "Douyin could not expand the shortlink anonymously. Check the network "
            "and retry. No cookies were sent."
        ) from exc
    raise DouyinRedirectError(
        "Douyin rejected a shortlink with too many redirects. No cookies were sent."
    )


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


def detect_language_hint(url: str, override: str | None, preferred: str | None = None) -> str | None:
    from omd._language import choose_whisper_language

    chosen = choose_whisper_language(override, preferred=preferred)
    if override is not None:
        return chosen
    if chosen:
        return chosen
    host = urlparse(url).netloc.lower()
    return "zh" if any(h in host for h in DOUYIN_HOSTS) else None


def _resolve_whisper_cli_language(
    legacy: str | None,
    explicit: str | None,
) -> tuple[str | None, bool]:
    """Reconcile reel's deprecated ASR-only ``--lang`` alias."""
    if legacy is not None and any(separator in legacy for separator in ("+", ",")):
        raise ValueError(
            "reel --lang accepts a single Whisper language; use --ocr-language for OCR sets"
        )
    from omd.cli import _coalesce_whisper_language_values

    return _coalesce_whisper_language_values(legacy, explicit), legacy is not None


def download_ytdlp(
    url: str,
    workdir: Path,
    cookies_browser: str | None,
    cookies_file: str | None,
    max_duration: int | None = None,
) -> tuple[Path, dict, Path | None]:
    yt_dlp = require("yt-dlp")
    cmd = _build_ytdlp_command(
        yt_dlp,
        url,
        workdir,
        cookies_browser=cookies_browser,
        cookies_file=cookies_file,
        max_duration=max_duration,
    )
    run(cmd)
    return _read_ytdlp_outputs(workdir)


def _build_ytdlp_command(
    yt_dlp: str,
    url: str,
    workdir: Path,
    *,
    cookies_browser: str | None,
    cookies_file: str | None,
    max_duration: int | None,
) -> list[str]:
    from omd._download import ytdlp_max_filesize_arg

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
    return cmd


def _read_ytdlp_outputs(workdir: Path) -> tuple[Path, dict, Path | None]:
    audio = next(workdir.glob("reel.mp3"))
    info_path = next(workdir.glob("reel.info.json"))
    info = json.loads(info_path.read_text())
    thumbs = sorted([p for p in workdir.glob("reel.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])
    return audio, info, thumbs[0] if thumbs else None


def _yt_dlp_has_douyin_extractor(yt_dlp: str) -> bool:
    try:
        result = subprocess.run(
            [yt_dlp, "--list-extractors"],
            text=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return any(
        line.strip().split(maxsplit=1)[0].casefold() == "douyin"
        for line in result.stdout.splitlines()
        if line.strip()
    )


def _douyin_failure_is_cookie_rejection(output: str) -> bool:
    lowered = output.casefold()
    return any(
        marker in lowered
        for marker in (
            "authentication required",
            "cookies are invalid",
            "cookies are no longer valid",
            "fresh cookies",
            "login required",
            "please log in",
            "requires authentication",
            "sign in to confirm",
        )
    )


def _fatal_douyin(kind: str, message: str) -> NoReturn:
    from omd import _events

    _events.fatal(kind, message)
    raise AssertionError("unreachable")


def download_douyin_ytdlp(
    url: str,
    workdir: Path,
    cookies_file: str | None,
    *,
    max_duration: int | None = None,
) -> tuple[Path, dict, Path | None]:
    """Download Douyin with a scoped cookie jar; never flatten cookies."""
    yt_dlp = require("yt-dlp")
    if not _yt_dlp_has_douyin_extractor(yt_dlp):
        _fatal_douyin(
            "douyin_fetch_failed",
            "The installed yt-dlp does not include the Douyin extractor. Upgrade "
            "yt-dlp with `python -m pip install --upgrade yt-dlp` or your package "
            "manager, then retry.",
        )
    try:
        resolved_url = expand_douyin_short_url(url)
    except DouyinRedirectError as exc:
        _fatal_douyin("douyin_redirect_rejected", str(exc))

    with _private_douyin_cookie_dir() as cookie_dir:
        private_jar = cookie_dir / "cookies.txt"
        try:
            write_scoped_cookie_jar(
                cookies_file,
                DOUYIN_COOKIE_DOMAINS,
                private_jar,
                platform="Douyin",
            )
        except CookieFileError as exc:
            _fatal_douyin(exc.kind, str(exc))
        except OSError:
            _fatal_douyin(
                "douyin_fetch_failed",
                "Douyin could not prepare a private cookie jar. Check local temporary "
                "file permissions, then retry.",
            )

        cmd = _build_ytdlp_command(
            yt_dlp,
            resolved_url,
            workdir,
            cookies_browser=None,
            cookies_file=str(private_jar),
            max_duration=max_duration,
        )
        try:
            result = subprocess.run(cmd, text=True, capture_output=True)
        except OSError:
            _fatal_douyin(
                "douyin_fetch_failed",
                "yt-dlp could not start the Douyin download. Reinstall or upgrade "
                "yt-dlp, then retry.",
            )

    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}"
        if _douyin_failure_is_cookie_rejection(output):
            _fatal_douyin(
                "cookies_rejected",
                "Douyin rejected the supplied cookies. Sign in again, export fresh "
                "Douyin cookies, then retry.",
            )
        if "unsupported url" in output.casefold() or "no suitable extractor" in output.casefold():
            _fatal_douyin(
                "douyin_fetch_failed",
                "The installed yt-dlp could not handle this Douyin URL. Upgrade yt-dlp "
                "with `python -m pip install --upgrade yt-dlp`, then retry.",
            )
        _fatal_douyin(
            "douyin_fetch_failed",
            "yt-dlp could not fetch this Douyin URL. Check that the link is public "
            "and still available, update yt-dlp, then retry.",
        )

    try:
        return _read_ytdlp_outputs(workdir)
    except (OSError, StopIteration, json.JSONDecodeError):
        _fatal_douyin(
            "douyin_fetch_failed",
            "yt-dlp finished without usable Douyin audio or metadata. Update yt-dlp "
            "and retry; if it repeats, check that the post is still available.",
        )
        raise AssertionError("unreachable")


def download(
    url: str,
    workdir: Path,
    cookies_browser: str | None,
    cookies_file: str | None,
    max_duration: int | None = None,
) -> tuple[Path, dict, Path | None]:
    if is_douyin(url):
        return download_douyin_ytdlp(
            url,
            workdir,
            cookies_file,
            max_duration=max_duration,
        )
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


def ocr_thumbnail(
    thumb: Path,
    lang: str = DEFAULT_OCR_LANGUAGE,
    *,
    executable: str | None = None,
) -> str:
    from omd.cli import _prepare_ocr_execution

    if executable is None:
        lang, executable = _prepare_ocr_execution(lang)
    res = subprocess.run(
        [executable, str(thumb), "-", "-l", lang],
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


def _main() -> int:
    from omd.config import SPOKEN_LANGUAGE_MODES

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="URL or full share blob (e.g. Douyin '5.15 复制打开抖音…' string).")
    p.add_argument("-o", "--output", help="Write markdown here (default: stdout).")
    p.add_argument(
        "--lang",
        dest="legacy_whisper_lang",
        help="Deprecated ASR alias for --whisper-lang (one language code only).",
    )
    p.add_argument(
        "--whisper-lang",
        default=None,
        help="Whisper language hint such as zh/en, or auto; omit to inherit adapter/config defaults.",
    )
    p.add_argument(
        "--spoken-language-mode",
        choices=SPOKEN_LANGUAGE_MODES,
        default=None,
        help="Spoken-language policy; explicit requires --whisper-lang.",
    )
    p.add_argument(
        "--ocr-lang",
        "--ocr-language",
        dest="ocr_language",
        default=None,
        help="Tesseract OCR language set for --ocr (for example chi_sim+eng).",
    )
    p.add_argument("--preferred-languages", default=None,
                   help="Comma-separated common Whisper languages; first is used when --whisper-lang is omitted.")
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
    try:
        whisper_override, used_legacy_lang = _resolve_whisper_cli_language(
            args.legacy_whisper_lang,
            args.whisper_lang,
        )
        from omd.config import config_path, load_config, resolve_capture_config

        runtime_config_path = config_path()
        configured = load_config(runtime_config_path) if runtime_config_path.exists() else None
        from omd._language import parse_preferred_languages

        spoken_mode = args.spoken_language_mode
        spoken_hint = whisper_override
        if spoken_mode is None:
            if whisper_override == "auto":
                spoken_mode, spoken_hint = "auto-detect", None
            elif whisper_override is not None:
                spoken_mode = "explicit"
            elif args.preferred_languages is not None:
                preferred = parse_preferred_languages(args.preferred_languages)
                spoken_mode = "explicit" if preferred else "inherit-adapter-default"
                spoken_hint = preferred[0] if preferred else None
        elif spoken_mode != "explicit" and whisper_override is not None:
            raise ValueError(
                "--whisper-lang may only accompany explicit spoken-language mode"
            )
        resolved_capture = resolve_capture_config(
            configured,
            explicit_ocr_language=args.ocr_language,
            explicit_spoken_language_mode=spoken_mode,
            explicit_spoken_language_hint=spoken_hint,
        )
    except ValueError as exc:
        _events.fatal("language_config_invalid", str(exc))
    if used_legacy_lang:
        _progress.warn("reel --lang is deprecated; use --whisper-lang for speech recognition")
    if args.polish:
        from omd._network_policy import validate_ollama_host

        try:
            validate_ollama_host(args.ollama_host, allow_remote=args.allow_remote_ollama)
        except ValueError as exc:
            _events.fatal("ollama_host_blocked", str(exc))

    args.url = extract_url(args.url)
    if resolved_capture.spoken_language_mode == "auto-detect":
        detection_override = "auto"
        preferred_languages = None
    elif resolved_capture.spoken_language_mode == "explicit":
        detection_override = resolved_capture.spoken_language_hint
        preferred_languages = None
    else:
        detection_override = None
        # The policy is already resolved; an empty preference list prevents
        # environment settings from overriding the selected adapter default.
        preferred_languages = ""
    lang = detect_language_hint(args.url, detection_override, preferred_languages)
    ocr_language = resolved_capture.ocr_language
    ocr_executable: str | None = None
    if args.ocr:
        from omd.cli import _prepare_ocr_execution

        ocr_language, ocr_executable = _prepare_ocr_execution(ocr_language)

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
        ocr_text = (
            ocr_thumbnail(thumb, ocr_language, executable=ocr_executable)
            if args.ocr and thumb
            else ""
        )
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


def main() -> int:
    _prune_stale_douyin_cookie_dirs()
    with _sigterm_as_system_exit():
        return _main()


if __name__ == "__main__":
    sys.exit(main())
