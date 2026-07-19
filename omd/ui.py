"""omd UI - minimal Gradio wrapper around the `omd` CLI.

Run: `omd-ui` (after `pip install -e '.[ui]'`) or `python -m omd.ui`.

Opens a local browser tab. Paste a URL/share-blob or pick a file, choose an
output folder or vault folder, tick options, hit Run. Stderr streams to the log
pane; the finished `.md` previews below it. The output folder doubles as the
video download dir (--keep), so the .mp4/.mp3/.info.json land next to the text
output.
"""
from __future__ import annotations

import atexit
import html
import os
import json
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlparse

from omd.batch import iter_batch_items
from omd._models import (
    local_text_model_issue,
    local_text_model_recommendation,
    model_parameter_billions,
    recommended_local_text_model,
)

DEFAULT_OUTPUT_DIR = str(Path.home() / "omd_out")
DEFAULT_VAULT_DIR = str(Path.home() / "Obsidian" / "AI-Memory")
OMD_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "omd"
COOKIES_STAGING = OMD_DATA_DIR / "cookies"
BATCH_STAGING = OMD_DATA_DIR / "batch-lists"
UI_STAGING_ROOT = Path(tempfile.gettempdir()) / f"omd-ui-{os.getpid()}"
SOURCE_STAGING = UI_STAGING_ROOT / "sources"
DOWNLOAD_STAGING = UI_STAGING_ROOT / "downloads"
DOWNLOAD_STAGING_MAX_AGE_SECONDS = 60 * 60
DOWNLOAD_STAGING_MAX_FILES = 10
SOURCE_FILE_LIMIT = 5
DEFAULT_COOKIES = str(COOKIES_STAGING / "douyin_cookies.txt")
LEGACY_DEFAULT_COOKIES = str(Path.home() / "Desktop" / "douyin_cookies.txt")
PROJECT_URL = "https://github.com/omd-local/markdown-everything"
STARTUP_RECOMMENDED_TEXT_MODEL = recommended_local_text_model()
PUBLIC_DEMO_OUTPUT_DIR = Path(tempfile.gettempdir()) / "omd-public-demo"
PUBLIC_DEMO_MAX_UPLOAD_MB = 100
PUBLIC_DEMO_MAX_MEDIA_SECONDS = 600
UI_MEMORY_TIMEOUT_SECONDS = 45
URL_RE = re.compile(r"https?://[^\s'\"<>，。、）)】]+", re.UNICODE)
BATCH_COUNT_RE = re.compile(r"batch:\s+(\d+)\s+items")
BATCH_ITEM_RE = re.compile(r"\[(\d+)/(\d+)\]")
BATCH_SUCCEEDED_RE = re.compile(r"\[(\d+)/(\d+)\]\s+converted:")
BATCH_FAILED_RE = re.compile(r"warn:\s+\[(\d+)/(\d+)\]\s+failed:")
BATCH_FAILURE_SUMMARY_RE = re.compile(r"batch complete with failures:\s+(\d+)/(\d+)\s+succeeded")
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LOCAL_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SOURCE_PLACEHOLDER = """
Paste a link, share text, or a local file path. Use one item per line for a small batch.

Examples:
https://example.com/article
https://www.reddit.com/r/.../comments/...
https://mp.weixin.qq.com/s/...
https://x.com/.../status/...
/Users/me/Downloads/report.pdf

App share text also works:
8.92 复制打开抖音，看看... https://v.douyin.com/yGWf39cCbCE/
32 复制本条信息，打开【小红书】App查看精彩内容！http://xhslink.com/a/abcDEF/

Blank lines and # comments are ignored.
""".strip()
SOURCE_QUICK_START = """
- Drop one or more files, or paste one or more URLs/share texts above.
- For sites that may need login, click **Inspect source / cookies** before Start.
- Choose **Capture to vault note** for Obsidian. It writes the note directly into the vault folder you choose.
- Use separate cookies files for Douyin and XHS. X, Threads, and Reddit use public-only fallbacks, so login-gated posts may fail.

Typed text wins over the uploaded file.
""".strip()
LOCAL_MODEL_NOTICE = f"""
**Local AI setup**

OMD never downloads Ollama models automatically.

**Set up once**

1. Install and start Ollama on this Mac.
2. Open Terminal and run `ollama pull {STARTUP_RECOMMENDED_TEXT_MODEL}`.
3. Keep **Ollama host** set to `http://localhost:11434` unless your local Ollama server uses another address.

**Model choice**

`{STARTUP_RECOMMENDED_TEXT_MODEL}` is recommended for this Mac based on available memory; it is not a fixed default for every computer. You can choose another installed instruct model in **Advanced settings**. Avoid the plain `qwen3:4b` tag because it currently points to a thinking-only model that is unsuitable for bounded copy editing.

**Language**

Markdown polish preserves the source language: English stays English, Chinese stays Chinese, and mixed-language notes stay mixed. For audio or video, leave **Spoken language hint** blank for automatic or platform defaults, use `en` for English, or use `zh,en` for Chinese and English.

**If local AI is unavailable**

- If Ollama is stopped or the selected model is missing, OMD still saves the raw Markdown and skips AI-added sections.
- A missing Evidence or generated-tags warning means the raw capture was saved, but you should review the AI-added sections.
""".strip()
PROCESS_LOG_DISCLAIMER = """
### Personal note use only

OMD is designed for personal research, note-taking, and AI-assisted knowledge workflows. It is not a legal, compliance, evidentiary, or archival system.

#### Use and access

- Only process content you are authorised to access and store. You are responsible for any permissions required to process it.
- OMD does not bypass paywalls, access controls, or platform restrictions.
- A source may fail even when it opens in your browser. Common causes include VPN or proxy IP blocking, data-centre IP filtering, workplace or organisation network restrictions, rate limits after repeated requests, or capturing many posts in a short period.
- A capture may also fail when content is private, age- or NSFW-gated, quarantined, deleted, or requires sign-in.
- Reddit and other platforms can change their access rules without notice. A failed capture does not necessarily mean the link is invalid.

#### Check what was saved

- Output may omit, reorder, or reformat source content.
- Review AI-generated summaries, tags, Evidence references, and [[links]] before relying on them.
""".strip()

COOKIES_TUTORIAL = """
### Cookie file

抖音 / 小红书 / B站 may require cookies. YouTube and podcasts usually do not.

1. Log in to the target site in your browser.
2. Export a Netscape-format `.txt` file with a cookies extension.
3. Choose that `.txt` file here, or drop it into the upload field.
""".strip()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _public_demo_enabled() -> bool:
    return _env_flag("OMD_PUBLIC_DEMO")


def _public_demo_output_dir() -> Path:
    return Path(os.environ.get("OMD_PUBLIC_DEMO_OUTPUT_DIR", str(PUBLIC_DEMO_OUTPUT_DIR))).expanduser()


def _public_demo_max_upload_bytes() -> int:
    try:
        mb = int(os.environ.get("OMD_PUBLIC_DEMO_MAX_UPLOAD_MB", str(PUBLIC_DEMO_MAX_UPLOAD_MB)))
    except ValueError:
        mb = PUBLIC_DEMO_MAX_UPLOAD_MB
    return max(1, mb) * 1024 * 1024


def _public_demo_max_media_seconds() -> int:
    try:
        seconds = int(os.environ.get("OMD_PUBLIC_DEMO_MAX_MEDIA_SECONDS", str(PUBLIC_DEMO_MAX_MEDIA_SECONDS)))
    except ValueError:
        seconds = PUBLIC_DEMO_MAX_MEDIA_SECONDS
    return max(1, seconds)


def _unique_staged_path(folder: Path, prefix: str, source_name: str) -> Path:
    return folder / f"{prefix}_{time.time_ns()}_{Path(source_name).name}"


atexit.register(shutil.rmtree, UI_STAGING_ROOT, ignore_errors=True)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _prune_stale_ui_staging_roots(
    *,
    temp_root: Path | None = None,
    current_pid: int | None = None,
) -> None:
    """Remove OMD temp roots whose owning process no longer exists."""
    root = Path(temp_root or tempfile.gettempdir())
    own_pid = os.getpid() if current_pid is None else current_pid
    try:
        resolved_root = root.resolve()
        candidates = list(root.glob("omd-ui-*"))
    except OSError:
        return
    for candidate in candidates:
        try:
            pid = int(candidate.name.removeprefix("omd-ui-"))
        except ValueError:
            continue
        if pid == own_pid or _pid_is_running(pid) or candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            if candidate.resolve().parent != resolved_root:
                continue
            shutil.rmtree(candidate)
        except OSError:
            continue


def _stage_cookies(uploaded_path: str | None) -> str:
    """Copy an uploaded cookies file into a stable staging dir so the path survives
    the temp cleanup gradio does on the original upload. Returns the new path."""
    return _stage_uploaded_file(uploaded_path, folder=COOKIES_STAGING, label="Cookie file")


def _default_polish_model() -> str:
    configured = os.environ.get("OMD_POLISH_MODEL", "").strip()
    if configured:
        return configured
    recommendation = local_text_model_recommendation()
    fallback_model = recommendation.model
    ollama = shutil.which("ollama") or next(
        (
            str(path)
            for path in (Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama"))
            if path.is_file()
        ),
        "",
    )
    if not ollama:
        return fallback_model
    try:
        proc = subprocess.run(
            [ollama, "list"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback_model
    installed: list[str] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            installed.append(parts[0])
    if fallback_model in installed:
        return fallback_model
    compatible_installed = [
        (parameters, model)
        for model in installed
        if "instruct" in model.lower()
        and local_text_model_issue(model) is None
        and (parameters := model_parameter_billions(model)) is not None
        and parameters <= recommendation.max_parameters_billions
    ]
    if compatible_installed:
        return max(compatible_installed)[1]
    return fallback_model


def _ollama_model_names(host: str, *, timeout: float = 0.75) -> tuple[set[str] | None, str]:
    host = (host or "http://localhost:11434").strip() or "http://localhost:11434"
    from omd._network_policy import validate_ollama_host

    try:
        validate_ollama_host(host)
    except ValueError as exc:
        return (
            None,
            "Ollama model checks only support a loopback host such as "
            f"http://localhost:11434: {exc}",
        )
    if "://" not in host:
        host = f"http://{host}"
    request = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"Ollama is not reachable at {host}: {exc}"
    models = payload.get("models") if isinstance(payload, dict) else None
    names = {
        str(item.get("name") or item.get("model") or "")
        for item in models or []
        if isinstance(item, dict)
    }
    return {name for name in names if name}, ""


def _local_model_status_html(
    markdown_model: str,
    memory_model: str = "",
    host: str = "http://localhost:11434",
    *,
    public_demo: bool | None = None,
) -> str:
    is_public_demo = _public_demo_enabled() if public_demo is None else public_demo
    if is_public_demo:
        return (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>Hosted demo LLM:</strong> Hugging Face demo mode does not auto-load, "
            "does not auto-download, and does not run local Ollama models. "
            "Local model polish is disabled until a hosted LLM provider is explicitly configured."
            "</div>"
        )

    host = (host or "http://localhost:11434").strip() or "http://localhost:11434"
    recommended_model = recommended_local_text_model()
    requested = []
    for model in (markdown_model, memory_model):
        model_name = (model or recommended_model).strip() or recommended_model
        if model_name not in requested:
            requested.append(model_name)

    incompatible = [issue for model in requested if (issue := local_text_model_issue(model))]
    if incompatible:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Local model warning:</strong> "
            f"{html.escape(incompatible[0])}. Open Terminal and run "
            f"<code>ollama pull {html.escape(recommended_model)}</code>. "
            "OMD keeps the converted Markdown unchanged until a compatible model is selected."
            "</div>"
        )

    names, reason = _ollama_model_names(host)
    if names is None:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Local model warning:</strong> "
            f"{html.escape(reason)}. Start Ollama, then run "
            f"<code>ollama pull {html.escape(requested[0])}</code> in Terminal if the model is missing."
            "</div>"
        )
    if not names:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Local model warning:</strong> Ollama is reachable but no local models were found. "
            f"Open Terminal and run <code>ollama pull {html.escape(requested[0])}</code>."
            "</div>"
        )

    missing = [model for model in requested if model not in names]
    if missing:
        commands = ", ".join(f"<code>ollama pull {html.escape(model)}</code>" for model in missing)
        installed = ", ".join(html.escape(model) for model in requested if model in names) or "none"
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Local model warning:</strong> missing "
            f"{', '.join(html.escape(model) for model in missing)}. "
            f"Installed selected models: {installed}. This only affects optional AI polish; "
            "Markdown conversion still works without it. Run "
            f"{commands} in Terminal, or choose an installed model."
            "</div>"
        )

    return (
        '<div class="omd-model-status omd-model-status-ok">'
        "<strong>Local model ready:</strong> Ollama is reachable and the requested model"
        f"{'s are' if len(requested) > 1 else ' is'} installed: "
        f"{', '.join(html.escape(model) for model in requested)}."
        "</div>"
    )


def _stage_batch_list(uploaded_path: str | None) -> str:
    """Copy an uploaded URL list into a stable path for the batch subprocess."""
    return _stage_uploaded_file(uploaded_path, folder=BATCH_STAGING, label="Batch list")


def _stage_uploaded_file(
    uploaded_path: str | None,
    *,
    folder: Path,
    label: str,
    preserve_name: bool = False,
) -> str:
    if not uploaded_path:
        return ""
    src = Path(uploaded_path)
    if not src.exists():
        return ""
    if not src.is_file():
        raise ValueError(f"{label} must be a file: {src}")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if preserve_name:
            item_folder = folder / f"uploaded_{time.time_ns()}"
            item_folder.mkdir(mode=0o700)
            dest = item_folder / src.name
        else:
            dest = _unique_staged_path(folder, "uploaded", src.name)
        shutil.copy(src, dest)
        dest.chmod(0o600)
    except (OSError, shutil.Error) as exc:
        raise ValueError(f"Could not stage {label.lower()}: {exc}") from exc
    return str(dest)


def _batch_items_from_text(text: str) -> list[str]:
    """Batch text format: one URL/share blob/path per line; blank/# ignored."""
    urls = [m.group(0).rstrip("/.,;)") for m in URL_RE.finditer(text)]
    if len(urls) > 1:
        return urls
    return list(iter_batch_items(text.splitlines()))


def _contains_reddit_source(items: Sequence[str]) -> bool:
    from omd.cli import extract_url_from_blob, is_reddit_url

    for item in items:
        url = extract_url_from_blob(item)
        if url and is_reddit_url(url):
            return True
    return False


def _uploaded_file_paths(file_input: object) -> list[str]:
    if not file_input:
        return []
    values = file_input if isinstance(file_input, (list, tuple)) else [file_input]
    paths: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, (str, Path)):
            paths.append(str(value).strip())
            continue
        if isinstance(value, dict):
            candidate = value.get("staged_path") or value.get("path") or value.get("name")
            if candidate:
                paths.append(str(candidate).strip())
            continue
        candidate = getattr(value, "name", None)
        if candidate:
            paths.append(str(candidate).strip())
    return [path for path in paths if path]


def _source_file_queue_entries(file_input: object) -> list[dict[str, str]]:
    if not file_input:
        return []
    values = file_input if isinstance(file_input, (list, tuple)) else [file_input]
    entries: list[dict[str, str]] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, dict):
            source_path = str(
                value.get("source_path") or value.get("path") or value.get("name") or ""
            ).strip()
            staged_path = str(value.get("staged_path") or "").strip()
            display_name = str(
                value.get("display_name")
                or value.get("orig_name")
                or Path(source_path or staged_path).name
            ).strip()
        else:
            source_path = str(value).strip()
            staged_path = ""
            display_name = Path(source_path).name
        if source_path or staged_path:
            entries.append(
                {
                    "source_path": source_path or staged_path,
                    "staged_path": staged_path,
                    "display_name": display_name or Path(source_path or staged_path).name,
                }
            )
    return entries


def _source_file_queue_summary(file_input: object, *, rejected_count: int = 0) -> str:
    entries = _source_file_queue_entries(file_input)
    count = len(entries)
    if count == 0:
        detail = "Add PDF, image, document, audio, or other supported files."
    elif count >= SOURCE_FILE_LIMIT:
        detail = "Limit reached. Remove one file before adding another."
    else:
        detail = "Add more with the upload button above."
    lines = [f"**{count} of {SOURCE_FILE_LIMIT} files queued.** {detail}"]
    if rejected_count:
        noun = "file was" if rejected_count == 1 else "files were"
        lines.extend(
            [
                "",
                f"> **File limit reached:** {rejected_count} extra {noun} not added.",
            ]
        )
    return "\n".join(lines)


def _source_file_identity(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _merge_source_file_queue(current: object, uploaded: object) -> tuple[list[dict[str, str]], str]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    rejected_count = 0
    candidates = [*_source_file_queue_entries(current), *_source_file_queue_entries(uploaded)]
    for entry in candidates:
        source_path = entry["source_path"]
        staged_path = entry["staged_path"]
        identities = {_source_file_identity(source_path)}
        if staged_path:
            identities.add(_source_file_identity(staged_path))
        if seen.intersection(identities):
            continue
        if len(merged) >= SOURCE_FILE_LIMIT:
            rejected_count += 1
            _remove_staged_source(entry)
            continue
        if not staged_path or not Path(staged_path).is_file():
            staged_path = _stage_uploaded_file(
                source_path,
                folder=SOURCE_STAGING,
                label="Source file",
                preserve_name=True,
            )
        if not staged_path:
            continue
        identities.add(_source_file_identity(staged_path))
        seen.update(identities)
        merged.append(
            {
                "source_path": source_path,
                "staged_path": staged_path,
                "display_name": entry["display_name"],
            }
        )
    return merged, _source_file_queue_summary(merged, rejected_count=rejected_count)


def _merge_source_file_queue_for_ui(
    current: object,
    uploaded: object,
) -> tuple[list[dict[str, str]], str, list[str]]:
    merged, summary = _merge_source_file_queue(current, uploaded)
    visible_files = [entry["staged_path"] for entry in merged]
    return merged, summary, visible_files


def _remove_staged_source(entry: dict[str, str]) -> None:
    staged_path = entry.get("staged_path", "")
    if not staged_path:
        return
    candidate = Path(staged_path)
    try:
        candidate.resolve().relative_to(SOURCE_STAGING.resolve())
    except (OSError, ValueError):
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        parent = candidate.parent
        if parent != SOURCE_STAGING and parent.parent.resolve() == SOURCE_STAGING.resolve():
            parent.rmdir()
    except OSError:
        pass


def _remove_source_file_from_queue(current: object, deleted_path: str) -> tuple[list[dict[str, str]], str]:
    deleted_identity = _source_file_identity(deleted_path)
    remaining: list[dict[str, str]] = []
    for entry in _source_file_queue_entries(current):
        source_identity = _source_file_identity(entry["source_path"])
        staged_identity = (
            _source_file_identity(entry["staged_path"])
            if entry["staged_path"]
            else ""
        )
        if deleted_identity in {source_identity, staged_identity}:
            _remove_staged_source(entry)
            continue
        remaining.append(entry)
    return remaining, _source_file_queue_summary(remaining)


def _clear_source_file_queue(current: object = None) -> tuple[list[dict[str, str]], str]:
    for entry in _source_file_queue_entries(current):
        _remove_staged_source(entry)
    return [], _source_file_queue_summary([])


def _select_primary_source(text_input: str, file_input: object) -> str:
    text = (text_input or "").strip()
    if text:
        return text
    return "\n".join(_uploaded_file_paths(file_input))


def _write_pasted_batch_list(items: list[str]) -> Path:
    BATCH_STAGING.mkdir(parents=True, exist_ok=True)
    path = BATCH_STAGING / f"pasted_{time.time_ns()}.txt"
    path.write_text("\n".join(items) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _md_code(value: object) -> str:
    text = str(value if value is not None else "")
    return "`" + text.replace("`", "\\`") + "`"


def _format_inspect_result(target: str, info: dict[str, object]) -> str:
    backend = info.get("probable_backend")
    tools = ", ".join(str(tool) for tool in info.get("needs_tools", []) or []) or "none"
    metadata = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    cookie_strategy = str(metadata.get("cookie_strategy") or "")
    cookie_domains = ", ".join(str(item) for item in metadata.get("cookie_domains", []) or []) or ""
    readiness = info.get("readiness") if isinstance(info.get("readiness"), dict) else None
    warnings = [str(w) for w in info.get("warnings", []) or []]
    risks = [str(r) for r in info.get("risks", []) or []]
    lines = [
        f"### {_md_code(target)}",
        "",
        f"- Backend: {_md_code(backend or 'unknown')}",
        f"- Type: {_md_code(info.get('detected_type') or 'unknown')}",
        f"- Network: {_md_code(info.get('needs_network'))}",
        f"- Cookies: {_md_code(info.get('needs_cookies'))}",
        f"- Tools: {_md_code(tools)}",
    ]
    if cookie_strategy:
        lines.append(f"- Cookie strategy: {_md_code(cookie_strategy)}")
    if cookie_domains:
        lines.append(f"- Cookie domains: {_md_code(cookie_domains)}")
    if readiness is not None:
        missing = ", ".join(str(tool) for tool in readiness.get("missing_tools", []) or []) or "none"
        missing_auth = ", ".join(str(item) for item in readiness.get("missing_auth", []) or []) or "none"
        cookies = readiness.get("cookies_file") if isinstance(readiness.get("cookies_file"), dict) else {}
        cookie_status = cookies.get("status") or "not_needed"
        cookie_path = cookies.get("path")
        if not readiness.get("needs_cookies") and cookie_path:
            cookie_label = f"optional: {cookie_path}"
        elif not readiness.get("needs_cookies"):
            cookie_label = "not needed"
        elif cookie_path:
            cookie_label = f"{cookie_status}: {cookie_path}"
        else:
            cookie_label = str(cookie_status)
        lines.extend(
            [
                f"- Ready: {_md_code(readiness.get('ready'))}",
                f"- Missing tools: {_md_code(missing)}",
                f"- Cookies needed: {_md_code(readiness.get('needs_cookies'))}",
                f"- Cookie file: {_md_code(cookie_label)}",
                f"- Missing auth: {_md_code(missing_auth)}",
            ]
        )
    if warnings:
        lines.extend(["", "**Warnings**"])
        lines.extend(f"- {warning}" for warning in warnings)
    if risks:
        lines.extend(["", "**Risks**"])
        lines.extend(f"- {_md_code(risk)}" for risk in risks)
    return "\n".join(lines)


def _inspect_source(
    text_input: str,
    file_input: object,
    batch_file_input: str | None,
    cookies_file: str = "",
    cookies_browser: str = "(none)",
    xhs_cookies_file: str = "",
) -> str:
    """Return a Markdown preflight summary for the current UI source fields."""
    from omd._preflight import inspect_target
    from omd.doctor import readiness_for_preflight, run_checks

    batch_file = (batch_file_input or "").strip()
    source_files = _uploaded_file_paths(file_input)
    text = text_input or ""

    targets: list[str] = []
    if batch_file:
        try:
            targets = list(iter_batch_items(Path(batch_file).read_text(encoding="utf-8").splitlines()))
        except OSError as exc:
            return f"### Inspect failed\n\nCould not read batch list: {_md_code(exc)}"
    else:
        primary = _select_primary_source(text, source_files)
        targets = _batch_items_from_text(primary)
        if not targets and primary:
            targets = [primary]

    if not targets:
        return "_Provide a URL/share blob, source file, or batch list to inspect._"

    shown = targets[:20]
    header = [f"## Source Inspect", "", f"Items detected: **{len(targets)}**"]
    if len(targets) > len(shown):
        header.append(f"Showing first **{len(shown)}** items.")
    sections: list[str] = ["\n".join(header)]
    checks = run_checks()
    for target in shown:
        try:
            info = inspect_target(target)
        except SystemExit as exc:
            sections.append(f"### {_md_code(target)}\n\nInspect failed: {_md_code(exc)}")
        else:
            target_cookies_file = (
                xhs_cookies_file
                if info.get("detected_type") == "xhs_url" and xhs_cookies_file.strip()
                else cookies_file
            )
            metadata = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
            cookie_strategy = str(metadata.get("cookie_strategy") or "")
            if cookie_strategy == "public_only_no_cookie_passthrough":
                target_cookies_file = ""
            info["readiness"] = readiness_for_preflight(
                info,
                checks,
                cookies_file=(target_cookies_file or "").strip() or None,
                cookies_from_browser=None if cookies_browser == "(none)" else cookies_browser,
            )
            detected_type = str(info.get("detected_type") or "")
            source_warnings = list(info.get("warnings") or [])
            if detected_type == "douyin_url" and not (cookies_file or "").strip():
                source_warnings.insert(
                    0,
                    "Douyin source detected, but the Default / Douyin cookies.txt path is empty. "
                    "Upload a Douyin Netscape cookies.txt file before converting private or gated Douyin links.",
                )
            elif detected_type == "xhs_url" and not (xhs_cookies_file or "").strip():
                source_warnings.insert(
                    0,
                    "XHS / Rednote source detected, but the XHS / Rednote cookies.txt path is empty. "
                    "Upload an XHS Netscape cookies.txt file before converting private or gated XHS links.",
                )
            if source_warnings:
                info["warnings"] = source_warnings
            sections.append(_format_inspect_result(target, info))
    return "\n\n---\n\n".join(sections)


def _choose_output_dir(current: str) -> str:
    """Open the platform folder picker and return the selected path.

    On macOS this uses the native Apple folder chooser. If the user cancels or
    the platform does not support a native chooser, keep the current value.
    """
    current_path = Path(current or DEFAULT_OUTPUT_DIR).expanduser()
    start = current_path if current_path.is_dir() else current_path.parent
    if sys.platform != "darwin":
        return str(current_path)
    script = (
        'set startFolder to POSIX file '
        f'{json.dumps(str(start))}\n'
        'set chosenFolder to choose folder with prompt "Choose output folder for omd" default location startFolder\n'
        'POSIX path of chosenFolder'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return str(current_path)
    selected = proc.stdout.strip()
    return selected.rstrip("/") if proc.returncode == 0 and selected else str(current_path)


def _choose_cookies_file(current: str) -> str:
    """Open the native macOS file picker for a cookies.txt file."""
    current_path = Path(current or DEFAULT_COOKIES).expanduser()
    start = current_path.parent if current_path.suffix else current_path
    if not start.exists():
        start = Path.home()
    if sys.platform != "darwin":
        return str(current_path) if current else ""
    script = (
        'set startFolder to POSIX file '
        f'{json.dumps(str(start))}\n'
        'set chosenFile to choose file with prompt "Choose cookies.txt for omd" '
        'default location startFolder of type {"txt", "text", "public.plain-text"}\n'
        'POSIX path of chosenFile'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return str(current_path) if current else ""
    selected = proc.stdout.strip()
    return selected if proc.returncode == 0 and selected else (str(current_path) if current else "")


def _open_output_path(path_value: str) -> str:
    path_text = (path_value or "").strip()
    if not path_text:
        return _status_html("err", "failed", detail="no output path", percent=100)
    path = Path(path_text).expanduser()
    target = path.parent if path.is_file() else path
    if not target.exists():
        return _status_html("err", "failed", detail=f"missing: {target}", percent=100)
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(["open", str(target)], check=False)
        elif sys.platform.startswith("linux"):
            proc = subprocess.run(["xdg-open", str(target)], check=False)
        else:
            return _status_html("err", "failed", detail="open folder is unsupported here", percent=100)
    except OSError as exc:
        return _status_html("err", "failed", detail=str(exc), percent=100)
    if proc.returncode != 0:
        return _status_html("err", "failed", detail=f"open exited {proc.returncode}", percent=100)
    return _status_html("ok", "opened", detail=str(target), percent=100)


def _prune_staging_directory(
    folder: Path,
    *,
    now: float | None = None,
    max_age_seconds: float = DOWNLOAD_STAGING_MAX_AGE_SECONDS,
    max_files: int = DOWNLOAD_STAGING_MAX_FILES,
) -> None:
    """Bound temporary artifact retention without touching symlinks or subdirectories."""
    try:
        candidates = [
            path
            for path in folder.iterdir()
            if path.is_file() and not path.is_symlink()
        ]
    except OSError:
        return
    cutoff = (time.time() if now is None else now) - max(0.0, max_age_seconds)
    retained: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            modified = candidate.stat().st_mtime
            if modified < cutoff:
                candidate.unlink(missing_ok=True)
            else:
                retained.append((modified, candidate))
        except OSError:
            continue
    retained.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    for _modified, candidate in retained[max(0, max_files) :]:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _download_value_for_output(
    path: Path,
    *,
    public_demo: bool | None = None,
    modified_since: float | None = None,
) -> str | None:
    """Stage generated Markdown under a temp root that Gradio may serve.

    A single output stays a Markdown file. Batch and vault-directory outputs are
    packaged as a ZIP containing only Markdown/RMarkdown files.
    """
    del public_demo  # Kept for compatibility with existing callers/tests.
    if not path.exists():
        return None
    try:
        DOWNLOAD_STAGING.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            if path.suffix.lower() not in {".md", ".rmd"}:
                return None
            if modified_since is not None and path.stat().st_mtime < modified_since:
                return None
            staged = _unique_staged_path(DOWNLOAD_STAGING, "download", path.name)
            shutil.copyfile(path, staged)
        elif path.is_dir():
            vault_sources = path / "Sources"
            is_vault = (path / "Index" / "OMD Captures.md").is_file() and vault_sources.is_dir()
            candidates = (
                vault_sources.rglob("*")
                if is_vault
                else (*path.glob("*.md"), *path.glob("*.Rmd"))
            )
            markdown_files = sorted(
                candidate
                for candidate in candidates
                if candidate.is_file()
                and not candidate.is_symlink()
                and candidate.suffix.lower() in {".md", ".rmd"}
                and (modified_since is None or candidate.stat().st_mtime >= modified_since)
            )
            if not markdown_files:
                return None
            archive_name = f"{path.name or 'markdown-output'}.zip"
            staged = _unique_staged_path(DOWNLOAD_STAGING, "download", archive_name)
            with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for candidate in markdown_files:
                    archive.write(candidate, arcname=candidate.relative_to(path))
        else:
            return None
        staged.chmod(0o600)
        _prune_staging_directory(DOWNLOAD_STAGING)
    except (OSError, shutil.Error, zipfile.BadZipFile):
        return None
    return str(staged)


def _slug_from_input(s: str) -> str:
    s = s.strip()
    if not s:
        return "output"
    m = URL_RE.search(s)
    if m:
        url = m.group(0).rstrip("/.,;)")
        host = urlparse(url).netloc.lower().replace("www.", "").split(".")[0] or "url"
        tail = urlparse(url).path.strip("/").split("/")[-1] or "page"
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", tail)[:40] or "page"
        return f"{host}-{slug}"
    p = Path(s)
    if p.exists():
        return p.stem
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:40] or "output"


def _filename_for_format(name: str, suffix: str) -> str:
    path = Path(name)
    if path.suffix.lower() in {".md", ".rmd"}:
        return str(path.with_suffix(suffix))
    return name + suffix


def _validate_output_filename(name: str) -> str:
    path = Path(name)
    if path.is_absolute() or path.parent != Path(".") or ".." in path.parts:
        raise ValueError("Filename must be a file name only. Choose the folder separately.")
    return name


def _blocking_file_parent(path: Path) -> Path | None:
    for parent in path.expanduser().parents:
        if parent.exists():
            return None if parent.is_dir() else parent
    return None


def _validate_output_folder(path: Path) -> Path:
    path = path.expanduser()
    if path.exists() and not path.is_dir():
        raise ValueError(f"Output folder must be a folder, not a file: {path}")
    blocked_parent = _blocking_file_parent(path)
    if blocked_parent is not None:
        raise ValueError(f"Output folder parent must be a folder, not a file: {blocked_parent}")
    return path


def _is_capture_mode(value: str) -> bool:
    return str(value or "").strip().lower().startswith("capture")


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_uploaded_path(value: str, uploaded_paths: set[str]) -> bool:
    return value in uploaded_paths


def _media_duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if proc.returncode != 0:
        return None
    try:
        duration = float(proc.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def _validate_public_demo_uploaded_file(path_value: str) -> None:
    path = Path(path_value)
    if not path.is_file():
        raise ValueError("Hosted sample demo accepts uploaded document/image files only, not local filesystem paths.")
    max_bytes = _public_demo_max_upload_bytes()
    size = path.stat().st_size
    if size > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise ValueError(f"Hosted sample demo upload limit is {limit_mb} MB.")
    if path.suffix.lower() in LOCAL_VIDEO_EXTENSIONS:
        raise ValueError("Hosted sample demo video file uploads are not supported; paste a supported video URL instead.")
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        if not _env_flag("OMD_PUBLIC_DEMO_ALLOW_MEDIA"):
            raise ValueError(
                "Hosted sample demo media transcription is disabled until the hosted Linux whisper backend is enabled."
            )
        duration = _media_duration_seconds(path)
        max_seconds = _public_demo_max_media_seconds()
        if duration is None:
            raise ValueError("Hosted sample demo could not verify media duration with ffprobe.")
        if duration > max_seconds:
            minutes = max_seconds // 60
            raise ValueError(f"Hosted sample demo media is capped at {minutes} minutes.")


def _validate_public_demo_target(target: str, *, uploaded_paths: set[str]) -> None:
    from omd._preflight import inspect_target

    url_match = URL_RE.search(target)
    public_url = url_match.group(0) if url_match else ""
    if public_url:
        from omd._network_policy import validate_public_http_url

        validate_public_http_url(public_url)
        info = inspect_target(public_url)
        if info.get("needs_cookies"):
            raise ValueError(
                "Hosted sample demo does not support authenticated or cookie-gated sources yet. "
                "Use the local Full Power Demo for Douyin/XHS cookies."
            )
        if "mlx_whisper" in (info.get("needs_tools") or []) and not _env_flag("OMD_PUBLIC_DEMO_ALLOW_MEDIA"):
            raise ValueError(
                "Hosted sample demo media URLs are disabled until the hosted Linux whisper backend is enabled."
            )
        return
    if _looks_like_uploaded_path(target, uploaded_paths):
        _validate_public_demo_uploaded_file(target)
        return
    raise ValueError("Hosted sample demo accepts public URLs or document/image uploads by default; local paths require Full Power Demo.")


def _validate_public_demo_policy(
    *,
    targets: list[str],
    uploaded_paths: set[str],
    cookies_file: str,
    xhs_cookies_file: str,
    instagram_cookies_file: str,
    cookies_browser: str,
    polish_md: bool,
    reel_polish: bool,
    ollama_host: str,
    keep_video: bool,
) -> None:
    if cookies_browser and cookies_browser != "(none)":
        raise ValueError("Hosted sample demo disables browser cookie extraction. Use Full Power Demo for local Chrome cookies.")
    for cookie_value in (cookies_file, xhs_cookies_file, instagram_cookies_file):
        if not cookie_value.strip():
            continue
        if not _env_flag("OMD_PUBLIC_DEMO_ALLOW_COOKIE_UPLOAD"):
            raise ValueError("Hosted sample demo disables cookie files by default. Use Full Power Demo for Douyin/XHS cookies.")
        cookie_path = Path(cookie_value).expanduser()
        try:
            cookie_path.relative_to(COOKIES_STAGING)
        except ValueError as exc:
            raise ValueError("Hosted sample demo only accepts uploaded/staged cookie files, not raw local cookie paths.") from exc
    if polish_md or reel_polish or ollama_host.strip():
        raise ValueError("Hosted sample demo disables local Ollama/cloud polish until a hosted LLM provider is configured.")
    if keep_video:
        raise ValueError("Hosted sample demo returns Markdown downloads only; keeping media files is disabled.")
    for target in targets:
        _validate_public_demo_target(target, uploaded_paths=uploaded_paths)


def _build_argv(
    text_input: str,
    file_input: object,
    batch_file_input: str | None,
    workflow_mode: str,
    out_dir: str,
    vault_dir: str,
    filename: str,
    output_format: str,
    polish_md: bool,
    polish_md_keep_raw: bool,
    polish_md_model: str,
    memory_cards: bool,
    memory_model: str,
    reel_polish: bool,
    reel_polish_model: str,
    ocr_thumbnail: bool,
    ocr_article_images: bool,
    keep_video: bool,
    cookies_file: str,
    cookies_browser: str,
    lang: str,
    preferred_languages: str,
    whisper_model: str,
    ollama_host: str,
    verbose: bool,
    json_events: bool,
    reddit_comment_scope: str = "OP only",
    xhs_cookies_file: str = "",
    instagram_cookies_file: str = "",
    public_demo: bool | None = None,
) -> tuple[list[str], Path]:
    from omd._network_policy import validate_ollama_host

    is_public_demo = _public_demo_enabled() if public_demo is None else public_demo
    file_paths = _uploaded_file_paths(file_input)
    primary_source = _select_primary_source(text_input, file_input)
    text_wins_over_file = bool((text_input or "").strip())
    batch_items = _batch_items_from_text(primary_source)
    is_pasted_batch = not batch_file_input and len(batch_items) > 1
    src = primary_source
    batch_file = (batch_file_input or "").strip()
    if batch_file:
        src = batch_file
    elif is_pasted_batch:
        src = str(_write_pasted_batch_list(batch_items))
    if not src:
        raise ValueError("Provide a URL/blob, paste one item per line, or pick a file.")
    src_path = Path(src).expanduser()
    if not is_public_demo and not is_pasted_batch and src_path.is_file() and src_path.suffix.lower() in LOCAL_VIDEO_EXTENSIONS:
        raise ValueError("Local video file capture is not supported yet; paste a supported video URL or extract audio first.")
    if verbose and json_events:
        raise ValueError("--json-events and --verbose are mutually exclusive")
    if ollama_host.strip() and not is_public_demo:
        validate_ollama_host(ollama_host)
    capture_mode = _is_capture_mode(workflow_mode)
    if memory_cards and not capture_mode:
        raise ValueError("Polish for Obsidian requires Capture to vault mode.")
    if capture_mode and polish_md:
        raise ValueError("Polish Markdown is for Convert to file mode; use Polish for Obsidian in Capture to vault mode.")
    is_batch = bool(batch_file) or is_pasted_batch
    scope_items = batch_items or [src]
    if batch_file:
        try:
            scope_items = list(iter_batch_items(Path(batch_file).read_text(encoding="utf-8").splitlines()))
        except OSError:
            scope_items = []

    if is_public_demo and capture_mode:
        raise ValueError("Hosted demo cannot write to a local vault. Use Full Power Demo locally for Capture to vault.")

    if capture_mode:
        if not vault_dir.strip():
            raise ValueError("Choose a vault folder.")
        out_dir_p = _validate_output_folder(Path(vault_dir))
        out_dir_p.mkdir(parents=True, exist_ok=True)
    elif is_public_demo:
        out_dir_p = _public_demo_output_dir()
        out_dir_p.mkdir(parents=True, exist_ok=True)
        public_targets = batch_items if is_pasted_batch else [src]
        uploaded_paths = {
            path
            for path in [*([] if text_wins_over_file else file_paths), batch_file_input]
            if path
        }
        if batch_file:
            public_targets = list(iter_batch_items(Path(batch_file).read_text(encoding="utf-8").splitlines()))
        _validate_public_demo_policy(
            targets=public_targets,
            uploaded_paths=uploaded_paths,
            cookies_file=cookies_file,
            xhs_cookies_file=xhs_cookies_file,
            instagram_cookies_file=instagram_cookies_file,
            cookies_browser=cookies_browser,
            polish_md=polish_md,
            reel_polish=reel_polish,
            ollama_host=ollama_host,
            keep_video=keep_video,
        )
    elif not out_dir.strip():
        raise ValueError("Choose an output folder.")
    else:
        out_dir_p = _validate_output_folder(Path(out_dir))
        out_dir_p.mkdir(parents=True, exist_ok=True)
    fmt = "rmd" if output_format.strip().lower().startswith("rmarkdown") or output_format.strip().lower() == "rmd" else "md"
    suffix = ".Rmd" if fmt == "rmd" else ".md"

    if capture_mode:
        output_target = out_dir_p
        argv = [sys.executable, "-m", "omd.cli", "capture", src, "--vault", str(out_dir_p)]
        if is_batch:
            argv += ["--batch"]
        if memory_cards:
            argv += [
                "--memory-cards",
                "--memory-model",
                memory_model.strip() or recommended_local_text_model(),
                "--memory-timeout",
                str(UI_MEMORY_TIMEOUT_SECONDS),
            ]
            if ollama_host.strip():
                argv += ["--memory-host", ollama_host.strip()]
    elif is_batch:
        output_target = out_dir_p
        argv = [sys.executable, "-m", "omd.cli", "batch", src, "-o", str(out_dir_p)]
    else:
        name = filename.strip() or _slug_from_input(src)
        name = _filename_for_format(name, suffix)
        name = _validate_output_filename(name)
        output_target = out_dir_p / name
        argv = [sys.executable, "-m", "omd.cli", src, "-o", str(output_target)]
    if not capture_mode and fmt != "md":
        argv += ["--format", fmt]
    if lang.strip():
        argv += ["--lang", lang.strip()]
    if preferred_languages.strip():
        argv += ["--preferred-languages", preferred_languages.strip()]
    if is_public_demo and _env_flag("OMD_PUBLIC_DEMO_ALLOW_MEDIA"):
        argv += ["--whisper-backend", "faster-whisper"]
        argv += ["--max-duration", str(_public_demo_max_media_seconds())]
    if verbose:
        argv += ["-v"]
    if json_events:
        argv += ["--json-events"]
    if polish_md and not capture_mode:
        argv += ["--polish-md", "--polish-md-model", polish_md_model.strip() or recommended_local_text_model()]
        if polish_md_keep_raw:
            argv += ["--polish-md-keep-raw"]
        if ollama_host.strip():
            argv += ["--polish-md-host", ollama_host.strip()]

    # Pass-through flags consumed by omd.reel / omd.xhs / omd.podcast subprocesses.
    if reel_polish:
        argv += ["--polish", reel_polish_model.strip() or recommended_local_text_model()]
    if ocr_thumbnail:
        argv += ["--ocr"]
    if ocr_article_images:
        argv += ["--ocr-article-images"]
    if keep_video:
        keep_dir = out_dir_p / "_attachments" if capture_mode else out_dir_p
        argv += ["--keep", str(keep_dir)]
    if (
        reddit_comment_scope.strip().lower() in {"op + top comments", "top", "top comments"}
        and _contains_reddit_source(scope_items)
    ):
        argv += ["--reddit-comments", "top"]
    if cookies_file.strip():
        cookies_path = Path(cookies_file).expanduser()
        if not cookies_path.exists():
            raise ValueError(f"Cookie file not found: {cookies_path}")
        argv += ["--douyin-cookies", str(cookies_path)]
    if xhs_cookies_file.strip():
        xhs_cookies_path = Path(xhs_cookies_file).expanduser()
        if not xhs_cookies_path.exists():
            raise ValueError(f"XHS cookie file not found: {xhs_cookies_path}")
        argv += ["--xhs-cookies", str(xhs_cookies_path)]
    if instagram_cookies_file.strip():
        instagram_cookies_path = Path(instagram_cookies_file).expanduser()
        if not instagram_cookies_path.exists():
            raise ValueError(f"Instagram cookie file not found: {instagram_cookies_path}")
        argv += ["--instagram-cookies", str(instagram_cookies_path)]
    if cookies_browser and cookies_browser != "(none)":
        argv += ["--cookies-from-browser", cookies_browser]
    if whisper_model.strip():
        argv += ["--model", whisper_model.strip()]
    if ollama_host.strip() and not capture_mode:
        argv += ["--ollama-host", ollama_host.strip()]

    return argv, output_target


def _stream_subprocess(argv: list[str]):
    """Spawn argv and stream output plus quiet-process heartbeat ticks."""
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if _public_demo_enabled():
        from omd._network_policy import PUBLIC_NETWORK_POLICY_ENV, PUBLIC_NETWORK_POLICY_VALUE

        env[PUBLIC_NETWORK_POLICY_ENV] = PUBLIC_NETWORK_POLICY_VALUE
    tool_paths = [
        Path(sys.executable).parent,
        Path(sys.executable).resolve().parent,
        *[
            Path(p).expanduser()
            for p in env.get("OMD_TOOL_PATH", "").split(os.pathsep)
            if p
        ],
        Path.home() / ".local/share/omd/toolenv-py312/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    path_prefix = [str(path) for path in tool_paths if path.is_dir()]
    env["PATH"] = os.pathsep.join([*path_prefix, env.get("PATH", "")])
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    q: Queue = Queue()

    def pump(stream, tag):
        for line in iter(stream.readline, ""):
            q.put((tag, line))
        stream.close()
        q.put((tag, None))

    threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True).start()

    try:
        closed = 0
        last_heartbeat = time.monotonic()
        while closed < 2:
            try:
                tag, line = q.get(timeout=0.2)
            except Empty:
                now = time.monotonic()
                if now - last_heartbeat >= 1.0:
                    last_heartbeat = now
                    yield "tick", ""
                continue
            if line is None:
                closed += 1
                continue
            yield tag, line.rstrip("\n")
        proc.wait()
        yield "rc", str(proc.returncode)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()


def _preview_output(target: Path) -> tuple[str, str]:
    if target.is_file():
        body = target.read_text(errors="replace")
        if not body.strip():
            raise ValueError(f"output file is empty: {target}")
        return body, f"{target} ({len(body)} chars)"
    if target.is_dir():
        vault_index = target / "Index" / "OMD Captures.md"
        if vault_index.is_file():
            captures = sorted((target / "Sources").glob("**/*.md")) if (target / "Sources").is_dir() else []
            index_body = vault_index.read_text(errors="replace")
            shown = captures[:50]
            lines = [
                "# Vault capture output",
                "",
                f"Vault folder: `{target}`",
                f"Capture notes: {len(captures)}",
                "",
                "## Recent capture files",
                "",
            ]
            lines.extend(f"- `{path.relative_to(target).as_posix()}`" for path in shown)
            if len(captures) > len(shown):
                lines.append(f"- ... {len(captures) - len(shown)} more")
            lines.extend(["", "## Index Preview", "", index_body])
            return "\n".join(lines), str(target)
        files = sorted([*target.glob("*.md"), *target.glob("*.Rmd")])
        if not files:
            raise ValueError(f"batch produced no Markdown/RMarkdown files: {target}")
        empty_files = [path for path in files if not path.read_text(errors="replace").strip()]
        if empty_files:
            names = ", ".join(path.name for path in empty_files[:5])
            if len(empty_files) > 5:
                names += f", ... {len(empty_files) - 5} more"
            raise ValueError(f"batch produced empty Markdown/RMarkdown files: {names}")
        shown = files[:50]
        lines = [
            "# Batch output",
            "",
            f"Output folder: `{target}`",
            "",
            f"Markdown/RMarkdown files: {len(files)}",
            "",
        ]
        lines.extend(f"- `{path.name}`" for path in shown)
        if len(files) > len(shown):
            lines.append(f"- ... {len(files) - len(shown)} more")
        return "\n".join(lines), str(target)
    raise ValueError(f"output path was not created: {target}")


def run(*args):
    import gradio as gr  # local - UI surface only
    try:
        argv, out_md = _build_argv(*args)
    except Exception as e:  # noqa: BLE001
        raise gr.Error(f"Argument error: {e}") from e

    header = f"$ {' '.join(shlex.quote(a) for a in argv)}\n"
    log = header
    yield log, gr.update(value="(running...)"), gr.update(value=None)

    rc = None
    for tag, line in _stream_subprocess(argv):
        if tag == "rc":
            rc = int(line)
            break
        log += line + "\n"
        # cap log to last ~4000 lines to keep browser snappy
        if log.count("\n") > 4000:
            log = "...(truncated)...\n" + "\n".join(log.splitlines()[-3500:]) + "\n"
        yield log, gr.update(), gr.update()

    if rc == 0:
        try:
            preview, label = _preview_output(out_md)
        except ValueError as exc:
            log += f"\n✗ failed: {exc}\n"
            yield log, gr.update(value=f"_failed: {exc}_"), gr.update(value=None)
            return
        log += f"\n✓ wrote {label}\n"
        yield log, gr.update(value=preview), gr.update(value=str(out_md))
    else:
        try:
            preview, label = _preview_output(out_md)
        except ValueError:
            log += f"\n✗ failed (exit {rc})\n"
            yield log, gr.update(value=f"_failed (exit {rc})_"), gr.update(value=None)
            return
        log += f"\n⚠ partial output available despite exit {rc}: {label}\n"
        yield log, gr.update(value=preview), gr.update(value=str(out_md))


def _status_html(
    state: str,
    label: str,
    *,
    detail: str = "",
    eta: str = "",
    percent: int | None = None,
    summary: Sequence[tuple[str, str]] | None = None,
) -> str:
    pct = 0 if percent is None else max(0, min(100, int(percent)))
    indeterminate = state == "running" and percent is None
    state_class = {"running": "run"}.get(state, state)
    classes = f"omd-status-{state_class}"
    if indeterminate:
        classes += " omd-status-indeterminate"
    label_html = html.escape(label)
    detail_html = html.escape(detail)
    eta_html = html.escape(eta)
    summary_html = ""
    if summary:
        pills = "".join(
            f'<span class="omd-status-pill omd-status-pill-{html.escape(kind)}">{html.escape(text)}</span>'
            for kind, text in summary
        )
        summary_html = f'<div class="omd-status-summary">{pills}</div>'
    return (
        f'<div id="omd-status" class="{classes}">'
        '<div class="omd-status-head">'
        f'<span class="omd-status-label">{label_html}</span>'
        f'<span class="omd-status-detail">{detail_html}</span>'
        f'<span class="omd-status-eta">{eta_html}</span>'
        '</div>'
        f'{summary_html}'
        '<div class="omd-progress-track">'
        f'<span class="omd-progress-bar" style="--omd-progress:{pct}%"></span>'
        '</div>'
        '</div>'
    )


STATUS_HTML = {
    "idle": _status_html(
        "idle",
        "ready",
        detail="Add source, choose output, then press Convert",
        percent=0,
    ),
    "running": _status_html("running", "running...", detail="starting", eta="ETA: working", percent=None),
    "ok": _status_html("ok", "done", detail="complete", eta="ETA: done", percent=100),
    "err": _status_html("err", "failed", detail="check log", percent=100),
    "cancelled": _status_html("idle", "cancelled", detail="stopped by user", percent=0),
}


def _format_duration(seconds: float) -> str:
    seconds_i = max(0, int(round(seconds)))
    if seconds_i < 60:
        return f"{seconds_i}s"
    minutes, secs = divmod(seconds_i, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _total_elapsed_detail(started_at: float, now: float | None = None) -> str:
    current = time.monotonic() if now is None else now
    return f"Total: {_format_duration(max(0.0, current - started_at))}"


def _eta_detail(started_at: float, percent: int | None, now: float | None = None) -> str:
    if percent is None or percent <= 0:
        return "ETA: working"
    if percent >= 100:
        return "ETA: done"
    now = time.monotonic() if now is None else now
    elapsed = max(0.0, now - started_at)
    if elapsed < 8 or percent >= 90:
        return "ETA: finishing" if percent >= 90 else "ETA: working"
    remaining = elapsed * ((100 - percent) / max(percent, 1))
    return f"ETA: ~{_format_duration(remaining)}"


@dataclass
class _EtaEstimator:
    """Small EWMA progress estimator inspired by tqdm's smoothed rate display."""

    started_at: float
    initial_range: tuple[int, int] | None = None
    alpha: float = 0.3
    last_percent: int | None = None
    last_time: float | None = None
    rate: float | None = None
    rate_samples: int = 0

    def _fallback_label(self, current: float) -> str:
        elapsed = max(0.0, current - self.started_at)
        if self.initial_range is None:
            return f"Elapsed: {_format_duration(elapsed)}"
        low, high = self.initial_range
        low_remaining = max(0.0, low - elapsed)
        high_remaining = max(0.0, high - elapsed)
        if high_remaining < 1:
            return f"Elapsed: {_format_duration(elapsed)}"
        if low_remaining < 5:
            return f"ETA: ≤{_format_duration(high_remaining)}"
        return (
            f"ETA: ~{_format_duration(low_remaining)}-"
            f"{_format_duration(high_remaining)}"
        )

    def label(self, percent: int | None, now: float | None = None) -> str:
        current = time.monotonic() if now is None else now
        if percent is None or percent <= 0:
            return self._fallback_label(current)
        if percent >= 100:
            return "ETA: done"
        if percent >= 90:
            return "ETA: finishing"
        if self.last_percent is None or self.last_time is None:
            self.last_percent = percent
            self.last_time = current
            return self._fallback_label(current)
        delta_percent = percent - self.last_percent
        delta_time = current - self.last_time
        if delta_percent <= 0 or delta_time < 1.5:
            return self._fallback_label(current)
        self.last_percent = percent
        self.last_time = current
        instant_rate = delta_percent / delta_time
        self.rate = instant_rate if self.rate is None else self.alpha * instant_rate + (1 - self.alpha) * self.rate
        self.rate_samples += 1
        if self.rate_samples < 2 or not self.rate or self.rate <= 0:
            return self._fallback_label(current)
        remaining = (100 - percent) / self.rate
        if remaining < 5:
            return "ETA: finishing"
        return f"ETA: ~{_format_duration(remaining)}"


def _initial_eta_range(argv: Sequence[str]) -> tuple[int, int]:
    """Return a conservative task-type estimate until measured progress stabilizes."""
    sources = list(argv)
    item_count = 1
    batch_file: Path | None = None
    try:
        batch_index = sources.index("batch")
        batch_file = Path(sources[batch_index + 1])
    except (ValueError, IndexError):
        if "--batch" in sources:
            try:
                capture_index = sources.index("capture")
                batch_file = Path(
                    next(arg for arg in sources[capture_index + 1:] if not arg.startswith("-"))
                )
            except (ValueError, StopIteration):
                batch_file = None
    try:
        if batch_file is not None and batch_file.is_file():
            batch_items = list(iter_batch_items(batch_file.read_text(encoding="utf-8").splitlines()))
            if batch_items:
                sources = batch_items
                item_count = len(batch_items)
    except (OSError, UnicodeError):
        pass

    model_work = any(
        flag in argv for flag in ("--polish-md", "--memory-cards", "--polish")
    )
    media_hosts = (
        "youtube.com",
        "youtu.be",
        "douyin.com",
        "tiktok.com",
        "bilibili.com",
        "podcasts.apple.com",
    )
    media_suffixes = AUDIO_EXTENSIONS | LOCAL_VIDEO_EXTENSIONS
    media_work = any(
        any(host in str(source).lower() for host in media_hosts)
        or Path(urlparse(str(source)).path).suffix.lower() in media_suffixes
        for source in sources
    )

    if model_work and media_work:
        per_item = (90, 300)
    elif model_work:
        per_item = (60, 180)
    elif media_work:
        per_item = (60, 240)
    else:
        per_item = (10, 60)
    return per_item[0] * item_count, per_item[1] * item_count


@dataclass
class _RunCounts:
    total: int | None = None
    success: int = 0
    failed: int = 0
    partial: int = 0

    @property
    def seen(self) -> int:
        return self.success + self.failed + self.partial

    def summary(self) -> tuple[tuple[str, str], ...]:
        parts: list[tuple[str, str]] = []
        if self.total:
            if self.seen:
                parts.append(("total", f"{min(self.seen, self.total)}/{self.total} processed"))
            else:
                parts.append(("total", f"{self.total} queued"))
        elif self.seen:
            suffix = "" if self.seen == 1 else "s"
            parts.append(("total", f"{self.seen} item{suffix}"))
        if self.success:
            parts.append(("ok", f"{self.success} succeeded"))
        if self.failed:
            parts.append(("fail", f"{self.failed} failed"))
        if self.partial:
            parts.append(("partial", f"{self.partial} partial"))
        return tuple(parts)

    def apply_batch_summary(self, succeeded: int, total: int) -> None:
        self.total = total
        self.success = max(0, min(succeeded, total))
        self.failed = max(0, total - self.success)
        self.partial = 0


def _argv_uses_batch(argv: Sequence[str]) -> bool:
    return "batch" in argv or "--batch" in argv


def _stop_conversion_status():
    return STATUS_HTML["cancelled"]


def _run_button_label(workflow_mode: str) -> str:
    return "Capture to vault" if _is_capture_mode(workflow_mode) else "Convert to Markdown"


def _workflow_mode_updates(workflow_mode: str):
    import gradio as gr

    capture_mode = _is_capture_mode(workflow_mode)
    local_polish_enabled = not _public_demo_enabled()
    return (
        gr.update(value=_run_button_label(workflow_mode)),
        gr.update(value=(not capture_mode) and local_polish_enabled),
        gr.update(value=capture_mode and local_polish_enabled),
    )


def _menu_view_updates(view: str):
    import gradio as gr

    selected = (view or "all").strip().lower()
    open_states = {
        "all": (True, True, True, True, False, False),
        "source": (True, False, False, False, False, False),
        "output": (False, True, True, True, False, False),
        "advanced": (False, False, False, False, True, True),
    }.get(selected, (True, True, True, True, False, False))
    return tuple(gr.update(visible=True, open=value) for value in open_states)


def _status_for_log_line(
    line: str,
    *,
    batch_total: int | None,
    batch_index: int,
    percent: int | None,
) -> tuple[int | None, int | None, int, str, str]:
    label = "running..."
    detail = line.strip().lstrip("→✓").strip()[:80] or "working"
    next_total = batch_total
    next_index = batch_index
    next_percent = percent

    if m := BATCH_COUNT_RE.search(line):
        next_total = int(m.group(1))
        next_index = 0
        next_percent = 2
        return next_total, next_index, next_percent, "batch", f"{next_total} items"

    if m := BATCH_SUCCEEDED_RE.search(line):
        next_index = int(m.group(1))
        next_total = int(m.group(2))
        result_percent = int((next_index / max(next_total, 1)) * 85)
        next_percent = max(next_percent or 0, result_percent)
        return next_total, next_index, next_percent, "converted", f"{next_index}/{next_total} result"

    if m := BATCH_FAILED_RE.search(line):
        next_index = int(m.group(1))
        next_total = int(m.group(2))
        result_percent = int((next_index / max(next_total, 1)) * 85)
        next_percent = max(next_percent or 0, result_percent)
        return next_total, next_index, next_percent, "warning", f"{next_index}/{next_total} failed"

    if m := BATCH_ITEM_RE.search(line):
        next_index = int(m.group(1))
        next_total = int(m.group(2))
        base = int(((next_index - 1) / max(next_total, 1)) * 100)
        next_percent = max(next_percent or 0, base, 3)
        return next_total, next_index, next_percent, "running...", f"current {next_index}/{next_total}"

    if "Downloading reel" in line:
        label = "downloading"
        detail = f"current {batch_index}/{batch_total}" if batch_total else "media"
        if batch_total:
            base = int(((batch_index - 1) / max(batch_total, 1)) * 100)
            next_percent = min(95, base + int(12 / max(batch_total, 1)))
        else:
            next_percent = 20
    elif "Transcribing audio" in line:
        label = "transcribing"
        detail = f"current {batch_index}/{batch_total}" if batch_total else "whisper"
        if batch_total:
            base = int(((batch_index - 1) / max(batch_total, 1)) * 100)
            next_percent = min(95, base + int(34 / max(batch_total, 1)))
        else:
            next_percent = 55
    elif "polish" in line.lower():
        label = "polishing"
        clean_line = line.strip().lstrip("→✓").strip()
        detail = clean_line.split(":", 1)[-1].strip()[:80] or "local model"
        if batch_total:
            base = int(((batch_index - 1) / max(batch_total, 1)) * 100)
            next_percent = max(
                next_percent or 0,
                min(96, base + int(44 / max(batch_total, 1))),
            )
        else:
            next_percent = max(next_percent or 0, 82)
    elif line.startswith("✓ wrote"):
        label = "wrote output"
        if batch_total:
            next_percent = int((batch_index / max(batch_total, 1)) * 100)
            detail = f"item {batch_index}/{batch_total}"
        else:
            next_percent = 95
            detail = "finalizing"
    elif line.startswith("warn:"):
        label = "warning"
    elif line.startswith("error:") or "Traceback" in line:
        label = "error"

    return next_total, next_index, next_percent, label, detail


def _status_state_for_log_line(line: str) -> str:
    lowered = line.lower()
    if lowered.startswith("error:") or "traceback" in lowered:
        return "err"
    if line.startswith("warn:"):
        return "warn"
    if "omd will not download models automatically" in lowered:
        return "warn"
    if "recommended explicit pulls:" in lowered:
        return "warn"
    return "running"


def _warning_marks_partial_output(line: str) -> bool:
    """Identify successful conversions where a requested polish step fell back."""
    if not line.startswith("warn:"):
        return False
    lowered = line.lower()
    return any(
        marker in lowered
        for marker in (
            "keeping the converted markdown",
            "keeping the original markdown for failed chunks",
        )
    )


def _update_counts_from_log_line(counts: _RunCounts, line: str, batch_total: int | None) -> None:
    if batch_total:
        counts.total = batch_total
        counts.partial = 0
    lowered = line.lower()
    if m := BATCH_FAILURE_SUMMARY_RE.search(lowered):
        counts.apply_batch_summary(int(m.group(1)), int(m.group(2)))
    elif m := BATCH_SUCCEEDED_RE.search(line):
        counts.total = int(m.group(2))
        counts.success = min(counts.success + 1, counts.total - counts.failed)
    elif m := BATCH_FAILED_RE.search(line):
        counts.total = int(m.group(2))
        counts.failed = min(counts.failed + 1, counts.total - counts.success)
    elif line.startswith("✓ wrote"):
        if not counts.total:
            counts.success += 1
    elif line.startswith("warn:") and ("converter failed" in lowered or "item failed" in lowered):
        counts.failed += 1


def run_with_status(*args):
    """Wrapper around run() that also yields a status badge for the HTML pane."""
    import gradio as gr
    try:
        argv, out_md = _build_argv(*args)
    except Exception as e:  # noqa: BLE001
        raise gr.Error(f"Argument error: {e}") from e

    header = f"$ {' '.join(shlex.quote(a) for a in argv)}\n"
    log = header
    started_at = time.monotonic()
    download_modified_since = time.time() - 1.0
    eta = _EtaEstimator(started_at=started_at, initial_range=_initial_eta_range(argv))
    current_state = "running"
    had_warning = False
    had_partial_warning = False
    current_label = "running..."
    current_detail = "starting"
    percent: int | None = None
    counts = _RunCounts(total=None if _argv_uses_batch(argv) else 1)
    status_html = _status_html(
        current_state,
        current_label,
        detail=current_detail,
        eta=eta.label(percent, now=started_at),
        percent=percent,
        summary=counts.summary(),
    )
    yield (
        log,
        gr.update(value="_running..._"),
        gr.update(value=""),
        status_html,
        gr.update(value=None, interactive=False),
    )

    rc = None
    batch_total: int | None = None
    batch_index = 0
    for tag, line in _stream_subprocess(argv):
        if tag == "rc":
            rc = int(line)
            break
        if tag == "tick":
            status_html = _status_html(
                current_state,
                current_label,
                detail=current_detail,
                eta=eta.label(percent),
                percent=percent,
                summary=counts.summary(),
            )
            yield log, gr.update(), gr.update(), status_html, gr.update()
            continue
        log += line + "\n"
        if log.count("\n") > 4000:
            log = "...(truncated)...\n" + "\n".join(log.splitlines()[-3500:]) + "\n"
        batch_total, batch_index, percent, current_label, current_detail = _status_for_log_line(
            line,
            batch_total=batch_total,
            batch_index=batch_index,
            percent=percent,
        )
        _update_counts_from_log_line(counts, line, batch_total)
        line_state = _status_state_for_log_line(line)
        if line_state == "warn":
            had_warning = True
            had_partial_warning = had_partial_warning or _warning_marks_partial_output(line)
        if current_state == "err":
            current_label = "error"
        else:
            current_state = line_state
        status_html = _status_html(
            current_state,
            current_label,
            detail=current_detail,
            eta=eta.label(percent),
            percent=percent,
            summary=counts.summary(),
        )
        yield log, gr.update(), gr.update(), status_html, gr.update()

    if rc == 0:
        try:
            preview, label = _preview_output(out_md)
        except ValueError as exc:
            log += f"\n✗ failed: {exc}\n"
            if counts.seen == 0:
                counts.failed += 1
            status_html = _status_html(
                "err",
                "failed",
                detail=str(exc)[:80],
                eta=_total_elapsed_detail(started_at),
                percent=100,
                summary=counts.summary(),
            )
            yield (
                log,
                gr.update(value=f"_failed: {exc}_"),
                gr.update(value=""),
                status_html,
                gr.update(value=None, interactive=False),
            )
            return
        log += f"\n✓ wrote {label}\n"
        single_partial = had_partial_warning and batch_total is None and counts.total == 1
        if single_partial:
            counts.success = 0
            counts.failed = 0
            counts.partial = 1
        elif counts.total:
            counts.partial = 0
            counts.success = max(counts.success, max(0, counts.total - counts.failed))
        elif counts.seen == 0:
            counts.success = counts.total or 1
        final_state = "warn" if had_warning else "ok"
        final_label = "saved with warning" if single_partial else ("done with warning" if had_warning else "done")
        final_detail = (
            "Markdown saved; requested polish was not completed"
            if single_partial
            else ("Markdown saved; check log" if had_warning else "complete")
        )
        status_html = _status_html(
            final_state,
            final_label,
            detail=final_detail,
            eta=_total_elapsed_detail(started_at),
            percent=100,
            summary=counts.summary(),
        )
        download_value = _download_value_for_output(out_md, modified_since=download_modified_since)
        yield (
            log,
            gr.update(value=preview),
            gr.update(value=str(out_md)),
            status_html,
            gr.update(value=download_value, interactive=bool(download_value)),
        )
    else:
        try:
            preview, label = _preview_output(out_md)
        except ValueError:
            log += f"\n✗ failed (exit {rc})\n"
            if counts.seen == 0:
                counts.failed += 1
            status_html = _status_html(
                "err",
                "failed",
                detail=f"exit {rc}",
                eta=_total_elapsed_detail(started_at),
                percent=100,
                summary=counts.summary(),
            )
            yield (
                log,
                gr.update(value=f"_failed (exit {rc})_"),
                gr.update(value=""),
                status_html,
                gr.update(value=None, interactive=False),
            )
            return
        log += f"\n⚠ partial output available despite exit {rc}: {label}\n"
        single_partial = batch_total is None and counts.total == 1
        if single_partial:
            counts.success = 0
            counts.failed = 0
            counts.partial = 1
        elif counts.failed == 0:
            counts.failed += 1
        status_html = _status_html(
            "warn" if single_partial else "err",
            "saved with warning" if single_partial else "partial failure",
            detail="output available; check log" if single_partial else "open output folder",
            eta=_total_elapsed_detail(started_at),
            percent=100,
            summary=counts.summary(),
        )
        download_value = _download_value_for_output(out_md, modified_since=download_modified_since)
        yield (
            log,
            gr.update(value=preview),
            gr.update(value=str(out_md)),
            status_html,
            gr.update(value=download_value, interactive=bool(download_value)),
        )


DELL_1996_CSS = """
:root {
    color-scheme: light;
    --omd-bg: #bfc3c7;
    --omd-desktop: #0f6f78;
    --omd-window: #d8d8d8;
    --omd-panel: #ececec;
    --omd-panel-hi: #f8f8f8;
    --omd-panel-low: #b8b8b8;
    --omd-border-dark: #4a4a4a;
    --omd-border-mid: #8d8d8d;
    --omd-border-light: #ffffff;
    --omd-ink: #121212;
    --omd-muted: #343a40;
    --omd-blue: #073b9a;
    --omd-blue-2: #0d56c9;
    --omd-green: #0d6f39;
    --omd-red: #9b1c1c;
    --omd-amber: #a66a00;
    --omd-control-height: 36px;
    --omd-run-height: 46px;
    --omd-status-height: 104px;
    --omd-command-width: 144px;
    --body-background-fill: var(--omd-desktop);
    --background-fill-primary: var(--omd-desktop);
    --background-fill-secondary: var(--omd-window);
    --block-background-fill: var(--omd-window);
    --block-border-color: var(--omd-border-mid);
    --block-label-background-fill: transparent;
    --block-label-text-color: var(--omd-muted);
    --button-primary-background-fill: var(--omd-blue);
    --button-primary-text-color: #ffffff;
    --button-secondary-background-fill: var(--omd-window);
    --button-secondary-text-color: var(--omd-ink);
}

html,
body,
gradio-app,
.gradio-container,
.main,
.app {
    min-height: 100%;
    background:
        linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px),
        linear-gradient(0deg, rgba(255,255,255,0.05) 1px, transparent 1px),
        var(--omd-desktop) !important;
    background-size: 18px 18px !important;
    color: var(--omd-ink) !important;
}

.gradio-container {
    width: 100% !important;
    max-width: none !important;
    min-height: 100vh !important;
    margin: 0 !important;
    padding: 12px !important;
    font-family: Chicago, "Charcoal CY", "Geneva", "SF Pro Text", -apple-system,
        BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif !important;
    font-size: 14px !important;
    letter-spacing: 0 !important;
}

.gradio-container,
.gradio-container * {
    box-sizing: border-box !important;
    letter-spacing: 0 !important;
}

.gradio-container > .contain,
.gradio-container .contain {
    width: min(1180px, calc(100vw - 24px)) !important;
    max-width: 1180px !important;
    margin: 0 auto !important;
    padding: 0 !important;
    gap: 0 !important;
}

.omd-grid {
    margin-top: 0 !important;
}

#omd-desktop {
    border: 1px solid var(--omd-border-dark);
    border-bottom: 0;
    background: var(--omd-window);
    box-shadow:
        inset 1px 1px 0 var(--omd-border-light),
        inset -1px 0 0 #666;
}

#omd-titlebar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    min-height: 32px;
    padding: 6px 9px;
    border-bottom: 1px solid #001d68;
    background: linear-gradient(90deg, #082a78, #0b62c2);
    color: #fff;
}

#omd-titlebar .title {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    font-weight: 700;
}

#omd-titlebar .title strong {
    font-size: 1.02rem;
    white-space: nowrap;
}

#omd-titlebar .title span {
    color: rgba(255, 255, 255, 0.82);
    font-size: 0.84rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

#omd-window-buttons {
    display: flex;
    gap: 4px;
}

#omd-window-buttons i {
    display: block;
    width: 14px;
    height: 12px;
    border: 1px solid #1f1f1f;
    background: #d8d8d8;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #777;
}

#omd-menubar {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 28px;
    padding: 5px 9px;
    border-top: 1px solid #fff !important;
    border-bottom: 1px solid var(--omd-border-mid) !important;
    color: var(--omd-ink) !important;
    background: #d0d0d0 !important;
    font-size: 0.82rem !important;
}

#omd-menubar .omd-title-status {
    margin-left: auto;
    padding: 2px 7px;
    border: 1px solid #777;
    background: #ececec;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aaa;
    font-weight: 700;
}

#omd-menubar a.omd-title-status {
    color: #001d68 !important;
    text-decoration: underline !important;
    text-underline-offset: 2px;
}

#omd-menubar a.omd-title-status:hover,
#omd-menubar a.omd-title-status:focus-visible {
    border-color: #202020;
    background: #fff;
    outline: 2px solid #83a9ff;
    outline-offset: 1px;
}

#omd-menubar,
#omd-menubar span,
#omd-menubar .wrap,
#omd-menubar .wrap *,
#omd-menubar button {
    color: var(--omd-ink) !important;
    opacity: 1 !important;
}

#omd-menubar .block,
#omd-menubar .form,
#omd-menubar .wrap,
#omd-menubar .styler {
    min-width: 0 !important;
    min-height: 0 !important;
    width: auto !important;
    flex: 0 0 auto !important;
}

#omd-menubar .html-container,
#omd-menubar .html-container > div {
    width: 100% !important;
}

#omd-menubar .html-container > div {
    display: flex;
    justify-content: flex-end;
}

#omd-menubar .omd-menu-btn {
    width: 136px !important;
    min-width: 136px !important;
    flex: 0 0 136px !important;
}

#omd-menubar .omd-menu-btn button,
#omd-menubar button.omd-menu-btn,
#omd-menubar button {
    width: 100% !important;
    min-height: 28px !important;
    height: 28px !important;
    padding: 2px 8px !important;
    border: 1px solid #777 !important;
    background: #d8d8d8 !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aaa !important;
    font-size: 0.82rem !important;
    font-weight: 700 !important;
    opacity: 1 !important;
}

#omd-menubar .omd-menu-btn button:hover,
#omd-menubar button:hover {
    border-color: #333 !important;
    background: #ececec !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #777 !important;
    transform: none !important;
}

#omd-workbench {
    padding: 8px;
    background: var(--omd-window);
}

.omd-grid {
    display: grid !important;
    grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr) !important;
    align-items: start !important;
    gap: 10px !important;
    padding: 10px !important;
    border: 1px solid var(--omd-border-dark) !important;
    border-top: 0 !important;
    background: var(--omd-window) !important;
    box-shadow:
        inset 1px 0 0 var(--omd-border-light),
        inset -1px -1px 0 #666,
        10px 12px 0 rgba(0, 0, 0, 0.22) !important;
}

.omd-panel-source { grid-area: source; }
.omd-panel-output { grid-area: output; }
.omd-panel-run { grid-area: run; }
.omd-panel-result { grid-area: result; }
.omd-panel-cookies { grid-area: cookies; }
.omd-panel-advanced { grid-area: advanced; }

.gradio-container .gr-form,
.gradio-container .form,
.gradio-container .block,
.gradio-container .panel,
.gradio-container .row,
.gradio-container .column,
.gradio-container fieldset,
.gradio-container .styler {
    border: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
}

.omd-window,
.omd-pane {
    border: 1px solid var(--omd-border-dark) !important;
    border-radius: 0 !important;
    background: var(--omd-panel) !important;
    box-shadow:
        inset 1px 1px 0 var(--omd-border-light),
        inset -1px -1px 0 var(--omd-border-mid) !important;
}

.omd-window {
    padding: 0 !important;
    overflow: hidden !important;
}

.omd-pane {
    padding: 10px !important;
}

.omd-pane + .omd-pane,
.omd-stack > * + * {
    margin-top: 10px !important;
}

.omd-pane-title,
.omd-section-title {
    margin: 0 0 7px !important;
    padding: 6px 8px !important;
    border: 1px solid #0b2d7e !important;
    background: var(--omd-blue) !important;
    color: #fff !important;
    font-size: 0.82rem !important;
    font-weight: 700 !important;
    line-height: 1.1 !important;
    text-transform: none !important;
}

.omd-subtitle,
.omd-hint,
.omd-hint p,
.omd-hint span,
.omd-hint .md,
.omd-hint .md *,
.omd-model-warning,
.omd-model-warning p,
.omd-field-label,
.omd-field-label p,
.omd-field-label span,
.omd-field-label .md,
.omd-field-label .md * {
    margin: 0 0 8px !important;
    color: var(--omd-muted) !important;
    font-size: 0.84rem !important;
    line-height: 1.46 !important;
}

.omd-model-warning {
    padding: 10px 11px !important;
    border: 2px solid #8a6d00 !important;
    background: #fff2b8 !important;
    color: #2f2500 !important;
    box-shadow: inset 1px 1px 0 #fff9d6, inset -1px -1px 0 #c4a000 !important;
}

.omd-model-warning .md,
.omd-model-warning .md *,
.omd-model-warning p,
.omd-model-warning span {
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
    color: #2f2500 !important;
}

.omd-model-warning p + p {
    margin-top: 5px !important;
}

.omd-model-warning strong,
.omd-model-warning code {
    color: #111 !important;
}

.omd-legal-note {
    padding: 9px 10px !important;
    border: 1px solid #707070 !important;
    background: #f5f5f5 !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #bbb !important;
}

.omd-legal-note,
.omd-legal-note p,
.omd-legal-note li,
.omd-legal-note strong {
    color: var(--omd-ink) !important;
}

.omd-legal-note h3 {
    margin: 0 0 8px !important;
    padding: 0 0 6px !important;
    border-bottom: 1px solid #a4a4a4 !important;
    color: #0b2d7e !important;
    font-size: 0.94rem !important;
    font-weight: 800 !important;
    line-height: 1.25 !important;
}

.omd-legal-note h4 {
    margin: 11px 0 5px !important;
    color: var(--omd-ink) !important;
    font-size: 0.86rem !important;
    font-weight: 800 !important;
    line-height: 1.25 !important;
}

.omd-legal-note ul {
    margin: 6px 0 0 17px !important;
    padding: 0 !important;
}

.omd-legal-note li {
    margin: 0 0 4px !important;
}

.omd-result-tools-title {
    margin: 10px 0 6px !important;
    padding: 5px 7px !important;
    border: 1px solid #777 !important;
    background: #dedede !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aaa !important;
    font-size: 0.82rem !important;
    font-weight: 800 !important;
    line-height: 1.2 !important;
}

.omd-model-warning code {
    display: inline !important;
    padding: 0 2px !important;
    border: 0 !important;
    background: rgba(255, 255, 255, 0.38) !important;
    box-shadow: none !important;
}

.omd-model-status {
    margin: 2px 0 8px !important;
    padding: 9px 10px !important;
    border: 1px solid #707070 !important;
    background: #f5f5f5 !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #bbb !important;
    font-size: 0.83rem !important;
    line-height: 1.45 !important;
}

.omd-model-status strong {
    color: var(--omd-ink) !important;
}

.omd-model-status code {
    padding: 0 3px !important;
    border: 1px solid #999 !important;
    background: #fff !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #eee !important;
}

.omd-model-status-warn {
    border-color: #8a6d00 !important;
    background: #fff2b8 !important;
    color: #2f2500 !important;
    box-shadow: inset 1px 1px 0 #fff9d6, inset -1px -1px 0 #c4a000 !important;
}

.omd-model-status-ok {
    border-color: #557a5f !important;
    background: #e7f5e8 !important;
}

.omd-model-status-info {
    border-color: #5e6f8a !important;
    background: #e8eef8 !important;
}

.omd-drop-grid,
.omd-path-grid,
.omd-format-grid,
.omd-auth-grid,
.omd-option-grid,
.omd-action-row {
    gap: 10px !important;
    align-items: end !important;
}

.omd-drop-grid > *,
.omd-path-grid > *,
.omd-format-grid > *,
.omd-auth-grid > *,
.omd-option-grid > * {
    min-width: 0 !important;
}

.gradio-container textarea,
.gradio-container input:not([type="checkbox"]):not([type="radio"]),
.gradio-container select {
    border: 1px solid #454545 !important;
    border-radius: 0 !important;
    background: #fff !important;
    color: var(--omd-ink) !important;
    padding: 8px 9px !important;
    font-size: 0.92rem !important;
    line-height: 1.45 !important;
    box-shadow: inset 1px 1px 0 #7b7b7b, inset -1px -1px 0 #fff !important;
}

.gradio-container textarea:focus,
.gradio-container input:not([type="checkbox"]):not([type="radio"]):focus {
    border-color: #001d68 !important;
    outline: 3px solid #83a9ff !important;
    outline-offset: 1px !important;
    box-shadow: inset 1px 1px 0 #555, inset -1px -1px 0 #fff !important;
}

.gradio-container label,
.gradio-container .label-wrap span,
.gradio-container .block > .label-wrap span {
    padding: 0 !important;
    color: var(--omd-ink) !important;
    font-size: 0.84rem !important;
    font-weight: 700 !important;
    background: transparent !important;
}

.gradio-container label span,
.gradio-container .label-wrap span,
.gradio-container .container span.svelte-jdcl7l {
    padding: 0 !important;
    border: 0 !important;
    border-radius: 0 !important;
    background: transparent !important;
    color: var(--omd-ink) !important;
    box-shadow: none !important;
}

.gradio-container .wrap.center.full,
.gradio-container .wrap.default.full,
.gradio-container .wrap-inner,
.gradio-container .secondary-wrap,
.gradio-container .input-container {
    background: transparent !important;
    color: var(--omd-ink) !important;
}

.gradio-container .file .wrap,
.gradio-container [data-testid="file"] .wrap,
.gradio-container [class*="upload"] .wrap {
    background: transparent !important;
    color: var(--omd-ink) !important;
}

.gradio-container .file *,
.gradio-container [data-testid="file"] *,
.gradio-container [class*="upload"] * {
    color: var(--omd-ink) !important;
}

.gradio-container .file,
.gradio-container [data-testid="file"],
.gradio-container [class*="upload"] {
    min-height: 126px !important;
    border: 1px dashed #4b4b4b !important;
    border-radius: 0 !important;
    background:
        linear-gradient(45deg, rgba(0,0,0,0.03) 25%, transparent 25%, transparent 75%, rgba(0,0,0,0.03) 75%),
        #f7f7f7 !important;
    background-size: 10px 10px !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aaa !important;
}

.omd-drop-grid .file,
.omd-drop-grid [data-testid="file"],
.omd-drop-grid [class*="upload"],
.omd-drop-grid .block:has(button.boundedheight),
.omd-drop-grid .boundedheight {
    min-height: 132px !important;
    height: auto !important;
    max-height: none !important;
}

.omd-pane > .file,
.omd-pane > [data-testid="file"],
.omd-pane > [class*="upload"],
.omd-pane .block:has(button.boundedheight),
.omd-pane button.boundedheight {
    min-height: 132px !important;
    height: auto !important;
    max-height: none !important;
}

.omd-file-queue {
    margin: 6px 0 4px !important;
    padding: 7px 9px !important;
    border: 1px solid #777 !important;
    background: #f5f5f5 !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #bbb !important;
}

.omd-file-queue .md,
.omd-file-queue .md * {
    color: var(--omd-ink) !important;
    font-size: 0.82rem !important;
    line-height: 1.35 !important;
}

.omd-file-queue blockquote {
    margin: 7px 0 0 !important;
    padding: 6px 8px !important;
    border: 1px solid #8a6d00 !important;
    background: #fff2b8 !important;
    color: #2f2500 !important;
    box-shadow: inset 1px 1px 0 #fff9d6, inset -1px -1px 0 #c4a000 !important;
}

.omd-drop-grid .file-preview-holder {
    padding: 46px 6px 6px !important;
    overflow-x: hidden !important;
}

.omd-drop-grid table.file-preview {
    width: 100% !important;
    table-layout: fixed !important;
}

.omd-drop-grid tr.file,
.omd-drop-grid tr.file:hover {
    min-height: 0 !important;
    height: 40px !important;
    border: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
}

.omd-drop-grid tr.file td {
    height: 40px !important;
    padding: 5px 6px !important;
    border-bottom: 1px solid #b0b0b0 !important;
    vertical-align: middle !important;
}

.omd-drop-grid tr.file td.filename {
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}

.omd-drop-grid tr.file td.download {
    width: 96px !important;
    text-align: right !important;
}

.omd-drop-grid tr.file td:last-child {
    width: 32px !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel {
    top: 6px !important;
    right: 6px !important;
    z-index: 4 !important;
    display: flex !important;
    gap: 6px !important;
    width: auto !important;
    height: var(--omd-control-height) !important;
    padding: 0 !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button {
    position: relative !important;
    display: grid !important;
    place-items: center !important;
    width: auto !important;
    min-width: 0 !important;
    height: var(--omd-control-height) !important;
    min-height: var(--omd-control-height) !important;
    max-height: var(--omd-control-height) !important;
    padding: 0 12px !important;
    border: 1px solid #202020 !important;
    background: #d8d8d8 !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #777 !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button .small {
    position: absolute !important;
    inset: 0 !important;
    display: grid !important;
    place-items: center !important;
    width: 100% !important;
    height: 100% !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button svg {
    display: none !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button::after {
    position: absolute !important;
    inset: auto !important;
    top: 50% !important;
    left: 50% !important;
    z-index: 1 !important;
    display: block !important;
    width: max-content !important;
    transform: translate(-50%, -50%) !important;
    text-align: center !important;
    color: #111 !important;
    font-size: 0.82rem !important;
    font-weight: 800 !important;
    line-height: 1 !important;
    white-space: nowrap !important;
    pointer-events: none !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="common.upload"]::after,
.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="Add files"]::after {
    content: "+ Add files" !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="Clear"]::after {
    content: "× Clear" !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="common.upload"],
.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="Add files"] {
    width: 108px !important;
    min-width: 108px !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel > button.icon-button[aria-label="Clear"] {
    width: 82px !important;
    min-width: 82px !important;
}

.omd-drop-grid .icon-button-wrapper.top-panel button.boundedheight {
    position: absolute !important;
    inset: 0 !important;
    z-index: 2 !important;
    width: 100% !important;
    min-width: 0 !important;
    height: 100% !important;
    min-height: 0 !important;
    max-height: none !important;
    padding: 0 !important;
    border: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
    opacity: 0 !important;
}

.omd-drop-grid button.label-clear-button {
    width: 24px !important;
    min-width: 24px !important;
    height: 24px !important;
    min-height: 24px !important;
    padding: 0 !important;
    font-size: 1rem !important;
    line-height: 1 !important;
}

.omd-drop-grid .wrap,
.omd-drop-grid .wrap-inner,
.omd-drop-grid .secondary-wrap,
.omd-pane > .file .wrap,
.omd-pane > [data-testid="file"] .wrap,
.omd-pane > [class*="upload"] .wrap,
.omd-pane .block:has(button.boundedheight) .wrap {
    min-height: 0 !important;
    padding: 9px !important;
}

.omd-pane .block:has(button.boundedheight) .icon-wrap,
.omd-pane .block:has(button.boundedheight) svg {
    display: none !important;
}

.omd-pane .block:has(button.boundedheight) .or {
    height: 16px !important;
    line-height: 16px !important;
}

.omd-download-slot,
.omd-download-slot .file,
.omd-download-slot [data-testid="file"],
.omd-download-slot [class*="upload"],
.omd-download-slot .block:has(button.boundedheight),
.omd-download-slot .boundedheight,
.omd-download-slot button {
    min-height: 58px !important;
    height: auto !important;
    max-height: none !important;
}

.omd-download-slot {
    width: 100% !important;
    border-color: #00154f !important;
    background: var(--omd-blue) !important;
    color: #fff !important;
    box-shadow: inset 1px 1px 0 #6ca0ff, inset -1px -1px 0 #001845 !important;
}

.omd-download-slot:hover:not([disabled]) {
    background: var(--omd-blue-2) !important;
}

.omd-download-slot[disabled] {
    border-color: #777 !important;
    background: #c5c5c5 !important;
    color: #555 !important;
    box-shadow: inset 1px 1px 0 #eee, inset -1px -1px 0 #999 !important;
}

.omd-download-slot .wrap,
.omd-download-slot .wrap-inner,
.omd-download-slot .secondary-wrap,
.omd-download-slot .block:has(button.boundedheight) .wrap {
    min-height: 0 !important;
    padding: 7px 9px !important;
}

.omd-download-slot .file,
.omd-download-slot [data-testid="file"],
.omd-download-slot [class*="upload"] {
    border-style: solid !important;
    background: #f8f8f8 !important;
}

.gradio-container .file:hover,
.gradio-container [data-testid="file"]:hover,
.gradio-container [class*="upload"]:hover {
    background-color: #fff !important;
    border-style: solid !important;
}

.gradio-container button,
.gradio-container [role="button"] {
    min-height: var(--omd-control-height) !important;
    padding: 0 14px !important;
    border: 1px solid #202020 !important;
    border-radius: 0 !important;
    background: #d8d8d8 !important;
    color: var(--omd-ink) !important;
    font-size: 0.88rem !important;
    font-weight: 700 !important;
    line-height: 1.2 !important;
    white-space: nowrap !important;
    box-shadow:
        inset 1px 1px 0 #fff,
        inset -1px -1px 0 #777 !important;
    transition:
        transform 120ms ease,
        filter 120ms ease,
        background-color 120ms ease !important;
}

.gradio-container button .wrap,
.gradio-container button .wrap *,
.gradio-container [role="button"] .wrap,
.gradio-container [role="button"] .wrap * {
    color: var(--omd-ink) !important;
}

.gradio-container button:hover,
.gradio-container [role="button"]:hover {
    filter: brightness(1.04);
    transform: translateY(-1px);
}

.gradio-container button:focus-visible,
.gradio-container [role="button"]:focus-visible {
    outline: 3px solid #83a9ff !important;
    outline-offset: 2px !important;
}

.gradio-container button:disabled,
.gradio-container [role="button"][aria-disabled="true"] {
    filter: none !important;
    transform: none !important;
}

.gradio-container button:active,
.gradio-container [role="button"]:active {
    transform: translateY(1px);
    box-shadow:
        inset 1px 1px 0 #777,
        inset -1px -1px 0 #fff !important;
}

#omd-run-btn {
    width: 100% !important;
    min-height: var(--omd-run-height) !important;
    height: var(--omd-run-height) !important;
    border-color: #00154f !important;
    background: var(--omd-blue) !important;
    color: #fff !important;
    font-size: 0.98rem !important;
    box-shadow:
        inset 1px 1px 0 #6ca0ff,
        inset -1px -1px 0 #001845 !important;
}

#omd-run-btn .wrap,
#omd-run-btn .wrap *,
#omd-run-btn span {
    color: #fff !important;
}

#omd-run-btn:hover {
    background: var(--omd-blue-2) !important;
}

.omd-picker-btn,
.omd-inspect-btn {
    min-height: var(--omd-control-height) !important;
}

.omd-picker-btn {
    width: var(--omd-command-width) !important;
    min-width: var(--omd-command-width) !important;
    max-width: var(--omd-command-width) !important;
    align-self: end !important;
}

.omd-picker-btn button,
.omd-path-grid button,
.omd-auth-grid button,
.omd-inspect-btn button {
    width: 100% !important;
    min-height: var(--omd-control-height) !important;
    height: var(--omd-control-height) !important;
}

.omd-inspect-btn {
    width: 100% !important;
}

.omd-path-grid,
.omd-auth-grid {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) var(--omd-command-width) !important;
    gap: 10px !important;
    align-items: end !important;
}

.omd-path-grid > *,
.omd-auth-grid > * {
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
    flex: none !important;
}

.omd-path-grid .form,
.omd-path-grid .block,
.omd-path-grid label,
.omd-path-grid .input-container,
.omd-format-grid .form,
.omd-format-grid .block,
.omd-format-grid label,
.omd-format-grid .input-container,
.omd-auth-grid .form,
.omd-auth-grid .block,
.omd-auth-grid label,
.omd-auth-grid .input-container {
    min-height: 0 !important;
    height: auto !important;
}

.omd-path-grid textarea,
.omd-format-grid textarea,
.omd-auth-grid textarea {
    min-height: 34px !important;
    height: 34px !important;
    padding: 8px 9px !important;
    overflow: hidden !important;
    resize: none !important;
}

.omd-path-grid .wrap,
.omd-format-grid .wrap,
.omd-auth-grid .wrap {
    min-height: 0 !important;
}

.omd-auth-grid {
    align-items: end !important;
}

#omd-source-input textarea {
    min-height: 230px !important;
    font-family: "SF Mono", Monaco, Menlo, Consolas, monospace !important;
    font-size: 12.5px !important;
    line-height: 1.56 !important;
}

.omd-option-panel {
    padding: 10px !important;
    border: 1px solid var(--omd-border-mid) !important;
    border-radius: 0 !important;
    background: #dedede !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aaa !important;
}

.omd-advanced-pane {
    display: flex !important;
    flex-direction: column !important;
    gap: 10px !important;
}

.omd-settings-section {
    margin-top: 0 !important;
}

.omd-settings-section .omd-option-panel {
    margin-top: 0 !important;
}

.omd-settings-section + .omd-settings-section {
    margin-top: 10px !important;
}

.omd-settings-grid,
.omd-checkbox-grid {
    gap: 10px !important;
    align-items: start !important;
}

.omd-checkbox-grid label {
    min-height: 26px !important;
}

.omd-option-panel + .omd-option-panel {
    margin-top: 10px !important;
}

.omd-option-title {
    margin: 0 0 6px !important;
    color: var(--omd-ink) !important;
    font-size: 0.88rem !important;
    font-weight: 800 !important;
}

.omd-option-panel .info,
.omd-option-panel [class*="info"],
.omd-option-panel .secondary,
.omd-option-panel small,
.omd-option-panel .description,
.omd-option-panel .prose {
    color: var(--omd-muted) !important;
    font-size: 0.8rem !important;
    line-height: 1.45 !important;
}

.omd-option-panel [role="radiogroup"],
.omd-option-panel [role="group"] {
    gap: 7px !important;
}

.omd-option-panel label,
.omd-option-panel input,
.omd-option-panel [role="checkbox"] {
    cursor: pointer !important;
    pointer-events: auto !important;
}

.omd-option-panel input[type="checkbox"] {
    appearance: auto !important;
    accent-color: var(--omd-blue) !important;
}

.omd-option-panel label:has(input[type="checkbox"]) {
    position: relative !important;
    z-index: 3 !important;
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;
    width: 100% !important;
    min-height: 24px !important;
}

#omd-inspect-preview,
#omd-tutorial,
#omd-log textarea {
    border: 1px solid #454545 !important;
    border-radius: 0 !important;
    background: #fff !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #777, inset -1px -1px 0 #fff !important;
}

#omd-inspect-preview {
    margin-top: 8px !important;
    padding: 9px 10px !important;
    max-height: 270px !important;
    overflow: auto !important;
    font-size: 0.84rem !important;
    line-height: 1.55 !important;
}

#omd-inspect-preview h2,
#omd-inspect-preview h3 {
    margin: 0 0 6px !important;
    font-size: 0.8rem !important;
}

#omd-tutorial {
    padding: 10px !important;
    font-size: 0.84rem !important;
    line-height: 1.55 !important;
}

#omd-tutorial h3 {
    margin: 0 0 8px !important;
    font-size: 0.88rem !important;
}

#omd-log textarea {
    min-height: 210px !important;
    font-family: "SF Mono", Monaco, Menlo, Consolas, monospace !important;
    font-size: 12.5px !important;
    line-height: 1.56 !important;
}

.omd-run-dock {
    position: relative;
    z-index: 5;
    margin-top: 10px !important;
    padding: 10px !important;
    border: 1px solid var(--omd-border-dark) !important;
    background: #cfcfcf !important;
    box-shadow:
        inset 1px 1px 0 #fff,
        inset -1px -1px 0 #777,
        0 -8px 16px rgba(0,0,0,0.14) !important;
}

.omd-run-dock .row,
.omd-run-dock .column,
.omd-run-dock .block,
.omd-run-dock .form,
.omd-run-dock .styler {
    min-height: 0 !important;
    height: auto !important;
}

.omd-run-dock .omd-action-row {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) var(--omd-command-width) !important;
    align-items: stretch !important;
    gap: 10px !important;
}

.omd-run-dock .omd-action-row > * {
    width: 100% !important;
    min-width: 0 !important;
    height: var(--omd-run-height) !important;
    flex: none !important;
}

.omd-run-dock .omd-action-row > :nth-child(3) {
    grid-column: 1 / -1 !important;
    min-height: var(--omd-status-height) !important;
    height: auto !important;
}

#omd-stop-btn {
    width: 100% !important;
    min-height: var(--omd-run-height) !important;
    height: var(--omd-run-height) !important;
}

#omd-status {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    min-height: var(--omd-status-height);
    height: auto;
    padding: 10px 11px;
    border: 1px solid #454545;
    background: #eeeeee;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #999;
    font-size: 0.84rem;
    font-weight: 700;
}

.omd-status-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    min-width: 0;
}

.omd-status-label,
.omd-status-detail,
.omd-status-eta {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.omd-status-label {
    grid-column: 1;
    grid-row: 1;
    color: var(--omd-ink);
}

.omd-status-detail {
    grid-column: 1 / -1;
    grid-row: 2;
    color: var(--omd-muted);
}

.omd-status-eta {
    grid-column: 2;
    grid-row: 1;
    justify-self: end;
    color: var(--omd-muted);
}

.omd-status-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    min-height: 18px;
}

.omd-status-pill {
    display: inline-flex;
    align-items: center;
    min-height: 18px;
    padding: 1px 6px;
    border: 1px solid #777;
    background: #f7f7f7;
    color: var(--omd-ink);
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #bbb;
    font-size: 0.75rem;
    line-height: 1.2;
}

.omd-status-pill-ok {
    border-color: #5b8a68;
    color: var(--omd-green);
}

.omd-status-pill-fail {
    border-color: #a76060;
    color: var(--omd-red);
}

.omd-status-pill-partial {
    border-color: #a67a18;
    color: #7a4d00;
    background: #fff2b8;
}

.omd-progress-track {
    position: relative;
    height: 10px;
    overflow: hidden;
    border: 1px solid #555;
    background: #fff;
    box-shadow: inset 1px 1px 0 #999;
}

.omd-progress-bar {
    display: block;
    height: 100%;
    width: var(--omd-progress, 0%);
    background: repeating-linear-gradient(
        90deg,
        var(--omd-blue),
        var(--omd-blue) 8px,
        #6ca0ff 8px,
        #6ca0ff 10px
    );
    transition: width 220ms ease;
}

.omd-status-indeterminate .omd-progress-bar {
    width: 36%;
    animation: omd-progress-slide 1s steps(8) infinite;
}

@keyframes omd-progress-slide {
    0% { transform: translateX(-110%); }
    100% { transform: translateX(290%); }
}

.omd-status-ok .omd-progress-bar { background: var(--omd-green); }
.omd-status-warn .omd-progress-bar { background: #c48b00; }
.omd-status-err .omd-progress-bar { background: var(--omd-red); }
.omd-status-warn {
    border-color: #8a6d00 !important;
    background: #fff2b8 !important;
    box-shadow: inset 1px 1px 0 #fff9d6, inset -1px -1px 0 #c4a000 !important;
}
.omd-status-ok .omd-status-label { color: var(--omd-green); }
.omd-status-warn .omd-status-label { color: #8a5200; }
.omd-status-err .omd-status-label { color: var(--omd-red); }

.gradio-container .accordion,
.gradio-container details {
    border: 1px solid var(--omd-border-dark) !important;
    border-radius: 0 !important;
    background: var(--omd-panel) !important;
    box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #888 !important;
}

.gradio-container .accordion > .label-wrap,
.gradio-container details > summary {
    min-height: 34px !important;
    padding: 9px 10px !important;
    border: 0 !important;
    background: #d8d8d8 !important;
    color: var(--omd-ink) !important;
    cursor: pointer;
}

.gradio-container code {
    padding: 1px 4px !important;
    border: 1px solid #aaa !important;
    border-radius: 0 !important;
    background: #efefef !important;
    color: #111 !important;
}

.gradio-container footer,
.gradio-container .footer {
    display: none !important;
}

@media (max-width: 860px) {
    .gradio-container {
        padding: 10px !important;
    }
    .gradio-container > .contain,
    .gradio-container .contain {
        width: calc(100vw - 20px) !important;
    }
    #omd-titlebar,
    #omd-menubar {
        align-items: flex-start;
        flex-direction: column;
        gap: 6px;
    }
    #omd-menubar .omd-title-status {
        margin-left: 0;
    }
    #omd-menubar .omd-menu-btn {
        width: 100% !important;
        min-width: 0 !important;
        flex-basis: auto !important;
    }
    .omd-grid {
        grid-template-columns: 1fr !important;
        grid-template-areas:
            "source"
            "output"
            "run"
            "result"
            "cookies"
            "advanced" !important;
    }
    .omd-grid > .omd-stack {
        display: contents !important;
    }
    .omd-grid .omd-stack > * {
        margin-top: 0 !important;
    }
    .omd-run-dock {
        position: static;
    }
}

@media (max-width: 620px) {
    .omd-path-grid,
    .omd-auth-grid,
    .omd-run-dock .omd-action-row {
        grid-template-columns: minmax(0, 1fr) !important;
    }
    .omd-picker-btn {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
    }
    .omd-run-dock .omd-action-row > * {
        height: auto !important;
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
    }
}
"""

CUSTOM_CSS = DELL_1996_CSS
UI_TRANSLATIONS = {
    "common.upload": "Add files",
}


def build_launch_kwargs(
    *,
    inbrowser: bool = False,
    server_name: str | None = None,
    server_port: int | None = None,
) -> dict[str, object]:
    """Return Gradio launch kwargs supported by the installed version."""
    import inspect
    import gradio as gr

    kwargs: dict[str, object] = {
        "server_name": server_name or os.environ.get("OMD_UI_HOST", "127.0.0.1"),
        "server_port": server_port if server_port is not None else int(os.environ.get("OMD_UI_PORT", "7860")),
    }
    if inbrowser:
        kwargs["inbrowser"] = True

    accepted = set(inspect.signature(gr.Blocks.launch).parameters)
    if "theme" in accepted:
        try:
            kwargs["theme"] = gr.themes.Soft(
                primary_hue="orange",
                secondary_hue="gray",
                neutral_hue="gray",
            )
        except Exception:  # noqa: BLE001
            pass
    if "show_api" in accepted:
        kwargs["show_api"] = False
    if _public_demo_enabled() and "max_file_size" in accepted:
        limit_mb = _public_demo_max_upload_bytes() // (1024 * 1024)
        kwargs["max_file_size"] = f"{limit_mb}mb"
    if _public_demo_enabled() and "max_threads" in accepted:
        kwargs["max_threads"] = 4
    if _public_demo_enabled() and "enable_monitoring" in accepted:
        kwargs["enable_monitoring"] = False
    if "css" in accepted:
        kwargs["css"] = CUSTOM_CSS
    if "i18n" in accepted and hasattr(gr, "I18n"):
        kwargs["i18n"] = gr.I18n(en=UI_TRANSLATIONS)
    return kwargs


def build_app():
    import gradio as gr  # local - keeps `from omd.ui import _build_argv` import-light

    _prune_stale_ui_staging_roots()
    public_demo = _public_demo_enabled()
    default_polish_model = _default_polish_model()
    cookies_default = next(
        (
            path
            for path in (DEFAULT_COOKIES, LEGACY_DEFAULT_COOKIES)
            if Path(path).expanduser().exists()
        ),
        "",
    )

    import inspect as _inspect

    blocks_kwargs = {"title": "omd"}
    launch_params = set(_inspect.signature(gr.Blocks.launch).parameters)
    if "css" not in launch_params:
        blocks_kwargs["css"] = CUSTOM_CSS

    title_status_html = (
        '<span class="omd-title-status">Hosted demo: sample uploads only</span>'
        if public_demo
        else (
            f'<a class="omd-title-status" href="{PROJECT_URL}" target="_blank" '
            'rel="noopener noreferrer" aria-label="Open OMD on GitHub">GitHub / feedback</a>'
        )
    )

    with gr.Blocks(**blocks_kwargs) as app:
        source_file_queue = gr.State(value=[])
        gr.HTML(
            '<div id="omd-desktop">'
            '<div id="omd-titlebar">'
            '<div class="title"><strong>OMD.EXE</strong><span>Local AI context inbox</span></div>'
            '<div id="omd-window-buttons"><i></i><i></i><i></i></div>'
            '</div></div>'
        )
        with gr.Row(elem_id="omd-menubar"):
            all_tab = gr.Button("All", elem_classes=["omd-menu-btn"])
            source_tab = gr.Button("Source", elem_classes=["omd-menu-btn"])
            output_tab = gr.Button("Output", elem_classes=["omd-menu-btn"])
            advanced_tab = gr.Button("Advanced settings", elem_classes=["omd-menu-btn"])
            gr.HTML(title_status_html)

        with gr.Row(elem_classes=["omd-grid"]):
            with gr.Column(scale=7, elem_classes=["omd-stack"]):
                with gr.Accordion(
                    "Add source",
                    open=True,
                    elem_classes=["omd-window", "omd-panel-source"],
                ) as source_window:
                    with gr.Group(elem_classes=["omd-pane"]):
                        text_input = gr.Textbox(
                            label="Paste URLs, share text, or local file paths",
                            placeholder=SOURCE_PLACEHOLDER,
                            lines=10,
                            elem_id="omd-source-input",
                        )
                        gr.Markdown("Source files (up to 5)", elem_classes=["omd-field-label"])
                        file_input = gr.File(
                            label="Drop source files",
                            show_label=False,
                            type="filepath",
                            file_count="multiple",
                            elem_classes=["omd-drop-grid"],
                        )
                        source_file_queue_summary = gr.Markdown(
                            value=_source_file_queue_summary([]),
                            elem_classes=["omd-file-queue"],
                        )
                        batch_file_input = gr.File(
                            label="Upload saved URL list (.txt)",
                            type="filepath",
                            file_count="single",
                            file_types=[".txt"],
                            render=False,
                        )
                        batch_file_path = gr.Textbox(visible=False)
                        gr.Markdown(
                            SOURCE_QUICK_START,
                            elem_classes=["omd-hint"],
                        )
                        inspect_btn = gr.Button("Inspect source / cookies", elem_classes=["omd-inspect-btn"])
                        inspect_preview = gr.Markdown(
                            value="_No source inspected yet._",
                            elem_id="omd-inspect-preview",
                        )

                with gr.Accordion(
                    "Result and download",
                    open=True,
                    elem_classes=["omd-window", "omd-panel-result"],
                ) as result_window:
                    with gr.Group(elem_classes=["omd-pane"]):
                        gr.HTML('<div class="omd-result-tools-title">Generated Markdown</div>')
                        with gr.Row(elem_classes=["omd-path-grid"]):
                            out_path_box = gr.Textbox(label="Output path", interactive=False, scale=5)
                            open_output_btn = gr.Button(
                                "Open folder",
                                scale=1,
                                elem_classes=["omd-picker-btn"],
                                visible=not public_demo,
                            )
                        download_file = gr.DownloadButton(
                            label="Download Markdown",
                            value=None,
                            interactive=False,
                            visible=True,
                            elem_classes=["omd-download-slot"],
                        )
                        md_preview = gr.Markdown(value="_No output yet._")
                        log_box = gr.Textbox(
                            label="Process log",
                            lines=10,
                            max_lines=40,
                            autoscroll=True,
                            elem_id="omd-log",
                        )
                        gr.Markdown(
                            PROCESS_LOG_DISCLAIMER,
                            elem_classes=["omd-hint", "omd-legal-note"],
                        )

            with gr.Column(scale=5, elem_classes=["omd-stack"]):
                with gr.Accordion(
                    "Choose output",
                    open=True,
                    elem_classes=["omd-window", "omd-panel-output"],
                ) as output_window:
                    with gr.Group(elem_classes=["omd-pane"]):
                        workflow_mode = gr.Radio(
                            label="Action",
                            choices=["Convert to .md file", "Capture to vault note"],
                            value="Convert to .md file",
                            interactive=not public_demo,
                        )
                        with gr.Row(elem_classes=["omd-path-grid"]):
                            out_dir = gr.Textbox(
                                label="Folder",
                                value=str(_public_demo_output_dir()) if public_demo else DEFAULT_OUTPUT_DIR,
                                scale=5,
                                interactive=not public_demo,
                            )
                            choose_out_dir = gr.Button(
                                "Choose folder",
                                scale=1,
                                elem_classes=["omd-picker-btn"],
                                interactive=not public_demo,
                            )
                        with gr.Row(elem_classes=["omd-path-grid"]):
                            vault_dir = gr.Textbox(
                                label="Vault folder",
                                value=DEFAULT_VAULT_DIR,
                                scale=5,
                                interactive=not public_demo,
                            )
                            choose_vault_dir = gr.Button(
                                "Choose vault",
                                scale=1,
                                elem_classes=["omd-picker-btn"],
                                interactive=not public_demo,
                            )
                        filename = gr.Textbox(label="Filename", placeholder="auto", value="")
                        output_format = gr.State("Markdown (.md)")
                        gr.Markdown(
                            (
                                "Hosted demo writes to temporary server storage and returns a download. "
                                "Do not upload sensitive or private files. "
                                "Run Full Power Demo locally to choose a folder on your own Mac."
                                if public_demo
                                else "Convert writes one `.md` file. Capture writes an Obsidian-compatible note directly into the vault folder."
                            ),
                            elem_classes=["omd-hint"],
                        )

                with gr.Accordion(
                    "Convert",
                    open=True,
                    elem_classes=["omd-run-dock", "omd-panel-run"],
                ) as run_dock:
                    with gr.Row(elem_classes=["omd-action-row"]):
                        with gr.Column(scale=2, min_width=180):
                            run_btn = gr.Button("Convert to Markdown", variant="primary", elem_id="omd-run-btn")
                        with gr.Column(scale=1, min_width=140):
                            stop_btn = gr.Button("Stop", variant="stop", elem_id="omd-stop-btn")
                        with gr.Column(scale=3, min_width=260):
                            status = gr.HTML(value=STATUS_HTML["idle"], container=False)

                with gr.Accordion(
                    "Cookies for gated sources",
                    open=False,
                    visible=True,
                    elem_classes=["omd-window", "omd-panel-cookies"],
                ) as cookies_window:
                    with gr.Group(elem_classes=["omd-pane"]):
                        with gr.Row(elem_classes=["omd-auth-grid"]):
                            cookies_file = gr.Textbox(
                                label="Default / Douyin cookies.txt path",
                                value="" if public_demo else cookies_default,
                                placeholder="~/.local/share/omd/cookies/douyin_cookies.txt",
                                scale=5,
                                interactive=not public_demo,
                            )
                            choose_cookies_file = gr.Button(
                                "Choose Douyin",
                                scale=1,
                                elem_classes=["omd-picker-btn"],
                                interactive=not public_demo,
                            )
                        cookies_upload = gr.File(
                            label="Upload Default / Douyin cookies",
                            show_label=False,
                            type="filepath",
                            file_count="single",
                            file_types=[".txt"],
                            interactive=not public_demo,
                        )
                        with gr.Row(elem_classes=["omd-auth-grid"]):
                            xhs_cookies_file = gr.Textbox(
                                label="XHS / Rednote cookies.txt path",
                                value="",
                                placeholder="~/.local/share/omd/cookies/xhs_cookies.txt",
                                scale=5,
                                interactive=not public_demo,
                            )
                            choose_xhs_cookies_file = gr.Button(
                                "Choose XHS",
                                scale=1,
                                elem_classes=["omd-picker-btn"],
                                interactive=not public_demo,
                            )
                        xhs_cookies_upload = gr.File(
                            label="Upload XHS / Rednote cookies",
                            show_label=False,
                            type="filepath",
                            file_count="single",
                            file_types=[".txt"],
                            interactive=not public_demo,
                        )
                        gr.Markdown(
                            (
                                "Cookie upload and browser extraction are disabled in the hosted demo. "
                                "Use Full Power Demo locally for Douyin/XHS authenticated sources."
                                if public_demo
                                else (
                                    "Douyin and XHS / Rednote use separate cookies fields. Upload the Douyin cookies.txt file, "
                                    "the XHS cookies.txt file, or both, depending on your sources. If a Douyin or XHS source is "
                                    "missing its matching cookie file, Inspect source / cookies will show a warning. X, Threads, "
                                    "and Reddit are public-only in this app and do not consume these cookie files."
                                )
                            ),
                            elem_classes=["omd-field-label"],
                        )
                        cookies_browser = gr.Dropdown(
                            label="Read cookies from browser",
                            choices=["(none)", "chrome", "edge", "firefox", "brave"],
                            value="(none)",
                            info="Chrome, Edge, Firefox, and Brave only. Safari is not offered here. Douyin and XHS use the cookies.txt fields above; public-only sources do not consume browser cookies.",
                            interactive=not public_demo,
                        )

                with gr.Accordion(
                    "Advanced settings",
                    open=False,
                    visible=True,
                    elem_classes=["omd-window", "omd-panel-advanced"],
                ) as advanced_window:
                    with gr.Group(elem_classes=["omd-pane", "omd-advanced-pane"]):
                        gr.Markdown(
                            "Most users can leave these alone. Open only the section you need.",
                            elem_classes=["omd-hint"],
                        )
                        with gr.Accordion("Text and Obsidian polish", open=True, elem_classes=["omd-settings-section"]):
                            with gr.Group(elem_classes=["omd-option-panel"]):
                                gr.HTML('<div class="omd-option-title">Local model cleanup</div>')
                                polish_md = gr.Checkbox(
                                    label="Polish Markdown",
                                    value=not public_demo,
                                    info=(
                                        "Convert mode default: local Ollama fixes spacing, text-in-image cleanup, and line breaks without translating. "
                                        "If Ollama or the model is unavailable, OMD skips quickly and keeps raw Markdown."
                                    ),
                                    interactive=not public_demo,
                                )
                                polish_md_keep_raw = gr.Checkbox(label="Keep raw copy", value=False, interactive=not public_demo)
                                with gr.Row(elem_classes=["omd-settings-grid"]):
                                    polish_md_model = gr.Textbox(label="Markdown model", value=default_polish_model)
                                    ollama_host = gr.Textbox(
                                        label="Ollama host",
                                        value="" if public_demo else "http://localhost:11434",
                                        interactive=not public_demo,
                                    )
                                memory_cards = gr.Checkbox(
                                    label="Polish for Obsidian",
                                    value=False,
                                    info=(
                                        "Capture mode default: richer local summary, useful tags, and [[links]]/memory cards for Obsidian. "
                                        "Turn off for faster raw capture."
                                    ),
                                    interactive=not public_demo,
                                )
                                memory_model = gr.Textbox(
                                    label="Memory model",
                                    value=default_polish_model,
                                    interactive=not public_demo,
                                )
                                gr.Markdown(
                                    LOCAL_MODEL_NOTICE,
                                    elem_classes=["omd-model-warning"],
                                )
                                local_model_status = gr.HTML(
                                    value=_local_model_status_html(
                                        default_polish_model,
                                        default_polish_model,
                                        "" if public_demo else "http://localhost:11434",
                                        public_demo=public_demo,
                                    ),
                                    container=False,
                                )
                                check_local_model_btn = gr.Button(
                                    "Check local model",
                                    elem_classes=["omd-picker-btn"],
                                    interactive=not public_demo,
                                )
                                json_events = gr.State(False)
                                with gr.Accordion(
                                    "Developer diagnostics",
                                    open=False,
                                    elem_classes=["omd-settings-section"],
                                ):
                                    with gr.Group(elem_classes=["omd-option-panel"]):
                                        gr.HTML('<div class="omd-option-title">Debug output</div>')
                                        verbose = gr.Checkbox(
                                            label="Verbose log",
                                            value=False,
                                            info=(
                                                "Leave off for normal use. Turn on only when diagnosing converter or model output. "
                                                "Extra lines appear in Process log for this run; they are not saved into your Obsidian vault or generated Markdown."
                                            ),
                                        )

                        with gr.Accordion("Platform adapters", open=False, elem_classes=["omd-settings-section"]):
                            with gr.Group(elem_classes=["omd-option-panel"]):
                                gr.HTML('<div class="omd-option-title">Structured source output</div>')
                                reddit_comment_scope = gr.Radio(
                                    label="Reddit content",
                                    choices=["OP only", "OP + Top comments"],
                                    value="OP only",
                                    info=(
                                        "OP only keeps the original post and metadata. OP + Top comments also keeps "
                                        "comment authors, timestamps, nesting, permalinks, and edited/deleted markers."
                                    ),
                                )

                        with gr.Accordion("Media transcription", open=False, elem_classes=["omd-settings-section"]):
                            with gr.Group(elem_classes=["omd-option-panel"]):
                                gr.HTML('<div class="omd-option-title">Audio and video</div>')
                                whisper_model = gr.Textbox(
                                    label="Whisper model",
                                    value=(
                                        os.environ.get("OMD_PUBLIC_DEMO_WHISPER_MODEL", "small")
                                        if public_demo
                                        else "mlx-community/whisper-large-v3-turbo"
                                    ),
                                )
                                preferred_languages = gr.Textbox(
                                    label="Spoken language hint",
                                    value=os.environ.get("OMD_PREFERRED_LANGUAGES", ""),
                                    info="For audio/video transcription. Leave blank for auto/platform defaults; use en for English, or zh,en for Chinese + English. Local polish should preserve this language.",
                                )
                                with gr.Row(elem_classes=["omd-checkbox-grid"]):
                                    keep_video = gr.Checkbox(
                                        label="Keep media",
                                        value=False if public_demo else True,
                                        interactive=not public_demo,
                                    )
                                    reel_polish = gr.Checkbox(
                                        label="Polish transcript",
                                        value=False,
                                        interactive=not public_demo,
                                    )
                                reel_polish_model = gr.Textbox(label="Transcript model", value=default_polish_model)

                        with gr.Accordion("Text in images (OCR)", open=False, elem_classes=["omd-settings-section"]):
                            with gr.Group(elem_classes=["omd-option-panel"]):
                                gr.HTML('<div class="omd-option-title">Read visible text from images</div>')
                                lang = gr.Textbox(
                                    label="Text-in-image language",
                                    value="eng",
                                    info="OCR means reading text from screenshots, scanned PDFs, and article images. Default is English; for Chinese + English, use chi_sim+eng.",
                                )
                                with gr.Row(elem_classes=["omd-checkbox-grid"]):
                                    ocr_thumbnail = gr.Checkbox(
                                        label="Read text from video thumbnail",
                                        value=False,
                                        info=(
                                            "Use when the video cover image contains useful title text, captions, or labels "
                                            "that are not present in the transcript."
                                        ),
                                        interactive=not public_demo,
                                    )
                                    ocr_article_images = gr.Checkbox(
                                        label="Read text from article images",
                                        value=False,
                                        info=(
                                            "Use when screenshots, charts, scanned pages, or embedded images contain important text "
                                            "that the normal article text extractor may miss."
                                        ),
                                        interactive=not public_demo,
                                    )

                        with gr.Accordion("Saved URL list file", open=False, elem_classes=["omd-settings-section"]):
                            gr.Markdown(
                                "Optional: upload a saved `.txt` queue with one URL, share text, or local path per line. Most users can paste the same list directly into the main source box.",
                                elem_classes=["omd-field-label"],
                            )
                            batch_file_input.render()

        gr.HTML("")

        batch_file_input.change(
            fn=_stage_batch_list,
            inputs=[batch_file_input],
            outputs=[batch_file_path],
        )

        def remove_deleted_source_file(
            queued_files: object,
            event: gr.DeletedFileData,
        ) -> tuple[list[dict[str, str]], str]:
            return _remove_source_file_from_queue(queued_files, event.file.path)

        remove_deleted_source_file.__annotations__["event"] = gr.DeletedFileData

        file_input.upload(
            fn=_merge_source_file_queue_for_ui,
            inputs=[source_file_queue, file_input],
            outputs=[source_file_queue, source_file_queue_summary, file_input],
            queue=False,
        )
        file_input.delete(
            fn=remove_deleted_source_file,
            inputs=[source_file_queue],
            outputs=[source_file_queue, source_file_queue_summary],
            queue=False,
        )
        file_input.clear(
            fn=_clear_source_file_queue,
            inputs=[source_file_queue],
            outputs=[source_file_queue, source_file_queue_summary],
            queue=False,
        )
        menu_outputs = [source_window, result_window, output_window, run_dock, cookies_window, advanced_window]
        all_tab.click(
            fn=lambda: _menu_view_updates("all"),
            inputs=None,
            outputs=menu_outputs,
        )
        source_tab.click(
            fn=lambda: _menu_view_updates("source"),
            inputs=None,
            outputs=menu_outputs,
        )
        output_tab.click(
            fn=lambda: _menu_view_updates("output"),
            inputs=None,
            outputs=menu_outputs,
        )
        advanced_tab.click(
            fn=lambda: _menu_view_updates("advanced"),
            inputs=None,
            outputs=menu_outputs,
        )
        inspect_btn.click(
            fn=_inspect_source,
            inputs=[text_input, source_file_queue, batch_file_path, cookies_file, cookies_browser, xhs_cookies_file],
            outputs=[inspect_preview],
        )
        choose_out_dir.click(
            fn=_choose_output_dir,
            inputs=[out_dir],
            outputs=[out_dir],
        )
        choose_vault_dir.click(
            fn=_choose_output_dir,
            inputs=[vault_dir],
            outputs=[vault_dir],
        )
        cookies_upload.change(
            fn=_stage_cookies,
            inputs=[cookies_upload],
            outputs=[cookies_file],
        )
        xhs_cookies_upload.change(
            fn=_stage_cookies,
            inputs=[xhs_cookies_upload],
            outputs=[xhs_cookies_file],
        )
        choose_cookies_file.click(
            fn=_choose_cookies_file,
            inputs=[cookies_file],
            outputs=[cookies_file],
        )
        choose_xhs_cookies_file.click(
            fn=_choose_cookies_file,
            inputs=[xhs_cookies_file],
            outputs=[xhs_cookies_file],
        )
        workflow_mode.change(
            fn=_workflow_mode_updates,
            inputs=[workflow_mode],
            outputs=[run_btn, polish_md, memory_cards],
        )
        check_local_model_btn.click(
            fn=_local_model_status_html,
            inputs=[polish_md_model, memory_model, ollama_host],
            outputs=[local_model_status],
        )
        open_output_btn.click(
            fn=_open_output_path,
            inputs=[out_path_box],
            outputs=[status],
        )
        run_event = run_btn.click(
            fn=run_with_status,
            inputs=[
                text_input, source_file_queue, batch_file_path, workflow_mode, out_dir, vault_dir, filename,
                output_format,
                polish_md, polish_md_keep_raw, polish_md_model,
                memory_cards, memory_model,
                reel_polish, reel_polish_model,
                ocr_thumbnail, ocr_article_images, keep_video,
                cookies_file, cookies_browser,
                lang, preferred_languages, whisper_model, ollama_host,
                verbose, json_events, reddit_comment_scope, xhs_cookies_file,
            ],
            outputs=[log_box, md_preview, out_path_box, status, download_file],
        )
        stop_btn.click(
            fn=None,
            inputs=None,
            outputs=None,
            cancels=[run_event],
            queue=False,
        )
        stop_btn.click(
            fn=_stop_conversion_status,
            inputs=None,
            outputs=[status],
            queue=False,
        )

    return app


def main() -> int:
    app = build_app()
    kwargs = build_launch_kwargs(
        inbrowser=True,
        server_name=os.environ.get("OMD_UI_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("OMD_UI_PORT", "7860")),
    )
    app.queue().launch(**kwargs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
