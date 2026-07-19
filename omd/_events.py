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

SCHEMA_VERSION = 1
_ENABLED = False


def configure(enabled: bool) -> None:
    """Set module-global. Call once from main()."""
    global _ENABLED
    _ENABLED = enabled


def is_enabled() -> bool:
    return _ENABLED


def _emit(event: dict) -> None:
    """Write one JSON-Line event to stderr. Includes schema version + ts."""
    event["v"] = SCHEMA_VERSION
    event["ts"] = round(time.time(), 3)
    sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def stage(name: str) -> None:
    """Mark the start of a named pipeline stage. Examples: 'download',
    'transcribe', 'polish', 'compose'."""
    if _ENABLED:
        _emit({"event": "stage", "name": name})


def progress(label: str, cur: int, total: int, elapsed_s: float) -> None:
    """Per-tick progress update. cur/total are integer counts (chunks,
    files, bytes downloaded if you scale them). elapsed_s is seconds since
    the start of the bar."""
    if _ENABLED:
        pct = cur / total if total else 0
        eta_s = ((elapsed_s / pct) - elapsed_s) if pct > 0 and cur < total else 0
        _emit({
            "event": "progress",
            "label": label,
            "cur": cur,
            "total": total,
            "percent": round(pct * 100, 1),
            "elapsed_s": round(elapsed_s, 2),
            "eta_s": round(eta_s, 2),
        })


def done(output: str | None = None) -> None:
    """Pipeline finished successfully. `output` is the final .md path, or
    None if the result went to stdout."""
    if _ENABLED:
        _emit({"event": "done", "output": output})


def warn(msg: str) -> None:
    """Non-fatal warning. UI may surface as a toast."""
    if _ENABLED:
        _emit({"event": "warn", "message": msg})


def error(kind: str, message: str) -> None:
    """Fatal error event WITHOUT exiting. Use when the caller will sys.exit
    itself. `kind` is a stable machine token (e.g. 'tool_missing',
    'cookies_invalid'). `message` is a human-readable line."""
    if _ENABLED:
        _emit({"event": "error", "kind": kind, "message": message})


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
