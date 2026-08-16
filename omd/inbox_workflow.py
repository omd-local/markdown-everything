"""Obsidian-first Inbox persistence and explicit review transitions."""
from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ._io import write_atomic
from ._json_contract import canonical_json, json_object
from .inbox import InboxItem
from .knowledge_note import KnowledgeNote


_REVIEW_STATUSES = frozenset({"inbox", "accepted", "rejected"})


@dataclass(frozen=True)
class InboxSummary:
    item_id: str
    title: str
    captured_at: str
    review_status: str
    path: str


@dataclass(frozen=True, init=False)
class InboxReviewItem:
    item_id: str
    capture_surface: str
    provenance_kind: str
    title: str
    raw_content: str
    captured_at: str
    review_status: str
    path: str
    _source_locator_json: str = field(repr=False)

    def __init__(
        self,
        *,
        item_id: str,
        capture_surface: str,
        provenance_kind: str,
        title: str,
        raw_content: str,
        source_locator: Mapping[str, Any],
        captured_at: str,
        review_status: str,
        path: str,
    ) -> None:
        values = {
            "item_id": item_id,
            "capture_surface": capture_surface,
            "provenance_kind": provenance_kind,
            "title": title,
            "raw_content": raw_content,
            "captured_at": captured_at,
            "review_status": review_status,
            "path": path,
            "_source_locator_json": canonical_json(
                json_object(source_locator, name="source_locator")
            ),
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def source_locator(self) -> dict[str, Any]:
        return json.loads(self._source_locator_json)


def save_inbox_item(vault: str | Path, item: InboxItem) -> Path:
    root = _vault_root(vault)
    inbox = _safe_child_directory(root, "Inbox")
    stem = _item_stem(item)
    markdown_path = inbox / f"{stem}.md"
    sidecar_path = markdown_path.with_suffix(".omd.json")
    if sidecar_path.exists():
        existing = _read_sidecar(sidecar_path)
        if existing.get("item_id") != item.item_id:
            raise ValueError(f"conflicting Inbox item at {sidecar_path.name}")
        if not markdown_path.exists():
            write_atomic(markdown_path, _inbox_markdown(item))
        return markdown_path
    payload = item.to_dict()
    payload["source_locator"] = _public_locator(item.source_locator)
    payload["review_status"] = "inbox"
    payload["record_hash"] = _record_hash(payload)
    write_atomic(
        sidecar_path,
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    try:
        write_atomic(markdown_path, _inbox_markdown(item))
    except BaseException:
        sidecar_path.unlink(missing_ok=True)
        raise
    return markdown_path


def list_inbox_items(vault: str | Path) -> list[InboxSummary]:
    root = _vault_root(vault)
    inbox = _safe_child_directory(root, "Inbox")
    summaries: list[InboxSummary] = []
    for sidecar in sorted(inbox.glob("*.omd.json")):
        payload = _read_sidecar(sidecar)
        _validate_record(payload, sidecar)
        summaries.append(
            InboxSummary(
                item_id=str(payload["item_id"]),
                title=str(payload["title"]),
                captured_at=str(payload["captured_at"]),
                review_status=str(payload.get("review_status", "inbox")),
                path=sidecar.with_suffix("").with_suffix(".md").relative_to(root).as_posix(),
            )
        )
    return sorted(summaries, key=lambda entry: (entry.captured_at, entry.item_id), reverse=True)


def load_inbox_item(vault: str | Path, item_id: str) -> InboxReviewItem:
    root = _vault_root(vault)
    sidecar = _sidecar_for_item(root, item_id)
    payload = _read_sidecar(sidecar)
    _validate_record(payload, sidecar)
    locator = payload.get("source_locator")
    if not isinstance(locator, dict):
        raise ValueError(f"invalid Inbox source locator: {sidecar.name}")
    return InboxReviewItem(
        item_id=str(payload["item_id"]),
        capture_surface=str(payload["capture_surface"]),
        provenance_kind=str(payload["provenance_kind"]),
        title=str(payload["title"]),
        raw_content=str(payload["raw_content"]),
        source_locator=dict(locator),
        captured_at=str(payload["captured_at"]),
        review_status=str(payload.get("review_status", "inbox")),
        path=sidecar.with_suffix("").with_suffix(".md").relative_to(root).as_posix(),
    )


def promote_inbox_item(
    vault: str | Path,
    item_id: str,
    *,
    highlights: Sequence[str] = (),
    my_notes: Sequence[str] = (),
    ai_suggestions: Sequence[str] = (),
    linked_source_path: str | None = None,
    tags: Sequence[str] = (),
) -> Path:
    root = _vault_root(vault)
    sidecar = _sidecar_for_item(root, item_id)
    payload = _read_sidecar(sidecar)
    _validate_record(payload, sidecar)
    item_id_value = str(payload["item_id"])
    title = str(payload["title"])
    raw_content = str(payload["raw_content"])
    capture_surface = str(payload["capture_surface"])
    linked_source = _validated_linked_source(root, linked_source_path)
    source = {
        "kind": _knowledge_source_kind(capture_surface),
        "title": title,
        "raw_text": raw_content,
    }
    if linked_source:
        source["linked_markdown_path"] = linked_source
    note = KnowledgeNote(
        source=source,
        highlights=highlights,
        my_notes=my_notes,
        ai_suggestions=ai_suggestions,
        tags=tags,
        derived_from=item_id_value,
    )
    notes = _safe_child_directory(root, "Notes")
    stem = f"{_slug(title)}-{item_id_value}"
    output = notes / f"{stem}.md"
    output_sidecar = output.with_suffix(".omd.json")
    created_sidecar = False
    if output.exists() and not output_sidecar.exists():
        raise ValueError(f"reviewed note sidecar is missing: {output.name}")
    if output_sidecar.exists():
        existing = _read_sidecar(output_sidecar)
        if existing.get("derived_from") != item_id_value:
            raise ValueError(f"reviewed note path already exists: {output.name}")
        try:
            existing_note = KnowledgeNote.from_dict(existing)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid reviewed note sidecar: {output_sidecar.name}") from exc
        if existing_note != note:
            raise ValueError(
                "a reviewed note already exists with different content; "
                "current review edits were not applied"
            )
        if output.exists():
            if output.is_symlink() or not output.is_file():
                raise ValueError(f"reviewed note must be a regular non-symlink file: {output.name}")
            try:
                existing_markdown = output.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"could not verify reviewed note: {output.name}") from exc
            if existing_markdown != _knowledge_markdown(existing_note):
                raise ValueError(
                    f"reviewed note content does not match its sidecar: {output.name}"
                )
            set_review_status(root, item_id_value, "accepted")
            return output
    else:
        write_atomic(output_sidecar, note.to_json())
        created_sidecar = True
    try:
        write_atomic(output, _knowledge_markdown(note))
        set_review_status(root, item_id_value, "accepted")
    except BaseException:
        output.unlink(missing_ok=True)
        if created_sidecar:
            output_sidecar.unlink(missing_ok=True)
        raise
    return output


def set_review_status(vault: str | Path, item_id: str, status: str) -> None:
    if status not in _REVIEW_STATUSES:
        raise ValueError("status must be inbox, accepted, or rejected")
    root = _vault_root(vault)
    sidecar = _sidecar_for_item(root, item_id)
    payload = _read_sidecar(sidecar)
    _validate_record(payload, sidecar)
    payload["review_status"] = status
    write_atomic(sidecar, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _vault_root(vault: str | Path) -> Path:
    root = Path(vault).expanduser()
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise ValueError("vault must be an existing non-symlink directory")
    return root.resolve(strict=True)


def _safe_child_directory(root: Path, name: str) -> Path:
    child = root / name
    if child.is_symlink():
        raise ValueError(f"{name} directory must not be a symlink")
    child.mkdir(parents=True, exist_ok=True)
    resolved = child.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{name} directory resolves outside vault") from exc
    return resolved


def _item_stem(item: InboxItem) -> str:
    date = item.captured_at[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.captured_at[:10]) else "undated"
    return f"{date}-{_slug(item.title)}-{item.item_id[-8:]}"


def _slug(title: str) -> str:
    value = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-").lower()
    return (value or "inbox-item")[:56].rstrip("-")


def _inbox_markdown(item: InboxItem) -> str:
    return (
        "---\n"
        "omd_type: inbox\n"
        f'item_id: "{item.item_id}"\n'
        f'capture_surface: "{item.capture_surface}"\n'
        f'provenance: "{item.provenance_kind}"\n'
        f'captured_at: "{item.captured_at}"\n'
        "---\n\n"
        f"# {item.title}\n\n{item.raw_content}\n"
    )


def _sidecar_for_item(root: Path, item_id: str) -> Path:
    inbox = _safe_child_directory(root, "Inbox")
    matches = []
    for sidecar in inbox.glob("*.omd.json"):
        payload = _read_sidecar(sidecar)
        if payload.get("item_id") == item_id:
            matches.append(sidecar)
    if len(matches) != 1:
        raise ValueError(f"expected one Inbox item for {item_id}; found {len(matches)}")
    return matches[0]


def _read_sidecar(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f"Inbox sidecar must not be a symlink: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Inbox sidecar: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid Inbox sidecar: {path.name}")
    return payload


def _record_hash(payload: dict) -> str:
    public_record = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "item_id",
            "capture_surface",
            "provenance_kind",
            "title",
            "raw_content",
            "source_locator",
            "captured_at",
        )
    }
    encoded = json.dumps(
        public_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_record(payload: dict, path: Path) -> None:
    expected = payload.get("record_hash")
    if expected is None:
        return
    if not isinstance(expected, str) or expected != _record_hash(payload):
        raise ValueError(f"Inbox record integrity check failed: {path.name}")


def _knowledge_source_kind(capture_surface: str) -> str:
    return {
        "my_note": "personal_note",
        "highlight": "highlight",
        "voice_memo": "voice_memo",
        "import": "imported_source",
    }[capture_surface]


def _public_locator(locator: dict) -> dict:
    return {
        key: value
        for key, value in locator.items()
        if key in {"kind", "url", "selector", "timestamp", "page"}
    }


def _validated_linked_source(root: Path, value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    from .retrieval import validate_vault_markdown_path

    relative = Path(value.strip()).as_posix()
    validate_vault_markdown_path(root, relative)
    return relative


def _knowledge_markdown(note: KnowledgeNote) -> str:
    source = note.source
    sections: list[str] = []
    if note.tags:
        sections.extend(
            [
                "---",
                "tags:",
                *[f"  - {json.dumps(value, ensure_ascii=False)}" for value in note.tags],
                "---",
                "",
            ]
        )
    sections.extend([f"# {source['title']}", "", source["raw_text"]])
    linked_source = source.get("linked_markdown_path")
    if isinstance(linked_source, str) and linked_source:
        target = linked_source[:-3] if linked_source.lower().endswith(".md") else linked_source
        target = target.replace("|", "\\|").replace("]", "\\]")
        sections.extend(["", "## Linked source", "", f"[[{target}]]"])
    if note.highlights:
        sections.extend(["", "## Highlights", "", *[f"> {value}" for value in note.highlights]])
    if note.my_notes:
        sections.extend(["", "## My Notes", "", *note.my_notes])
    if note.ai_suggestions:
        sections.extend(["", "## AI Suggestions (review required)", "", *note.ai_suggestions])
    return "\n".join(sections).rstrip() + "\n"
