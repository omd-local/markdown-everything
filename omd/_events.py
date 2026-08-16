"""JSON-Lines event emitter for --json-events mode.

Stable contract for GUI consumers (Stage 2 paid Mac app). v1 schema.

When --json-events is on, every stage label, progress tick, error, and
completion is emitted as one JSON object per line on stderr instead of
the human-readable pretty mode. The GUI shell parses these to drive its
progress UI; humans never see them directly.

Schema is locked at v1 in docs/json-events.schema.md. Every event
includes `"v": 1` so the consumer can detect future versions.

Mutex with --verbose: events are for machines, --verbose is for humans
debugging subprocess output. Both at once is meaningless.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from omd.stage_progress import StageProgress, stage_id_for_label
from omd.runtime_metrics import process_peak_memory_bytes

SCHEMA_VERSION = 1
_ENABLED = False
_ITEM_CONTEXT: ContextVar[tuple[int, int, int] | None] = ContextVar(
    "omd_event_item_context",
    default=None,
)


def configure(enabled: bool) -> None:
    """Set module-global. Call once from main()."""
    global _ENABLED
    _ENABLED = enabled


def is_enabled() -> bool:
    return _ENABLED


@contextmanager
def item_context(*, index: int, total: int, attempt: int = 1) -> Iterator[None]:
    """Attach batch identity to nested stage events, including worker threads."""
    if type(index) is not int or type(total) is not int or not 1 <= index <= total:
        raise ValueError("batch event index must be within total")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("batch event attempt must be positive")
    token = _ITEM_CONTEXT.set((index, total, attempt))
    try:
        yield
    finally:
        _ITEM_CONTEXT.reset(token)


def _item_fields(
    item_index: int | None,
    item_total: int | None,
    attempt: int | None,
) -> tuple[int | None, int | None, int]:
    context = _ITEM_CONTEXT.get()
    if context is not None:
        context_index, context_total, context_attempt = context
        item_index = context_index if item_index is None else item_index
        item_total = context_total if item_total is None else item_total
        attempt = context_attempt if attempt is None else attempt
    return item_index, item_total, 1 if attempt is None else attempt


def _emit(event: dict) -> None:
    """Write one JSON-Line event to stderr. Includes schema version + ts."""
    event["v"] = SCHEMA_VERSION
    event["ts"] = round(time.time(), 3)
    sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def stage(
    name: str,
    *,
    stage_id: str | None = None,
    state: str = "indeterminate",
    unit: str | None = None,
    total: float | None = None,
    item_index: int | None = None,
    item_total: int | None = None,
    attempt: int | None = None,
) -> None:
    """Mark the start of a named pipeline stage. Examples: 'download',
    'transcribe', 'polish', 'compose'."""
    if _ENABLED:
        item_index, item_total, attempt = _item_fields(item_index, item_total, attempt)
        snapshot = StageProgress(
            stage_id=stage_id or stage_id_for_label(name),
            state=state,
            unit=unit,
            total=total,
            peak_memory_bytes=process_peak_memory_bytes(),
            item_index=item_index,
            item_total=item_total,
            attempt=attempt,
        ).to_event()
        snapshot.update({"event": "stage", "name": name})
        _emit(snapshot)


def progress(
    label: str,
    cur: int | float,
    total: int | float,
    elapsed_s: float,
    *,
    stage_id: str | None = None,
    unit: str = "items",
    item_index: int | None = None,
    item_total: int | None = None,
    attempt: int | None = None,
) -> None:
    """Per-tick progress update. cur/total are integer counts (chunks,
    files, bytes downloaded if you scale them). elapsed_s is seconds since
    the start of the bar."""
    if _ENABLED:
        item_index, item_total, attempt = _item_fields(item_index, item_total, attempt)
        pct = cur / total if total else 0
        eta_s = ((elapsed_s / pct) - elapsed_s) if pct > 0 and cur < total else 0
        snapshot = StageProgress(
            stage_id=stage_id or stage_id_for_label(label),
            state="determinate",
            unit=unit,
            completed=float(cur),
            total=float(total),
            elapsed_seconds=elapsed_s,
            peak_memory_bytes=process_peak_memory_bytes(),
            item_index=item_index,
            item_total=item_total,
            attempt=attempt,
        ).to_event()
        snapshot.update({
            "event": "progress",
            "label": label,
            "cur": cur,
            "total": total,
            "percent": round(pct * 100, 1),
            "elapsed_s": round(elapsed_s, 2),
            "eta_s": round(eta_s, 2),
        })
        _emit(snapshot)


def stage_state(
    stage_id: str,
    state: str,
    *,
    elapsed_s: float = 0.0,
    unit: str | None = None,
    completed: float | None = None,
    total: float | None = None,
    item_index: int | None = None,
    item_total: int | None = None,
    attempt: int | None = None,
) -> None:
    """Emit an explicit non-cosmetic stage state for GUI consumers."""
    if _ENABLED:
        item_index, item_total, attempt = _item_fields(item_index, item_total, attempt)
        _emit(
            StageProgress(
                stage_id=stage_id,
                state=state,
                unit=unit,
                completed=completed,
                total=total,
                elapsed_seconds=elapsed_s,
                peak_memory_bytes=process_peak_memory_bytes(),
                item_index=item_index,
                item_total=item_total,
                attempt=attempt,
            ).to_event()
        )


def done(output: str | None = None, *, request_id: str | None = None) -> None:
    """Pipeline finished successfully. `output` is the final .md path, or
    None if the result went to stdout."""
    if _ENABLED:
        event = {"event": "done", "output": output}
        if request_id is not None:
            event["request_id"] = request_id
        _emit(event)


def warn(msg: str) -> None:
    """Non-fatal warning. UI may surface as a toast."""
    if _ENABLED:
        _emit({"event": "warn", "message": msg})


def error(kind: str, message: str, *, request_id: str | None = None) -> None:
    """Fatal error event WITHOUT exiting. Use when the caller will sys.exit
    itself. `kind` is a stable machine token (e.g. 'tool_missing',
    'cookies_invalid'). `message` is a human-readable line."""
    if _ENABLED:
        event = {"event": "error", "kind": kind, "message": message}
        if request_id is not None:
            event["request_id"] = request_id
        _emit(event)


def fatal(kind: str, message: str, code: int = 1) -> None:
    """Emit error event (when --json-events is on) and sys.exit.

    Behavior parity with the legacy `sys.exit("error: ...")` pattern:
    - With --json-events: emits a structured error event, exits with `code`.
      Non-JSON message stays out of the GUI's parser.
    - Without --json-events: writes the legacy "error: ..." line to stderr,
      exits with `code`. Same UX as before this flag existed.
    """
    if _ENABLED:
        _emit({"event": "error", "kind": kind, "message": message})
        sys.exit(code)
    else:
        sys.exit(message if message.startswith("error:") else f"error: {message}")
