"""Bounded download helpers for untrusted remote media."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

DEFAULT_MAX_DOWNLOAD_MB = 500
MAX_DOWNLOAD_MB_ENV = "OMD_MAX_DOWNLOAD_MB"


def max_download_bytes() -> int:
    raw = os.environ.get(MAX_DOWNLOAD_MB_ENV, str(DEFAULT_MAX_DOWNLOAD_MB)).strip()
    try:
        mb = int(raw)
    except ValueError:
        mb = DEFAULT_MAX_DOWNLOAD_MB
    return max(1, mb) * 1024 * 1024


def ytdlp_max_filesize_arg() -> str:
    return f"{max_download_bytes()}B"


def _content_length(response) -> int:
    try:
        return int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def copy_response_bounded(response, dest: Path, *, label: str = "Download") -> int:
    """Copy an HTTP response to `dest`, refusing files over the configured cap."""
    from omd import _progress

    max_bytes = max_download_bytes()
    total = _content_length(response)
    if total > max_bytes:
        raise ValueError(
            f"{label} is {total} bytes, above limit {max_bytes}. "
            f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
        )

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".download",
        dir=dest.parent,
    )
    tmp = Path(tmp_name)
    written = 0
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = -1
            if not _progress.is_quiet():
                _progress.info(
                    f"{label} ({total // 1024 // 1024 if total else '?'} MB, "
                    f"limit {max_bytes // 1024 // 1024} MB)"
                )
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(
                        f"{label} exceeded limit {max_bytes} bytes. "
                        f"Set {MAX_DOWNLOAD_MB_ENV} to override for trusted workflows."
                    )
                f.write(chunk)
        os.replace(tmp, dest)
        return written
    except BaseException:
        if fd != -1:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
