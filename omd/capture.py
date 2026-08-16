"""Vault capture helpers for local AI memory workflows."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from omd import __version__
from omd._io import write_atomic
from omd._language import DEFAULT_OCR_LANGUAGE
from omd._models import recommended_local_text_model

if TYPE_CHECKING:
    from omd.memory_cards import MemoryCardsResult


@dataclass(frozen=True)
class CaptureResult:
    source: str
    output_path: Path
    index_path: Path
    source_type: str
    title: str
    tags: list[str]
    return_code: int


@dataclass(frozen=True)
class CaptureBatchResult:
    source: str
    vault: Path
    items: list[CaptureResult]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.return_code == 0)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    @property
    def exit_code(self) -> int:
        if self.total == 0:
            return 1
        return 0 if self.failed == 0 else 1


SOURCE_FOLDERS = {
    "audio": "Audio",
    "bilibili": "Bilibili",
    "bluesky": "Bluesky",
    "douyin": "Douyin",
    "hacker_news": "Hacker News",
    "image": "Images",
    "instagram": "Instagram",
    "mastodon": "Mastodon",
    "office_doc": "Documents",
    "pdf": "PDFs",
    "podcast": "Podcasts",
    "reddit": "Reddit",
    "telegram": "Telegram",
    "threads": "Threads",
    "tiktok": "TikTok",
    "webpage": "Web",
    "wechat": "WeChat",
    "xiaohongshu": "Xiaohongshu",
    "xpost": "X",
    "youtube": "YouTube",
}

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_TIMESTAMP_TITLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[\s_-]+\d{2}[-:]\d{2}[-:]\d{2}[_\s-]*")
_DOCTYPE_LINE_RE = re.compile(r"^\s*<!doctype\s+html\s*>\s*$", re.IGNORECASE)
_SPHINX_HEADING_LINK_RE = re.compile(
    r'^(?P<heading>\s*#{1,6}\s+.*?)(?:\[#\]\([^\n)]*(?:\s+"Link to this heading")?\))\s*$'
)
_TERMINAL_PROGRESS_RE = re.compile(r"^\s*\d{1,3}%\|.*\|.*\[[^\]]+\]\s*$")
USER_FRONTMATTER_KEYS = (
    "title",
    "source_type",
    "captured_at",
    "source_url",
    "local_source_path",
    "tags",
)


def capture_one(
    target: str,
    vault: str | Path,
    *,
    lang: str = DEFAULT_OCR_LANGUAGE,
    reel_extra: list[str] | None = None,
    tags: list[str] | None = None,
    agent_safe: bool = False,
    memory_cards: bool = False,
    memory_model: str | None = None,
    memory_host: str = "http://localhost:11434",
    memory_timeout: float = 180,
    polish_md: bool = False,
    polish_md_model: str | None = None,
    polish_md_host: str = "http://localhost:11434",
    allow_remote_ollama: bool = False,
) -> CaptureResult:
    """Convert one target into a vault, sizing an omitted memory model to local RAM."""
    from omd import cli
    from omd._preflight import inspect_target

    memory_model = _resolve_memory_model(memory_model)
    source = str(target)
    vault_root = Path(vault).expanduser()
    ensure_vault_layout(vault_root)
    preflight = inspect_target(source)
    source_type = source_type_for(source, preflight)
    output_path = reserve_capture_path(vault_root, source, source_type)
    rc = cli.route_one(
        source,
        output_path,
        lang,
        list(reel_extra or []),
        agent_safe=agent_safe,
        output_format="md",
    )
    if rc != 0:
        _cleanup_failed_capture(output_path)
        return CaptureResult(
            source=source,
            output_path=output_path,
            index_path=index_path(vault_root),
            source_type=source_type,
            title=_initial_title(source),
            tags=list(tags or []),
            return_code=rc,
        )

    captured_at = _now_iso()
    body = output_path.read_text(encoding="utf-8", errors="replace")
    body_without_frontmatter = normalize_captured_markdown(_strip_frontmatter(body))
    title = clean_capture_title(title_from_markdown(body_without_frontmatter) or _initial_title(source))
    output_path = rename_capture_to_title(output_path, vault_root, title=title, source_type=source_type)
    polish_md_model = (polish_md_model or "").strip() or recommended_local_text_model()
    if polish_md:
        from omd import _polish_md, _progress

        _progress.info(f"Polishing captured Markdown via Ollama: {polish_md_model}")
        try:
            body_without_frontmatter = _polish_md.polish_markdown(
                body_without_frontmatter,
                model=polish_md_model,
                host=polish_md_host,
                allow_remote=allow_remote_ollama,
            )
        except Exception as exc:  # noqa: BLE001 - optional polish must not discard a valid capture.
            _progress.warn(f"Markdown polish failed; keeping structurally cleaned capture: {exc}")
    from omd._transcript import transcript_warnings_from_markdown

    transcript_warnings = transcript_warnings_from_markdown(body_without_frontmatter)
    memory_result: MemoryCardsResult | None = None
    generated_tags: list[str] = []
    memory_error = ""
    if memory_cards and transcript_warnings:
        from omd import _progress

        memory_error = "Memory cards skipped because transcript quality checks require review."
        _progress.warn(memory_error)
    elif memory_cards:
        from omd import _progress
        from omd.memory_cards import generate_memory_cards

        _progress.info(f"Generating memory cards via Ollama: {memory_model}")
        _progress.info(
            "OMD will not download models automatically; install explicitly with "
            f"`ollama pull {memory_model}` if needed."
        )
        _progress.info(
            f"Recommended explicit pulls: {recommended_local_text_model()} for Chinese/mixed text memory cards; "
            "gemma3:4b for image/OCR enhancement; bge-m3 for future multilingual search."
        )
        try:
            memory_result = generate_memory_cards(
                body_without_frontmatter,
                model=memory_model,
                host=memory_host,
                timeout=memory_timeout,
                title=title,
                source_type=source_type,
                **({"allow_remote": True} if allow_remote_ollama else {}),
            )
        except Exception as exc:  # noqa: BLE001 - keep raw capture usable when optional LLM fails.
            memory_error = str(exc)
            _progress.warn(
                "memory cards failed; writing raw capture without generated sections: "
                f"{memory_error}. If the model is missing, run `ollama pull {memory_model}` explicitly."
            )
        else:
            for warning in memory_result.warnings:
                _progress.warn(warning)
            generated_tags = memory_result.tags

    metadata = capture_metadata(
        source=source,
        source_type=source_type,
        title=title,
        tags=[*list(tags or []), *generated_tags],
        captured_at=captured_at,
        preflight=preflight,
        reel_extra=list(reel_extra or []),
        output_path=output_path,
        memory_cards=memory_result is not None,
        memory_attempted=memory_cards,
        memory_model=memory_model,
        memory_host=memory_host,
        generated_tags=generated_tags,
        memory_warnings=memory_result.warnings if memory_result else [],
        memory_error=memory_error,
        markdown_polish_requested=polish_md,
        markdown_polish_model=polish_md_model,
        markdown_polish_host=polish_md_host,
    )
    write_atomic(
        output_path,
        format_frontmatter(metadata) + format_capture_body(metadata, body_without_frontmatter, memory_result=memory_result),
    )
    _refresh_manifest(output_path, source=source, preflight=preflight, metadata=metadata)
    idx = update_index(vault_root, output_path=output_path, metadata=metadata)
    return CaptureResult(
        source=source,
        output_path=output_path,
        index_path=idx,
        source_type=source_type,
        title=title,
        tags=list(tags or []),
        return_code=0,
    )


def capture_batch(
    target: str,
    vault: str | Path,
    *,
    lang: str = DEFAULT_OCR_LANGUAGE,
    reel_extra: list[str] | None = None,
    tags: list[str] | None = None,
    agent_safe: bool = False,
    retries: int = 0,
    memory_cards: bool = False,
    memory_model: str | None = None,
    memory_host: str = "http://localhost:11434",
    memory_timeout: float = 180,
    polish_md: bool = False,
    polish_md_model: str | None = None,
    polish_md_host: str = "http://localhost:11434",
    allow_remote_ollama: bool = False,
) -> CaptureBatchResult:
    """Capture every item into one vault, sizing an omitted memory model to local RAM."""
    from omd import _progress

    memory_model = _resolve_memory_model(memory_model)
    vault_root = Path(vault).expanduser()
    ensure_vault_layout(vault_root)
    items = batch_targets(target)
    if not items:
        _progress.warn(f"capture batch: no supported items in {target}")
        return CaptureBatchResult(source=str(target), vault=vault_root, items=[])

    results: list[CaptureResult] = []
    _progress.info(f"capture batch: {len(items)} items")
    with _progress.ProgressBar("Capture", total=len(items)) as bar:
        for index, item in enumerate(items, 1):
            _progress.info(f"[{index}/{len(items)}] {item}")
            result = _capture_one_with_retries(
                item,
                vault_root,
                lang=lang,
                reel_extra=list(reel_extra or []),
                tags=list(tags or []),
                agent_safe=agent_safe,
                retries=max(0, retries),
                memory_cards=memory_cards,
                memory_model=memory_model,
                memory_host=memory_host,
                memory_timeout=memory_timeout,
                polish_md=polish_md,
                polish_md_model=polish_md_model,
                polish_md_host=polish_md_host,
                allow_remote_ollama=allow_remote_ollama,
            )
            results.append(result)
            bar.update()

    summary = CaptureBatchResult(source=str(target), vault=vault_root, items=results)
    if summary.failed:
        _progress.warn(
            f"capture batch complete with failures: {summary.succeeded}/{summary.total} succeeded"
        )
    elif not _events_enabled():
        _progress.done(f"captured {summary.total} items into {vault_root}")
    return summary


def batch_targets(target: str | Path) -> list[str]:
    """Resolve a capture batch source into concrete capture inputs."""
    path = Path(target).expanduser()
    if path.is_dir():
        from omd import cli
        from omd import _progress

        supported = cli.IMAGE_EXTS | cli.MARKITDOWN_EXTS | cli.AUDIO_EXTS
        video_files = [
            str(child)
            for child in sorted(path.iterdir())
            if child.is_file() and child.suffix.lower() in cli.VIDEO_EXTS
        ]
        if video_files:
            _progress.warn(
                "capture batch skipped local video files; paste supported video URLs or extract audio first: "
                + ", ".join(video_files[:3])
                + (" ..." if len(video_files) > 3 else "")
            )
        return [
            str(child)
            for child in sorted(path.iterdir())
            if child.is_file() and child.suffix.lower() in supported
        ]
    if path.is_file():
        from omd.batch import load_batch_items

        return load_batch_items(path)
    raise FileNotFoundError(f"Batch source must be a directory or batch list file: {target}")


def ensure_vault_layout(vault: str | Path) -> None:
    vault_root = Path(vault).expanduser()
    for name in ("Sources", "Index", "_attachments"):
        (vault_root / name).mkdir(parents=True, exist_ok=True)


def _capture_one_with_retries(
    target: str,
    vault: Path,
    *,
    lang: str,
    reel_extra: list[str],
    tags: list[str],
    agent_safe: bool,
    retries: int,
    memory_cards: bool,
    memory_model: str,
    memory_host: str,
    memory_timeout: float,
    polish_md: bool,
    polish_md_model: str | None,
    polish_md_host: str,
    allow_remote_ollama: bool,
) -> CaptureResult:
    last = capture_one(
        target,
        vault,
        lang=lang,
        reel_extra=reel_extra,
        tags=tags,
        agent_safe=agent_safe,
        memory_cards=memory_cards,
        memory_model=memory_model,
        memory_host=memory_host,
        memory_timeout=memory_timeout,
        polish_md=polish_md,
        polish_md_model=polish_md_model,
        polish_md_host=polish_md_host,
        allow_remote_ollama=allow_remote_ollama,
    )
    for _attempt in range(retries):
        if last.return_code == 0:
            return last
        last = capture_one(
            target,
            vault,
            lang=lang,
            reel_extra=reel_extra,
            tags=tags,
            agent_safe=agent_safe,
            memory_cards=memory_cards,
            memory_model=memory_model,
            memory_host=memory_host,
            memory_timeout=memory_timeout,
            polish_md=polish_md,
            polish_md_model=polish_md_model,
            polish_md_host=polish_md_host,
            allow_remote_ollama=allow_remote_ollama,
        )
    return last


def source_type_for(source: str, preflight: dict[str, object]) -> str:
    detected = str(preflight.get("detected_type") or "")
    metadata = preflight.get("metadata")
    url = ""
    path = ""
    if isinstance(metadata, dict):
        url = str(metadata.get("url") or "")
        path = str(metadata.get("path") or "")
    host = urlparse(url or source).netloc.lower()
    ext = str(preflight.get("extension") or Path(path or source).suffix).lower()

    if detected == "xhs_url":
        return "xiaohongshu"
    if detected == "douyin_url":
        return "douyin"
    if detected == "podcast_url":
        return "podcast"
    if detected == "wechat_article_url":
        return "wechat"
    if detected == "reddit_post_url":
        return "reddit"
    if detected == "x_post_url":
        return "xpost"
    if detected == "bluesky_post_url":
        return "bluesky"
    if detected == "mastodon_status_url":
        return "mastodon"
    if detected == "threads_post_url":
        return "threads"
    if detected == "hacker_news_item_url":
        return "hacker_news"
    if detected == "telegram_post_url":
        return "telegram"
    if detected == "reel_url":
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "tiktok.com" in host:
            return "tiktok"
        if "instagram.com" in host:
            return "instagram"
        if "bilibili.com" in host or "b23.tv" in host:
            return "bilibili"
        return "video"
    if detected == "generic_url":
        return "webpage"
    if detected == "image_file":
        return "image"
    if detected == "audio_file":
        return "audio"
    if detected == "document_file":
        if ext == ".pdf":
            return "pdf"
        return "office_doc"
    return detected.removesuffix("_file").removesuffix("_url") or "source"


def reserve_capture_path(vault: str | Path, source: str, source_type: str, *, now: datetime | None = None) -> Path:
    vault_root = Path(vault).expanduser()
    folder = SOURCE_FOLDERS.get(source_type, source_type.replace("_", " ").title())
    out_dir = vault_root / "Sources" / folder
    date_prefix = _date_slug(now)
    slug = slugify(_initial_title(source))
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    base = out_dir / f"{date_prefix}-{source_type}-{slug}-{digest}.md"
    return _dedupe_path(base)


def rename_capture_to_title(output_path: Path, vault: str | Path, *, title: str, source_type: str) -> Path:
    """Move a successful capture from its temporary route path to a user-facing title path."""
    vault_root = Path(vault).expanduser()
    folder = SOURCE_FOLDERS.get(source_type, source_type.replace("_", " ").title())
    out_dir = vault_root / "Sources" / folder
    base = out_dir / f"{safe_filename_stem(title)}.md"
    final_path = _dedupe_path(base) if base != output_path else output_path
    if final_path == output_path:
        return output_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    old_sidecar = output_path.with_suffix(".omd.json")
    final_sidecar = final_path.with_suffix(".omd.json")
    try:
        output_path.replace(final_path)
    except OSError:
        return output_path
    try:
        if old_sidecar.exists():
            old_sidecar.replace(final_sidecar)
    except OSError:
        pass
    return final_path


def capture_metadata(
    *,
    source: str,
    source_type: str,
    title: str,
    tags: list[str],
    captured_at: str,
    preflight: dict[str, object],
    reel_extra: list[str],
    output_path: str | Path | None = None,
    memory_cards: bool = False,
    memory_attempted: bool = False,
    memory_model: str | None = None,
    memory_host: str = "http://localhost:11434",
    generated_tags: list[str] | None = None,
    memory_warnings: list[str] | None = None,
    memory_error: str = "",
    markdown_polish_requested: bool = False,
    markdown_polish_model: str | None = None,
    markdown_polish_host: str = "http://localhost:11434",
) -> dict[str, object]:
    from omd._manifest import MANIFEST_VERSION, capture_id_for, source_hash_for, source_id_for

    memory_model = _resolve_memory_model(memory_model)
    metadata = preflight.get("metadata")
    url = ""
    path = ""
    if isinstance(metadata, dict):
        url = str(metadata.get("url") or "")
        if not url:
            path = str(metadata.get("path") or "")
    if not url and source.startswith(("http://", "https://")):
        url = source
    if not path and not url:
        path = source
    if path:
        path = str(Path(path).expanduser().resolve(strict=False))
    source_hash = source_hash_for(source)
    markdown_polish_model = (markdown_polish_model or "").strip() or recommended_local_text_model()
    fields: dict[str, object] = {
        "omd_version": __version__,
        "manifest_version": MANIFEST_VERSION,
        "source_id": source_id_for(source),
        "source_hash": source_hash,
        "capture_id": capture_id_for(source=source, output_path=output_path or source),
        "source_type": source_type,
        "captured_at": captured_at,
        "title": title,
        "privacy": "local_storage",
        "storage": "local",
        "network_fetch": bool(preflight.get("needs_network", False)),
        "model_endpoint": (
            _model_endpoint_for_memory(memory_attempted, memory_host)
            or (_model_endpoint_for_host(markdown_polish_host) if markdown_polish_requested else None)
            or _model_endpoint(reel_extra)
        ),
        "llm_used": (
            memory_model
            if memory_attempted
            else markdown_polish_model if markdown_polish_requested else _llm_used(reel_extra)
        ),
        "detected_type": preflight.get("detected_type"),
        "tags": normalize_tags(tags, source_type=source_type),
    }
    if memory_cards:
        fields["memory_attempted"] = True
        fields["memory_cards"] = True
        fields["memory_model"] = memory_model
        fields["summary_generated"] = True
        fields["generated_tags"] = list(generated_tags or [])
        if memory_warnings:
            fields["memory_warnings"] = list(memory_warnings)
    elif memory_attempted or memory_error:
        fields["memory_attempted"] = True
        fields["memory_cards"] = False
        fields["memory_model"] = memory_model
        fields["summary_generated"] = False
        if memory_error:
            fields["memory_error"] = memory_error
    if markdown_polish_requested:
        fields["markdown_polish_requested"] = True
        fields["markdown_polish_model"] = markdown_polish_model
    if url:
        fields["source_url"] = url
    if path:
        fields["local_source_path"] = path
    return fields


def format_frontmatter(metadata: dict[str, object]) -> str:
    lines = ["---"]
    for key in USER_FRONTMATTER_KEYS:
        if key not in metadata:
            continue
        value = metadata[key]
        if value in ("", [], None):
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(str(item))}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {_yaml_scalar(str(value))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def format_capture_body(
    metadata: dict[str, object],
    raw_markdown: str,
    *,
    memory_result: MemoryCardsResult | None = None,
) -> str:
    from omd.memory_cards import format_memory_sections

    title = str(metadata.get("title") or "Capture").strip() or "Capture"
    source_lines = []
    if metadata.get("source_url"):
        source_lines.append(f"[Source]({metadata.get('source_url')})")
    if metadata.get("local_source_path"):
        source_lines.append(f"Source: `{metadata.get('local_source_path')}`")
    if metadata.get("captured_at"):
        source_lines.append(f"Captured: `{metadata.get('captured_at')}`")
    source_note = " · ".join(source_lines)

    raw = raw_markdown.lstrip()
    if not raw:
        raw = "_No raw content was produced._\n"
    elif not raw.endswith("\n"):
        raw += "\n"
    if memory_result is not None:
        generated_sections = format_memory_sections(memory_result)
    else:
        generated_sections = ""
    intro = f"> {source_note}\n\n" if source_note else ""
    return (
        f"# {title}\n\n"
        + intro
        + generated_sections
        + "## Full Content\n\n"
        + raw
    )


def normalize_tags(tags: list[str], *, source_type: str) -> list[str]:
    normalized: list[str] = []
    for raw in tags:
        for item in str(raw).split(","):
            tag = _normalize_tag(item)
            if tag and tag not in normalized:
                normalized.append(tag)
    source_tag = _normalize_tag(source_type)
    if source_tag and source_tag not in normalized:
        normalized.append(source_tag)
    return normalized[:12]


def title_from_markdown(markdown: str) -> str | None:
    match = _HEADING_RE.search(markdown)
    if not match:
        return None
    return match.group(1).strip().strip("#").strip() or None


def clean_capture_title(value: str) -> str:
    title = str(value or "").replace("\\_", "_").replace("*", "")
    title = _TIMESTAMP_TITLE_RE.sub("", title)
    title = re.split(r"\s*[#＃]", title, maxsplit=1)[0]
    title = re.sub(r"[_-]+(?:audio|video|music)$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*#{1,2}[\w\u4e00-\u9fff-]+", "", title)
    title = title.replace("_", " ")
    title = re.sub(r"\.{3,}", "…", title)
    title = re.sub(r"\s+", " ", title).strip(" -_·|…[")
    return title[:120].strip() or "Capture"


def normalize_captured_markdown(markdown: str) -> str:
    """Repair converter artifacts that can break CommonMark rendering.

    The pass is intentionally structural and deterministic. It never rewrites
    prose: it removes a leaked HTML doctype, strips Sphinx heading permalinks,
    and fences terminal progress output so sequences such as ``<?`` cannot
    turn the remainder of an Obsidian note into a raw HTML block.
    """
    lines = markdown.splitlines()
    normalized: list[str] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if fence is not None:
            normalized.append(line)
            if stripped.startswith(fence):
                fence = None
            index += 1
            continue
        if stripped.startswith("```"):
            fence = "```"
            normalized.append(line)
            index += 1
            continue
        if stripped.startswith("~~~"):
            fence = "~~~"
            normalized.append(line)
            index += 1
            continue
        if _DOCTYPE_LINE_RE.match(line):
            index += 1
            continue
        heading = _SPHINX_HEADING_LINK_RE.match(line)
        if heading:
            normalized.append(heading.group("heading").rstrip())
            index += 1
            continue
        if _TERMINAL_PROGRESS_RE.match(line):
            normalized.append("```text")
            while index < len(lines) and _TERMINAL_PROGRESS_RE.match(lines[index]):
                normalized.append(lines[index])
                index += 1
            normalized.append("```")
            continue
        normalized.append(line)
        index += 1
    result = "\n".join(normalized).strip("\n")
    return result + ("\n" if result else "")


def update_index(vault: str | Path, *, output_path: Path, metadata: dict[str, object]) -> Path:
    vault_root = Path(vault).expanduser()
    idx = index_path(vault_root)
    rel = output_path.relative_to(vault_root)
    line = (
        f"- {metadata.get('captured_at')} "
        f"[[{rel.with_suffix('').as_posix()}|{metadata.get('title')}]] "
        f"`{metadata.get('source_type')}`"
    )
    existing = idx.read_text(encoding="utf-8") if idx.exists() else "# OMD Captures\n\n"
    if line not in existing.splitlines():
        if not existing.endswith("\n"):
            existing += "\n"
        existing += line + "\n"
    write_atomic(idx, existing)
    return idx


def index_path(vault: str | Path) -> Path:
    return Path(vault).expanduser() / "Index" / "OMD Captures.md"


def slugify(value: str) -> str:
    return safe_filename_stem(clean_capture_title(value)).replace(" ", "-")


def safe_filename_stem(value: str) -> str:
    stem = clean_capture_title(value)
    stem = _UNSAFE_FILENAME_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .-_")
    return stem[:80].strip(" .-_") or "Capture"


def _normalize_tag(value: str) -> str:
    tag = str(value).strip().lstrip("#").replace("_", "-").replace(" ", "-").lower()
    tag = re.sub(r"[^a-z0-9\u4e00-\u9fff-]+", "-", tag).strip("-")
    return tag


def _refresh_manifest(
    output_path: Path,
    *,
    source: str,
    preflight: dict[str, object],
    metadata: dict[str, object],
) -> None:
    from omd._manifest import write_manifest_for_output
    from omd import _progress
    from omd._transcript import transcript_warnings_from_markdown

    try:
        warnings = [str(w) for w in preflight.get("warnings", [])]
        markdown = output_path.read_text(encoding="utf-8", errors="replace")
        for warning in transcript_warnings_from_markdown(markdown):
            if warning not in warnings:
                warnings.append(warning)
        write_manifest_for_output(
            output_path,
            source=source,
            backend=str(preflight.get("probable_backend") or "unknown"),
            untrusted=bool(preflight.get("untrusted", True)),
            warnings=warnings,
            metadata={
                "capture": metadata,
                "detected_type": preflight.get("detected_type"),
                "needs_network": preflight.get("needs_network"),
                "needs_cookies": preflight.get("needs_cookies"),
                "needs_tools": preflight.get("needs_tools"),
                "risks": preflight.get("risks"),
            },
        )
    except OSError as exc:
        _progress.warn(f"could not refresh capture manifest for {output_path}: {exc}")


def _strip_frontmatter(markdown: str) -> str:
    return _FRONTMATTER_RE.sub("", markdown, count=1)


def _dedupe_path(base: Path) -> Path:
    candidate = base
    index = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}-{index}{base.suffix}")
        index += 1
    return candidate


def _cleanup_failed_capture(output_path: Path) -> None:
    for path in (output_path, output_path.with_suffix(".omd.json")):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _events_enabled() -> bool:
    from omd import _events

    return _events.is_enabled()


def _initial_title(source: str) -> str:
    if source.startswith(("http://", "https://")):
        parsed = urlparse(source)
        path_bits = [part for part in parsed.path.split("/") if part]
        if path_bits:
            return path_bits[-1]
        return parsed.netloc or "capture"
    return Path(source).stem or "capture"


def _yaml_scalar(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _date_slug(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _llm_used(reel_extra: list[str]) -> str:
    recommended_model = recommended_local_text_model()
    for index, item in enumerate(reel_extra):
        if item == "--polish":
            if index + 1 < len(reel_extra) and not reel_extra[index + 1].startswith("-"):
                return reel_extra[index + 1]
            return recommended_model
        if item.startswith("--polish="):
            return item.split("=", 1)[1] or recommended_model
    return "none"


def _resolve_memory_model(model: str | None) -> str:
    return (model or "").strip() or recommended_local_text_model()


def _model_endpoint(reel_extra: list[str]) -> str:
    if _llm_used(reel_extra) == "none":
        return "none"
    host = _option_value(reel_extra, "--ollama-host") or "http://localhost:11434"
    return _model_endpoint_for_host(host)


def _model_endpoint_for_memory(memory_cards: bool, host: str) -> str | None:
    if not memory_cards:
        return None
    return _model_endpoint_for_host(host)


def _model_endpoint_for_host(host: str) -> str:
    from omd._network_policy import validate_ollama_host

    try:
        validate_ollama_host(host)
    except ValueError:
        return "remote_ollama"
    return "local_ollama"


def _option_value(args: list[str], option: str) -> str | None:
    for index, item in enumerate(args):
        if item == option and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(option + "="):
            return item.split("=", 1)[1]
    return None
