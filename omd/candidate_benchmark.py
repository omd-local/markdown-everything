"""Opt-in, privacy-safe benchmarks for non-default Phase 2 candidates.

The harness measures explicit local fixtures and caller-supplied candidates. It
does not discover a vault, make network requests, install dependencies, download
models, or alter OMD's production routing.
"""
from __future__ import annotations

import math
import re
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import __version__
from .eta_history import detect_device_tier
from .runtime_metrics import process_peak_memory_bytes


CANDIDATE_BENCHMARK_SCHEMA_VERSION = 1
VALID_DOMAINS = frozenset({"article", "asr", "vad"})
ARTICLE_TARGET_P95_SECONDS = 10.0
MAX_FIXTURE_BYTES = 512 * 1024 * 1024
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")


def _always_available() -> bool:
    return True


@dataclass(frozen=True)
class BenchmarkCase:
    """One explicit fixture and its behavioral quality expectations."""

    case_id: str
    domain: str
    source: Path
    required_terms: tuple[str, ...]
    duration_seconds: float | None = None
    min_retained_ratio: float | None = None
    max_retained_ratio: float | None = None

    def __post_init__(self) -> None:
        _require_token(self.case_id, name="case_id")
        if self.domain not in VALID_DOMAINS:
            raise ValueError(f"unsupported benchmark domain: {self.domain}")
        source = Path(self.source)
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise ValueError("benchmark source must be a regular non-symlink file") from exc
        if source.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("benchmark source must be a regular non-symlink file")
        if metadata.st_size > MAX_FIXTURE_BYTES:
            raise ValueError("benchmark source exceeds the fixture size limit")
        object.__setattr__(self, "source", source)

        if not self.required_terms or any(
            not isinstance(term, str) or not term.strip() for term in self.required_terms
        ):
            raise ValueError("required_terms must contain non-empty strings")
        object.__setattr__(
            self,
            "required_terms",
            tuple(term.strip().casefold() for term in self.required_terms),
        )

        if self.domain in {"asr", "vad"}:
            if not _positive_finite(self.duration_seconds):
                raise ValueError("audio benchmark cases require a positive duration_seconds")
        elif self.duration_seconds is not None:
            raise ValueError("article benchmark cases do not use duration_seconds")

        if self.domain == "vad":
            if not _ratio(self.min_retained_ratio) or not _ratio(self.max_retained_ratio):
                raise ValueError("VAD cases require retained-audio ratio bounds")
            if float(self.min_retained_ratio) > float(self.max_retained_ratio):
                raise ValueError("minimum retained ratio cannot exceed maximum retained ratio")
        elif self.min_retained_ratio is not None or self.max_retained_ratio is not None:
            raise ValueError("retained-audio ratio bounds are only valid for VAD cases")


@dataclass(frozen=True)
class CandidateOutput:
    """Candidate result held only long enough to score; never copied to reports."""

    text: str
    retained_audio_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("candidate output text must be a string")
        if self.retained_audio_seconds is not None and not _non_negative_finite(
            self.retained_audio_seconds
        ):
            raise ValueError("retained_audio_seconds must be finite and non-negative")


@dataclass(frozen=True)
class CandidateSpec:
    """Explicit candidate adapter; availability must not perform installation."""

    candidate_id: str
    domain: str
    run: Callable[[Path], CandidateOutput] = field(repr=False)
    available: Callable[[], bool] = field(default=_always_available, repr=False)

    def __post_init__(self) -> None:
        _require_token(self.candidate_id, name="candidate_id")
        if self.domain not in VALID_DOMAINS:
            raise ValueError(f"unsupported benchmark domain: {self.domain}")
        if not callable(self.run) or not callable(self.available):
            raise TypeError("candidate run and available values must be callable")


def run_candidate_benchmark(
    *,
    cases: Sequence[BenchmarkCase],
    candidates: Sequence[CandidateSpec],
    corpus_version: str,
    iterations: int = 1,
    include_vad: bool = False,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    """Run explicit candidates without exposing fixture paths or candidate text."""
    _require_token(corpus_version, name="corpus_version")
    if type(iterations) is not int or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    case_list = list(cases)
    candidate_list = [
        candidate for candidate in candidates if include_vad or candidate.domain != "vad"
    ]
    if any(not isinstance(case, BenchmarkCase) for case in case_list):
        raise TypeError("cases must contain BenchmarkCase values")
    if any(not isinstance(candidate, CandidateSpec) for candidate in candidate_list):
        raise TypeError("candidates must contain CandidateSpec values")

    reports = [
        _run_candidate(candidate, case_list, iterations=iterations, clock=clock)
        for candidate in candidate_list
    ]
    statuses = {str(report["status"]) for report in reports}
    if "fail" in statuses:
        status = "fail"
    elif reports and statuses == {"pass"}:
        status = "pass"
    else:
        status = "incomplete"
    return {
        "schema_version": CANDIDATE_BENCHMARK_SCHEMA_VERSION,
        "corpus_version": corpus_version,
        "app_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "device_tier": detect_device_tier(),
        "scope": "opt_in_candidate_benchmark_only",
        "status": status,
        "iterations": iterations,
        "vad_enabled": bool(include_vad),
        "candidates": reports,
        "claim_boundary": (
            "Candidate results do not change production defaults and do not authorise "
            "a public speed, quality, ASR, VAD, or ETA claim."
        ),
    }


def _run_candidate(
    candidate: CandidateSpec,
    cases: Sequence[BenchmarkCase],
    *,
    iterations: int,
    clock: Callable[[], float],
) -> dict[str, object]:
    matching = [case for case in cases if case.domain == candidate.domain]
    if not matching:
        return _skipped_candidate(candidate, "no_matching_cases")
    try:
        available = candidate.available()
    except Exception:  # noqa: BLE001 - availability details may contain private local paths
        available = False
    if available is not True:
        return _skipped_candidate(candidate, "runtime_unavailable")

    measurements: list[dict[str, object]] = []
    elapsed_values: list[float] = []
    quality_passes = 0
    failures = 0
    peak_memory: int | None = None
    for iteration in range(1, iterations + 1):
        for case in matching:
            started = clock()
            output: CandidateOutput | None = None
            failed = False
            try:
                output = candidate.run(case.source)
                if not isinstance(output, CandidateOutput):
                    raise TypeError("candidate must return CandidateOutput")
            except Exception:  # noqa: BLE001 - output report intentionally omits exception details
                failed = True
            elapsed = max(0.0, float(clock() - started))
            elapsed_values.append(elapsed)
            memory = process_peak_memory_bytes()
            if memory is not None:
                peak_memory = max(memory, peak_memory or 0)
            quality_passed, retained_ratio = _quality(case, output)
            if failed:
                quality_passed = False
            quality_passes += int(quality_passed)
            failures += int(failed)
            measurement: dict[str, object] = {
                "case_id": case.case_id,
                "iteration": iteration,
                "status": "error" if failed else "completed",
                "elapsed_seconds": round(elapsed, 6),
                "work_unit": "audio_seconds" if case.domain in {"asr", "vad"} else "items",
                "work_units": float(case.duration_seconds or 1.0),
                "quality_passed": quality_passed,
            }
            if retained_ratio is not None:
                measurement["retained_audio_ratio"] = round(retained_ratio, 6)
            measurements.append(measurement)

    ordered = sorted(elapsed_values)
    p95 = _nearest_rank(ordered, 0.95)
    latency_passed = candidate.domain != "article" or p95 <= ARTICLE_TARGET_P95_SECONDS
    quality_failures = len(measurements) - quality_passes
    status = "pass" if not failures and quality_failures == 0 and latency_passed else "fail"
    report: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "domain": candidate.domain,
        "status": status,
        "sample_count": len(measurements),
        "p50_seconds": round(_nearest_rank(ordered, 0.50), 6),
        "p95_seconds": round(p95, 6),
        "max_seconds": round(ordered[-1], 6),
        "quality_pass_count": quality_passes,
        "quality_failure_count": quality_failures,
        "runtime_error_count": failures,
        "cases": measurements,
    }
    if candidate.domain == "article":
        report["target_p95_seconds"] = ARTICLE_TARGET_P95_SECONDS
        report["latency_passed"] = latency_passed
    if peak_memory is not None:
        report["peak_memory_bytes"] = peak_memory
    return report


def _skipped_candidate(candidate: CandidateSpec, reason: str) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "domain": candidate.domain,
        "status": "skipped",
        "reason": reason,
        "sample_count": 0,
        "quality_pass_count": 0,
        "quality_failure_count": 0,
        "runtime_error_count": 0,
        "cases": [],
    }


def _quality(
    case: BenchmarkCase,
    output: CandidateOutput | None,
) -> tuple[bool, float | None]:
    if output is None:
        return False, None
    normalized = output.text.casefold()
    terms_present = all(term in normalized for term in case.required_terms)
    if case.domain != "vad":
        return terms_present, None
    if output.retained_audio_seconds is None or case.duration_seconds is None:
        return False, None
    retained_ratio = output.retained_audio_seconds / case.duration_seconds
    retention_passed = (
        float(case.min_retained_ratio) <= retained_ratio <= float(case.max_retained_ratio)
    )
    return terms_present and retention_passed, retained_ratio


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return float(values[index])


def _require_token(value: object, *, name: str) -> None:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ValueError(f"{name} must be a privacy-safe token")


def _positive_finite(value: object) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _non_negative_finite(value: object) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _ratio(value: object) -> bool:
    return _non_negative_finite(value) and float(value) <= 1.0
