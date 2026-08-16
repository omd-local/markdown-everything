"""Canonical, raw-preserving note contract for Phase 2 workflows."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ._json_contract import canonical_json, json_object


KNOWLEDGE_NOTE_SCHEMA_VERSION = 1


@dataclass(frozen=True, init=False)
class KnowledgeNote:
    """Immutable separation between source, user-authored, and AI content."""

    schema_version: int
    note_id: str
    derived_from: str | None
    _source_json: str = field(repr=False)
    _highlights: tuple[str, ...] = field(repr=False)
    _my_notes: tuple[str, ...] = field(repr=False)
    _ai_suggestions: tuple[str, ...] = field(repr=False)
    _tags: tuple[str, ...] = field(repr=False)

    def __init__(
        self,
        *,
        source: Mapping[str, Any],
        highlights: Sequence[str],
        my_notes: Sequence[str],
        ai_suggestions: Sequence[str],
        tags: Sequence[str] = (),
        note_id: str | None = None,
        derived_from: str | None = None,
        schema_version: int = KNOWLEDGE_NOTE_SCHEMA_VERSION,
    ) -> None:
        if schema_version != KNOWLEDGE_NOTE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")

        source_data = json_object(source, name="source")
        _validate_source(source_data)
        normalized_highlights = _string_sequence(highlights, name="highlights")
        normalized_my_notes = _string_sequence(my_notes, name="my_notes")
        normalized_suggestions = _string_sequence(ai_suggestions, name="ai_suggestions")
        normalized_tags = _string_sequence(tags, name="tags")
        source_json = canonical_json(source_data)
        normalized_derived_from = _optional_string(derived_from, name="derived_from")
        expected_id = _note_id_for(source_json=source_json)
        if note_id is not None and note_id != expected_id:
            raise ValueError("note_id does not match the note payload")

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "note_id", expected_id)
        object.__setattr__(self, "derived_from", normalized_derived_from)
        object.__setattr__(self, "_source_json", source_json)
        object.__setattr__(self, "_highlights", normalized_highlights)
        object.__setattr__(self, "_my_notes", normalized_my_notes)
        object.__setattr__(self, "_ai_suggestions", normalized_suggestions)
        object.__setattr__(self, "_tags", normalized_tags)

    @property
    def source(self) -> dict[str, Any]:
        return json.loads(self._source_json)

    @property
    def highlights(self) -> list[str]:
        return list(self._highlights)

    @property
    def my_notes(self) -> list[str]:
        return list(self._my_notes)

    @property
    def ai_suggestions(self) -> list[str]:
        return list(self._ai_suggestions)

    @property
    def tags(self) -> list[str]:
        return list(self._tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "note_id": self.note_id,
            "derived_from": self.derived_from,
            "source": self.source,
            "highlights": self.highlights,
            "my_notes": self.my_notes,
            "ai_suggestions": self.ai_suggestions,
            "tags": self.tags,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> KnowledgeNote:
        payload = json_object(data, name="knowledge_note")
        return cls(
            source=payload.get("source"),
            highlights=payload.get("highlights", []),
            my_notes=payload.get("my_notes", []),
            ai_suggestions=payload.get("ai_suggestions", []),
            tags=payload.get("tags", []),
            note_id=payload.get("note_id"),
            derived_from=payload.get("derived_from"),
            schema_version=payload.get("schema_version", KNOWLEDGE_NOTE_SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, value: str) -> KnowledgeNote:
        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid KnowledgeNote JSON: {exc}") from exc
        return cls.from_dict(data)


def _validate_source(source: dict[str, Any]) -> None:
    kind = _required_string(source.get("kind"), name="source.kind")
    allowed_kinds = {
        "webpage",
        "local_file",
        "personal_note",
        "highlight",
        "voice_memo",
        "imported_source",
    }
    if kind not in allowed_kinds:
        raise ValueError(f"source.kind must be one of: {', '.join(sorted(allowed_kinds))}")
    _required_string(source.get("title"), name="source.title")
    if not isinstance(source.get("raw_text"), str):
        raise ValueError("source.raw_text must be a string")
    if kind == "webpage":
        _required_string(source.get("url"), name="source.url")
    elif kind == "local_file":
        _required_string(source.get("path"), name="source.path")


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name=name)


def _string_sequence(value: object, *, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a sequence of strings")
    return tuple(value)


def _note_id_for(*, source_json: str) -> str:
    return "kn_" + hashlib.sha256(source_json.encode("utf-8")).hexdigest()[:16]
