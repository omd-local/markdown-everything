"""Transcript quality checks shared by local audio/video adapters."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

REPEATED_PHRASES_WARNING = (
    "The transcript contains unusually repeated phrases and may be inaccurate."
)
UNSTABLE_COMPRESSION_WARNING = (
    "Whisper reported unstable repeated-text segments; review the raw transcript."
)
ENGLISH_LANGUAGE_MISMATCH_WARNING = (
    "The detected transcript language does not match the requested English language hint."
)
CHINESE_LANGUAGE_MISMATCH_WARNING = (
    "The detected transcript language does not match the requested Chinese language hint."
)
TRANSCRIPT_REVIEW_NOTE = (
    "OMD detected possible transcription problems. The raw transcript is kept below "
    "for review, and AI polish is skipped so it cannot hide the source error."
)

_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)?|[\u3400-\u4dbf\u4e00-\u9fff]"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_MARKDOWN_WARNING_RE = re.compile(r"^- \*\*Transcript warning\*\*: (.+)$", re.MULTILINE)
_LANGUAGE_ALIASES = {
    "chi": "zh",
    "chinese": "zh",
    "cmn": "zh",
    "eng": "en",
    "english": "en",
    "japanese": "ja",
    "jpn": "ja",
    "spa": "es",
    "spanish": "es",
    "yue": "zh",
    "zho": "zh",
}


def assess_transcript_quality(
    transcript: Mapping[str, Any],
    *,
    expected_language: str | None = None,
    expected_duration: float | int | None = None,
) -> list[str]:
    """Return conservative warnings for strongly suspect Whisper output."""
    text = str(transcript.get("text") or "").strip()
    segments = transcript.get("segments")
    segment_list = list(segments) if isinstance(segments, Sequence) and not isinstance(segments, str) else []
    warnings: list[str] = []

    if _has_excessive_repetition(text):
        warnings.append(REPEATED_PHRASES_WARNING)
    if _has_unstable_compression(segment_list):
        warnings.append(UNSTABLE_COMPRESSION_WARNING)

    language_warning = _language_mismatch_warning(
        text,
        expected_language,
        str(transcript.get("language") or ""),
    )
    if language_warning:
        warnings.append(language_warning)

    duration_warning = _duration_warning(segment_list, expected_duration)
    if duration_warning:
        warnings.append(duration_warning)
    return warnings


def apply_transcript_quality(
    transcript: dict[str, Any],
    *,
    expected_language: str | None = None,
    expected_duration: float | int | None = None,
) -> list[str]:
    warnings = assess_transcript_quality(
        transcript,
        expected_language=expected_language,
        expected_duration=expected_duration,
    )
    transcript["quality_warnings"] = warnings
    return warnings


def report_transcript_quality(warnings: Sequence[str], *, polish_requested: bool = False) -> None:
    if not warnings:
        return
    from omd import _progress

    for warning in warnings:
        _progress.warn(f"transcript quality: {warning}")
    if polish_requested:
        _progress.warn("AI polish was skipped because the raw transcript needs review")


def transcript_warnings_from_markdown(markdown: str) -> list[str]:
    return [
        warning.strip()
        for warning in _MARKDOWN_WARNING_RE.findall(markdown)
        if warning.strip()
    ]


def _has_excessive_repetition(text: str) -> bool:
    tokens = [token.lower() for token in _TOKEN_RE.findall(text)]
    window_size = 8
    if len(tokens) < 60:
        return False
    windows = [tuple(tokens[index:index + window_size]) for index in range(len(tokens) - window_size + 1)]
    counts = Counter(windows)
    repeated_windows = sum(count - 1 for count in counts.values() if count > 1)
    repeated_ratio = repeated_windows / max(1, len(windows))
    return max(counts.values(), default=0) >= 4 and repeated_ratio >= 0.15


def _has_unstable_compression(segments: Sequence[Any]) -> bool:
    ratios: list[float] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        try:
            ratios.append(float(segment.get("compression_ratio") or 0))
        except (TypeError, ValueError):
            continue
    return any(ratio >= 4.0 for ratio in ratios) or sum(ratio >= 2.8 for ratio in ratios) >= 2


def _normalize_language(value: str | None) -> str:
    token = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(token, token)


def _language_mismatch_warning(
    text: str,
    expected_language: str | None,
    reported_language: str | None,
) -> str | None:
    if not expected_language:
        return None
    expected = _normalize_language(expected_language)
    reported = _normalize_language(reported_language)
    if reported and expected and reported != expected:
        return (
            f"Whisper reported language '{reported}', which does not match "
            f"the requested language hint '{expected}'."
        )
    if not text:
        return None
    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(_LATIN_RE.findall(text))
    language_chars = cjk_count + latin_count
    if language_chars < 80:
        return None
    if expected == "en" and cjk_count >= 40 and cjk_count / language_chars >= 0.2:
        return ENGLISH_LANGUAGE_MISMATCH_WARNING
    if expected == "zh" and latin_count >= 80 and cjk_count / language_chars < 0.1:
        return CHINESE_LANGUAGE_MISMATCH_WARNING
    return None


def _duration_warning(segments: Sequence[Any], expected_duration: float | int | None) -> str | None:
    try:
        source_duration = float(expected_duration or 0)
    except (TypeError, ValueError):
        return None
    if source_duration < 30:
        return None

    transcript_end = 0.0
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        try:
            transcript_end = max(transcript_end, float(segment.get("end") or 0))
        except (TypeError, ValueError):
            continue
    if transcript_end <= 0:
        return None
    if transcript_end < source_duration * 0.5:
        return "The transcript covers much less time than the source duration and may be incomplete."
    if transcript_end > source_duration * 1.35:
        return "The transcript extends well beyond the source duration and may come from the wrong media."
    return None
