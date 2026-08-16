"""Small, dependency-free runtime policy for optional Ollama requests."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections.abc import Callable

from ._models import GIB, detect_total_memory_bytes
from ._network_policy import build_no_redirect_opener


_KEEP_ALIVE_RE = re.compile(r"^(?:0|\d+(?:\.\d+)?(?:ns|us|ms|s|m|h))$")
_HIGH_PRESSURE_RATIO = 0.12
MAX_OLLAMA_RESPONSE_BYTES = 8 * 1024 * 1024


def request_ollama_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    open_call: Callable | None = None,
) -> dict[str, object]:
    """Read one bounded Ollama JSON response without forwarding redirects."""
    open_request = open_call or build_no_redirect_opener().open
    with open_request(request, timeout=timeout) as response:
        raw = response.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
    if len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
        raise RuntimeError("Ollama response too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Ollama returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Ollama response must be a JSON object")
    return payload


def detect_available_memory_bytes() -> int | None:
    """Return currently available RAM when the host exposes a portable counter."""
    override = os.environ.get("OMD_AVAILABLE_MEMORY_GB", "").strip()
    if override:
        try:
            value = float(override)
        except ValueError:
            pass
        else:
            if math.isfinite(value) and value >= 0:
                return int(value * GIB)

    try:
        pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    available = pages * page_size
    return available if available >= 0 else None


def memory_pressure_level() -> str:
    """Classify memory pressure conservatively without exposing machine details."""
    override = os.environ.get("OMD_MEMORY_PRESSURE", "").strip().lower()
    if override in {"high", "critical"}:
        return "high"
    if override in {"normal", "low"}:
        return "normal"

    total = detect_total_memory_bytes()
    available = detect_available_memory_bytes()
    if total and available is not None and available / total < _HIGH_PRESSURE_RATIO:
        return "high"
    return "normal"


def ollama_keep_alive() -> str | int:
    """Choose model residency from explicit policy, RAM size, and current pressure."""
    if memory_pressure_level() == "high":
        return 0

    override = os.environ.get("OMD_OLLAMA_KEEP_ALIVE", "").strip().lower()
    if _KEEP_ALIVE_RE.fullmatch(override):
        return 0 if override == "0" else override

    total = detect_total_memory_bytes()
    if total is None:
        return "2m"
    if total <= 16 * GIB:
        return "60s"
    return "5m"
