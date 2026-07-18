"""Atomic file write helpers.

Crash-safe write: write to a sibling temp file, then atomically rename.
Reader sees either the old file (if any) or the fully-written new file —
never a half-written one.

Used by every `route_*` function that emits Markdown so a kill -9 / OOM /
power loss mid-write doesn't leave behind a corrupted .md.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically.

    Strategy:
      1. Write to a unique sibling temp file in the same directory (so rename is
         atomic per POSIX — cross-filesystem rename is not).
      2. `os.replace(tmp, path)` — atomic on POSIX, also on Windows since 3.3.
      3. On ANY abort (BaseException — covers KeyboardInterrupt + SystemExit),
         remove the temp file so the next run isn't confused by stale .tmp.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = -1
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        if fd != -1:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def write_atomic_bytes(path: Path, content: bytes) -> None:
    """Bytes variant. Same atomic guarantees."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        if fd != -1:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
