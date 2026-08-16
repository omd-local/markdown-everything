"""Evaluate privacy-safe ETA calibration samples against existing gate thresholds."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ._io import write_atomic
from .eta_history import (
    DEFAULT_PIPELINE_VERSION,
    MIN_CALIBRATED_SAMPLES,
    MIN_CALIBRATION_IMPROVEMENT_RATIO,
    MIN_P90_COVERAGE,
    VALID_UNITS,
    _TOKEN_RE,
    privacy_safe_identity,
)


CALIBRATION_REPORT_SCHEMA_VERSION = 1
DEFAULT_BENCHMARK_ID = "shadow-calibration-v1"
LONG_DURATION_THRESHOLD_SECONDS = 30.0
SHORT_REGIME = "short"
LONG_REGIME = "long"
SHORT_ERROR_METRIC = "absolute_error_seconds"
LONG_ERROR_METRIC = "relative_absolute_error"
REASON_MIXED_DURATION_REGIMES = "requires_homogeneous_duration_regime"
REASON_INSUFFICIENT_SAMPLES = f"requires_at_least_{MIN_CALIBRATED_SAMPLES}_samples"
REASON_WEAK_IMPROVEMENT = (
    f"requires_at_least_{int(MIN_CALIBRATION_IMPROVEMENT_RATIO * 100)}_percent_improvement"
)
REASON_POOR_P90_COVERAGE = f"requires_at_least_{int(MIN_P90_COVERAGE * 100)}_percent_p90_coverage"
_MAX_CALIBRATION_INPUT_BYTES = 4 * 1024 * 1024
_MAX_CALIBRATION_SAMPLES = 10_000


@dataclass(frozen=True)
class EtaCalibrationSample:
    stage: str
    source: str
    device: str
    runtime: str
    model: str
    cold: bool
    unit: str
    actual_seconds: float
    baseline_seconds: float
    shadow_p50_seconds: float
    shadow_p90_seconds: float

    def __post_init__(self) -> None:
        for name in ("stage", "source", "device", "runtime"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
                raise ValueError(f"{name} must be a privacy-safe token")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a privacy-safe identity")
        if privacy_safe_identity(self.model) != self.model:
            raise ValueError("model must be a privacy-safe identity")
        if type(self.cold) is not bool:
            raise ValueError("cold must be true or false")
        if self.unit not in VALID_UNITS:
            raise ValueError(f"unsupported ETA unit: {self.unit}")
        for name in (
            "actual_seconds",
            "baseline_seconds",
            "shadow_p50_seconds",
            "shadow_p90_seconds",
        ):
            value = getattr(self, name)
            if not _is_positive_finite_number(value):
                raise ValueError(f"{name} must be positive and finite")
        if self.shadow_p90_seconds < self.shadow_p50_seconds:
            raise ValueError("shadow_p90_seconds must be greater than or equal to shadow_p50_seconds")


_SAMPLE_FIELDS = frozenset(field.name for field in fields(EtaCalibrationSample))


class EtaCalibrationStore:
    """Bounded local shadow samples; source content and locators are not schema fields."""

    def __init__(self, path: str | Path, *, max_count: int = 2000) -> None:
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.max_count = max(1, int(max_count))

    def record(self, sample: EtaCalibrationSample) -> bool:
        if not isinstance(sample, EtaCalibrationSample):
            raise TypeError("sample must be an EtaCalibrationSample")
        self._reject_symlink()
        with self._lock():
            samples = load_eta_calibration_samples(self.path) if self.path.exists() else []
            samples.append(sample)
            self._write(samples[-self.max_count :])
        return True

    def summary(self) -> dict[str, int]:
        self._reject_symlink()
        with self._lock():
            samples = load_eta_calibration_samples(self.path) if self.path.exists() else []
        return {
            "schema_version": CALIBRATION_REPORT_SCHEMA_VERSION,
            "sample_count": len(samples),
        }

    def reset(self) -> None:
        self._reject_symlink()
        with self._lock():
            self.path.unlink(missing_ok=True)

    def _write(self, samples: Sequence[EtaCalibrationSample]) -> None:
        payload = {
            "schema_version": CALIBRATION_REPORT_SCHEMA_VERSION,
            "samples": [asdict(sample) for sample in samples],
        }
        write_atomic(
            self.path,
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        )

    def _reject_symlink(self) -> None:
        if self.path.is_symlink():
            raise ValueError("calibration sample store must be a regular non-symlink file")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise ValueError("calibration sample lock must be a regular non-symlink file") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("calibration sample lock must be a regular non-symlink file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def load_eta_calibration_samples(path: str | Path) -> list[EtaCalibrationSample]:
    """Load an allowlisted local benchmark corpus without following symlinks."""
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("calibration input must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise ValueError("calibration input must be a regular non-symlink file") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_CALIBRATION_INPUT_BYTES:
            raise ValueError("calibration input must be a bounded regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read(_MAX_CALIBRATION_INPUT_BYTES + 1)
        if len(payload) > _MAX_CALIBRATION_INPUT_BYTES:
            raise ValueError("calibration input exceeds its size limit")
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("calibration input must be valid UTF-8 JSON") from exc
    if not isinstance(envelope, Mapping) or set(envelope) - {"schema_version", "samples"}:
        raise ValueError("calibration input must contain only schema_version and samples")
    if envelope.get("schema_version", CALIBRATION_REPORT_SCHEMA_VERSION) != CALIBRATION_REPORT_SCHEMA_VERSION:
        raise ValueError("unsupported calibration input schema_version")
    raw_samples = envelope.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("calibration input must contain a non-empty samples list")
    if len(raw_samples) > _MAX_CALIBRATION_SAMPLES:
        raise ValueError("calibration input contains too many samples")
    loaded: list[EtaCalibrationSample] = []
    for raw_sample in raw_samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("each calibration sample must be an object")
        keys = set(raw_sample)
        unexpected = keys - _SAMPLE_FIELDS
        missing = _SAMPLE_FIELDS - keys
        if unexpected:
            raise ValueError("unexpected calibration sample fields")
        if missing:
            raise ValueError("calibration sample is missing required fields")
        loaded.append(EtaCalibrationSample(**{key: raw_sample[key] for key in _SAMPLE_FIELDS}))
    return loaded


def evaluate_eta_calibration(
    samples: Sequence[EtaCalibrationSample],
    *,
    pipeline_version: str = DEFAULT_PIPELINE_VERSION,
    benchmark_id: str = DEFAULT_BENCHMARK_ID,
) -> dict[str, Any]:
    sample_list = list(samples)
    if not sample_list:
        raise ValueError("samples must contain at least one sample")
    if not isinstance(pipeline_version, str) or not _TOKEN_RE.fullmatch(pipeline_version):
        raise ValueError("pipeline_version must be a privacy-safe token")
    if not isinstance(benchmark_id, str) or not _TOKEN_RE.fullmatch(benchmark_id):
        raise ValueError("benchmark_id must be a privacy-safe token")
    for sample in sample_list:
        if not isinstance(sample, EtaCalibrationSample):
            raise TypeError("samples must contain EtaCalibrationSample values")

    regime_buckets: dict[str, list[EtaCalibrationSample]] = {SHORT_REGIME: [], LONG_REGIME: []}
    for sample in sample_list:
        regime_buckets[_duration_regime(sample.actual_seconds)].append(sample)

    public_regimes: dict[str, dict[str, Any]] = {}
    internal_regimes: dict[str, dict[str, Any]] = {}
    for name in (LONG_REGIME, SHORT_REGIME):
        bucket = regime_buckets[name]
        if bucket:
            internal = _regime_summary(name, bucket)
            internal_regimes[name] = internal
            public_regimes[name] = dict(internal)

    candidate_name = _select_candidate_name(internal_regimes)
    candidate = internal_regimes[candidate_name]
    eligible = bool(candidate["eligible"])
    reasons = [] if eligible else _report_reasons(internal_regimes, candidate_name)

    return {
        "schema_version": CALIBRATION_REPORT_SCHEMA_VERSION,
        "benchmark_id": benchmark_id,
        "pipeline_version": pipeline_version,
        "sample_count": len(sample_list),
        "selected_regime": candidate_name,
        "baseline_median_error": candidate["baseline_median_error"],
        "shadow_median_error": candidate["shadow_median_error"],
        "p90_upper_bound_coverage": candidate["p90_upper_bound_coverage"],
        "improvement_ratio": candidate["improvement_ratio"],
        "eligible": eligible,
        "ineligibility_reasons": reasons,
        "regimes": public_regimes,
        "segments": {
            "by_stage": _segment_summary(sample_list, key_name="stage"),
            "by_source": _segment_summary(sample_list, key_name="source"),
            "by_device": _segment_summary(sample_list, key_name="device"),
            "by_cold_warm": _segment_summary(sample_list, key_name="cold_warm"),
        },
    }


def apply_eta_calibration_gate(store: Any, report: Mapping[str, Any]) -> bool:
    if not isinstance(report, Mapping) or not bool(report.get("eligible")):
        return False
    selected_regime = report.get("selected_regime")
    regimes = report.get("regimes")
    if not isinstance(selected_regime, str) or not isinstance(regimes, Mapping):
        return False
    regime = regimes.get(selected_regime)
    if not isinstance(regime, Mapping):
        return False
    return bool(
        store.record_calibration_gate(
            benchmark_id=report.get("benchmark_id"),
            pipeline_version=report.get("pipeline_version"),
            sample_count=regime.get("sample_count"),
            baseline_median_error=report.get("baseline_median_error"),
            shadow_median_error=report.get("shadow_median_error"),
            p90_coverage=report.get("p90_upper_bound_coverage"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a privacy-safe baseline-versus-shadow ETA benchmark corpus."
    )
    parser.add_argument("--input", required=True, help="Local JSON benchmark sample file.")
    parser.add_argument("--output", required=True, help="Aggregate JSON calibration report.")
    parser.add_argument("--history", help="ETA history file that receives an eligible gate.")
    parser.add_argument("--apply", action="store_true", help="Apply an eligible gate to --history.")
    parser.add_argument("--pipeline-version", default=DEFAULT_PIPELINE_VERSION)
    parser.add_argument("--benchmark-id", default=DEFAULT_BENCHMARK_ID)
    args = parser.parse_args(argv)
    if args.apply and not args.history:
        parser.error("--apply requires --history")

    samples = load_eta_calibration_samples(args.input)
    report = evaluate_eta_calibration(
        samples,
        pipeline_version=args.pipeline_version,
        benchmark_id=args.benchmark_id,
    )
    gate_applied = False
    if args.apply:
        from .eta_history import EtaHistoryStore

        gate_applied = apply_eta_calibration_gate(EtaHistoryStore(args.history), report)
    output_report = dict(report)
    output_report["gate_applied"] = gate_applied

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(
        output,
        json.dumps(output_report, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
    )
    return 0 if report["eligible"] and (not args.apply or gate_applied) else 1


def _duration_regime(actual_seconds: float) -> str:
    return LONG_REGIME if actual_seconds >= LONG_DURATION_THRESHOLD_SECONDS else SHORT_REGIME


def _regime_summary(regime: str, samples: Sequence[EtaCalibrationSample]) -> dict[str, Any]:
    if regime == LONG_REGIME:
        error_metric = LONG_ERROR_METRIC
        baseline_errors = [
            abs(sample.baseline_seconds - sample.actual_seconds) / sample.actual_seconds for sample in samples
        ]
        shadow_errors = [
            abs(sample.shadow_p50_seconds - sample.actual_seconds) / sample.actual_seconds for sample in samples
        ]
    else:
        error_metric = SHORT_ERROR_METRIC
        baseline_errors = [abs(sample.baseline_seconds - sample.actual_seconds) for sample in samples]
        shadow_errors = [abs(sample.shadow_p50_seconds - sample.actual_seconds) for sample in samples]

    baseline_median_error = _round_metric(_median(baseline_errors))
    shadow_median_error = _round_metric(_median(shadow_errors))
    p90_upper_bound_coverage = _round_metric(
        sum(sample.shadow_p90_seconds >= sample.actual_seconds for sample in samples) / len(samples)
    )
    improvement_ratio = _round_metric(
        (baseline_median_error - shadow_median_error) / baseline_median_error
        if baseline_median_error > 0
        else 0.0
    )

    reasons: list[str] = []
    if len(samples) < MIN_CALIBRATED_SAMPLES:
        reasons.append(REASON_INSUFFICIENT_SAMPLES)
    if improvement_ratio < MIN_CALIBRATION_IMPROVEMENT_RATIO:
        reasons.append(REASON_WEAK_IMPROVEMENT)
    if p90_upper_bound_coverage < MIN_P90_COVERAGE:
        reasons.append(REASON_POOR_P90_COVERAGE)

    return {
        "sample_count": len(samples),
        "error_metric": error_metric,
        "baseline_median_error": baseline_median_error,
        "shadow_median_error": shadow_median_error,
        "p90_upper_bound_coverage": p90_upper_bound_coverage,
        "improvement_ratio": improvement_ratio,
        "eligible": not reasons,
        "ineligibility_reasons": reasons,
    }


def _select_candidate_name(regimes: Mapping[str, Mapping[str, Any]]) -> str:
    ordered = sorted(
        regimes.items(),
        key=lambda item: (
            1 if item[1]["eligible"] else 0,
            int(item[1]["sample_count"]),
            float(item[1]["improvement_ratio"]),
            float(item[1]["p90_upper_bound_coverage"]),
            1 if item[0] == LONG_REGIME else 0,
        ),
        reverse=True,
    )
    return ordered[0][0]


def _report_reasons(regimes: Mapping[str, Mapping[str, Any]], candidate_name: str) -> list[str]:
    reasons: list[str] = []
    if len(regimes) > 1 and not any(bool(summary["eligible"]) for summary in regimes.values()):
        reasons.append(REASON_MIXED_DURATION_REGIMES)
    for reason in regimes[candidate_name]["ineligibility_reasons"]:
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _segment_summary(
    samples: Sequence[EtaCalibrationSample],
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for sample in samples:
        if key_name == "cold_warm":
            key_value = "cold" if sample.cold else "warm"
        else:
            key_value = getattr(sample, key_name)
        bucket = grouped.setdefault(key_value, {"sample_count": 0, "regime_counts": {}})
        bucket["sample_count"] += 1
        regime = _duration_regime(sample.actual_seconds)
        bucket["regime_counts"][regime] = int(bucket["regime_counts"].get(regime, 0)) + 1
    records = []
    for key_value in sorted(grouped):
        record = {
            key_name: key_value,
            "sample_count": grouped[key_value]["sample_count"],
            "regime_counts": dict(sorted(grouped[key_value]["regime_counts"].items())),
        }
        records.append(record)
    return records


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _is_positive_finite_number(value: object) -> bool:
    return type(value) in {int, float} and not isinstance(value, bool) and math.isfinite(value) and value > 0


if __name__ == "__main__":
    raise SystemExit(main())
