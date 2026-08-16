"""Privacy-safe process metrics used by structured progress events."""

from __future__ import annotations

import sys

try:
    import resource
except ImportError:  # pragma: no cover - resource is available on supported macOS/Linux hosts.
    resource = None  # type: ignore[assignment]


def _raw_peak_rss() -> int | None:
    if resource is None:
        return None
    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def process_peak_memory_bytes() -> int | None:
    """Return the process high-water RSS in bytes, normalised across hosts."""
    value = _raw_peak_rss()
    if value is None:
        return None
    # Darwin reports bytes; Linux and other common Unix hosts report KiB.
    return value if sys.platform == "darwin" else value * 1024
