"""Local, explicit, resettable preference signals for note organization."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._json_contract import canonical_json, json_object


PREFERENCE_SCHEMA_VERSION = 1
_ACTIONS = frozenset({"accept", "reject", "edit"})
_SIGNAL_KINDS = frozenset({"tag", "output_style", "note_length", "link_style", "source_type"})
_CAPTURE_SOURCE_TYPES = frozenset(
    {
        "audio",
        "bilibili",
        "bluesky",
        "douyin",
        "hacker_news",
        "image",
        "instagram",
        "mastodon",
        "office_doc",
        "pdf",
        "podcast",
        "reddit",
        "telegram",
        "threads",
        "tiktok",
        "video",
        "webpage",
        "wechat",
        "xiaohongshu",
        "xpost",
        "youtube",
    }
)
_CONTROLLED_VALUES = {
    "output_style": frozenset({"bullet_list", "short_paragraph", "outline"}),
    "note_length": frozenset({"compact", "standard", "detailed"}),
    "link_style": frozenset({"wiki", "inline", "both"}),
    # Preserve the legacy local-file value while accepting every emitted capture type.
    "source_type": _CAPTURE_SOURCE_TYPES | {"local_file"},
}
_TAG_RE = re.compile(r"^[\w][\w +.-]{0,31}$", re.UNICODE)
_RESERVED_TAG_MARKERS = ("cookie", "browser_", "token", "secret")


@dataclass(frozen=True, init=False)
class PreferenceProfile:
    """A compact ranking profile derived only from explicit user feedback."""

    schema_version: int
    _signals_json: str = field(repr=False)

    def __init__(
        self,
        *,
        signals: Mapping[str, Mapping[str, int]] | None = None,
        schema_version: int = PREFERENCE_SCHEMA_VERSION,
    ) -> None:
        if schema_version != PREFERENCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")
        normalized = _validate_signals(signals or {})
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "_signals_json", canonical_json(normalized))

    @property
    def signals(self) -> dict[str, dict[str, int]]:
        return json.loads(self._signals_json)

    @classmethod
    def empty_profile(cls) -> PreferenceProfile:
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "signals": self.signals}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_json(cls, value: str) -> PreferenceProfile:
        try:
            payload = json.loads(value)
            data = json_object(payload, name="preference_profile")
            return cls(
                signals=data.get("signals", {}),
                schema_version=data.get("schema_version", PREFERENCE_SCHEMA_VERSION),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            warnings.warn(f"preference profile is invalid; using empty defaults: {exc}", RuntimeWarning)
            return cls.empty_profile()


def record_feedback(
    profile: PreferenceProfile,
    action: str,
    signal_kind: str,
    value: str,
    *,
    replacement: str | None = None,
) -> PreferenceProfile:
    if action not in _ACTIONS:
        raise ValueError("action must be accept, reject, or edit")
    if signal_kind not in _SIGNAL_KINDS:
        raise ValueError("signal_kind is not supported")
    normalized_value = _signal_value(value, name="value", signal_kind=signal_kind)
    if action == "edit":
        if replacement is None:
            raise ValueError("replacement is required for edit feedback")
        normalized_replacement = _signal_value(
            replacement, name="replacement", signal_kind=signal_kind
        )
    elif replacement is not None:
        raise ValueError("replacement is only valid for edit feedback")
    else:
        normalized_replacement = None

    signals = profile.signals
    rankings = signals.setdefault(signal_kind, {})
    delta = 1 if action == "accept" else -1
    rankings[normalized_value] = rankings.get(normalized_value, 0) + delta
    if normalized_replacement is not None:
        rankings[normalized_replacement] = rankings.get(normalized_replacement, 0) + 1
    return PreferenceProfile(signals=signals)


def reset_preferences(_profile: PreferenceProfile) -> PreferenceProfile:
    return PreferenceProfile.empty_profile()


def load_preference_profile(path: str | Path) -> PreferenceProfile:
    try:
        value = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return PreferenceProfile.empty_profile()
    except OSError as exc:
        warnings.warn(f"preference profile could not be read; using empty defaults: {exc}", RuntimeWarning)
        return PreferenceProfile.empty_profile()
    return PreferenceProfile.from_json(value)


def save_preference_profile(path: str | Path, profile: PreferenceProfile) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(profile.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def reset_stored_preferences(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _validate_signals(value: object) -> dict[str, dict[str, int]]:
    data = json_object(value, name="signals")
    normalized: dict[str, dict[str, int]] = {}
    for kind, rankings in data.items():
        if kind not in _SIGNAL_KINDS:
            raise ValueError("signals contains an unsupported signal_kind")
        ranking_data = json_object(rankings, name=f"signals.{kind}")
        normalized[kind] = {}
        for signal, weight in ranking_data.items():
            normalized_signal = _signal_value(
                signal, name=f"signals.{kind} value", signal_kind=kind
            )
            if not isinstance(weight, int) or isinstance(weight, bool):
                raise ValueError("preference weights must be integers")
            normalized[kind][normalized_signal] = weight
    return normalized


def _signal_value(value: object, *, name: str, signal_kind: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if signal_kind == "source_type" and normalized == "x":
        normalized = "xpost"
    if signal_kind == "tag":
        lowered = normalized.lower()
        valid = bool(_TAG_RE.fullmatch(normalized)) and not any(
            marker in lowered for marker in _RESERVED_TAG_MARKERS
        )
    else:
        valid = normalized in _CONTROLLED_VALUES.get(signal_kind, frozenset())
    if not valid:
        raise ValueError(f"{name} is not a safe preference signal")
    return normalized
