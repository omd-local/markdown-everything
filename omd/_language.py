"""Language preference helpers for OCR/transcription routing."""
from __future__ import annotations

import os
import re

PREFERRED_LANGUAGES_ENV = "OMD_PREFERRED_LANGUAGES"
DEFAULT_OCR_LANGUAGE = "eng"
MIXED_OCR_LANGUAGE_EXAMPLE = "chi_sim+eng"
_LANG_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def parse_preferred_languages(raw: str | None) -> list[str]:
    """Parse comma/space separated Whisper language hints.

    Whisper accepts one language hint at a time. OMD treats the first preferred
    language as the default hint when a command did not pass --whisper-lang.
    """
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw) if p.strip()]
    return [p for p in parts if _LANG_TOKEN_RE.match(p)]


def preferred_languages(raw: str | None = None) -> list[str]:
    return parse_preferred_languages(
        raw if raw is not None else os.environ.get(PREFERRED_LANGUAGES_ENV)
    )


def choose_whisper_language(
    explicit: str | None,
    *,
    preferred: str | None = None,
    default: str | None = None,
) -> str | None:
    if explicit:
        return explicit
    langs = preferred_languages(preferred)
    if langs:
        return langs[0]
    return default
