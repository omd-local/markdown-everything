"""Sidecar manifest helpers for Markdown outputs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from omd._io import write_atomic
from omd._source_url import sanitize_persisted_source_text, sanitize_persisted_source_url

MANIFEST_VERSION = 2
_URL_RE = re.compile(r"https?://[^\s<>'\"）)]+")
_ORDERED_LIST_RE = re.compile(r"^\d+[.)]\s+")
_UNORDERED_LIST_RE = re.compile(r"^[-*+]\s+")


def manifest_path_for_output(output_path: str | Path) -> Path:
    """Return the `.omd.json` sidecar path for a Markdown output."""
    output = Path(output_path)
    if output.suffix.lower() == ".md":
        return output.with_suffix(".omd.json")
    return output.with_name(output.name + ".omd.json")


def write_manifest_for_output(
    output_path: str | Path,
    *,
    source: str,
    backend: str,
    transcript_language: str | None = None,
    recognition: Mapping[str, Any] | None = None,
    untrusted: bool = False,
    warnings: Sequence[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write a JSON sidecar manifest for a generated Markdown file.

    The output file must already exist. Rewrites preserve capture identity and
    recognition metadata only when the previous sidecar names the same
    canonical source.
    """
    output = Path(output_path)
    content = output.read_bytes()
    manifest_path = manifest_path_for_output(output)
    timestamp = _normalize_timestamp(now)
    existing = _read_manifest(manifest_path)
    canonical_source = canonical_source_for(source)
    existing_source = existing.get("source")
    same_source = (
        isinstance(existing_source, str)
        and canonical_source_for(existing_source) == canonical_source
    )
    created_at = (
        existing.get("created_at")
        if same_source and isinstance(existing.get("created_at"), str)
        else timestamp
    )
    content_checksum = hashlib.sha256(content).hexdigest()
    source_hash = source_hash_for(canonical_source)
    existing_output = existing.get("output")
    preserve_capture_id = same_source and (
        not isinstance(existing_output, str) or existing_output == str(output)
    )
    capture_id = (
        existing.get("capture_id")
        if preserve_capture_id and isinstance(existing.get("capture_id"), str)
        else capture_id_for(source=canonical_source, output_path=output)
    )
    effective_transcript_language = transcript_language
    if (
        effective_transcript_language is None
        and same_source
        and isinstance(existing.get("transcript_language"), str)
    ):
        effective_transcript_language = existing["transcript_language"]
    recognition_payload = _recognition_payload(
        recognition
        if recognition is not None
        else existing.get("recognition") if same_source else None,
        transcript_language=effective_transcript_language,
    )
    raw_source_fields = _raw_source_fields(source, canonical_source)
    if (
        same_source
        and not raw_source_fields
        and isinstance(existing.get("raw_source"), str)
    ):
        raw_source_fields = _raw_source_fields(existing["raw_source"], canonical_source)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "source_id": source_id_for(canonical_source),
        "source_hash": source_hash,
        "capture_id": capture_id,
        "source": canonical_source,
        **raw_source_fields,
        **_source_locator_fields(canonical_source),
        "output": str(output),
        "backend": backend,
        "created_at": created_at,
        "updated_at": timestamp,
        "content_checksum": content_checksum,
        "checksum": content_checksum,
        "transcript_language": effective_transcript_language,
        "recognition": recognition_payload,
        "untrusted": bool(untrusted),
        "warnings": [str(item) for item in (warnings or [])],
        "elements": elements_for_markdown(content.decode("utf-8", errors="replace"), source_ref=canonical_source),
        "metadata": _jsonable_mapping(metadata or {}),
    }
    write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def _recognition_payload(
    value: object,
    *,
    transcript_language: str | None,
) -> dict[str, Any]:
    """Return the additive recognition contract, preserving future fields."""
    payload = _jsonable(value) if isinstance(value, Mapping) else {}
    if not isinstance(payload, dict):
        payload = {}

    ocr = payload.get("ocr")
    if not isinstance(ocr, dict):
        ocr = {}
    ocr.setdefault("requested", None)
    ocr.setdefault("effective", None)
    ocr.setdefault("source", None)

    asr = payload.get("asr")
    if not isinstance(asr, dict):
        asr = {}
    asr.setdefault("requested", None)
    asr.setdefault("detected", None)
    if transcript_language is not None:
        asr["detected"] = transcript_language

    generation = payload.get("generation")
    if not isinstance(generation, dict):
        generation = {}
    generation["language_policy"] = "same-as-source"

    # These fields describe observed content, not the requested OCR/ASR route.
    # Keep them additive so a legacy sidecar gains a stable schema on refresh,
    # while values reported by a recognizer retain their provenance.
    payload.setdefault("content_languages_detected", [])
    payload.setdefault("detected_script", None)
    payload.setdefault("confidence", None)
    payload["ocr"] = ocr
    payload["asr"] = asr
    payload["generation"] = generation
    return payload


def canonical_source_for(source: str) -> str:
    value = str(source).strip()
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return sanitize_persisted_source_url(value)
    match = _URL_RE.search(value)
    if match:
        return sanitize_persisted_source_url(match.group(0).rstrip(".,;:!?"))
    return str(Path(value).expanduser().resolve(strict=False))


def source_hash_for(source: str) -> str:
    return hashlib.sha256(canonical_source_for(source).encode("utf-8")).hexdigest()


def source_id_for(source: str) -> str:
    return "src_" + source_hash_for(source)[:16]


def capture_id_for(*, source: str, output_path: str | Path) -> str:
    basis = f"{source_hash_for(source)}:{Path(output_path).expanduser().resolve(strict=False)}"
    return "cap_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def elements_for_markdown(markdown: str, *, source_ref: str) -> list[dict[str, Any]]:
    """Build a conservative line-level element skeleton for RAG ingestion."""
    lines = markdown.splitlines()
    elements: list[dict[str, Any]] = []
    index = _content_start_line_index(lines)
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        start = index + 1
        element_type = _element_type_for_line(stripped)
        end = start
        if element_type in {"paragraph", "list", "table"}:
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor].strip()
                if not candidate:
                    break
                if _element_type_for_line(candidate) != element_type:
                    break
                end = cursor + 1
                cursor += 1
            index = cursor
        else:
            index += 1
        elements.append(
            {
                "id": f"el_{len(elements) + 1:04d}",
                "type": element_type,
                "markdown_start_line": start,
                "markdown_end_line": end,
                "page_number": None,
                "timestamp_start": None,
                "timestamp_end": None,
                "source_ref": source_ref,
            }
        )
    return elements


def _content_start_line_index(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def _source_locator_fields(source: str) -> dict[str, str]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return {"source_url": source}
    return {"local_source_path": str(Path(source).expanduser().resolve(strict=False))}


def _raw_source_fields(raw_source: str, canonical_source: str) -> dict[str, str]:
    safe_raw_source = _safe_raw_source(raw_source)
    if safe_raw_source == canonical_source:
        return {}
    return {"raw_source": safe_raw_source}


def _safe_raw_source(raw_source: str) -> str:
    """Preserve share text while replacing any access-bearing source URL."""
    return sanitize_persisted_source_text(raw_source)


def _element_type_for_line(stripped: str) -> str:
    if stripped.startswith("#"):
        return "title"
    if stripped.startswith("![") or stripped.startswith("<img"):
        return "image"
    if _UNORDERED_LIST_RE.match(stripped) or _ORDERED_LIST_RE.match(stripped):
        return "list"
    if _looks_like_table_row(stripped):
        return "table"
    return "paragraph"


def _looks_like_table_row(stripped: str) -> bool:
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _normalize_timestamp(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def _jsonable_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _jsonable(value) for key, value in data.items()}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return _jsonable_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)
