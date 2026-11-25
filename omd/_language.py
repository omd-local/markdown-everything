"""Language preference helpers for OCR/transcription routing."""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Collection, Mapping
from typing import Any

PREFERRED_LANGUAGES_ENV = "OMD_PREFERRED_LANGUAGES"
OCR_LANGUAGE_ENV = "OMD_OCR_LANGUAGE"
SPOKEN_LANGUAGE_MODE_ENV = "OMD_SPOKEN_LANGUAGE_MODE"
SPOKEN_LANGUAGE_HINT_ENV = "OMD_SPOKEN_LANGUAGE_HINT"
DEFAULT_OCR_LANGUAGE = "eng"
MIXED_OCR_LANGUAGE_EXAMPLE = "chi_sim+eng"
_OCR_LANG_TOKEN_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z][A-Za-z0-9_-]*)?$"
)
MAX_OCR_LANGUAGE_COUNT = 8
MAX_OCR_LANGUAGE_TOKEN_LENGTH = 64
MAX_OCR_LANGUAGE_SET_LENGTH = 256
DEFAULT_TESSERACT_PROBE_TIMEOUT = 5.0
_AUTO_LANGUAGE_VALUES = frozenset({"auto", "auto-detect"})
_SPOKEN_LANGUAGE_HINT_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")


class OcrLanguageError(ValueError):
    """Base error for an invalid or unusable OCR language request."""


class OcrLanguageProbeError(OcrLanguageError):
    """Raised when installed Tesseract languages cannot be inspected safely."""


class MissingOcrLanguagePacks(OcrLanguageError):
    """Raised when a requested Tesseract language pack is not installed."""

    def __init__(
        self,
        requested: str,
        missing: Collection[str],
        available: Collection[str],
    ) -> None:
        self.requested = requested
        self.missing = tuple(missing)
        self.available = tuple(sorted(set(available)))
        available_summary = ", ".join(self.available[:20]) or "none detected"
        if len(self.available) > 20:
            available_summary += f", ... ({len(self.available)} total)"
        super().__init__(
            "Missing Tesseract language pack(s): "
            f"{', '.join(self.missing)}. Requested OCR language: {requested}. "
            "Install language data (on Homebrew: `brew install tesseract-lang`) "
            f"and retry. Available language packs: {available_summary}"
        )


def parse_ocr_language_set(raw: str) -> tuple[str, ...]:
    """Parse a bounded Tesseract composite language expression.

    Tesseract joins trained-data identifiers with ``+``.  The parser permits
    the optional ``script/HanS`` form emitted by ``tesseract --list-langs`` but
    rejects path traversal, shell syntax, empty components, and unbounded
    language lists before the value reaches a subprocess argv.
    """
    if not isinstance(raw, str):
        raise OcrLanguageError("OCR language must be a string")
    if not raw.strip():
        raise OcrLanguageError("OCR language must not be empty")
    if len(raw) > MAX_OCR_LANGUAGE_SET_LENGTH:
        raise OcrLanguageError(
            f"OCR language must be at most {MAX_OCR_LANGUAGE_SET_LENGTH} characters"
        )

    raw_parts = raw.split("+")
    if len(raw_parts) > MAX_OCR_LANGUAGE_COUNT:
        raise OcrLanguageError(
            f"OCR language may contain at most {MAX_OCR_LANGUAGE_COUNT} packs"
        )

    languages: list[str] = []
    seen: set[str] = set()
    for raw_part in raw_parts:
        language = raw_part.strip()
        if not language:
            raise OcrLanguageError("OCR language contains an empty pack name")
        if len(language) > MAX_OCR_LANGUAGE_TOKEN_LENGTH:
            raise OcrLanguageError(
                "OCR language pack names must be at most "
                f"{MAX_OCR_LANGUAGE_TOKEN_LENGTH} characters"
            )
        if not _OCR_LANG_TOKEN_RE.fullmatch(language) or ".." in language:
            raise OcrLanguageError(f"invalid OCR language pack name: {language!r}")
        if language not in seen:
            languages.append(language)
            seen.add(language)

    if not languages:
        raise OcrLanguageError("OCR language must contain at least one pack")
    return tuple(languages)


def normalize_ocr_language_set(raw: str) -> str:
    """Return the canonical ``+``-joined representation of an OCR language set."""
    return "+".join(parse_ocr_language_set(raw))


def normalize_spoken_language_hint(raw: str, *, allow_auto: bool = False) -> str:
    """Normalize one BCP-47-like language hint accepted by OMD's ASR CLI.

    This intentionally supports only the compact subset documented by OMD:
    a two- or three-letter primary language plus at most one optional subtag.
    Whisper accepts one hint, so language names and composite lists are invalid.
    """
    if not isinstance(raw, str):
        raise ValueError("spoken language hint must be one language code")
    normalized = raw.strip().lower().replace("_", "-")
    if allow_auto and normalized in _AUTO_LANGUAGE_VALUES:
        return "auto"
    if not _SPOKEN_LANGUAGE_HINT_RE.fullmatch(normalized):
        expected = "'auto' or one language code" if allow_auto else "one language code"
        raise ValueError(f"spoken language hint must be {expected}, such as en or zh-cn")
    return normalized


def _bounded_probe_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise OcrLanguageProbeError("Tesseract probe timeout must be numeric") from exc
    if not 0 < value <= 30:
        raise OcrLanguageProbeError("Tesseract probe timeout must be between 0 and 30 seconds")
    return value


def probe_tesseract_languages(
    executable: str = "tesseract",
    *,
    timeout: float = DEFAULT_TESSERACT_PROBE_TIMEOUT,
) -> frozenset[str]:
    """Return installed Tesseract pack identifiers using a bounded local probe."""
    timeout = _bounded_probe_timeout(timeout)
    try:
        result = subprocess.run(
            [str(executable), "--list-langs"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrLanguageProbeError("Tesseract language probe timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise OcrLanguageProbeError(f"could not run Tesseract language probe: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        raise OcrLanguageProbeError(
            f"Tesseract language probe exited with status {result.returncode}{suffix}"
        )

    languages = frozenset(
        line.strip()
        for line in result.stdout.splitlines()
        if _OCR_LANG_TOKEN_RE.fullmatch(line.strip()) and ".." not in line
    )
    if not languages:
        raise OcrLanguageProbeError("Tesseract reported no available language packs")
    return languages


def validate_ocr_language_packs(
    language: str,
    *,
    available_languages: Collection[str] | None = None,
    executable: str = "tesseract",
    timeout: float = DEFAULT_TESSERACT_PROBE_TIMEOUT,
) -> str:
    """Normalize an OCR language and fail clearly when any pack is unavailable."""
    normalized = normalize_ocr_language_set(language)
    requested = parse_ocr_language_set(normalized)
    available = (
        probe_tesseract_languages(executable, timeout=timeout)
        if available_languages is None
        else frozenset(str(item) for item in available_languages)
    )
    missing = tuple(item for item in requested if item not in available)
    if missing:
        raise MissingOcrLanguagePacks(normalized, missing, available)
    return normalized


def _capture_config_value(config: object | None, name: str) -> Any:
    capture = getattr(config, "capture", None)
    if capture is None and isinstance(config, Mapping):
        capture = config.get("capture")
    if isinstance(capture, Mapping):
        return capture.get(name)
    return getattr(capture, name, None)


def resolve_ocr_language(
    explicit: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    config: object | None = None,
    default: str = DEFAULT_OCR_LANGUAGE,
) -> str:
    """Resolve OCR language using explicit > environment > config > default."""
    environment = os.environ if environ is None else environ
    if explicit is not None:
        return normalize_ocr_language_set(explicit)
    if OCR_LANGUAGE_ENV in environment:
        return normalize_ocr_language_set(environment[OCR_LANGUAGE_ENV])
    configured = _capture_config_value(config, "ocr_language")
    if configured is not None:
        return normalize_ocr_language_set(str(configured))
    return normalize_ocr_language_set(default)


def parse_preferred_languages(raw: str | None) -> list[str]:
    """Parse comma/space separated Whisper language hints.

    Whisper accepts one language hint at a time. OMD treats the first preferred
    language as the default hint when a command did not pass --whisper-lang.
    """
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw) if p.strip()]
    languages: list[str] = []
    for part in parts:
        try:
            languages.append(normalize_spoken_language_hint(part))
        except ValueError:
            continue
    return languages


def preferred_languages(raw: str | None = None) -> list[str]:
    return parse_preferred_languages(
        raw if raw is not None else os.environ.get(PREFERRED_LANGUAGES_ENV)
    )


def choose_whisper_language(
    explicit: str | None,
    *,
    preferred: str | None = None,
    default: str | None = None,
    environ: Mapping[str, str] | None = None,
    config: object | None = None,
) -> str | None:
    """Resolve a Whisper hint without conflating inherit and auto-detect.

    Existing callers retain their adapter-specific ``default``.  Passing
    ``auto`` or ``auto-detect`` is an explicit request for backend detection,
    represented by ``None`` and never replaced by a lower-precedence default.
    """
    environment = os.environ if environ is None else environ
    if explicit and explicit.strip():
        value = normalize_spoken_language_hint(explicit, allow_auto=True)
        return None if value == "auto" else value

    # A function argument is an explicit legacy override and therefore stays
    # ahead of environment/config values.  When omitted, preserve support for
    # OMD_PREFERRED_LANGUAGES as an environment-level compatibility alias.
    if preferred is not None:
        langs = parse_preferred_languages(preferred)
        return langs[0] if langs else default

    environment_has_mode = SPOKEN_LANGUAGE_MODE_ENV in environment
    environment_has_hint = SPOKEN_LANGUAGE_HINT_ENV in environment
    if environment_has_mode or environment_has_hint:
        mode = environment.get(SPOKEN_LANGUAGE_MODE_ENV, "explicit").strip()
        hint = environment.get(SPOKEN_LANGUAGE_HINT_ENV, "").strip() or None
        return _resolve_spoken_mode(mode, hint, default)

    legacy_preferred = parse_preferred_languages(
        environment.get(PREFERRED_LANGUAGES_ENV)
    )
    if legacy_preferred:
        return legacy_preferred[0]

    configured_mode = _capture_config_value(config, "spoken_language_mode")
    configured_hint = _capture_config_value(config, "spoken_language_hint")
    if configured_mode is not None:
        return _resolve_spoken_mode(
            str(configured_mode),
            None if configured_hint is None else str(configured_hint),
            default,
        )
    return default


def _resolve_spoken_mode(
    mode: str,
    hint: str | None,
    default: str | None,
) -> str | None:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "inherit-adapter-default":
        if hint:
            raise ValueError("spoken language hint must be empty in inherit mode")
        return default
    if normalized_mode in _AUTO_LANGUAGE_VALUES:
        if hint:
            raise ValueError("spoken language hint must be empty in auto-detect mode")
        return None
    if normalized_mode == "explicit":
        if not hint or not hint.strip():
            raise ValueError("spoken language hint is required in explicit mode")
        return normalize_spoken_language_hint(hint)
    raise ValueError(
        "spoken language mode must be inherit-adapter-default, auto-detect, or explicit"
    )
