"""Versioned, presentation-independent progress state for OMD pipelines."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


WORK_SCHEMA_VERSION = 2
VALID_STATES = frozenset(
    {
        "determinate",
        "indeterminate",
        "retrying",
        "needs_action",
        "completed",
        "failed",
        "cancelled",
    }
)
VALID_UNITS = frozenset({"bytes", "pages", "pixels", "audio_seconds", "tokens", "items"})

_STAGE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STAGE_ALIASES = (
    ("download", "download"),
    ("fetch", "fetch"),
    ("resolv", "resolve"),
    ("transcrib", "transcribe"),
    ("whisper", "transcribe"),
    ("ocr", "ocr"),
    ("polish", "polish"),
    ("memory", "memory_cards"),
    ("convert", "convert"),
    ("capture", "capture"),
    ("batch", "batch"),
    ("write", "write"),
    ("compos", "compose"),
)
_DISPLAY_LABELS = {
    "batch": "batch",
    "capture": "capturing",
    "compose": "composing",
    "convert": "converting",
    "download": "downloading",
    "fetch": "fetching",
    "memory_cards": "generating memory cards",
    "ocr": "reading text from images",
    "polish": "polishing",
    "resolve": "resolving source",
    "transcribe": "transcribing",
    "write": "writing output",
}


def stage_id_for_label(label: object) -> str:
    """Return a stable stage token for legacy labels without preserving source data."""
    lowered = str(label or "work").strip().casefold()
    for marker, stage_id in _STAGE_ALIASES:
        if marker in lowered:
            return stage_id
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")[:64]
    if not slug or not slug[0].isalpha():
        return "work"
    return slug


def display_stage(stage_id: str) -> str:
    return _DISPLAY_LABELS.get(stage_id, stage_id.replace("_", " "))


@dataclass(frozen=True)
class StageProgress:
    """One stage snapshot using real work units or an explicit non-determinate state."""

    stage_id: str
    state: str
    unit: str | None = None
    completed: float | None = None
    total: float | None = None
    elapsed_seconds: float = 0.0
    peak_memory_bytes: int | None = None
    item_index: int | None = None
    item_total: int | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.stage_id, str) or not _STAGE_ID_RE.fullmatch(self.stage_id):
            raise ValueError("stage_id must be a stable lowercase token")
        if self.state not in VALID_STATES:
            raise ValueError(f"unsupported progress state: {self.state}")
        if self.unit is not None and self.unit not in VALID_UNITS:
            raise ValueError(f"unsupported work unit: {self.unit}")
        elapsed = _finite_number(self.elapsed_seconds, name="elapsed_seconds")
        if elapsed < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        if (
            self.peak_memory_bytes is not None
            and (type(self.peak_memory_bytes) is not int or self.peak_memory_bytes < 0)
        ):
            raise ValueError("peak_memory_bytes must be a non-negative integer")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be positive")
        if (self.item_index is None) != (self.item_total is None):
            raise ValueError("item_index and item_total must be provided together")
        if self.item_total is not None:
            if type(self.item_index) is not int or type(self.item_total) is not int:
                raise ValueError("item_index and item_total must be integers")
            if self.item_total < 1 or not 1 <= self.item_index <= self.item_total:
                raise ValueError("item_index must be within item_total")
        completed = _optional_finite_number(self.completed, name="completed")
        total = _optional_finite_number(self.total, name="total")
        if completed is not None and completed < 0:
            raise ValueError("completed must be non-negative")
        if total is not None and total <= 0:
            raise ValueError("total must be positive")
        if self.completed is not None and self.total is not None and self.completed > self.total:
            raise ValueError("completed must not exceed total")
        if self.state == "determinate":
            if self.unit is None:
                raise ValueError("determinate progress requires a unit")
            if self.completed is None:
                raise ValueError("determinate progress requires completed work")
            if self.total is None:
                raise ValueError("determinate progress requires a total")

    @property
    def percent(self) -> float | None:
        if self.state != "determinate" or self.completed is None or self.total is None:
            return None
        return round((self.completed / self.total) * 100, 1)

    def to_event(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event": "progress" if self.state == "determinate" else "stage_state",
            "work_v": WORK_SCHEMA_VERSION,
            "stage_id": self.stage_id,
            "state": self.state,
            "elapsed_s": round(self.elapsed_seconds, 2),
            "attempt": self.attempt,
        }
        if self.unit is not None:
            event["unit"] = self.unit
        if self.completed is not None:
            event["completed"] = self.completed
        if self.total is not None:
            event["total"] = self.total
        if self.percent is not None:
            event["percent"] = self.percent
        if self.peak_memory_bytes is not None:
            event["peak_memory_bytes"] = self.peak_memory_bytes
        if self.item_index is not None:
            event["item_index"] = self.item_index
            event["item_total"] = self.item_total
        return event

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> StageProgress:
        if event.get("work_v") == WORK_SCHEMA_VERSION:
            return cls(
                stage_id=str(event.get("stage_id") or "work"),
                state=str(event.get("state") or "indeterminate"),
                unit=_optional_string(event.get("unit")),
                completed=_optional_finite_number(event.get("completed"), name="completed"),
                total=_optional_finite_number(event.get("total"), name="total"),
                elapsed_seconds=_finite_number(event.get("elapsed_s") or 0.0, name="elapsed_seconds"),
                peak_memory_bytes=_optional_int(event.get("peak_memory_bytes"), name="peak_memory_bytes"),
                item_index=_optional_int(event.get("item_index"), name="item_index"),
                item_total=_optional_int(event.get("item_total"), name="item_total"),
                attempt=_required_int(event.get("attempt") or 1, name="attempt"),
            )
        if event.get("event") == "progress":
            return cls(
                stage_id=stage_id_for_label(event.get("label")),
                state="determinate",
                unit="items",
                completed=_finite_number(event.get("cur", 0), name="completed"),
                total=_finite_number(event.get("total", 0), name="total"),
                elapsed_seconds=_finite_number(event.get("elapsed_s") or 0.0, name="elapsed_seconds"),
            )
        if event.get("event") == "stage":
            return cls(
                stage_id=stage_id_for_label(event.get("name")),
                state="indeterminate",
            )
        raise ValueError("event does not contain supported progress data")


@dataclass(frozen=True)
class ProgressView:
    state: str
    label: str
    detail: str
    percent: int | None
    eta_state: str
    item_summary: str
    succeeded: int
    failed: int


class StructuredProgressTracker:
    """Merge stage and batch events without conflating active and completed items."""

    def __init__(self) -> None:
        self.current: StageProgress | None = None
        self.item_total: int | None = None
        self.active_item: int | None = None
        self._outcomes: dict[int, str] = {}

    def apply(self, event: Mapping[str, Any]) -> ProgressView:
        event_type = str(event.get("event") or "")
        if event_type == "batch_started":
            self.item_total = _positive_int(event.get("total"))
            self.active_item = None
            self.current = None
            self._outcomes.clear()
        elif event_type == "batch_item_started":
            self.item_total = _positive_int(event.get("total")) or self.item_total
            self.active_item = _positive_int(event.get("index"))
            if (
                self.active_item is not None
                and self.item_total is not None
                and self.active_item > self.item_total
            ):
                self.active_item = None
            self.current = StageProgress(
                stage_id="work",
                state="indeterminate",
                item_index=self.active_item if self.item_total is not None else None,
                item_total=self.item_total if self.active_item is not None else None,
            )
        elif event_type in {"batch_item_succeeded", "batch_item_failed"}:
            self.item_total = _positive_int(event.get("total")) or self.item_total
            index = _positive_int(event.get("index"))
            if index is not None:
                self._outcomes[index] = "success" if event_type.endswith("succeeded") else "failed"
                if self.active_item == index:
                    self.active_item = None
        elif event_type == "batch_item_retry":
            self.item_total = _positive_int(event.get("total")) or self.item_total
            self.active_item = _positive_int(event.get("index"))
            stage_id = self.current.stage_id if self.current else "work"
            self.current = StageProgress(stage_id=stage_id, state="retrying")
        elif event_type in {"progress", "stage", "stage_state"}:
            try:
                self.current = StageProgress.from_event(event)
            except (TypeError, ValueError):
                pass
            else:
                if self.current.item_total is not None:
                    self.item_total = self.current.item_total
                    self.active_item = self.current.item_index
        return self.view()

    def view(self) -> ProgressView:
        current = self.current or StageProgress(stage_id="work", state="indeterminate")
        succeeded = sum(outcome == "success" for outcome in self._outcomes.values())
        failed = sum(outcome == "failed" for outcome in self._outcomes.values())
        processed = succeeded + failed
        if self.item_total is None:
            item_summary = ""
        elif processed:
            item_summary = f"{processed}/{self.item_total} processed"
        else:
            item_summary = f"{self.item_total} queued"
        detail = (
            f"item {self.active_item} of {self.item_total}"
            if self.active_item is not None and self.item_total is not None
            else (item_summary or current.state.replace("_", " "))
        )
        eta_state = {
            "determinate": "measuring real work",
            "indeterminate": "estimating after measurable work starts",
            "retrying": "retrying; ETA paused",
            "needs_action": "needs action; ETA paused",
            "completed": "done",
            "failed": "ETA unavailable",
            "cancelled": "cancelled",
        }[current.state]
        percent = None if current.percent is None else int(round(current.percent))
        return ProgressView(
            state=current.state,
            label=display_stage(current.stage_id),
            detail=detail,
            percent=percent,
            eta_state=eta_state,
            item_summary=item_summary,
            succeeded=succeeded,
            failed=failed,
        )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _optional_finite_number(value: object, *, name: str) -> float | None:
    return None if value is None else _finite_number(value, name=name)


def _required_int(value: object, *, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_int(value: object, *, name: str) -> int | None:
    return None if value is None else _required_int(value, name=name)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
