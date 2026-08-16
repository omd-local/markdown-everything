"""Privacy-minimised local calibration history for stage-level ETA."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ._io import write_atomic
from ._models import GIB, detect_total_memory_bytes


ETA_HISTORY_SCHEMA_VERSION = 1
DEFAULT_PIPELINE_VERSION = "omd-phase2-v1"
MIN_CALIBRATED_SAMPLES = 30
MIN_CALIBRATION_IMPROVEMENT_RATIO = 0.10
MIN_P90_COVERAGE = 0.90
MAX_CALIBRATION_GATES = 16
SUCCESS_OUTCOME = "success"
VALID_OUTCOMES = frozenset({SUCCESS_OUTCOME, "failed", "cancelled", "needs_action", "partial"})
VALID_UNITS = frozenset({"bytes", "pages", "pixels", "audio_seconds", "tokens", "items"})
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}")
_MAX_HISTORY_BYTES = 4 * 1024 * 1024


def privacy_safe_identity(value: object) -> str:
    """Keep registry-like model IDs; hash absolute/path-like or unsafe values."""
    raw = str(value or "unknown").strip()
    path_like = (
        not raw
        or raw.startswith(("/", "~", "."))
        or "\\" in raw
        or ".." in raw.split("/")
        or not _MODEL_RE.fullmatch(raw)
    )
    if path_like:
        return "sha256:" + hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    return raw


def detect_device_tier() -> str:
    """Return a coarse internal bucket; never a promise that a model will fit."""
    arch = re.sub(r"[^a-z0-9]+", "_", platform.machine().casefold()).strip("_") or "unknown"
    memory = detect_total_memory_bytes()
    if memory is None:
        memory_label = "ram_unknown"
    else:
        memory_gib = memory / GIB
        memory_label = next(
            label
            for upper, label in (
                (8, "8gb"),
                (16, "16gb"),
                (24, "24gb"),
                (32, "32gb"),
                (64, "64gb"),
                (math.inf, "64gb_plus"),
            )
            if memory_gib <= upper
        )
    cores = os.cpu_count()
    if cores is None:
        core_label = "core_unknown"
    elif cores <= 4:
        core_label = "4core"
    elif cores <= 8:
        core_label = "8core"
    elif cores <= 12:
        core_label = "12core"
    else:
        core_label = "16core_plus"
    return f"{arch}-{memory_label}-{core_label}"


@dataclass(frozen=True)
class EtaObservation:
    stage_id: str
    source_class: str
    device_tier: str
    runtime: str
    model_key: str
    cold_start: bool | None
    unit: str
    work_units: float
    duration_seconds: float
    outcome: str
    observed_at: float
    pipeline_version: str = DEFAULT_PIPELINE_VERSION
    attempt: int = 1
    queue_depth: int = 0
    throughput_per_second: float | None = None

    def __post_init__(self) -> None:
        for name in ("stage_id", "source_class", "device_tier", "runtime", "pipeline_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
                raise ValueError(f"{name} must be a privacy-safe token")
        safe_model = privacy_safe_identity(self.model_key)
        object.__setattr__(self, "model_key", safe_model)
        if self.unit not in VALID_UNITS:
            raise ValueError(f"unsupported ETA unit: {self.unit}")
        if not math.isfinite(self.work_units) or self.work_units <= 0:
            raise ValueError("work_units must be positive and finite")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive and finite")
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"unsupported ETA outcome: {self.outcome}")
        if not math.isfinite(self.observed_at) or self.observed_at <= 0:
            raise ValueError("observed_at must be a positive timestamp")
        if self.cold_start not in {True, False, None}:
            raise ValueError("cold_start must be true, false, or null")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if type(self.queue_depth) is not int or self.queue_depth < 0:
            raise ValueError("queue_depth must be a non-negative integer")
        if self.throughput_per_second is not None and (
            type(self.throughput_per_second) not in {int, float}
            or isinstance(self.throughput_per_second, bool)
            or not math.isfinite(self.throughput_per_second)
            or self.throughput_per_second <= 0
        ):
            raise ValueError("throughput_per_second must be positive and finite or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "source_class": self.source_class,
            "device_tier": self.device_tier,
            "runtime": self.runtime,
            "model_key": self.model_key,
            "cold_start": self.cold_start,
            "unit": self.unit,
            "work_units": self.work_units,
            "duration_seconds": self.duration_seconds,
            "outcome": self.outcome,
            "observed_at": self.observed_at,
            "pipeline_version": self.pipeline_version,
            "attempt": self.attempt,
            "queue_depth": self.queue_depth,
            "throughput_per_second": self.throughput_per_second,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EtaObservation:
        return cls(
            stage_id=str(value.get("stage_id") or ""),
            source_class=str(value.get("source_class") or ""),
            device_tier=str(value.get("device_tier") or ""),
            runtime=str(value.get("runtime") or ""),
            model_key=str(value.get("model_key") or ""),
            cold_start=value.get("cold_start"),
            unit=str(value.get("unit") or ""),
            work_units=float(value.get("work_units") or 0),
            duration_seconds=float(value.get("duration_seconds") or 0),
            outcome=str(value.get("outcome") or ""),
            observed_at=float(value.get("observed_at") or 0),
            pipeline_version=str(value.get("pipeline_version") or ""),
            attempt=int(value.get("attempt", 1)),
            queue_depth=int(value.get("queue_depth", 0)),
            throughput_per_second=(
                None
                if value.get("throughput_per_second") is None
                else float(value["throughput_per_second"])
            ),
        )


@dataclass(frozen=True)
class EtaEstimate:
    p50_seconds: float | None
    p90_seconds: float | None
    sample_count: int
    confidence: str
    source: str


class EtaHistoryStore:
    """Small atomic JSON store; no source locator or content fields exist in its schema."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_count: int = 2000,
        max_age_days: int = 90,
    ) -> None:
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.max_count = max(1, int(max_count))
        self.max_age_days = max(1, int(max_age_days))

    def record(self, observation: EtaObservation) -> bool:
        if not isinstance(observation, EtaObservation):
            raise TypeError("observation must be EtaObservation")
        with self._lock():
            state, warning, corrupt = self._load_unlocked()
            if corrupt:
                self._preserve_corrupt_unlocked()
                state = self._empty_state(enabled=True)
            if not state["enabled"]:
                return False
            observations = [EtaObservation.from_dict(value) for value in state["observations"]]
            observations.append(observation)
            state["observations"] = [value.to_dict() for value in self._retained(observations)]
            self._write_unlocked(state)
        return True

    def set_enabled(self, enabled: bool) -> None:
        with self._lock():
            state, _warning, corrupt = self._load_unlocked()
            if corrupt:
                self._preserve_corrupt_unlocked()
                state = self._empty_state(enabled=bool(enabled))
            state["enabled"] = bool(enabled)
            self._write_unlocked(state)

    def reset(self) -> None:
        with self._lock():
            state, _warning, corrupt = self._load_unlocked()
            if corrupt:
                self._preserve_corrupt_unlocked()
            self._write_unlocked(self._empty_state(enabled=bool(state["enabled"])))

    def record_calibration_gate(
        self,
        *,
        benchmark_id: str,
        sample_count: int,
        baseline_median_error: float,
        shadow_median_error: float,
        p90_coverage: float,
        pipeline_version: str = DEFAULT_PIPELINE_VERSION,
        recorded_at: float | None = None,
    ) -> bool:
        gate = _calibration_gate(
            pipeline_version=pipeline_version,
            benchmark_id=benchmark_id,
            sample_count=sample_count,
            baseline_median_error=baseline_median_error,
            shadow_median_error=shadow_median_error,
            p90_coverage=p90_coverage,
            recorded_at=recorded_at,
        )
        if gate is None:
            return False
        with self._lock():
            state, _warning, corrupt = self._load_unlocked()
            if corrupt:
                self._preserve_corrupt_unlocked()
                state = self._empty_state(enabled=True)
            calibration_gates = dict(state["calibration_gates"])
            calibration_gates[pipeline_version] = gate
            state["calibration_gates"] = _retained_calibration_gates(calibration_gates)
            self._write_unlocked(state)
        return True

    def summary(self) -> dict[str, Any]:
        if self.path.exists():
            with self._lock():
                state, warning, _corrupt = self._load_unlocked()
        else:
            state, warning = self._empty_state(enabled=True), None
        observations = [EtaObservation.from_dict(value) for value in state["observations"]]
        stages: dict[str, int] = {}
        for observation in observations:
            stages[observation.stage_id] = stages.get(observation.stage_id, 0) + 1
        return {
            "schema_version": ETA_HISTORY_SCHEMA_VERSION,
            "enabled": bool(state["enabled"]),
            "observation_count": len(observations),
            "successful_observation_count": sum(
                observation.outcome == SUCCESS_OUTCOME for observation in observations
            ),
            "calibrated_pipeline_versions": sorted(state["calibration_gates"]),
            "calibration_gate_count": len(state["calibration_gates"]),
            "stages": dict(sorted(stages.items())),
            "warning": warning,
        }

    def estimate(self, query: EtaObservation) -> EtaEstimate:
        """Return only a range authorised by the current calibration gate."""
        return self._estimate(query, require_gate=True)

    def shadow_estimate(self, query: EtaObservation) -> EtaEstimate:
        """Evaluate a mature history bucket without exposing it to the user."""
        return self._estimate(query, require_gate=False)

    def _estimate(self, query: EtaObservation, *, require_gate: bool) -> EtaEstimate:
        if self.path.exists():
            with self._lock():
                state, _warning, _corrupt = self._load_unlocked()
        else:
            state = self._empty_state(enabled=True)
        if not state["enabled"]:
            return EtaEstimate(None, None, 0, "indeterminate", "indeterminate")
        observations = [
            EtaObservation.from_dict(value)
            for value in state["observations"]
            if value.get("outcome") == SUCCESS_OUTCOME
        ]
        exact = [value for value in observations if _exact_bucket(value) == _exact_bucket(query)]
        parent = [value for value in observations if _parent_bucket(value) == _parent_bucket(query)]
        global_stage = [value for value in observations if _stage_bucket(value) == _stage_bucket(query)]
        if len(exact) >= MIN_CALIBRATED_SAMPLES:
            selected, source = exact, "exact"
        elif len(parent) >= MIN_CALIBRATED_SAMPLES:
            selected, source = parent, "parent"
        elif len(global_stage) >= MIN_CALIBRATED_SAMPLES:
            selected, source = global_stage, "stage"
        else:
            sample_count = max(len(exact), len(parent), len(global_stage))
            if sample_count:
                return EtaEstimate(None, None, sample_count, "collecting", "uncalibrated")
            return EtaEstimate(None, None, 0, "indeterminate", "indeterminate")
        if require_gate and query.pipeline_version not in state["calibration_gates"]:
            return EtaEstimate(None, None, len(selected), "collecting", "uncalibrated")

        now = time.time()
        values = [value.duration_seconds / value.work_units * query.work_units for value in selected]
        weights = [2 ** (-max(0.0, now - value.observed_at) / (30 * 86400)) for value in selected]
        p50 = _weighted_quantile(values, weights, 0.5)
        p90 = _weighted_quantile(values, weights, 0.9)
        confidence = "high" if source == "exact" else "low"
        return EtaEstimate(p50, p90, len(selected), confidence, source)

    def _retained(self, observations: Sequence[EtaObservation]) -> list[EtaObservation]:
        cutoff = time.time() - self.max_age_days * 86400
        recent = [value for value in observations if value.observed_at >= cutoff]
        return sorted(recent, key=lambda value: value.observed_at)[-self.max_count :]

    def _load_unlocked(self) -> tuple[dict[str, Any], str | None, bool]:
        if not self.path.exists():
            return self._empty_state(enabled=True), None, False
        try:
            value = json.loads(_read_history_text(self.path))
            if not isinstance(value, dict) or value.get("schema_version") != ETA_HISTORY_SCHEMA_VERSION:
                raise ValueError("unsupported schema")
            if not isinstance(value.get("enabled"), bool) or not isinstance(value.get("observations"), list):
                raise ValueError("invalid history envelope")
            observations = [EtaObservation.from_dict(item).to_dict() for item in value["observations"]]
            calibration_gates = _loaded_calibration_gates(value.get("calibration_gates"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._empty_state(enabled=True), "ETA history is corrupt; using no history.", True
        return {
            "schema_version": ETA_HISTORY_SCHEMA_VERSION,
            "enabled": value["enabled"],
            "observations": observations,
            "calibration_gates": calibration_gates,
        }, None, False

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        write_atomic(self.path, payload)

    def _preserve_corrupt_unlocked(self) -> None:
        if not self.path.exists():
            return
        if self.path.is_symlink():
            self.path.unlink()
            return
        backup = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
        os.replace(self.path, backup)

    @staticmethod
    def _empty_state(*, enabled: bool) -> dict[str, Any]:
        return {
            "schema_version": ETA_HISTORY_SCHEMA_VERSION,
            "enabled": enabled,
            "observations": [],
            "calibration_gates": {},
        }

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("ETA history lock must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _read_history_text(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("ETA history must be a regular file")
        if metadata.st_size > _MAX_HISTORY_BYTES:
            raise ValueError("ETA history exceeds its size limit")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read(_MAX_HISTORY_BYTES + 1)
        if len(payload) > _MAX_HISTORY_BYTES:
            raise ValueError("ETA history exceeds its size limit")
        return payload.decode("utf-8")
    finally:
        if fd >= 0:
            os.close(fd)


def live_stage_estimate(
    *,
    completed: float,
    total: float,
    elapsed_seconds: float,
) -> float | None:
    """Estimate remaining stage time only after real units provide enough evidence."""
    if total <= 0 or completed <= 0 or completed >= total or elapsed_seconds < 5:
        return None
    fraction = completed / total
    if fraction < 0.10:
        return None
    return round((elapsed_seconds / completed) * (total - completed), 2)


def _exact_bucket(value: EtaObservation) -> tuple[object, ...]:
    return (
        value.stage_id,
        value.source_class,
        value.device_tier,
        value.runtime,
        value.model_key,
        value.cold_start,
        value.unit,
        value.pipeline_version,
        value.attempt > 1,
        _queue_depth_bucket(value.queue_depth),
    )


def _parent_bucket(value: EtaObservation) -> tuple[object, ...]:
    return (
        value.stage_id,
        value.source_class,
        value.device_tier,
        value.unit,
        value.pipeline_version,
        value.attempt > 1,
        _queue_depth_bucket(value.queue_depth),
    )


def _stage_bucket(value: EtaObservation) -> tuple[object, ...]:
    return (
        value.stage_id,
        value.unit,
        value.pipeline_version,
        value.attempt > 1,
        _queue_depth_bucket(value.queue_depth),
    )


def _queue_depth_bucket(queue_depth: int) -> str:
    if queue_depth <= 0:
        return "empty"
    if queue_depth == 1:
        return "one"
    if queue_depth <= 4:
        return "two_to_four"
    return "five_plus"


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], quantile: float) -> float:
    ordered = sorted(zip(values, weights), key=lambda pair: pair[0])
    total_weight = sum(weight for _value, weight in ordered)
    threshold = total_weight * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return float(value)
    return float(ordered[-1][0])


def _calibration_gate(
    *,
    pipeline_version: str,
    benchmark_id: str,
    sample_count: int,
    baseline_median_error: float,
    shadow_median_error: float,
    p90_coverage: float,
    recorded_at: float | None,
) -> dict[str, Any] | None:
    if not isinstance(pipeline_version, str) or not _TOKEN_RE.fullmatch(pipeline_version):
        return None
    if not isinstance(benchmark_id, str) or not _TOKEN_RE.fullmatch(benchmark_id):
        return None
    if type(sample_count) is not int or sample_count < MIN_CALIBRATED_SAMPLES:
        return None
    if not _is_positive_finite_number(baseline_median_error):
        return None
    if not _is_positive_finite_number(shadow_median_error):
        return None
    if not _is_probability_number(p90_coverage):
        return None
    if shadow_median_error > baseline_median_error * (1.0 - MIN_CALIBRATION_IMPROVEMENT_RATIO):
        return None
    if p90_coverage < MIN_P90_COVERAGE:
        return None
    gate_recorded_at = time.time() if recorded_at is None else recorded_at
    if not _is_positive_finite_number(gate_recorded_at):
        return None
    return {
        "benchmark_id": benchmark_id,
        "sample_count": sample_count,
        "recorded_at": float(gate_recorded_at),
        "baseline_median_error": float(baseline_median_error),
        "shadow_median_error": float(shadow_median_error),
        "p90_coverage": float(p90_coverage),
    }


def _is_positive_finite_number(value: object) -> bool:
    return type(value) in {int, float} and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _is_probability_number(value: object) -> bool:
    return type(value) in {int, float} and not isinstance(value, bool) and math.isfinite(value) and 0.0 <= value <= 1.0


def _loaded_calibration_gates(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    loaded: dict[str, dict[str, Any]] = {}
    for pipeline_version, metadata in value.items():
        if not isinstance(pipeline_version, str) or not isinstance(metadata, Mapping):
            continue
        try:
            gate = _calibration_gate(
                pipeline_version=pipeline_version,
                benchmark_id=metadata.get("benchmark_id"),
                sample_count=metadata.get("sample_count"),
                baseline_median_error=metadata.get("baseline_median_error"),
                shadow_median_error=metadata.get("shadow_median_error"),
                p90_coverage=metadata.get("p90_coverage"),
                recorded_at=metadata.get("recorded_at"),
            )
        except (TypeError, ValueError):
            gate = None
        if gate is not None:
            loaded[pipeline_version] = gate
    return _retained_calibration_gates(loaded)


def _retained_calibration_gates(
    calibration_gates: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    retained = sorted(
        calibration_gates.items(),
        key=lambda item: float(item[1].get("recorded_at", 0.0)),
    )[-MAX_CALIBRATION_GATES:]
    return {pipeline_version: dict(metadata) for pipeline_version, metadata in retained}
