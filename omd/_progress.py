"""Stdlib-only progress + logging primitives for the omd CLI.

Three verbosity modes, controlled by `configure()` from `omd.cli`:

    quiet    — nothing on stderr except errors
    default  — single-line stage labels + animated bars when stderr is a TTY
    verbose  — everything (subprocess output, debug lines, no bars)

TTY detection auto-disables animation when stderr is piped or redirected so
piping into other tools, MCP server stdio, or CI logs stays clean.
Honours the NO_COLOR env var (no ANSI escapes when set).
"""
from __future__ import annotations

import os
import sys
import time

_VERBOSE = False
_QUIET = False


def configure(*, verbose: bool = False, quiet: bool = False) -> None:
    """Set module-global verbosity. Call once from cli.main()."""
    global _VERBOSE, _QUIET
    _VERBOSE = verbose
    _QUIET = quiet


def is_verbose() -> bool:
    return _VERBOSE


def is_quiet() -> bool:
    return _QUIET


def _is_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _use_color() -> bool:
    return _is_tty() and not os.environ.get("NO_COLOR")


def _animated() -> bool:
    """Animation only when stderr is a TTY and we're not in quiet mode.
    Verbose mode also disables animation — the caller wants raw subprocess
    output, and \r overwrite collides with that."""
    return _is_tty() and not _QUIET and not _VERBOSE


# ─── single-line status helpers ──────────────────────────────────────────────

def log(msg: str) -> None:
    """Stderr line gated behind --verbose. Use for subprocess details,
    chunk-by-chunk debug, segment timestamps."""
    if _VERBOSE:
        sys.stderr.write(msg if msg.endswith("\n") else msg + "\n")
        sys.stderr.flush()


def _events_on() -> bool:
    """Lazy import so _events isn't required at module-load time."""
    from omd import _events
    return _events.is_enabled()


def info(
    msg: str,
    *,
    stage_id: str | None = None,
    state: str = "indeterminate",
    unit: str | None = None,
    total: float | None = None,
    item_index: int | None = None,
    item_total: int | None = None,
) -> None:
    """Always-shown stage label. In --json-events mode, emit a `stage` event
    with `msg` as the name (slugified to a stable token)."""
    if _events_on():
        from omd import _events
        from omd.stage_progress import stage_id_for_label
        # Slugify msg → stable stage name token (e.g. "Downloading reel" → "download").
        # Strip leading verbs and lowercase the first word as the canonical stage.
        first_word = msg.split()[0].lower().rstrip(":")
        _events.stage(
            first_word,
            stage_id=stage_id or stage_id_for_label(msg),
            state=state,
            unit=unit,
            total=total,
            item_index=item_index,
            item_total=item_total,
        )
        return
    if _QUIET:
        return
    prefix = "→" if not _use_color() else "\x1b[36m→\x1b[0m"
    sys.stderr.write(f"{prefix} {msg}\n")
    sys.stderr.flush()


def item_result(msg: str) -> None:
    """Show a human batch-result line without duplicating structured events."""
    if _events_on() or _QUIET:
        return
    prefix = "→" if not _use_color() else "\x1b[36m→\x1b[0m"
    sys.stderr.write(f"{prefix} {msg}\n")
    sys.stderr.flush()


def done(msg: str) -> None:
    """Final success line. In --json-events mode, emit a `done` event."""
    if _events_on():
        from omd import _events
        # `msg` for done is typically "wrote /path/to/output.md".
        output = msg.removeprefix("wrote ").strip() or None
        _events.done(output)
        return
    if _QUIET:
        return
    prefix = "✓" if not _use_color() else "\x1b[32m✓\x1b[0m"
    sys.stderr.write(f"{prefix} {msg}\n")
    sys.stderr.flush()


def warn(msg: str) -> None:
    """Always-shown warning. In --json-events mode, emit a `warn` event."""
    if _events_on():
        from omd import _events
        _events.warn(msg)
        return
    if _QUIET:
        return
    prefix = "warn:" if not _use_color() else "\x1b[33mwarn:\x1b[0m"
    sys.stderr.write(f"{prefix} {msg if msg.endswith(chr(10)) else msg + chr(10)}")
    sys.stderr.flush()


# ─── determinate progress bar ────────────────────────────────────────────────

class ProgressBar:
    """Single-line progress bar. Use as a context manager.

        with ProgressBar("Polish", total=12) as bar:
            for i in range(12):
                ...
                bar.update()

    Falls back to silent (verbose / quiet / non-TTY) automatically — wrap
    every loop in this without checking modes yourself.
    """
    BAR_WIDTH = 24

    def __init__(
        self,
        label: str,
        total: int,
        *,
        stage_id: str | None = None,
        unit: str = "items",
        item_index: int | None = None,
        item_total: int | None = None,
    ) -> None:
        from omd.stage_progress import stage_id_for_label

        self.label = label
        self.total = max(1, total)
        self.stage_id = stage_id or stage_id_for_label(label)
        self.unit = unit
        self.item_index = item_index
        self.item_total = item_total
        self.cur = 0
        self.start = time.monotonic()
        self.events_mode = _events_on()
        self.active = self.events_mode or _animated()
        self._last_render = 0.0

    def update(self, n: int = 1) -> None:
        self.cur = min(self.total, self.cur + n)
        self._render()

    def set(self, value: int) -> None:
        self.cur = min(self.total, max(0, value))
        self._render()

    def _render(self, *, force: bool = False) -> None:
        if not self.active:
            return
        now = time.monotonic()
        # Throttle to ~10 fps so we don't flood stderr.
        if not force and now - self._last_render < 0.1 and self.cur < self.total:
            return
        self._last_render = now
        elapsed = now - self.start
        if self.events_mode:
            from omd import _events
            _events.progress(
                self.label,
                self.cur,
                self.total,
                elapsed,
                stage_id=self.stage_id,
                unit=self.unit,
                item_index=self.item_index,
                item_total=self.item_total,
            )
            return
        pct = self.cur / self.total
        eta = ((elapsed / pct) - elapsed) if pct > 0 and self.cur < self.total else 0
        filled = int(self.BAR_WIDTH * pct)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        line = (
            f"\r{self.label}  [{bar}] {int(pct * 100):3d}% • "
            f"{self.cur}/{self.total} • {elapsed:5.1f}s elapsed • {eta:5.1f}s ETA"
        )
        sys.stderr.write(line)
        sys.stderr.flush()

    def close(self) -> None:
        if not self.active:
            return
        self._render(force=True)
        if not self.events_mode:
            sys.stderr.write("\n")
            sys.stderr.flush()
        self.active = False

    def __enter__(self) -> ProgressBar:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


# ─── byte-stream progress (download) ─────────────────────────────────────────

def copy_with_progress(src, dest, label: str, total_bytes: int, chunk: int = 64 * 1024) -> int:
    """shutil.copyfileobj equivalent that renders a progress bar by bytes.
    `total_bytes` may be 0 if Content-Length is unknown — falls back to a
    simple "downloading…" line."""
    events_mode = _events_on()
    if total_bytes <= 0:
        started = time.monotonic()
        if events_mode:
            from omd import _events

            _events.stage(
                "download",
                stage_id="download",
                state="indeterminate",
                unit="bytes",
            )
        elif not _QUIET:
            info(f"{label} (? MB)")
        n = 0
        while True:
            buf = src.read(chunk)
            if not buf:
                break
            dest.write(buf)
            n += len(buf)
        if events_mode:
            _events.stage_state(
                "download",
                "completed",
                elapsed_s=time.monotonic() - started,
                unit="bytes",
                completed=n,
            )
        return n

    if not _animated() and not events_mode:
        if not _QUIET:
            info(f"{label} ({total_bytes // 1024 // 1024} MB)")
        n = 0
        while True:
            buf = src.read(chunk)
            if not buf:
                break
            dest.write(buf)
            n += len(buf)
        return n

    start = time.monotonic()
    written = 0
    last_render = 0.0
    if events_mode:
        from omd import _events

        _events.progress(
            label,
            0,
            total_bytes,
            0.0,
            stage_id="download",
            unit="bytes",
        )
    while True:
        buf = src.read(chunk)
        if not buf:
            break
        dest.write(buf)
        written += len(buf)
        now = time.monotonic()
        if now - last_render < 0.1 and written < total_bytes:
            continue
        last_render = now
        pct = written / total_bytes
        elapsed = now - start
        if events_mode:
            _events.progress(
                label,
                written,
                total_bytes,
                elapsed,
                stage_id="download",
                unit="bytes",
            )
            continue
        filled = int(ProgressBar.BAR_WIDTH * pct)
        bar = "█" * filled + "░" * (ProgressBar.BAR_WIDTH - filled)
        eta = ((elapsed / pct) - elapsed) if pct > 0 and written < total_bytes else 0
        mb_done = written / 1_000_000
        mb_total = total_bytes / 1_000_000
        sys.stderr.write(
            f"\r{label}  [{bar}] {int(pct * 100):3d}% • "
            f"{mb_done:5.1f}/{mb_total:5.1f} MB • {elapsed:5.1f}s elapsed • {eta:5.1f}s ETA"
        )
        sys.stderr.flush()
    if not events_mode:
        sys.stderr.write("\n")
        sys.stderr.flush()
    return written
