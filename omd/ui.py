"""omd UI - minimal Gradio wrapper around the `omd` CLI.

Run: `omd-ui` (after `pip install -e '.[ui]'`) or `python -m omd.ui`.

Opens a local browser tab. Paste a URL/share-blob or pick a file, choose an
output folder or vault folder, tick options, hit Run. Stderr streams to the log
pane; the finished `.md` previews below it. The output folder doubles as the
video download dir (--keep), so the .mp4/.mp3/.info.json land next to the text
output.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import html
import ipaddress
import os
import json
import re
import secrets
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
from datetime import date, datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlparse

from omd.batch import iter_batch_items
from omd.ai_service import (
    AIConsentGrant,
    AIRequestPreview,
    AIServiceError,
    AITextTask,
    create_text_task_consent,
    execute_text_task,
    prepare_text_task,
)
from omd.credentials import (
    CredentialError,
    delete_api_key,
    load_api_key,
    store_api_key,
)
from omd.context_receipt import ContextOutbox, ContextReceipt
from omd.eta_calibration import EtaCalibrationStore
from omd.eta_history import EtaHistoryStore, MIN_CALIBRATED_SAMPLES
from omd.inbox import InboxItem, InboxJob
from omd.inbox_workflow import (
    list_inbox_items,
    load_inbox_item,
    promote_inbox_item,
    save_inbox_item,
    set_review_status,
)
from omd._network_policy import validate_ollama_host
from omd.provider_models import ProviderCatalogError, discover_provider_models
from omd.preferences import (
    load_preference_profile,
    record_feedback,
    reset_stored_preferences,
    save_preference_profile,
)
from omd.retrieval import (
    VaultCatalogError,
    find_duplicate_notes,
    read_vault_markdown,
    related_notes,
    search_notes,
    validate_vault_markdown_path,
)
from omd.run_telemetry import (
    RunTelemetrySession,
    event_log_line,
    parse_json_event,
    telemetry_context_from_argv,
)
from omd.structured_output import AIOutputSchema
from omd.voice_inbox import VoiceInboxRecord, VoiceInboxStore
from omd._io import write_atomic
from omd._models import (
    assess_local_text_model,
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
AI_PROVIDER_CHOICES = (
    "No AI",
    "Local Ollama",
    "OpenAI API",
    "Anthropic API",
    "DeepSeek API",
)
_AI_PROVIDER_KEYS = {
    "No AI": "none",
    "Local Ollama": "ollama",
    "OpenAI API": "openai",
    "Anthropic API": "anthropic",
    "DeepSeek API": "deepseek",
}
DEFAULT_COOKIES = str(COOKIES_STAGING / "douyin_cookies.txt")
LEGACY_DEFAULT_COOKIES = str(Path.home() / "Desktop" / "douyin_cookies.txt")
PROJECT_URL = "https://github.com/omd-local/markdown-everything"
STARTUP_RECOMMENDED_TEXT_MODEL = recommended_local_text_model()
PUBLIC_DEMO_OUTPUT_DIR = Path(tempfile.gettempdir()) / "omd-public-demo"
PUBLIC_DEMO_MAX_UPLOAD_MB = 100
PUBLIC_DEMO_MAX_MEDIA_SECONDS = 600
UI_MEMORY_TIMEOUT_SECONDS = 45
UI_LINKED_SOURCE_MAX_BYTES = 64 * 1024
UI_LINKED_SOURCE_CHOICE_LIMIT = 200
INBOX_AI_CONTEXT_TOKENS = 32 * 1024
INTERNAL_JSON_EVENTS_DEFAULT = True
_NOTE_SUGGESTION_SCHEMA = AIOutputSchema(
    name="omd_note_suggestion",
    schema={
        "type": "object",
        "properties": {
            "suggestion": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["suggestion", "evidence", "tags"],
        "additionalProperties": False,
    },
)
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

DOUYIN_COOKIE_REMEDIATION = (
    "Douyin source detected, but the Default / Douyin cookies.txt path is empty. "
    "Upload a Douyin Netscape cookies.txt file before converting private or gated Douyin links."
)
XHS_COOKIE_REMEDIATION = (
    "XHS / Rednote source detected, but the XHS / Rednote cookies.txt path is empty. "
    "Upload an XHS Netscape cookies.txt file before converting private or gated XHS links."
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _public_demo_enabled() -> bool:
    return _env_flag("OMD_PUBLIC_DEMO")


def _require_local_ui(feature: str) -> None:
    if _public_demo_enabled():
        raise ValueError(f"{feature} is available only in the local OMD app")


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
    _require_local_ui("Cookie staging")
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
    parsed = urlparse(host)
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return (
            None,
            "Ollama host must be a base URL such as http://localhost:11434, "
            "without a path, query, or fragment.",
        )
    host = f"{parsed.scheme}://{parsed.netloc}"
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

    assessments = [
        assess_local_text_model(model, installed_models=names)
        for model in requested
    ]
    machine_fit_warning = next(
        (
            assessment
            for assessment in assessments
            if assessment.status in {"too_large", "unknown_size"}
        ),
        None,
    )
    if machine_fit_warning is not None:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Local model fit warning:</strong> "
            f"{html.escape(machine_fit_warning.reason)}. "
            "OMD will not replace or download a model automatically. For a more "
            "responsive local workflow, select "
            f"<code>{html.escape(machine_fit_warning.recommended_model)}</code> if installed. "
            "Raw Markdown remains available if AI polish is slow or fails."
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
    _require_local_ui("Saved URL list files")
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
        if _public_demo_enabled():
            raise ValueError("Saved URL list files are available only in the local OMD app")
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

    if _public_demo_enabled():
        _validate_public_demo_policy(
            targets=targets,
            uploaded_paths=set(source_files),
            cookies_file=cookies_file,
            xhs_cookies_file=xhs_cookies_file,
            instagram_cookies_file="",
            cookies_browser=cookies_browser,
            polish_md=False,
            reel_polish=False,
            ollama_host="",
            keep_video=False,
        )

    shown = targets[:20]
    header = ["## Source Inspect", "", f"Items detected: **{len(targets)}**"]
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
                source_warnings.insert(0, DOUYIN_COOKIE_REMEDIATION)
            elif detected_type == "xhs_url" and not (xhs_cookies_file or "").strip():
                source_warnings.insert(0, XHS_COOKIE_REMEDIATION)
            if source_warnings:
                info["warnings"] = source_warnings
            sections.append(_format_inspect_result(target, info))
    return "\n\n---\n\n".join(sections)


def _choose_output_dir(current: str) -> str:
    """Open the platform folder picker and return the selected path.

    On macOS this uses the native Apple folder chooser. If the user cancels or
    the platform does not support a native chooser, keep the current value.
    """
    _require_local_ui("Folder picking")
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
    _require_local_ui("Cookie file picking")
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


def _platform_cookie_warnings(
    targets: Sequence[str],
    *,
    cookies_file: str,
    xhs_cookies_file: str,
) -> list[str]:
    from omd._preflight import inspect_target

    warnings: list[str] = []
    seen: set[str] = set()
    for target in targets:
        try:
            info = inspect_target(target)
        except (Exception, SystemExit):  # noqa: BLE001 - target classification is best-effort here.
            continue
        detected_type = str(info.get("detected_type") or "")
        warning = None
        if detected_type == "douyin_url" and not (cookies_file or "").strip():
            warning = DOUYIN_COOKIE_REMEDIATION
        elif detected_type == "xhs_url" and not (xhs_cookies_file or "").strip():
            warning = XHS_COOKIE_REMEDIATION
        if warning and warning not in seen:
            warnings.append(warning)
            seen.add(warning)
    return warnings


def _require_platform_cookie_files(
    targets: Sequence[str],
    *,
    cookies_file: str,
    xhs_cookies_file: str,
) -> None:
    warnings = _platform_cookie_warnings(
        targets,
        cookies_file=cookies_file,
        xhs_cookies_file=xhs_cookies_file,
    )
    if warnings:
        raise ValueError("\n".join(warnings))


def _open_output_path(path_value: str) -> str:
    _require_local_ui("Opening output folders")
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
    if _public_demo_enabled():
        try:
            path.resolve().relative_to(SOURCE_STAGING.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Hosted sample demo accepts only files staged by this upload session, not server filesystem paths."
            ) from exc
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


def _validate_public_demo_cookie_file(path_value: str) -> None:
    cookie_path = Path(path_value).expanduser()
    try:
        if COOKIES_STAGING.is_symlink() or cookie_path.is_symlink():
            raise ValueError("symlinked cookie paths are not accepted")
        staging_root = COOKIES_STAGING.resolve(strict=True)
        resolved_cookie = cookie_path.resolve(strict=True)
        if not staging_root.is_dir() or not resolved_cookie.is_file():
            raise ValueError("cookie path must be a regular staged file")
        resolved_cookie.relative_to(staging_root)
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Hosted sample demo only accepts uploaded/staged cookie files, not raw local cookie paths."
        ) from exc


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
        _validate_public_demo_cookie_file(cookie_value)
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
    if not is_public_demo:
        _require_platform_cookie_files(
            scope_items,
            cookies_file=cookies_file,
            xhs_cookies_file=xhs_cookies_file,
        )

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
            captures = (
                list((target / "Sources").glob("**/*.md"))
                if (target / "Sources").is_dir()
                else []
            )
            captures.sort(
                key=lambda path: (
                    -path.stat().st_mtime_ns,
                    path.relative_to(target).as_posix().casefold(),
                )
            )
            shown = captures[:3]
            lines = [
                "# Saved to vault",
                "",
                f"Vault: `{target}`",
                "",
                "## Most recent capture files",
                "",
            ]
            lines.extend(f"- `{path.relative_to(target).as_posix()}`" for path in shown)
            lines.extend(
                [
                    "",
                    "External sources are stored under `Sources/`; `Inbox/` is unchanged.",
                    "Full capture history: `Index/OMD Captures.md`.",
                ]
            )
            if len(captures) > len(shown):
                lines.append(
                    f"Showing the 3 most recently modified of {len(captures)} capture notes."
                )
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
    progress_attrs = (
        'role="progressbar" aria-label="Task progress" '
        'aria-valuemin="0" aria-valuemax="100"'
    )
    if percent is not None:
        progress_attrs += f' aria-valuenow="{pct}"'
    return (
        f'<div id="omd-status" class="{classes}" role="status" '
        'aria-live="polite" aria-atomic="true">'
        '<div class="omd-status-head">'
        f'<span class="omd-status-label">{label_html}</span>'
        f'<span class="omd-status-detail">{detail_html}</span>'
        f'<span class="omd-status-eta">{eta_html}</span>'
        '</div>'
        f'{summary_html}'
        f'<div class="omd-progress-track" {progress_attrs}>'
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


@dataclass
class _ContextRun:
    """UI-owned handle for durable receipts created before conversion starts."""

    outbox: ContextOutbox
    receipts: list[ContextReceipt]
    local_sources: list[Path | None]

    def secure_sources(self) -> None:
        for index, source in enumerate(self.local_sources):
            if source is None:
                continue
            self.receipts[index] = self.outbox.secure_local_source(
                self.receipts[index].job_id,
                source,
            )

    def start_processing(self) -> None:
        for index, receipt in enumerate(self.receipts):
            self.receipts[index] = self.outbox.start_stage(
                receipt.job_id,
                "conversion",
            )

    def apply_batch_event(self, event: dict[str, object]) -> None:
        """Persist one batch item's outcome without changing its neighbours."""
        event_type = event.get("event")
        if event_type not in {"batch_item_succeeded", "batch_item_failed"}:
            return
        item_index = event.get("index")
        if type(item_index) is not int or not 1 <= item_index <= len(self.receipts):
            return
        receipt_index = item_index - 1
        receipt = self.receipts[receipt_index]
        if receipt.state not in {"processing", "partial_output"}:
            return
        if event_type == "batch_item_succeeded":
            self.receipts[receipt_index] = self.outbox.complete(receipt.job_id)
            return
        if receipt.state == "processing":
            self.receipts[receipt_index] = self.outbox.fail_stage(
                receipt.job_id,
                error_code="conversion_failed",
                retryable=True,
            )

    def complete(self) -> None:
        for index, receipt in enumerate(self.receipts):
            if receipt.state not in {"processing", "partial_output"}:
                continue
            self.receipts[index] = self.outbox.complete(receipt.job_id)

    def mark_partial_output(self) -> None:
        for index, receipt in enumerate(self.receipts):
            if receipt.state != "processing":
                continue
            self.receipts[index] = self.outbox.mark_partial_output(receipt.job_id)

    def mark_failed(self) -> None:
        for index, receipt in enumerate(self.receipts):
            if receipt.state != "processing":
                continue
            self.receipts[index] = self.outbox.fail_stage(
                receipt.job_id,
                error_code="conversion_failed",
                retryable=True,
            )

    def cancel(self) -> None:
        for index, receipt in enumerate(self.receipts):
            if receipt.state not in {"queued", "source_secured", "processing"}:
                continue
            self.receipts[index] = self.outbox.cancel(receipt.job_id)

    def status_summary(self) -> tuple[str, str]:
        return "receipt", _receipt_status_text(self.receipts)

    def log_lines(self) -> list[str]:
        lines: list[str] = []
        for receipt in self.receipts:
            line = (
                f"receipt: {receipt.job_id} · {receipt.source_type} · "
                f"{receipt.state.replace('_', ' ')}"
            )
            if receipt.recovery_action:
                line += f" · next: {_receipt_action_text(receipt.recovery_action)}"
            lines.append(line)
        return lines


def _context_outbox_root() -> Path:
    root = OMD_DATA_DIR / "context-outbox"
    if root.is_symlink():
        raise ValueError("context outbox directory must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _receipt_targets(
    text_input: str,
    file_input: object,
    batch_file_input: str | None,
) -> list[str]:
    batch_file = (batch_file_input or "").strip()
    if batch_file:
        try:
            return list(
                iter_batch_items(
                    Path(batch_file).expanduser().read_text(encoding="utf-8").splitlines()
                )
            )
        except (OSError, UnicodeError):
            return [batch_file]
    text = (text_input or "").strip()
    if text:
        return _batch_items_from_text(text) or [text]
    return _uploaded_file_paths(file_input)


def _receipt_source_type(target: str) -> tuple[str, Path | None]:
    from omd._preflight import inspect_target
    from omd.capture import source_type_for

    candidate = Path(target).expanduser()
    local_source = candidate if candidate.is_file() else None
    try:
        source_type = source_type_for(target, inspect_target(target))
    except (Exception, SystemExit):
        source_type = "local_file" if local_source is not None else "source"
    if local_source is not None and source_type not in {
        "audio",
        "image",
        "local_file",
        "office_doc",
        "pdf",
    }:
        source_type = "local_file"
    return source_type, local_source


def _queue_context_run(
    text_input: str,
    file_input: object,
    batch_file_input: str | None,
    workflow_mode: str,
    out_dir: str,
    vault_dir: str,
) -> _ContextRun | None:
    """Persist privacy-minimised receipts without changing the public UI API."""
    if _public_demo_enabled():
        return None
    targets = _receipt_targets(text_input, file_input, batch_file_input)
    if not targets:
        return None
    outbox = ContextOutbox(_context_outbox_root())
    capture_mode = _is_capture_mode(workflow_mode)
    destination_value = vault_dir if capture_mode else out_dir
    destination_label = "Obsidian vault" if capture_mode else "Markdown folder"
    submission_id = secrets.token_hex(8)
    destination_identity = hashlib.sha256(
        (destination_value or destination_label).encode("utf-8")
    ).hexdigest()
    receipts: list[ContextReceipt] = []
    local_sources: list[Path | None] = []
    for ordinal, target in enumerate(targets, 1):
        source_type, local_source = _receipt_source_type(target)
        source_identity = hashlib.sha256(target.encode("utf-8")).hexdigest()
        job = InboxJob(
            job_type="capture" if capture_mode else "convert",
            source="desktop_ui",
            payload={
                "submission_id": submission_id,
                "source_identity": source_identity,
                "destination_identity": destination_identity,
                "ordinal": ordinal,
                "item_count": len(targets),
            },
        )
        receipts.append(
            outbox.queue(
                job,
                source_type=source_type,
                destination=destination_label,
                privacy_mode="local_only",
            )
        )
        local_sources.append(local_source)
    return _ContextRun(outbox=outbox, receipts=receipts, local_sources=local_sources)


def _run_status_summary(
    counts: _RunCounts,
    context_run: _ContextRun | None,
) -> tuple[tuple[str, str], ...]:
    receipt = () if context_run is None else (context_run.status_summary(),)
    return (*counts.summary(), *receipt)


def _transition_context_run(
    context_run: _ContextRun | None,
    action: str,
) -> str | None:
    if context_run is None:
        return None
    try:
        getattr(context_run, action)()
    except (OSError, ValueError) as exc:
        return f"receipt update failed during {action.replace('_', ' ')}: {exc}"
    return None


def _transition_context_batch_event(
    context_run: _ContextRun | None,
    event: dict[str, object],
) -> str | None:
    if context_run is None:
        return None
    try:
        context_run.apply_batch_event(event)
    except (OSError, ValueError) as exc:
        return f"receipt update failed for batch item: {exc}"
    return None


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
        "all": (True, False, True, True, True, False, False),
        "source": (True, False, False, False, False, False, False),
        "inbox": (False, True, False, False, False, False, False),
        "output": (False, False, True, True, True, False, False),
        "advanced": (False, False, False, False, False, True, True),
    }.get(selected, (True, False, True, True, True, False, False))
    layout_classes = ["omd-grid"]
    if selected in {"source", "inbox"}:
        layout_classes.append("omd-grid-primary-only")
    elif selected == "advanced":
        layout_classes.append("omd-grid-secondary-only")
    return (
        tuple(gr.update(visible=True, open=value) for value in open_states)
        + tuple(
            gr.update(variant="primary" if name == selected else "secondary")
            for name in ("all", "source", "inbox", "output", "advanced")
        )
        + (gr.update(elem_classes=layout_classes),)
    )


def _ai_provider_key(choice: str) -> str:
    if not isinstance(choice, str) or choice not in _AI_PROVIDER_KEYS:
        raise ValueError("AI provider is not supported")
    return _AI_PROVIDER_KEYS[choice]


def _ai_note_task(
    provider_choice: str,
    model: str,
    ollama_host: str,
    *,
    capture_surface: str = "my_note",
) -> AITextTask:
    provider = _ai_provider_key(provider_choice)
    if provider == "none":
        raise ValueError("Choose an AI provider before requesting an AI suggestion")
    selected_model = (model or "").strip()
    if not selected_model:
        raise ValueError("Choose an exact provider model first")
    endpoint = (ollama_host or "http://localhost:11434") if provider == "ollama" else None
    if endpoint is not None:
        validate_ollama_host(endpoint)
    task_focus = (
        "For an exact highlight, draft one concise takeaway that stays within the excerpt."
        if capture_surface == "highlight"
        else "For a personal note, draft a concise structure or takeaway without adding claims."
    )
    return AITextTask(
        provider=provider,
        model=selected_model,
        capability="note_organisation",
        operation=(
            f"{task_focus} Preserve the source language and claims. "
            "Return at least one Evidence item copied exactly from the supplied text. "
            "If the text is only a URL or lacks a meaningful passage, do not invent a result."
        ),
        system_prompt=(
            "Use only the supplied selected text. You cannot open links or read unselected vault files. "
            "Do not translate it. Separate one useful suggestion, exact Evidence excerpts, "
            "and a few specific tags. Every Evidence item must be a verbatim substring of the input. "
            "Do not invent claims, links, or placeholder tags."
        ),
        max_output_tokens=1024,
        endpoint=endpoint,
        timeout_seconds=90.0 if provider == "ollama" else 60.0,
        output_schema=_NOTE_SUGGESTION_SCHEMA,
        context_window_tokens=INBOX_AI_CONTEXT_TOKENS if provider == "ollama" else None,
    )


def _ai_request_preview_html(preview: AIRequestPreview, *, source_label: str = "Inbox original") -> str:
    policy = (
        f' <a href="{html.escape(preview.policy_url)}" target="_blank" '
        'rel="noopener noreferrer">Current provider policy</a>.'
        if preview.policy_url
        else ""
    )
    if preview.provider == "ollama" and preview.destination_domain in {"localhost", "127.0.0.1", "::1"}:
        return (
            '<div class="omd-model-status omd-model-status-info">'
            f"<strong>Local AI:</strong> OMD will send only <code>{html.escape(source_label)}</code> "
            f"({preview.character_count} characters) to <code>{html.escape(preview.model)}</code> "
            "through Ollama on this Mac. It will not open links, read unselected files, "
            "or upload an attachment.</div>"
        )
    attachment = "yes" if preview.sends_attachment else "no"
    return (
        '<div class="omd-model-status omd-model-status-info">'
        f"<strong>Before sending to cloud:</strong> OMD will send only <code>{html.escape(source_label)}</code> "
        f"({preview.character_count} characters, about {preview.estimated_input_tokens} tokens) "
        f"to <code>{html.escape(preview.provider)}</code> model "
        f"<code>{html.escape(preview.model)}</code> at "
        f"<code>{html.escape(preview.destination_domain)}</code>. Attachment sent: {attachment}. "
        f"{html.escape(preview.data_handling_summary)}{policy} "
        "This uses a provider API key, not a consumer ChatGPT login or consumer Claude login."
        "</div>"
    )


def _inbox_ai_source_text(vault: str, item: object, selected_markdown_path: str) -> tuple[str, str]:
    relative = (selected_markdown_path or "").strip()
    if relative:
        source_text = read_vault_markdown(
            vault,
            relative,
            max_bytes=UI_LINKED_SOURCE_MAX_BYTES,
        )
        return source_text, relative
    return str(getattr(item, "raw_content")), "Inbox original"


def _preview_inbox_ai_request(
    vault: str,
    item_id: str,
    provider_choice: str,
    model: str,
    ollama_host: str,
    selected_markdown_path: str = "",
    *,
    as_of: date | None = None,
) -> str:
    if not item_id:
        raise ValueError("Choose an Inbox item before previewing an AI request")
    item = load_inbox_item(vault, item_id)
    task = _ai_note_task(
        provider_choice,
        model,
        ollama_host,
        capture_surface=item.capture_surface,
    )
    source_text, source_label = _inbox_ai_source_text(vault, item, selected_markdown_path)
    preview = prepare_text_task(task, source_text=source_text, as_of=as_of)
    return _ai_request_preview_html(preview, source_label=source_label)


def _preview_inbox_ai_request_for_ui(
    vault: str,
    item_id: str,
    provider_choice: str,
    model: str,
    ollama_host: str,
    selected_markdown_path: str = "",
) -> tuple[str, AIConsentGrant | None, bool]:
    _require_local_ui("Inbox AI review")
    if not item_id:
        raise ValueError("Choose an Inbox item before previewing an AI request")
    item = load_inbox_item(vault, item_id)
    task = _ai_note_task(
        provider_choice,
        model,
        ollama_host,
        capture_surface=item.capture_surface,
    )
    source_text, source_label = _inbox_ai_source_text(vault, item, selected_markdown_path)
    preview = prepare_text_task(task, source_text=source_text)
    grant = (
        create_text_task_consent(task, source_text=source_text)
        if task.provider in {"openai", "anthropic", "deepseek"}
        else None
    )
    return _ai_request_preview_html(preview, source_label=source_label), grant, False


def _ai_provider_ui_updates(provider_choice: str, local_model: str):
    import gradio as gr

    provider = _ai_provider_key(provider_choice)
    hosted = provider in {"openai", "anthropic", "deepseek"}
    if provider == "ollama":
        model_update = gr.update(
            choices=[local_model] if local_model else [],
            value=local_model or None,
        )
        message = (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>Local-only provider:</strong> source text stays on this Mac and is sent "
            "only to the selected loopback Ollama endpoint. No cloud consent or API key is used."
            "</div>"
        )
    elif hosted:
        model_update = gr.update(choices=[], value=None)
        message = (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Optional BYOK cloud:</strong> enter a session API key or save it in macOS "
            "Keychain, then check models. Each content request still requires a fresh preview "
            "and consent. No provider fallback is used."
            "</div>"
        )
    else:
        model_update = gr.update(choices=[], value=None)
        message = (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>AI disabled:</strong> Inbox capture, review, search, and raw Markdown remain available."
            "</div>"
        )
    return (
        model_update,
        gr.update(visible=hosted),
        gr.update(value=""),
        gr.update(visible=hosted, value=False),
        message,
        gr.update(visible=hosted),
        gr.update(visible=hosted),
        gr.update(
            value="Generate draft from selected text",
            visible=provider != "none",
            interactive=provider != "none",
        ),
        gr.update(visible=provider != "none"),
    )


def _provider_api_key(provider: str, session_api_key: str) -> str | None:
    if provider == "ollama":
        return None
    entered = (session_api_key or "").strip()
    return entered or load_api_key(provider)


def _check_ai_provider_connection(
    provider_choice: str,
    selected_model: str,
    session_api_key: str,
    ollama_host: str,
):
    import gradio as gr

    _require_local_ui("AI provider checks")
    provider = _ai_provider_key(provider_choice)
    if provider == "none":
        return gr.update(choices=[], value=None), (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>AI disabled:</strong> there is no provider connection to test."
            "</div>"
        )
    selected = (selected_model or "").strip()
    try:
        api_key = _provider_api_key(provider, session_api_key)
        catalog = discover_provider_models(
            provider,
            api_key=api_key,
            endpoint=(ollama_host or "http://localhost:11434") if provider == "ollama" else None,
            timeout_seconds=8.0,
        )
    except (CredentialError, ProviderCatalogError, ValueError) as exc:
        return gr.update(value=selected or None), (
            '<div class="omd-model-status omd-model-status-warn">'
            f"<strong>Provider check failed:</strong> {html.escape(str(exc))}. "
            "No content was sent and no fallback provider was tried."
            "</div>"
        )
    available = selected in catalog.models if selected else False
    if selected and not available:
        message = (
            f"Selected model <code>{html.escape(selected)}</code> is not available. "
            "Choose an exact model from the loaded catalogue; OMD will not replace it automatically."
        )
        state_class = "omd-model-status-warn"
    else:
        message = (
            f"Connected to <code>{html.escape(catalog.destination_domain)}</code>; "
            f"{len(catalog.models)} model(s) found in {catalog.elapsed_seconds:.2f}s."
        )
        state_class = "omd-model-status-ok"
    return gr.update(choices=list(catalog.models), value=selected or None), (
        f'<div class="omd-model-status {state_class}">'
        f"<strong>Provider check:</strong> {message}</div>"
    )


def _save_cloud_api_key(provider_choice: str, session_api_key: str):
    import gradio as gr

    _require_local_ui("Keychain credentials")
    provider = _ai_provider_key(provider_choice)
    if provider not in {"openai", "anthropic", "deepseek"}:
        return gr.update(), (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Keychain:</strong> choose a hosted provider first.</div>"
        )
    try:
        store_api_key(provider, (session_api_key or "").strip())
    except (CredentialError, ValueError) as exc:
        return gr.update(), (
            '<div class="omd-model-status omd-model-status-warn">'
            f"<strong>Keychain save failed:</strong> {html.escape(str(exc))} "
            "The key remains session-only while it stays in this field.</div>"
        )
    return gr.update(value=""), (
        '<div class="omd-model-status omd-model-status-ok">'
        f"<strong>Keychain:</strong> saved the {html.escape(provider)} API key. "
        "The secret is not stored in OMD files.</div>"
    )


def _delete_cloud_api_key(provider_choice: str) -> str:
    _require_local_ui("Keychain credentials")
    provider = _ai_provider_key(provider_choice)
    if provider not in {"openai", "anthropic", "deepseek"}:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>Keychain:</strong> choose a hosted provider first.</div>"
        )
    try:
        delete_api_key(provider)
    except CredentialError as exc:
        return (
            '<div class="omd-model-status omd-model-status-warn">'
            f"<strong>Keychain delete:</strong> {html.escape(str(exc))}</div>"
        )
    return (
        '<div class="omd-model-status omd-model-status-ok">'
        f"<strong>Keychain:</strong> deleted the saved {html.escape(provider)} API key. "
        "An environment variable, if set, is separate and still takes precedence.</div>"
    )


def _run_inbox_ai_suggestion(
    vault: str,
    item_id: str,
    provider_choice: str,
    model: str,
    session_api_key: str,
    ollama_host: str,
    consent_granted: bool,
    current_suggestion: str,
    consent_grant: AIConsentGrant | None = None,
    selected_markdown_path: str = "",
):
    _require_local_ui("Inbox AI review")
    if not item_id:
        return current_suggestion, (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>AI suggestion:</strong> choose an Inbox item first.</div>"
        )
    try:
        item = load_inbox_item(vault, item_id)
        task = _ai_note_task(
            provider_choice,
            model,
            ollama_host,
            capture_surface=item.capture_surface,
        )
        source_text, source_label = _inbox_ai_source_text(
            vault,
            item,
            selected_markdown_path,
        )
        text_without_urls = URL_RE.sub("", source_text)
        if URL_RE.search(source_text) and not re.search(r"\w", text_without_urls, re.UNICODE):
            return current_suggestion, (
                '<div class="omd-model-status omd-model-status-warn">'
                "<strong>AI draft needs text, not only a link:</strong> OMD does not open the URL "
                "or infer page contents. Select a converted Markdown source above, or save the exact "
                "passage you want to work with as a Highlight, then generate again. Nothing was sent "
                "and no vault file was changed.</div>"
            )
        provider = _ai_provider_key(provider_choice)
        result = execute_text_task(
            task,
            source_text=source_text,
            consent_granted=consent_granted,
            consent_grant=consent_grant,
            credential_loader=lambda name: _provider_api_key(name, session_api_key) or "",
        )
    except (
        AIServiceError,
        CredentialError,
        ProviderCatalogError,
        ValueError,
        VaultCatalogError,
    ) as exc:
        if isinstance(exc, AIServiceError) and exc.code == "context_limit_exceeded":
            message = (
                f"The selected text still does not fit the {INBOX_AI_CONTEXT_TOKENS}-token "
                "Inbox AI window. Choose a shorter source or excerpt; local models still have "
                "context and memory limits"
            )
        else:
            message = str(exc)
        return current_suggestion, (
            '<div class="omd-model-status omd-model-status-warn">'
            f"<strong>AI suggestion skipped:</strong> {html.escape(message)}. "
            "The current draft, editable tags, raw Inbox item, and selected source are unchanged."
            "</div>"
        )

    structured = result.structured or {}
    suggestion = str(structured.get("suggestion") or result.text).strip()
    evidence = structured.get("evidence") if isinstance(structured.get("evidence"), list) else []
    tags = structured.get("tags") if isinstance(structured.get("tags"), list) else []
    grounded_evidence = [
        value.strip()
        for value in evidence
        if isinstance(value, str) and value.strip() and value.strip() in source_text
    ]
    evidence_is_exact = len(grounded_evidence) == len(evidence)
    if not suggestion or not grounded_evidence or not evidence_is_exact:
        return current_suggestion, (
            '<div class="omd-model-status omd-model-status-warn">'
            "<strong>AI could not create a grounded draft:</strong> no usable exact evidence "
            f"was returned from <code>{html.escape(source_label)}</code>; select or save the exact "
            "passage you want to use, then try again. "
            "OMD did not include the AI output and did not change the Inbox item.</div>"
        )
    sections = [suggestion]
    sections.extend(["", "Evidence from selected text", *[f"- {value}" for value in grounded_evidence]])
    useful_tags = [value.strip() for value in tags if isinstance(value, str) and value.strip()]
    if useful_tags:
        sections.extend(["", "Suggested tags", ", ".join(useful_tags)])
    output = "\n".join(sections).strip()
    elapsed = result.timing.get("elapsed_seconds", 0.0)
    total_tokens = result.usage.get("total_tokens")
    usage = f"; {total_tokens} total tokens" if total_tokens is not None else ""
    return output, (
        '<div class="omd-model-status omd-model-status-ok">'
        "<strong>AI draft ready for review:</strong> "
        f"{html.escape(provider)} / {html.escape(result.actual_model)} via "
        f"{html.escape(result.destination_domain)} in {elapsed:.1f}s{usage}. "
        f"Source: <code>{html.escape(source_label)}</code>. Edit the draft if needed, then choose "
        "whether to add it when creating the note."
        "</div>"
    )


def _parse_note_tags(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    tags: list[str] = []
    for candidate in re.split(r"[,;\n]+", value):
        tag = candidate.strip().lstrip("#").strip()
        tag = re.sub(r"\s+", "-", tag)
        if not tag or tag in tags:
            continue
        tags.append(tag)
    return tags


def _split_suggested_tags(draft: str) -> tuple[str, list[str]]:
    if not isinstance(draft, str):
        return "", []
    marker = re.search(r"\n{2,}Suggested tags\s*\n", draft, flags=re.IGNORECASE)
    if marker is None:
        return draft, []
    return draft[: marker.start()].rstrip(), _parse_note_tags(draft[marker.end() :])


def _merge_note_tags(current: str, suggested: Sequence[str]) -> str:
    merged = _parse_note_tags(current)
    for tag in suggested:
        normalized = _parse_note_tags(str(tag))
        for value in normalized:
            if value not in merged:
                merged.append(value)
    return ", ".join(merged)


def _run_inbox_ai_suggestion_for_ui(
    vault: str,
    item_id: str,
    provider_choice: str,
    model: str,
    session_api_key: str,
    ollama_host: str,
    consent_granted: bool,
    current_suggestion: str,
    current_tags: str,
    consent_grant: AIConsentGrant | None = None,
    selected_markdown_path: str = "",
):
    draft, status = _run_inbox_ai_suggestion(
        vault,
        item_id,
        provider_choice,
        model,
        session_api_key,
        ollama_host,
        consent_granted,
        current_suggestion,
        consent_grant,
        selected_markdown_path,
    )
    clean_draft, suggested_tags = _split_suggested_tags(draft)
    merged_tags = _merge_note_tags(current_tags, suggested_tags)
    if suggested_tags:
        visible_tags = ", ".join(
            f"<code>{html.escape(tag)}</code>" for tag in suggested_tags
        )
        status += (
            '<div class="omd-model-status omd-model-status-ok" '
            'role="status" aria-live="polite">'
            "<strong>Suggested tags added to the editable field below:</strong> "
            f"{visible_tags}. Review or remove them before creating the note.</div>"
        )
    return clean_draft, status, merged_tags


def _preference_state_path() -> Path:
    configured = os.environ.get("OMD_PREFERENCES_PATH")
    return (
        Path(configured).expanduser()
        if configured
        else OMD_DATA_DIR / "preferences.json"
    )


def _eta_history_state_path() -> Path:
    configured = os.environ.get("OMD_ETA_HISTORY_PATH")
    return (
        Path(configured).expanduser()
        if configured
        else OMD_DATA_DIR / "eta-history.json"
    )


def _eta_history_store() -> EtaHistoryStore:
    return EtaHistoryStore(_eta_history_state_path())


def _eta_calibration_state_path() -> Path:
    configured = os.environ.get("OMD_ETA_CALIBRATION_PATH")
    return (
        Path(configured).expanduser()
        if configured
        else _eta_history_state_path().with_name("eta-calibration-samples.json")
    )


def _eta_calibration_store() -> EtaCalibrationStore:
    return EtaCalibrationStore(_eta_calibration_state_path())


def _eta_history_summary_markdown(*, public_demo: bool | None = None) -> str:
    if _public_demo_enabled() or bool(public_demo):
        return "_Local ETA history is unavailable in the hosted demo._"
    summary = _eta_history_store().summary()
    enabled = "enabled" if summary["enabled"] else "disabled"
    lines = [
        "### Local ETA history",
        f"- Status: **{enabled}**",
        f"- Stage observations: **{summary['observation_count']}**",
        f"- Successful calibration samples: **{summary['successful_observation_count']}**",
        (
            "- Prediction gate: at least "
            f"**{MIN_CALIBRATED_SAMPLES} comparable successful samples** per calibrated bucket"
        ),
    ]
    try:
        shadow_count = _eta_calibration_store().summary()["sample_count"]
        lines.append(f"- Baseline-vs-shadow comparison samples: **{shadow_count}**")
    except (OSError, TypeError, ValueError):
        lines.append("- Baseline-vs-shadow comparison samples: **unavailable**")
    stages = summary["stages"]
    if stages:
        stage_text = ", ".join(
            f"{_safe_markdown_text(stage)} ({count})" for stage, count in stages.items()
        )
        lines.append(f"- Coverage: {stage_text}")
    else:
        lines.append("- Coverage: no local samples yet")
    if warning := summary["warning"]:
        lines.append(f"- Warning: {_safe_markdown_text(warning)}")
    return "\n".join(lines)


def _set_eta_history_enabled(enabled: bool) -> tuple[str, str]:
    if _public_demo_enabled():
        return (
            _eta_history_summary_markdown(public_demo=True),
            "ETA history is unavailable in the hosted demo",
        )
    _eta_history_store().set_enabled(bool(enabled))
    state = "enabled" if enabled else "disabled"
    return _eta_history_summary_markdown(public_demo=False), f"Local ETA history {state}"


def _reset_eta_history() -> tuple[str, str]:
    if _public_demo_enabled():
        return (
            _eta_history_summary_markdown(public_demo=True),
            "ETA history is unavailable in the hosted demo",
        )
    _eta_history_store().reset()
    _eta_calibration_store().reset()
    return _eta_history_summary_markdown(public_demo=False), "Local ETA history reset"


def _export_eta_history_summary():
    import gradio as gr

    if _public_demo_enabled():
        return gr.update(value=None, interactive=False), "ETA history export is unavailable in the hosted demo"
    summary = _eta_history_store().summary()
    try:
        summary["shadow_calibration_sample_count"] = _eta_calibration_store().summary()[
            "sample_count"
        ]
    except (OSError, TypeError, ValueError):
        summary["shadow_calibration_sample_count"] = None
    export_dir = UI_STAGING_ROOT / "exports"
    export_path = export_dir / "eta-history-summary.json"
    write_atomic(
        export_path,
        json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
    )
    return gr.update(value=str(export_path), interactive=True), "Privacy-safe ETA summary prepared"


def _safe_markdown_text(value: object) -> str:
    escaped = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", escaped)


def _safe_code_text(value: object) -> str:
    return html.escape(str(value), quote=False).replace("`", "&#96;")


def _receipt_action_text(action: str | None) -> str:
    if not action:
        return ""
    return action.replace("_", " ")


def _receipt_status_text(receipts: Sequence[ContextReceipt]) -> str:
    if not receipts:
        return ""
    if len(receipts) == 1:
        receipt = receipts[0]
        text = f"{receipt.job_id} · {receipt.state.replace('_', ' ')}"
        if receipt.recovery_action:
            text += f" · next: {_receipt_action_text(receipt.recovery_action)}"
        return text
    states = {receipt.state for receipt in receipts}
    text = (
        f"{len(receipts)} receipts · "
        f"{next(iter(states)).replace('_', ' ') if len(states) == 1 else 'mixed states'}"
    )
    actions: list[str] = []
    for receipt in receipts:
        action = _receipt_action_text(receipt.recovery_action)
        if action and action not in actions:
            actions.append(action)
    if actions:
        text += f" · next: {', '.join(actions)}"
    return text


def _retrieval_results_markdown(hits: Sequence[object], *, empty: str) -> str:
    if not hits:
        return f"_{_safe_markdown_text(empty)}_"
    sections: list[str] = []
    for hit in hits:
        title = _safe_markdown_text(getattr(hit, "title"))
        path = _safe_code_text(getattr(hit, "path"))
        evidence = _safe_markdown_text(getattr(hit, "evidence"))
        sections.append(f"### {title}\n\n`{path}`\n\n{evidence}")
    return "\n\n".join(sections)


def _linkable_hit_choices(hits: Sequence[object]) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for hit in hits:
        path = str(getattr(hit, "path", ""))
        title = str(getattr(hit, "title", ""))
        if not path or path.casefold().startswith("inbox/"):
            continue
        choices.append((f"{title} · {path}", path))
    return choices


def _vault_markdown_source_choices(vault: str) -> list[tuple[str, str]]:
    _require_local_ui("Vault Markdown source selection")
    root = Path(vault).expanduser()
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise ValueError("Vault folder must be an existing non-symlink directory")
    root = root.resolve(strict=True)
    sources = root / "Sources"
    if not sources.exists():
        return []
    if not sources.is_dir() or sources.is_symlink():
        raise ValueError("Sources must be a non-symlink directory")

    candidates: list[tuple[float, str]] = []
    for current, directory_names, filenames in os.walk(sources, followlinks=False):
        current_path = Path(current)
        directory_names[:] = [
            name
            for name in directory_names
            if not name.startswith(".") and not (current_path / name).is_symlink()
        ]
        for filename_value in filenames:
            if filename_value.startswith(".") or Path(filename_value).suffix.lower() != ".md":
                continue
            candidate = current_path / filename_value
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(root).as_posix()
            try:
                validate_vault_markdown_path(root, relative)
                modified = candidate.stat().st_mtime
            except (OSError, ValueError, VaultCatalogError):
                continue
            candidates.append((modified, relative))
            if len(candidates) > UI_LINKED_SOURCE_CHOICE_LIMIT * 4:
                break
        if len(candidates) > UI_LINKED_SOURCE_CHOICE_LIMIT * 4:
            break
    candidates.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
    return [(relative, relative) for _modified, relative in candidates[:UI_LINKED_SOURCE_CHOICE_LIMIT]]


def _load_vault_markdown_source(vault: str, relative_path: str) -> tuple[str, str]:
    _require_local_ui("Vault Markdown source preview")
    relative = (relative_path or "").strip()
    if not relative:
        return "", (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>AI source:</strong> the selected Inbox original will be used. "
            "No extra source link will be added.</div>"
        )
    try:
        source_text = read_vault_markdown(
            vault,
            relative,
            max_bytes=UI_LINKED_SOURCE_MAX_BYTES,
        )
    except (OSError, ValueError, VaultCatalogError) as exc:
        return "", (
            '<div class="omd-model-status omd-model-status-warn">'
            f"<strong>Markdown source unavailable:</strong> {html.escape(str(exc))}. "
            "Choose another vault Markdown file. Nothing was sent and no vault file was changed.</div>"
        )
    return source_text, (
        '<div class="omd-model-status omd-model-status-ok">'
        f"<strong>Markdown source selected:</strong> <code>{html.escape(relative)}</code>. "
        "AI will read this preview, and Create note in Notes will add a traceable link. "
        "The source file remains unchanged.</div>"
    )


def _load_vault_markdown_sources_for_ui(vault: str):
    import gradio as gr

    try:
        choices = _vault_markdown_source_choices(vault)
    except ValueError as exc:
        return gr.update(choices=[], value=None), (
            f"**Converted Markdown unavailable:** {_safe_markdown_text(exc)}"
        )
    if not choices:
        return gr.update(choices=[], value=None), (
            "_No Markdown files found under `Sources/`. Capture an external source to this vault first._"
        )
    result = "\n".join(f"- `{_safe_code_text(path)}`" for _label, path in choices)
    return gr.update(choices=choices, value=None), result


def _search_vault_notes(vault: str, query: str) -> str:
    _require_local_ui("Vault search")
    try:
        hits = search_notes(vault, query, limit=10)
    except ValueError as exc:
        return f"**Search unavailable:** {_safe_markdown_text(exc)}"
    return _retrieval_results_markdown(hits, empty="No matching Markdown notes found")


def _search_vault_notes_with_choices(vault: str, query: str) -> tuple[str, list[tuple[str, str]]]:
    _require_local_ui("Vault search")
    try:
        hits = search_notes(vault, query, limit=10)
    except ValueError as exc:
        return f"**Search unavailable:** {_safe_markdown_text(exc)}", []
    return (
        _retrieval_results_markdown(hits, empty="No matching Markdown notes found"),
        _linkable_hit_choices(hits),
    )


def _search_vault_notes_for_ui(vault: str, query: str):
    import gradio as gr

    markdown, choices = _search_vault_notes_with_choices(vault, query)
    return markdown, gr.update(choices=choices, value=None)


def _related_inbox_notes(vault: str, item_id: str) -> str:
    _require_local_ui("Related-note search")
    if not item_id:
        return "_Choose an Inbox item first._"
    try:
        item = load_inbox_item(vault, item_id)
        hits = related_notes(
            vault,
            item.raw_content,
            exclude_path=item.path,
            limit=5,
        )
    except ValueError as exc:
        return f"**Related-note search unavailable:** {_safe_markdown_text(exc)}"
    return _retrieval_results_markdown(hits, empty="No related Markdown notes found")


def _related_inbox_notes_with_choices(
    vault: str,
    item_id: str,
) -> tuple[str, list[tuple[str, str]]]:
    _require_local_ui("Related-note search")
    if not item_id:
        return "_Choose an Inbox item first._", []
    try:
        item = load_inbox_item(vault, item_id)
        hits = related_notes(
            vault,
            item.raw_content,
            exclude_path=item.path,
            limit=5,
        )
    except ValueError as exc:
        return f"**Related-note search unavailable:** {_safe_markdown_text(exc)}", []
    return (
        _retrieval_results_markdown(hits, empty="No related Markdown notes found"),
        _linkable_hit_choices(hits),
    )


def _related_inbox_notes_for_ui(vault: str, item_id: str):
    import gradio as gr

    markdown, choices = _related_inbox_notes_with_choices(vault, item_id)
    return markdown, gr.update(choices=choices, value=None)


def _suggest_vault_sources_with_choices(
    vault: str,
    item_id: str,
) -> tuple[str, list[tuple[str, str]]]:
    """Return one deduplicated list for the single source-selection decision."""
    _require_local_ui("Vault source suggestions")
    candidates: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    if item_id:
        try:
            item = load_inbox_item(vault, item_id)
            hits = related_notes(
                vault,
                item.raw_content,
                exclude_path=item.path,
                limit=5,
            )
        except ValueError as exc:
            return f"**Source suggestions unavailable:** {_safe_markdown_text(exc)}", []
        for hit in hits:
            path = str(getattr(hit, "path", ""))
            if not path or path.casefold().startswith("inbox/") or path in seen:
                continue
            kind = "Converted source" if path.startswith("Sources/") else "Related note"
            candidates.append(
                (
                    kind,
                    str(getattr(hit, "title", Path(path).stem)),
                    path,
                    str(getattr(hit, "evidence", "Related to the selected Inbox text.")),
                )
            )
            seen.add(path)

    try:
        converted = _vault_markdown_source_choices(vault)
    except ValueError as exc:
        return f"**Source suggestions unavailable:** {_safe_markdown_text(exc)}", []
    for _label, path in converted[:20]:
        if path in seen:
            continue
        candidates.append(
            (
                "Converted source",
                Path(path).stem,
                path,
                "Recent Markdown under Sources; selection remains read-only.",
            )
        )
        seen.add(path)

    if not candidates:
        return (
            "_No source candidates found. Capture Markdown to this vault or search by words below._",
            [],
        )
    sections = [
        (
            f"### {_safe_markdown_text(kind)} · {_safe_markdown_text(title)}\n\n"
            f"`{_safe_code_text(path)}`\n\n{_safe_markdown_text(evidence)}"
        )
        for kind, title, path, evidence in candidates
    ]
    choices = [
        (f"{kind} · {title} · {path}", path)
        for kind, title, path, _evidence in candidates
    ]
    return "\n\n".join(sections), choices


def _suggest_vault_sources_for_ui(vault: str, item_id: str):
    import gradio as gr

    markdown, choices = _suggest_vault_sources_with_choices(vault, item_id)
    return markdown, gr.update(choices=choices, value=None)


def _find_duplicate_notes_for_ui(vault: str) -> str:
    _require_local_ui("Duplicate-note search")
    try:
        groups = find_duplicate_notes(vault)
    except ValueError as exc:
        return f"**Duplicate check unavailable:** {_safe_markdown_text(exc)}"
    if not groups:
        return "_No exact duplicate Markdown notes found._"
    lines = []
    for index, group in enumerate(groups, start=1):
        paths = ", ".join(f"`{_safe_code_text(path)}`" for path in group)
        lines.append(f"- **Group {index}:** {paths}")
    return "\n".join(lines)


def _inspect_local_preferences() -> str:
    if _public_demo_enabled():
        return "_Local preferences are unavailable in the hosted demo._"
    profile = load_preference_profile(_preference_state_path())
    if not profile.signals:
        return "_No explicit preferences saved. OMD does not learn from passive behaviour._"
    lines = ["### Local preference signals"]
    for kind in sorted(profile.signals):
        lines.append(f"\n**{_safe_markdown_text(kind.replace('_', ' ').title())}**")
        for value, weight in sorted(profile.signals[kind].items()):
            lines.append(f"- `{_safe_code_text(value)}`: {weight:+d}")
    return "\n".join(lines)


def _record_local_preference_feedback(
    action: str,
    observed_style: str,
    replacement_style: str,
) -> tuple[str, str]:
    _require_local_ui("Local preference feedback")
    normalized_action = (action or "").strip().lower()
    replacement = (replacement_style or "").strip()
    profile_path = _preference_state_path()
    profile = load_preference_profile(profile_path)
    try:
        updated = record_feedback(
            profile,
            normalized_action,
            "output_style",
            observed_style,
            replacement=replacement if normalized_action == "edit" else None,
        )
        save_preference_profile(profile_path, updated)
    except ValueError as exc:
        return _inspect_local_preferences(), f"Preference not saved: {exc}"
    return (
        _inspect_local_preferences(),
        "Explicit output-style feedback saved locally. It can be inspected, exported, or reset.",
    )


def _export_local_preferences() -> tuple[str | None, str]:
    _require_local_ui("Local preference export")
    profile = load_preference_profile(_preference_state_path())
    DOWNLOAD_STAGING.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOAD_STAGING / f"omd-preferences-{time.time_ns()}.json"
    try:
        save_preference_profile(destination, profile)
    except OSError as exc:
        return None, f"Preference export failed: {exc}"
    return str(destination), "Preference export prepared. It contains explicit ranking signals only."


def _reset_local_preferences() -> tuple[str, str]:
    _require_local_ui("Local preference reset")
    reset_stored_preferences(_preference_state_path())
    return _inspect_local_preferences(), "Local preferences reset. Deterministic defaults are active."


def _inbox_queue_state(vault: str) -> tuple[str, list[tuple[str, str]]]:
    summaries = list_inbox_items(vault)
    if not summaries:
        return "_No Inbox items yet. Save a thought or highlight above._", []
    status_labels = {
        "inbox": "needs review",
        "accepted": "note created",
        "rejected": "not needed",
    }
    lines = [
        (
            f"- **{_safe_markdown_text(entry.title)}** | "
            f"{_safe_markdown_text(status_labels.get(entry.review_status, entry.review_status))} | "
            f"`{_safe_code_text(entry.path)}`"
        )
        for entry in summaries
    ]
    choices = [
        (
            f"{entry.title} · {status_labels.get(entry.review_status, entry.review_status)}",
            entry.item_id,
        )
        for entry in summaries
    ]
    return "\n".join(lines), choices


def _load_inbox_review(vault: str, item_id: str | None) -> tuple[str, str]:
    _require_local_ui("Inbox review")
    if not item_id:
        return "Choose an Inbox item above.", ""
    item = load_inbox_item(vault, item_id)
    capture_label = "Highlight" if item.capture_surface == "highlight" else "My note"
    details = (
        f"{capture_label} saved {item.captured_at}. "
        f"Current status: {item.review_status}. Original file: {item.path}"
    )
    return details, item.raw_content


def _save_inbox_note(vault: str, capture_type: str, title: str, content: str):
    import gradio as gr

    _require_local_ui("Inbox capture")
    normalized_title = (title or "").strip()
    if not normalized_title or not isinstance(content, str) or not content.strip():
        raise ValueError("Inbox title and content are required")
    is_highlight = capture_type == "Highlight"
    item = InboxItem(
        capture_surface="highlight" if is_highlight else "my_note",
        provenance_kind="excerpt" if is_highlight else "authored",
        title=normalized_title,
        raw_content=content,
        source_locator={"kind": "manual"},
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    save_inbox_item(vault, item)
    summary, item_ids = _inbox_queue_state(vault)
    status = _inbox_action_status_html(
        "ok",
        "Saved to Inbox",
        f"{item.title}. The original is available in Inbox and no AI was used.",
    )
    return summary, gr.update(choices=item_ids, value=item.item_id), status


def _refresh_inbox_ui(vault: str):
    import gradio as gr

    _require_local_ui("Inbox access")
    summary, choices = _inbox_queue_state(vault)
    selected_item_id = choices[0][1] if choices else None
    return summary, gr.update(choices=choices, value=selected_item_id)


def _reset_inbox_review_context():
    import gradio as gr

    return (
        False,
        False,
        "",
        None,
        gr.update(choices=[], value=None),
        "",
        (
            '<div class="omd-model-status omd-model-status-info">'
            "<strong>AI source:</strong> the selected Inbox original will be used. "
            "No extra source link will be added.</div>"
        ),
        _inbox_action_status_html(
            "info",
            "No review action yet",
            "Choose one final action after reviewing this item.",
        ),
        "",
        "",
        "_No local search run yet._",
        "",
    )


def _review_inbox_note(
    vault: str,
    item_id: str,
    decision: str,
    my_notes: str,
    ai_suggestion: str = "",
    include_ai_suggestion: bool = False,
    linked_source_path: str = "",
    tags_text: str = "",
):
    _require_local_ui("Inbox review")
    import gradio as gr

    try:
        if not item_id:
            raise ValueError("Choose an Inbox item first")
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        current_item = load_inbox_item(vault, item_id)
        if current_item.review_status != "inbox":
            expected_status = "accepted" if decision == "accept" else "rejected"
            if current_item.review_status == expected_status:
                state = "info"
                title = "Review already completed"
                message = (
                    "This Inbox item already has a created Note. The existing Note, "
                    "Inbox original, tags, source link, and AI draft were left unchanged; "
                    "current edits were not applied again."
                    if expected_status == "accepted"
                    else (
                        "This Inbox item is already marked as not needed. The Inbox original "
                        "remains available and no Note was created. Current optional additions "
                        "were not saved."
                    )
                )
            else:
                completed_label = (
                    "a created Note"
                    if current_item.review_status == "accepted"
                    else "a not-needed decision"
                )
                raise ValueError(
                    f"This Inbox item already has {completed_label}; final review actions "
                    "cannot be changed here. Its existing files and status were preserved"
                )
        elif decision == "accept":
            notes = [my_notes] if isinstance(my_notes, str) and my_notes.strip() else []
            tags = _parse_note_tags(tags_text)
            suggestions = (
                [ai_suggestion]
                if include_ai_suggestion
                and isinstance(ai_suggestion, str)
                and ai_suggestion.strip()
                else []
            )
            output = promote_inbox_item(
                vault,
                item_id,
                my_notes=notes,
                ai_suggestions=suggestions,
                linked_source_path=linked_source_path,
                tags=tags,
            )
            linked_detail = (
                f" Linked source: {linked_source_path}." if linked_source_path else ""
            )
            tag_detail = f" Tags: {', '.join(tags)}." if tags else ""
            ai_detail = ""
            if isinstance(ai_suggestion, str) and ai_suggestion.strip():
                ai_detail = (
                    " The edited AI draft was included."
                    if include_ai_suggestion
                    else " The AI draft was not included because its checkbox was off."
                )
            message = (
                f"Created in Notes: {output.name}. The original remains in Inbox."
                f"{linked_detail}{tag_detail}{ai_detail}"
            )
            state = "ok"
            title = "Note created"
        elif decision == "reject":
            set_review_status(vault, item_id, "rejected")
            message = (
                "Marked as not needed. The original remains in Inbox and no Note was created. "
                "Current optional additions were not saved."
            )
            state = "ok"
            title = "Review completed"
    except (OSError, ValueError, VaultCatalogError) as exc:
        try:
            summary, item_ids = _inbox_queue_state(vault)
        except (OSError, ValueError):
            summary, item_ids = "_Inbox could not be refreshed._", []
        failure = _inbox_action_status_html(
            "warn",
            "Inbox action not completed",
            f"{exc}. No vault files were changed. Review the selected item and try again.",
        )
        return summary, gr.update(choices=item_ids, value=item_id or None), failure

    try:
        summary, item_ids = _inbox_queue_state(vault)
    except (OSError, ValueError) as exc:
        warning = _inbox_action_status_html(
            "warn",
            title,
            f"{message} The action completed, but the queue could not refresh: {exc}.",
        )
        return "_Inbox queue refresh failed._", gr.update(value=item_id), warning
    status = _inbox_action_status_html(state, title, message)
    return summary, gr.update(choices=item_ids, value=item_id), status


def _inbox_action_status_html(state: str, title: str, message: str) -> str:
    status_class = {
        "ok": "omd-model-status-ok",
        "warn": "omd-model-status-warn",
        "info": "omd-model-status-info",
    }.get(state, "omd-model-status-warn")
    return (
        f'<div class="omd-model-status {status_class}" role="status" aria-live="polite">'
        f"<strong>{html.escape(title)}:</strong> {html.escape(message)}</div>"
    )


def _voice_queue_state(vault: str) -> tuple[str, list[str]]:
    records = VoiceInboxStore(vault).list_records()
    if not records:
        return "_No voice attachments in this Inbox._", []
    lines = [
        (
            f"- **{_safe_markdown_text(record.title)}** | "
            f"transcript: `{_safe_code_text(record.transcription_state)}` | "
            f"review: `{_safe_code_text(record.review_state)}` | "
            f"`{record.record_id}`"
        )
        for record in records
    ]
    return "\n".join(lines), [record.record_id for record in records]


def _voice_quality_markdown(record: VoiceInboxRecord) -> str:
    if record.quality_warnings:
        warnings = "\n".join(
            f"- {_safe_markdown_text(value)}" for value in record.quality_warnings
        )
        return f"**Transcript needs review**\n\n{warnings}"
    if record.transcription_state == "failed":
        return (
            "**Local transcription failed.** The preserved audio and My Notes are still available; "
            "check the local Whisper setup and retry."
        )
    if record.transcription_state == "needs_review":
        return "**Transcript ready for review.** Compare it with the source audio before accepting."
    if record.transcription_state == "transcribing":
        return "_Local transcription is running._"
    return "_No transcript yet. The original audio is already saved locally._"


def _voice_record_status(record: VoiceInboxRecord, message: str) -> str:
    return (
        f"**{_safe_markdown_text(message)}**\n\n"
        f"- Receipt: `{record.record_id}`\n"
        f"- Audio: `{_safe_code_text(record.attachment_path)}`\n"
        f"- Transcription: `{_safe_code_text(record.transcription_state)}` "
        f"(attempt {record.transcription_attempts})\n"
        f"- Review: `{_safe_code_text(record.review_state)}`"
    )


def _save_voice_attachment(
    vault: str,
    uploaded_path: str | None,
    title: str,
    my_notes: str,
):
    import gradio as gr

    _require_local_ui("Voice attachments")
    if not uploaded_path:
        raise ValueError("Choose an existing audio file first")
    source = Path(uploaded_path)
    display_title = (title or "").strip() or source.stem.replace("_", " ").strip()
    store = VoiceInboxStore(vault)
    record = store.create(source, title=display_title or "Voice note", my_notes=my_notes or "")
    summary, record_ids = _voice_queue_state(vault)
    return (
        summary,
        gr.update(choices=record_ids, value=record.record_id),
        _voice_record_status(record, "Audio saved locally; transcription has not started"),
        gr.update(value=None),
    )


def _refresh_voice_ui(vault: str):
    import gradio as gr

    _require_local_ui("Voice Inbox access")
    summary, record_ids = _voice_queue_state(vault)
    return summary, gr.update(choices=record_ids, value=record_ids[0] if record_ids else None)


def _load_voice_review(vault: str, record_id: str | None):
    _require_local_ui("Voice Inbox review")
    if not record_id:
        return "", "", "", "_Choose a voice attachment to review._", "_No voice item selected._"
    record = VoiceInboxStore(vault).load(record_id)
    return (
        record.raw_transcript,
        record.my_notes,
        record.ai_suggestion,
        _voice_quality_markdown(record),
        _voice_record_status(record, "Voice attachment loaded"),
    )


def _begin_voice_transcription(
    vault: str,
    record_id: str,
    my_notes: str,
    model: str,
    language_hint: str,
) -> str:
    _require_local_ui("Voice transcription")
    if not record_id:
        raise ValueError("Choose a voice attachment first")
    store = VoiceInboxStore(vault)
    record = store.set_my_notes(record_id, my_notes or "")
    from omd._language import choose_whisper_language

    language = choose_whisper_language(None, preferred=(language_hint or "").strip() or None) or ""
    backend = (os.environ.get("OMD_WHISPER_BACKEND") or "mlx").strip()
    if record.transcription_state == "transcribing":
        record = store.resume_transcription(record.record_id)
    else:
        record = store.begin_transcription(
            record.record_id,
            backend=backend,
            model=(model or "").strip() or "mlx-community/whisper-large-v3-turbo",
            language=language,
        )
    return _voice_record_status(record, "Local transcription started locally")


def _voice_error_code(exc: BaseException) -> str:
    lowered = str(exc).casefold()
    if "not on path" in lowered or "not importable" in lowered or "tool_missing" in lowered or "missing" in lowered:
        return "runtime_missing"
    if "timeout" in lowered or "timed out" in lowered:
        return "transcription_timeout"
    if "duration" in lowered:
        return "audio_duration_invalid"
    return "transcription_failed"


def _finish_voice_transcription(vault: str, record_id: str):
    _require_local_ui("Voice transcription")
    if not record_id:
        raise ValueError("Choose a voice attachment first")
    store = VoiceInboxStore(vault)
    record = store.load(record_id)
    if record.transcription_state != "transcribing":
        raise ValueError("Start local transcription before running it")
    try:
        from omd.reel import transcribe

        with tempfile.TemporaryDirectory(prefix="omd-voice-") as workdir:
            transcript = transcribe(
                store.attachment_path(record_id),
                Path(workdir),
                record.transcript_model,
                record.transcript_language or None,
                record.transcript_backend,
            )
        record = store.save_transcript(record_id, transcript)
        message = "Local transcript is ready for review"
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - source audio is the fallback.
        record = store.fail_transcription(record_id, error_code=_voice_error_code(exc))
        message = "Local transcription failed; original audio and My Notes were kept"
    queue, _record_ids = _voice_queue_state(vault)
    return (
        record.raw_transcript,
        record.my_notes,
        record.ai_suggestion,
        _voice_quality_markdown(record),
        _voice_record_status(record, message),
        queue,
    )


def _review_voice_attachment(
    vault: str,
    record_id: str,
    action: str,
    transcript: str,
    my_notes: str,
    ai_suggestion: str,
):
    import gradio as gr

    _require_local_ui("Voice Inbox review")
    if not record_id:
        raise ValueError("Choose a voice attachment first")
    store = VoiceInboxStore(vault)
    record = store.load(record_id)
    if (my_notes or "") != record.my_notes:
        record = store.set_my_notes(record_id, my_notes or "")
    if (ai_suggestion or "") != record.ai_suggestion:
        record = store.set_ai_suggestion(record_id, ai_suggestion or "")
    if (transcript or "").strip() != record.raw_transcript:
        record = store.edit_transcript(record_id, transcript or "")
    if action == "keep_raw":
        record = store.keep_raw(record_id)
        message = "Raw transcript kept for later review"
    elif action == "accept":
        record = store.accept(record_id)
        message = "Reviewed note saved to Notes; source audio remains preserved in Inbox"
    elif action == "reject":
        record = store.reject(record_id)
        message = "Voice attachment rejected; source audio remains preserved"
    else:
        raise ValueError("voice action must be keep_raw, accept, or reject")
    queue, record_ids = _voice_queue_state(vault)
    return (
        queue,
        gr.update(choices=record_ids, value=record.record_id),
        _voice_quality_markdown(record),
        _voice_record_status(record, message),
    )


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

    context_run: _ContextRun | None = None
    if len(args) >= 6:
        try:
            context_run = _queue_context_run(*args[:6])
        except (OSError, ValueError) as exc:
            raise gr.Error(f"Could not create a durable local receipt: {exc}") from exc

    header = f"$ {' '.join(shlex.quote(a) for a in argv)}\n"
    receipt_log = [] if context_run is None else context_run.log_lines()
    log = header + ("\n".join(receipt_log) + "\n" if receipt_log else "")
    started_at = time.monotonic()
    download_modified_since = time.time() - 1.0
    structured_events = "--json-events" in argv
    history_store = _eta_history_store() if structured_events else None
    calibration_store = None
    if history_store is not None and history_store.summary()["enabled"]:
        calibration_store = _eta_calibration_store()
    telemetry = (
        RunTelemetrySession(
            history_store,
            telemetry_context_from_argv(argv),
            calibration_store=calibration_store,
        )
        if history_store is not None
        else None
    )
    eta = _EtaEstimator(started_at=started_at, initial_range=_initial_eta_range(argv))
    structured_eta_label = (
        "ETA: estimating after the first measurable stage"
        if structured_events
        else ""
    )
    current_state = "running"
    had_warning = False
    had_partial_warning = False
    current_label = "accepted" if context_run is not None else "running..."
    current_detail = "durable receipt created" if context_run is not None else "starting"
    percent: int | None = None
    counts = _RunCounts(total=None if _argv_uses_batch(argv) else 1)
    status_html = _status_html(
        current_state,
        current_label,
        detail=current_detail,
        eta=structured_eta_label or eta.label(percent, now=started_at),
        percent=percent,
        summary=_run_status_summary(counts, context_run),
    )
    yield (
        log,
        gr.update(value="_running..._"),
        gr.update(value=""),
        status_html,
        gr.update(value=None, interactive=False),
    )

    if context_run is not None:
        try:
            context_run.secure_sources()
            context_run.start_processing()
        except (OSError, ValueError) as exc:
            log += f"warn: source could not be secured; processing did not start: {exc}\n"
            status_html = _status_html(
                "err",
                "source not secured",
                detail="original input needs attention",
                eta=_total_elapsed_detail(started_at),
                percent=0,
                summary=_run_status_summary(counts, context_run),
            )
            yield (
                log,
                gr.update(value="_Source was not secured; processing did not start._"),
                gr.update(value=""),
                status_html,
                gr.update(value=None, interactive=False),
            )
            return
        log += "\n".join(context_run.log_lines()) + "\n"
        current_label = "processing"
        current_detail = "source secured; conversion started"
        status_html = _status_html(
            current_state,
            current_label,
            detail=current_detail,
            eta=structured_eta_label or eta.label(percent),
            percent=percent,
            summary=_run_status_summary(counts, context_run),
        )
        yield log, gr.update(), gr.update(), status_html, gr.update()

    rc = None
    batch_total: int | None = None
    batch_index = 0
    try:
        for tag, line in _stream_subprocess(argv):
            if tag == "rc":
                rc = int(line)
                break
            if tag == "tick":
                status_html = _status_html(
                    current_state,
                    current_label,
                    detail=current_detail,
                    eta=structured_eta_label or eta.label(percent),
                    percent=percent,
                    summary=_run_status_summary(counts, context_run),
                )
                yield log, gr.update(), gr.update(), status_html, gr.update()
                continue
            structured_event = parse_json_event(line) if structured_events else None
            if structured_event is not None and telemetry is not None:
                receipt_event_warning = _transition_context_batch_event(
                    context_run,
                    structured_event,
                )
                if receipt_event_warning:
                    log += f"warn: {receipt_event_warning}\n"
                    had_warning = True
                update = telemetry.consume(structured_event)
                if rendered_line := event_log_line(structured_event):
                    log += rendered_line + "\n"
                if log.count("\n") > 4000:
                    log = "...(truncated)...\n" + "\n".join(log.splitlines()[-3500:]) + "\n"

                if telemetry.tracker.item_total:
                    counts.total = telemetry.tracker.item_total
                    counts.success = update.succeeded
                    counts.failed = update.failed
                    counts.partial = 0
                current_label = update.label
                current_detail = update.detail
                percent = update.percent
                structured_eta_label = update.eta_label

                event_type = structured_event.get("event")
                if event_type in {"warn", "batch_item_failed"}:
                    had_warning = True
                    if event_type == "warn":
                        warning_line = f"warn: {structured_event.get('message') or ''}"
                        had_partial_warning = had_partial_warning or _warning_marks_partial_output(
                            warning_line
                        )
                    current_state = "warn"
                    current_label = "item failed" if event_type == "batch_item_failed" else "warning"
                elif event_type == "error" or update.state == "failed":
                    current_state = "err"
                    current_label = "error"
                elif update.state in {"retrying", "needs_action"}:
                    current_state = "warn"
                    current_label = update.state.replace("_", " ")
                elif current_state not in {"warn", "err"}:
                    current_state = "running"

                status_html = _status_html(
                    current_state,
                    current_label,
                    detail=current_detail,
                    eta=structured_eta_label,
                    percent=percent,
                    summary=_run_status_summary(counts, context_run),
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
                summary=_run_status_summary(counts, context_run),
            )
            yield log, gr.update(), gr.update(), status_html, gr.update()
    finally:
        if rc is None:
            _transition_context_run(context_run, "cancel")

    if rc == 0:
        try:
            preview, label = _preview_output(out_md)
        except ValueError as exc:
            log += f"\n✗ failed: {exc}\n"
            if counts.seen == 0:
                counts.failed += 1
            receipt_warning = _transition_context_run(context_run, "mark_failed")
            if receipt_warning:
                log += f"warn: {receipt_warning}\n"
            status_html = _status_html(
                "err",
                "failed",
                detail=str(exc)[:80],
                eta=_total_elapsed_detail(started_at),
                percent=100,
                summary=_run_status_summary(counts, context_run),
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
        receipt_action = "mark_partial_output" if single_partial else "complete"
        receipt_warning = _transition_context_run(context_run, receipt_action)
        if receipt_warning:
            log += f"warn: {receipt_warning}\n"
            had_warning = True
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
            summary=_run_status_summary(counts, context_run),
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
            receipt_warning = _transition_context_run(context_run, "mark_failed")
            if receipt_warning:
                log += f"warn: {receipt_warning}\n"
            status_html = _status_html(
                "err",
                "failed",
                detail=f"exit {rc}",
                eta=_total_elapsed_detail(started_at),
                percent=100,
                summary=_run_status_summary(counts, context_run),
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
        receipt_action = "mark_failed" if _argv_uses_batch(argv) else "mark_partial_output"
        receipt_warning = _transition_context_run(context_run, receipt_action)
        if receipt_warning:
            log += f"warn: {receipt_warning}\n"
        status_html = _status_html(
            "warn" if single_partial else "err",
            "saved with warning" if single_partial else "partial failure",
            detail="output available; check log" if single_partial else "open output folder",
            eta=_total_elapsed_detail(started_at),
            percent=100,
            summary=_run_status_summary(counts, context_run),
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

#omd-menubar .omd-menu-btn button.primary,
#omd-menubar button.primary {
    border-color: #052863 !important;
    background: linear-gradient(180deg, #2d68d1 0%, #073b9a 100%) !important;
    color: #fff !important;
    box-shadow: inset 1px 1px 0 #7fa8ff, inset -1px -1px 0 #031a45 !important;
}

#omd-menubar .omd-menu-btn button.primary:hover,
#omd-menubar button.primary:hover,
#omd-menubar .omd-menu-btn button.primary:focus-visible,
#omd-menubar button.primary:focus-visible {
    border-color: #031a45 !important;
    background: linear-gradient(180deg, #3b78e0 0%, #0b49b7 100%) !important;
    color: #fff !important;
    box-shadow: inset 1px 1px 0 #9ab9ff, inset -1px -1px 0 #02112d !important;
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

.omd-grid-primary-only,
.omd-grid-secondary-only {
    grid-template-columns: minmax(0, 1fr) !important;
}

.omd-grid-primary-only > .omd-stack:last-child,
.omd-grid-secondary-only > .omd-stack:first-child {
    display: none !important;
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

.omd-field-help,
.omd-field-help p,
.omd-field-help span,
.omd-field-help .md,
.omd-field-help .md * {
    margin: 0 0 8px !important;
    padding: 0 !important;
    background: transparent !important;
    color: var(--omd-muted) !important;
    opacity: 1 !important;
    font-size: 0.8rem !important;
    line-height: 1.45 !important;
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

.omd-inbox-step {
    margin: 14px 0 7px !important;
    padding: 9px 10px !important;
    border: 1px solid #8b9fca !important;
    border-left: 5px solid var(--omd-blue) !important;
    background: #eef2fb !important;
    color: var(--omd-ink) !important;
    box-shadow: inset 1px 1px 0 #fff !important;
}

.omd-inbox-step:first-child {
    margin-top: 0 !important;
}

.omd-inbox-step .md,
.omd-inbox-step .md * {
    color: var(--omd-ink) !important;
}

.omd-inbox-step h3,
.omd-inbox-step p {
    margin: 0 !important;
}

.omd-inbox-step h3 {
    margin-bottom: 3px !important;
    font-size: 0.94rem !important;
}

.omd-inbox-privacy {
    margin-top: 7px !important;
    padding: 7px 9px !important;
    border-left: 3px solid #4775c7 !important;
    background: #f5f7fc !important;
}

.omd-inbox-ai {
    margin-top: 10px !important;
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

.omd-no-wrap-btn button,
button.omd-no-wrap-btn,
.omd-no-wrap-btn .wrap,
.omd-no-wrap-btn .wrap * {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
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

#omd-vault-folder textarea {
    min-height: 48px !important;
    height: 48px !important;
    box-sizing: border-box !important;
    line-height: 18px !important;
    overflow-x: auto !important;
    overflow-y: hidden !important;
    white-space: pre !important;
    overflow-wrap: normal !important;
    scrollbar-width: thin;
}

#omd-vault-folder textarea::-webkit-scrollbar {
    height: 8px;
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
    overflow: visible;
    text-overflow: clip;
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: break-word;
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

.omd-status-pill-receipt {
    border-color: #5d739c;
    color: #173c78;
    background: #eef4ff;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
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
    .omd-status-head {
        grid-template-columns: minmax(0, 1fr) !important;
    }
    .omd-status-eta {
        grid-column: 1 !important;
        grid-row: 3 !important;
        justify-self: start !important;
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

    selected_host = (server_name or os.environ.get("OMD_UI_HOST", "127.0.0.1")).strip()
    if not selected_host:
        raise ValueError("UI server name must not be empty")
    if not _public_demo_enabled() and not _is_loopback_ui_host(selected_host):
        raise ValueError(
            "Local OMD UI must bind to a loopback address because it has no remote authentication"
        )

    kwargs: dict[str, object] = {
        "server_name": selected_host,
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


def _is_loopback_ui_host(value: str) -> bool:
    normalized = value.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


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
        ai_consent_grant = gr.State(value=None)
        gr.HTML(
            '<div id="omd-desktop">'
            '<div id="omd-titlebar">'
            '<div class="title"><strong>OMD.EXE</strong><span>Local AI context inbox</span></div>'
            '<div id="omd-window-buttons"><i></i><i></i><i></i></div>'
            '</div></div>'
        )
        with gr.Row(elem_id="omd-menubar"):
            all_tab = gr.Button("All", elem_classes=["omd-menu-btn"], variant="primary")
            source_tab = gr.Button("Source", elem_classes=["omd-menu-btn"], variant="secondary")
            inbox_tab = gr.Button("Inbox / review", elem_classes=["omd-menu-btn"], variant="secondary")
            output_tab = gr.Button("Output", elem_classes=["omd-menu-btn"], variant="secondary")
            advanced_tab = gr.Button("Advanced settings", elem_classes=["omd-menu-btn"], variant="secondary")
            gr.HTML(title_status_html)

        with gr.Row(elem_classes=["omd-grid"]) as workbench:
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
                    "Inbox and review",
                    open=False,
                    elem_classes=["omd-window", "omd-panel-inbox"],
                ) as inbox_window:
                    with gr.Group(elem_classes=["omd-pane"]):
                        gr.Markdown(
                            "### 1. Save a thought or highlight\n\n"
                            "Use **My note** for your own words. Use **Highlight** for an exact passage "
                            "copied from something you read.",
                            elem_classes=["omd-inbox-step"],
                        )
                        with gr.Row(elem_classes=["omd-settings-grid"]):
                            inbox_capture_type = gr.Radio(
                                label="What are you saving?",
                                choices=[
                                    ("My note (my own words)", "My Note"),
                                    ("Highlight (an exact excerpt)", "Highlight"),
                                ],
                                value="My Note",
                            )
                            inbox_title = gr.Textbox(
                                label="Title",
                                placeholder="A short, recognisable title",
                            )
                        inbox_content = gr.Textbox(
                            label="Your words or exact highlight",
                            lines=5,
                            placeholder="Write a thought, or paste the exact passage you want to keep.",
                        )
                        save_inbox_btn = gr.Button("Save to Inbox", variant="primary")
                        gr.Markdown(
                            "Saving is local and does not use AI. The original text remains in Inbox "
                            "after either review action.",
                            elem_classes=["omd-hint", "omd-inbox-privacy"],
                        )
                        gr.Markdown(
                            "### 2. Review one Inbox item\n\n"
                            "Choose an item, read the unchanged original, and add your own note if useful.",
                            elem_classes=["omd-inbox-step"],
                        )
                        inbox_queue = gr.Markdown(
                            "_No Inbox items yet. Save a thought or highlight above._",
                            elem_classes=["omd-file-queue", "omd-inbox-queue"],
                        )
                        inbox_item_id = gr.Dropdown(
                            label="Choose an Inbox item",
                            choices=[],
                            value=None,
                            info="The list refreshes automatically when you open Inbox or save an item.",
                        )
                        inbox_provenance = gr.Textbox(
                            label="Saved details",
                            value="Choose an Inbox item above.",
                            lines=2,
                            interactive=False,
                        )
                        inbox_raw_source = gr.Textbox(
                            label="Original text (kept unchanged)",
                            value="",
                            lines=6,
                            interactive=False,
                        )
                        with gr.Accordion(
                            "Choose a vault source (optional)",
                            open=False,
                            elem_classes=["omd-settings-section", "omd-retrieval-panel"],
                        ):
                            gr.Markdown(
                                "Suggest sources combines related notes for this Inbox item with recent converted "
                                "Markdown under `Sources/` in one list. Search notes is for a phrase you enter. "
                                "Choosing a file lets AI read only its preview and adds an Obsidian wikilink only "
                                "when you create the Note. Existing files are never rewritten.",
                                elem_classes=["omd-hint"],
                            )
                            with gr.Row(elem_classes=["omd-action-row"]):
                                suggest_sources_btn = gr.Button(
                                    "Suggest sources",
                                    elem_classes=["omd-no-wrap-btn"],
                                )
                            vault_search_query = gr.Textbox(
                                label="Search this vault",
                                placeholder="Words or phrases to find",
                            )
                            search_notes_btn = gr.Button("Search notes")
                            retrieval_results = gr.Markdown(
                                "_No local search run yet._",
                                elem_classes=["omd-file-queue", "omd-retrieval-results"],
                            )
                            linked_markdown_path = gr.Dropdown(
                                label="Markdown source for AI and new-note link (optional)",
                                choices=[],
                                value=None,
                                info=(
                                    "Choose one result. Clear the selection to use only the Inbox original."
                                ),
                            )
                            linked_source_preview = gr.Textbox(
                                label="Selected Markdown source (kept unchanged)",
                                value="",
                                lines=7,
                                interactive=False,
                            )
                            linked_source_status = gr.HTML(
                                value=(
                                    '<div class="omd-model-status omd-model-status-info">'
                                    "<strong>AI source:</strong> the selected Inbox original will be used. "
                                    "No extra source link will be added.</div>"
                                ),
                                container=False,
                            )
                        with gr.Accordion(
                            "Draft a takeaway with AI (optional)",
                            open=False,
                            visible=not public_demo,
                            elem_classes=["omd-settings-section", "omd-inbox-ai"],
                        ) as inbox_ai_panel:
                            gr.Markdown(
                                "AI can draft one takeaway, quote exact evidence, and suggest tags from "
                                "the selected Inbox original or the explicitly selected Markdown preview above. "
                                "When the draft is ready, suggested tags are copied into the editable field directly "
                                "below this panel. AI does not open links or read any unselected file.",
                                elem_classes=["omd-hint"],
                            )
                            preview_ai_request_btn = gr.Button(
                                "Review cloud request",
                                visible=False,
                                interactive=not public_demo,
                            )
                            ai_request_preview = gr.HTML(
                                value=(
                                    '<div class="omd-model-status omd-model-status-info">'
                                    "<strong>Cloud AI is optional.</strong> Review exactly what will be sent "
                                    "before granting consent for one request.</div>"
                                ),
                                visible=False,
                                container=False,
                            )
                            cloud_consent = gr.Checkbox(
                                label="Cloud consent for this request",
                                value=False,
                                visible=False,
                                info="Required again for every OpenAI, Anthropic, or DeepSeek request.",
                                interactive=not public_demo,
                            )
                            generate_ai_suggestion_btn = gr.Button(
                                "Generate draft from selected text",
                                visible=not public_demo,
                                interactive=not public_demo,
                            )
                            ai_suggestion_box = gr.Textbox(
                                label="AI draft (edit before including)",
                                lines=7,
                                placeholder="A grounded draft will appear here. OMD rejects results without exact evidence.",
                                interactive=not public_demo,
                            )
                            include_ai_suggestion = gr.Checkbox(
                                label="Add this edited AI draft to the new note",
                                value=False,
                                interactive=not public_demo,
                            )
                            ai_suggestion_status = gr.HTML(
                                value=(
                                    '<div class="omd-model-status omd-model-status-info">'
                                    "<strong>Local AI scope:</strong> only the chosen read-only text is sent to "
                                    "Ollama on this Mac. Links are not opened and unselected files are not read.</div>"
                                ),
                                container=False,
                            )
                        note_tags = gr.Textbox(
                            label="Tags for new note (editable)",
                            placeholder="agents, knowledge-management",
                        )
                        gr.Markdown(
                            "AI suggestions are added here, not saved automatically. Review, edit, or remove tags before creating the note.",
                            elem_classes=["omd-field-help"],
                        )
                        inbox_review_notes = gr.Textbox(
                            label="Add your note (optional)",
                            lines=3,
                            placeholder="Your interpretation, question, or next step. Kept separate from the original.",
                        )
                        gr.Markdown(
                            "### 3. Decide what to keep\n\n"
                            "**Create note in Notes** copies the original plus your optional additions into "
                            "a traceable note, saves your edited tags, and includes the selected source as an "
                            "Obsidian wikilink, if any. "
                            "**Keep original · mark as not needed** is not Delete: it creates no Note and "
                            "keeps the Inbox original available.",
                            elem_classes=["omd-inbox-step"],
                        )
                        with gr.Row(elem_classes=["omd-action-row"]):
                            accept_inbox_btn = gr.Button(
                                "Create note in Notes",
                                variant="primary",
                            )
                            reject_inbox_btn = gr.Button(
                                "Keep original · mark as not needed"
                            )
                        inbox_action_status = gr.HTML(
                            value=(
                                '<div class="omd-model-status omd-model-status-info" '
                                'role="status" aria-live="polite">'
                                "<strong>No review action yet.</strong> Choose one final action above.</div>"
                            ),
                            container=False,
                        )
                        with gr.Accordion(
                            "Voice attachment and review",
                            open=False,
                            elem_classes=["omd-settings-section", "omd-voice-panel"],
                        ):
                            gr.Markdown(
                                "Attach an existing audio memo. OMD saves the original to this vault first, "
                                "then you can transcribe it locally and review the result. Recording is not "
                                "included in this desktop phase.",
                                elem_classes=["omd-hint"],
                            )
                            voice_upload = gr.File(
                                label="Voice attachment",
                                type="filepath",
                                file_count="single",
                                file_types=["audio"],
                                interactive=not public_demo,
                            )
                            with gr.Row(elem_classes=["omd-settings-grid"]):
                                voice_title = gr.Textbox(
                                    label="Voice note title",
                                    placeholder="Optional; the filename is used when blank",
                                    interactive=not public_demo,
                                )
                                voice_capture_notes = gr.Textbox(
                                    label="My Notes before transcription",
                                    placeholder="Optional context in your own words",
                                    interactive=not public_demo,
                                )
                            save_voice_btn = gr.Button(
                                "Save audio to Inbox",
                                variant="primary",
                                interactive=not public_demo,
                            )
                            gr.Markdown(
                                "**Step 1:** save the audio | **Step 2:** transcribe locally | "
                                "**Step 3:** compare and decide",
                                elem_classes=["omd-hint", "omd-inbox-privacy"],
                            )
                            with gr.Row(elem_classes=["omd-action-row"]):
                                refresh_voice_btn = gr.Button(
                                    "Refresh voice Inbox",
                                    interactive=not public_demo,
                                )
                                voice_record_id = gr.Dropdown(
                                    label="Voice item",
                                    choices=[],
                                    value=None,
                                    interactive=not public_demo,
                                )
                            voice_transcript = gr.Textbox(
                                label="Raw transcript (editable after local transcription)",
                                lines=7,
                                placeholder="The local transcript will appear here.",
                                interactive=not public_demo,
                            )
                            voice_review_notes = gr.Textbox(
                                label="My Notes",
                                lines=3,
                                placeholder="Your own notes stay separate from the transcript.",
                                interactive=not public_demo,
                            )
                            voice_ai_suggestion = gr.Textbox(
                                label="AI suggestion (optional and review required)",
                                lines=4,
                                placeholder="Optional organisation or summary; never replaces the raw transcript.",
                                interactive=not public_demo,
                            )
                            voice_quality = gr.Markdown(
                                "_Choose a voice attachment to review._",
                                elem_classes=["omd-file-queue", "omd-voice-quality"],
                            )
                            transcribe_voice_btn = gr.Button(
                                "Transcribe / Retry locally",
                                interactive=not public_demo,
                            )
                            with gr.Row(elem_classes=["omd-action-row"]):
                                keep_raw_voice_btn = gr.Button(
                                    "Keep raw",
                                    interactive=not public_demo,
                                )
                                accept_voice_btn = gr.Button(
                                    "Accept Voice note",
                                    variant="primary",
                                    interactive=not public_demo,
                                )
                                reject_voice_btn = gr.Button(
                                    "Reject",
                                    interactive=not public_demo,
                                )
                            voice_action_status = gr.Markdown("_No voice action yet._")
                            voice_queue = gr.Markdown(
                                "_No voice attachments in this Inbox._",
                                elem_classes=["omd-file-queue", "omd-voice-queue"],
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
                                elem_id="omd-vault-folder",
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
                                json_events = gr.State(INTERNAL_JSON_EVENTS_DEFAULT)
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

                        with gr.Accordion(
                            "AI provider for Inbox review",
                            open=False,
                            elem_classes=["omd-settings-section"],
                        ):
                            with gr.Group(elem_classes=["omd-option-panel"]):
                                gr.HTML('<div class="omd-option-title">Optional AI destination</div>')
                                gr.Markdown(
                                    (
                                        "Use local Ollama by default, turn AI off, or bring your own API key for "
                                        "OpenAI, Anthropic, or DeepSeek. OMD uses direct provider APIs, never a "
                                        "consumer ChatGPT/Claude login, and never falls back to another provider."
                                    ),
                                    elem_classes=["omd-hint"],
                                )
                                ai_provider_choice = gr.Dropdown(
                                    label="AI provider for Inbox review",
                                    choices=(list(AI_PROVIDER_CHOICES) if not public_demo else ["No AI"]),
                                    value="Local Ollama" if not public_demo else "No AI",
                                    interactive=not public_demo,
                                )
                                provider_model = gr.Dropdown(
                                    label="Provider model",
                                    choices=[default_polish_model] if not public_demo else [],
                                    value=default_polish_model if not public_demo else None,
                                    allow_custom_value=True,
                                    info=(
                                        "Use Check models, then choose the exact model. OMD does not "
                                        "silently substitute another model."
                                    ),
                                    interactive=not public_demo,
                                )
                                with gr.Group(visible=False) as cloud_key_controls:
                                    session_api_key = gr.Textbox(
                                        label="Session API key",
                                        type="password",
                                        placeholder="Used in memory for this UI session",
                                        interactive=not public_demo,
                                    )
                                    gr.Markdown(
                                        "Leave blank to use a supported environment variable or a key saved in "
                                        "macOS Keychain. Secrets are not written to OMD project or vault files.",
                                        elem_classes=["omd-field-help"],
                                    )
                                    with gr.Row(elem_classes=["omd-action-row"]):
                                        save_provider_key_btn = gr.Button(
                                            "Save key to Keychain",
                                            interactive=not public_demo,
                                        )
                                        delete_provider_key_btn = gr.Button(
                                            "Delete saved key",
                                            interactive=not public_demo,
                                        )
                                check_ai_provider_btn = gr.Button(
                                    "Check models",
                                    elem_classes=["omd-picker-btn", "omd-no-wrap-btn"],
                                    interactive=not public_demo,
                                )
                                ai_provider_status = gr.HTML(
                                    value=(
                                        '<div class="omd-model-status omd-model-status-info">'
                                        "<strong>Local-only provider:</strong> source text stays on this Mac and is "
                                        "sent only to the selected loopback Ollama endpoint. No cloud consent or API key is used."
                                        "</div>"
                                        if not public_demo
                                        else (
                                            '<div class="omd-model-status omd-model-status-info">'
                                            "<strong>AI disabled in hosted demo:</strong> use the local app for private model access."
                                            "</div>"
                                        )
                                    ),
                                    container=False,
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
            api_visibility="private",
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
            api_visibility="private",
        )
        file_input.delete(
            fn=remove_deleted_source_file,
            inputs=[source_file_queue],
            outputs=[source_file_queue, source_file_queue_summary],
            queue=False,
            api_visibility="private",
        )
        file_input.clear(
            fn=_clear_source_file_queue,
            inputs=[source_file_queue],
            outputs=[source_file_queue, source_file_queue_summary],
            queue=False,
            api_visibility="private",
        )
        menu_outputs = [
            source_window,
            inbox_window,
            result_window,
            output_window,
            run_dock,
            cookies_window,
            advanced_window,
            all_tab,
            source_tab,
            inbox_tab,
            output_tab,
            advanced_tab,
            workbench,
        ]
        all_tab.click(
            fn=lambda: _menu_view_updates("all"),
            inputs=None,
            outputs=menu_outputs,
            api_visibility="private",
        )
        source_tab.click(
            fn=lambda: _menu_view_updates("source"),
            inputs=None,
            outputs=menu_outputs,
            api_visibility="private",
        )
        inbox_tab.click(
            fn=lambda: _menu_view_updates("inbox"),
            inputs=None,
            outputs=menu_outputs,
            api_visibility="private",
        ).then(
            fn=_refresh_inbox_ui,
            inputs=[vault_dir],
            outputs=[inbox_queue, inbox_item_id],
            api_visibility="private",
        )
        output_tab.click(
            fn=lambda: _menu_view_updates("output"),
            inputs=None,
            outputs=menu_outputs,
            api_visibility="private",
        )
        advanced_tab.click(
            fn=lambda: _menu_view_updates("advanced"),
            inputs=None,
            outputs=menu_outputs,
            api_visibility="private",
        )
        save_inbox_btn.click(
            fn=_save_inbox_note,
            inputs=[vault_dir, inbox_capture_type, inbox_title, inbox_content],
            outputs=[inbox_queue, inbox_item_id, inbox_action_status],
            api_visibility="private",
        )
        accept_inbox_btn.click(
            fn=lambda vault, item_id, notes, suggestion, include_suggestion, linked_source, tags: (
                _review_inbox_note(
                    vault,
                    item_id,
                    "accept",
                    notes,
                    suggestion,
                    include_suggestion,
                    linked_source,
                    tags,
                )
            ),
            inputs=[
                vault_dir,
                inbox_item_id,
                inbox_review_notes,
                ai_suggestion_box,
                include_ai_suggestion,
                linked_markdown_path,
                note_tags,
            ],
            outputs=[inbox_queue, inbox_item_id, inbox_action_status],
            api_visibility="private",
        )
        reject_inbox_btn.click(
            fn=lambda vault, item_id, notes: _review_inbox_note(
                vault, item_id, "reject", notes
            ),
            inputs=[vault_dir, inbox_item_id, inbox_review_notes],
            outputs=[inbox_queue, inbox_item_id, inbox_action_status],
            api_visibility="private",
        )
        ai_provider_choice.change(
            fn=_ai_provider_ui_updates,
            inputs=[ai_provider_choice, polish_md_model],
            outputs=[
                provider_model,
                cloud_key_controls,
                session_api_key,
                cloud_consent,
                ai_provider_status,
                preview_ai_request_btn,
                ai_request_preview,
                generate_ai_suggestion_btn,
                inbox_ai_panel,
            ],
            api_visibility="private",
        ).then(fn=lambda: None, inputs=None, outputs=[ai_consent_grant], api_visibility="private")
        check_ai_provider_btn.click(
            fn=_check_ai_provider_connection,
            inputs=[ai_provider_choice, provider_model, session_api_key, ollama_host],
            outputs=[provider_model, ai_provider_status],
            api_visibility="private",
        )
        save_provider_key_btn.click(
            fn=_save_cloud_api_key,
            inputs=[ai_provider_choice, session_api_key],
            outputs=[session_api_key, ai_provider_status],
            api_visibility="private",
        )
        delete_provider_key_btn.click(
            fn=_delete_cloud_api_key,
            inputs=[ai_provider_choice],
            outputs=[ai_provider_status],
            api_visibility="private",
        )
        preview_ai_request_btn.click(
            fn=_preview_inbox_ai_request_for_ui,
            inputs=[
                vault_dir,
                inbox_item_id,
                ai_provider_choice,
                provider_model,
                ollama_host,
                linked_markdown_path,
            ],
            outputs=[ai_request_preview, ai_consent_grant, cloud_consent],
            api_visibility="private",
        )
        generate_ai_suggestion_btn.click(
            fn=_run_inbox_ai_suggestion_for_ui,
            inputs=[
                vault_dir,
                inbox_item_id,
                ai_provider_choice,
                provider_model,
                session_api_key,
                ollama_host,
                cloud_consent,
                ai_suggestion_box,
                note_tags,
                ai_consent_grant,
                linked_markdown_path,
            ],
            outputs=[ai_suggestion_box, ai_suggestion_status, note_tags],
            api_visibility="private",
        ).then(
            fn=lambda: (False, None),
            inputs=None,
            outputs=[cloud_consent, ai_consent_grant],
            api_visibility="private",
        )
        inbox_item_id.change(
            fn=_load_inbox_review,
            inputs=[vault_dir, inbox_item_id],
            outputs=[inbox_provenance, inbox_raw_source],
            api_visibility="private",
        ).then(
            fn=_reset_inbox_review_context,
            inputs=None,
            outputs=[
                cloud_consent,
                include_ai_suggestion,
                ai_suggestion_box,
                ai_consent_grant,
                linked_markdown_path,
                linked_source_preview,
                linked_source_status,
                inbox_action_status,
                note_tags,
                inbox_review_notes,
                retrieval_results,
                vault_search_query,
            ],
            api_visibility="private",
        )
        linked_markdown_path.change(
            fn=_load_vault_markdown_source,
            inputs=[vault_dir, linked_markdown_path],
            outputs=[linked_source_preview, linked_source_status],
            api_visibility="private",
        ).then(
            fn=lambda: (False, False, "", None),
            inputs=None,
            outputs=[cloud_consent, include_ai_suggestion, ai_suggestion_box, ai_consent_grant],
            api_visibility="private",
        )
        provider_model.change(
            fn=lambda: (False, None),
            inputs=None,
            outputs=[cloud_consent, ai_consent_grant],
            api_visibility="private",
        )
        suggest_sources_btn.click(
            fn=_suggest_vault_sources_for_ui,
            inputs=[vault_dir, inbox_item_id],
            outputs=[retrieval_results, linked_markdown_path],
            api_visibility="private",
        )
        search_notes_btn.click(
            fn=_search_vault_notes_for_ui,
            inputs=[vault_dir, vault_search_query],
            outputs=[retrieval_results, linked_markdown_path],
            api_visibility="private",
        )
        save_voice_btn.click(
            fn=_save_voice_attachment,
            inputs=[vault_dir, voice_upload, voice_title, voice_capture_notes],
            outputs=[voice_queue, voice_record_id, voice_action_status, voice_upload],
            api_visibility="private",
        )
        refresh_voice_btn.click(
            fn=_refresh_voice_ui,
            inputs=[vault_dir],
            outputs=[voice_queue, voice_record_id],
            api_visibility="private",
        )
        voice_record_id.change(
            fn=_load_voice_review,
            inputs=[vault_dir, voice_record_id],
            outputs=[
                voice_transcript,
                voice_review_notes,
                voice_ai_suggestion,
                voice_quality,
                voice_action_status,
            ],
            api_visibility="private",
        )
        transcribe_voice_btn.click(
            fn=_begin_voice_transcription,
            inputs=[
                vault_dir,
                voice_record_id,
                voice_review_notes,
                whisper_model,
                preferred_languages,
            ],
            outputs=[voice_action_status],
            api_visibility="private",
        ).then(
            fn=_finish_voice_transcription,
            inputs=[vault_dir, voice_record_id],
            outputs=[
                voice_transcript,
                voice_review_notes,
                voice_ai_suggestion,
                voice_quality,
                voice_action_status,
                voice_queue,
            ],
            api_visibility="private",
        )
        keep_raw_voice_btn.click(
            fn=lambda vault, item_id, transcript, notes, suggestion: _review_voice_attachment(
                vault, item_id, "keep_raw", transcript, notes, suggestion
            ),
            inputs=[
                vault_dir,
                voice_record_id,
                voice_transcript,
                voice_review_notes,
                voice_ai_suggestion,
            ],
            outputs=[voice_queue, voice_record_id, voice_quality, voice_action_status],
            api_visibility="private",
        )
        accept_voice_btn.click(
            fn=lambda vault, item_id, transcript, notes, suggestion: _review_voice_attachment(
                vault, item_id, "accept", transcript, notes, suggestion
            ),
            inputs=[
                vault_dir,
                voice_record_id,
                voice_transcript,
                voice_review_notes,
                voice_ai_suggestion,
            ],
            outputs=[voice_queue, voice_record_id, voice_quality, voice_action_status],
            api_visibility="private",
        )
        reject_voice_btn.click(
            fn=lambda vault, item_id, transcript, notes, suggestion: _review_voice_attachment(
                vault, item_id, "reject", transcript, notes, suggestion
            ),
            inputs=[
                vault_dir,
                voice_record_id,
                voice_transcript,
                voice_review_notes,
                voice_ai_suggestion,
            ],
            outputs=[voice_queue, voice_record_id, voice_quality, voice_action_status],
            api_visibility="private",
        )
        inspect_btn.click(
            fn=_inspect_source,
            inputs=[text_input, source_file_queue, batch_file_path, cookies_file, cookies_browser, xhs_cookies_file],
            outputs=[inspect_preview],
            api_visibility="private",
        )
        choose_out_dir.click(
            fn=_choose_output_dir,
            inputs=[out_dir],
            outputs=[out_dir],
            api_visibility="private",
        )
        choose_vault_dir.click(
            fn=_choose_output_dir,
            inputs=[vault_dir],
            outputs=[vault_dir],
            api_visibility="private",
        )
        cookies_upload.change(
            fn=_stage_cookies,
            inputs=[cookies_upload],
            outputs=[cookies_file],
            api_visibility="private",
        )
        xhs_cookies_upload.change(
            fn=_stage_cookies,
            inputs=[xhs_cookies_upload],
            outputs=[xhs_cookies_file],
            api_visibility="private",
        )
        choose_cookies_file.click(
            fn=_choose_cookies_file,
            inputs=[cookies_file],
            outputs=[cookies_file],
            api_visibility="private",
        )
        choose_xhs_cookies_file.click(
            fn=_choose_cookies_file,
            inputs=[xhs_cookies_file],
            outputs=[xhs_cookies_file],
            api_visibility="private",
        )
        workflow_mode.change(
            fn=_workflow_mode_updates,
            inputs=[workflow_mode],
            outputs=[run_btn, polish_md, memory_cards],
            api_visibility="private",
        )
        verbose.change(
            fn=lambda enabled: not enabled,
            inputs=[verbose],
            outputs=[json_events],
            api_visibility="private",
        )
        check_local_model_btn.click(
            fn=_local_model_status_html,
            inputs=[polish_md_model, memory_model, ollama_host],
            outputs=[local_model_status],
            api_visibility="private",
        )
        open_output_btn.click(
            fn=_open_output_path,
            inputs=[out_path_box],
            outputs=[status],
            api_visibility="private",
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
            api_name="run_with_status",
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
            api_visibility="private",
        )

    return app


def _ui_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _ui_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omd-ui",
        description="Launch the local OMD browser UI.",
        epilog=(
            "Environment: OMD_UI_PORT sets the default port and OMD_UI_HOST sets the "
            "loopback host. Run `omd doctor` if the UI dependencies are unavailable."
        ),
    )
    parser.add_argument(
        "--port",
        type=_ui_port,
        default=os.environ.get("OMD_UI_PORT", "7860"),
        help="Local server port (default: OMD_UI_PORT or 7860).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the UI without opening a browser tab.",
    )
    return parser


def _write_ui_startup_error(exc: Exception, *, port: int) -> None:
    message = str(exc)
    normalized = message.lower()
    if "cannot find empty port" in normalized or "address already in use" in normalized:
        alternative = port + 1 if port < 65535 else port - 1
        sys.stderr.write(
            f"error: OMD UI could not start because port {port} is already in use.\n"
            f"next: run `omd-ui --port {alternative}`.\n"
            f"alternative: run `OMD_UI_PORT={alternative} omd-ui`.\n"
            "preserved: no vault files were changed.\n"
        )
        sys.stderr.flush()
        return
    if isinstance(exc, ImportError):
        sys.stderr.write(
            "error: OMD UI dependencies are unavailable.\n"
            "next: install the UI extra with `python -m pip install 'omd[ui]'`.\n"
            "check: run `omd doctor` after installation.\n"
            "preserved: no vault files were changed.\n"
        )
        sys.stderr.flush()
        return
    sys.stderr.write(
        f"error: OMD UI could not start: {message}\n"
        "next: run `omd doctor`, correct the reported configuration, and retry.\n"
        "preserved: no vault files were changed.\n"
    )
    sys.stderr.flush()


def main(argv: Sequence[str] | None = None) -> int:
    args = _ui_argument_parser().parse_args(argv)
    try:
        app = build_app()
        kwargs = build_launch_kwargs(
            inbrowser=not args.no_browser,
            server_name=os.environ.get("OMD_UI_HOST", "127.0.0.1"),
            server_port=args.port,
        )
        app.queue().launch(**kwargs)
    except (ImportError, OSError, ValueError) as exc:
        _write_ui_startup_error(exc, port=args.port)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
