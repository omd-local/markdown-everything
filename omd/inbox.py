"""Versioned, immutable envelopes for desktop and mobile inbox handoff."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ._json_contract import canonical_json, json_object


INBOX_SCHEMA_VERSION = 1
INBOX_ITEM_SCHEMA_VERSION = 1
CAPTURE_SURFACES = frozenset({"my_note", "highlight", "voice_memo", "import"})
PROVENANCE_KINDS = frozenset({"authored", "excerpt", "audio", "imported"})


@dataclass(frozen=True, init=False)
class InboxItem:
    """Immutable source-of-truth record captured before organization."""

    schema_version: int
    item_id: str
    capture_surface: str
    provenance_kind: str
    title: str
    raw_content: str
    captured_at: str
    _source_locator_json: str = field(repr=False)

    def __init__(
        self,
        *,
        capture_surface: str,
        provenance_kind: str,
        title: str,
        raw_content: str,
        source_locator: Mapping[str, Any],
        captured_at: str | None = None,
        item_id: str | None = None,
        schema_version: int = INBOX_ITEM_SCHEMA_VERSION,
    ) -> None:
        if schema_version != INBOX_ITEM_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")
        surface = _choice(capture_surface, name="capture_surface", allowed=CAPTURE_SURFACES)
        provenance = _choice(provenance_kind, name="provenance_kind", allowed=PROVENANCE_KINDS)
        normalized_title = _required_string(title, name="title")
        if not isinstance(raw_content, str):
            raise ValueError("raw_content must be a string")
        locator_json = canonical_json(json_object(source_locator, name="source_locator"))
        normalized_captured_at = _timestamp(captured_at)
        expected_id = _item_id_for(
            capture_surface=surface,
            provenance_kind=provenance,
            title=normalized_title,
            raw_content=raw_content,
            source_locator_json=locator_json,
            captured_at=normalized_captured_at,
        )
        if item_id is not None and item_id != expected_id:
            raise ValueError("item_id does not match the inbox item")

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "item_id", expected_id)
        object.__setattr__(self, "capture_surface", surface)
        object.__setattr__(self, "provenance_kind", provenance)
        object.__setattr__(self, "title", normalized_title)
        object.__setattr__(self, "raw_content", raw_content)
        object.__setattr__(self, "captured_at", normalized_captured_at)
        object.__setattr__(self, "_source_locator_json", locator_json)

    @property
    def source_locator(self) -> dict[str, Any]:
        return json.loads(self._source_locator_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "item_id": self.item_id,
            "capture_surface": self.capture_surface,
            "provenance_kind": self.provenance_kind,
            "title": self.title,
            "raw_content": self.raw_content,
            "source_locator": self.source_locator,
            "captured_at": self.captured_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InboxItem:
        item = json_object(data, name="inbox_item")
        return cls(
            capture_surface=item.get("capture_surface"),
            provenance_kind=item.get("provenance_kind"),
            title=item.get("title"),
            raw_content=item.get("raw_content"),
            source_locator=item.get("source_locator"),
            captured_at=item.get("captured_at"),
            item_id=item.get("item_id"),
            schema_version=item.get("schema_version", INBOX_ITEM_SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, value: str) -> InboxItem:
        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid InboxItem JSON: {exc}") from exc
        return cls.from_dict(data)


@dataclass(frozen=True, init=False)
class InboxJob:
    """Content-addressed job envelope that preserves its original payload."""

    schema_version: int
    job_id: str
    job_type: str
    source: str
    created_at: str
    _payload_json: str = field(repr=False)

    def __init__(
        self,
        *,
        job_type: str,
        payload: Mapping[str, Any],
        source: str,
        created_at: str | None = None,
        job_id: str | None = None,
        schema_version: int = INBOX_SCHEMA_VERSION,
    ) -> None:
        if schema_version != INBOX_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")
        normalized_job_type = _required_string(job_type, name="job_type")
        normalized_source = _required_string(source, name="source")
        payload_data = json_object(payload, name="payload")
        payload_json = canonical_json(payload_data)
        normalized_created_at = _timestamp(created_at)
        expected_id = _job_id_for(
            job_type=normalized_job_type,
            source=normalized_source,
            payload_json=payload_json,
        )
        if job_id is not None and job_id != expected_id:
            raise ValueError("job_id does not match the inbox payload")

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "job_id", expected_id)
        object.__setattr__(self, "job_type", normalized_job_type)
        object.__setattr__(self, "source", normalized_source)
        object.__setattr__(self, "created_at", normalized_created_at)
        object.__setattr__(self, "_payload_json", payload_json)

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._payload_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "job_type": self.job_type,
            "source": self.source,
            "created_at": self.created_at,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InboxJob:
        envelope = json_object(data, name="inbox_job")
        return cls(
            job_type=envelope.get("job_type"),
            payload=envelope.get("payload"),
            source=envelope.get("source"),
            created_at=envelope.get("created_at"),
            job_id=envelope.get("job_id"),
            schema_version=envelope.get("schema_version", INBOX_SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, value: str) -> InboxJob:
        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid InboxJob JSON: {exc}") from exc
        return cls.from_dict(data)


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    timestamp = _required_string(value, name="created_at")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    return timestamp


def _choice(value: object, *, name: str, allowed: frozenset[str]) -> str:
    normalized = _required_string(value, name=name)
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _job_id_for(*, job_type: str, source: str, payload_json: str) -> str:
    basis = canonical_json(
        {
            "job_type": job_type,
            "source": source,
            "payload": json.loads(payload_json),
        }
    )
    return "job_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _item_id_for(
    *,
    capture_surface: str,
    provenance_kind: str,
    title: str,
    raw_content: str,
    source_locator_json: str,
    captured_at: str,
) -> str:
    basis = canonical_json(
        {
            "capture_surface": capture_surface,
            "provenance_kind": provenance_kind,
            "title": title,
            "raw_content": raw_content,
            "source_locator": json.loads(source_locator_json),
            "captured_at": captured_at,
        }
    )
    return "inbox_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
