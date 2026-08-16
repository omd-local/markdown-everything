"""Conservative work-lane limits and ETA critical-path helpers."""

from __future__ import annotations

import math
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, Mapping, Sequence, TypeVar
from urllib.parse import urlparse

from ._models import GIB


HARD_MAX_WORKERS = 3
VALID_LANES = frozenset({"convert", "network", "ocr", "asr", "model"})
_STAGE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
_MEDIA_SUFFIXES = frozenset(
    {
        ".aac",
        ".flac",
        ".m4a",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
    }
)

T = TypeVar("T")


@dataclass(frozen=True)
class LaneLimits:
    global_workers: int
    convert: int
    network: int
    ocr: int = 1
    asr: int = 1
    model: int = 1

    def __post_init__(self) -> None:
        values = (
            self.global_workers,
            self.convert,
            self.network,
            self.ocr,
            self.asr,
            self.model,
        )
        if any(type(value) is not int or value < 1 or value > HARD_MAX_WORKERS for value in values):
            raise ValueError(f"lane limits must be integers from 1 to {HARD_MAX_WORKERS}")
        if any(value > self.global_workers for value in values[1:]):
            raise ValueError("a lane limit cannot exceed the global worker limit")
        if self.model != 1:
            raise ValueError("the local model lane must remain single-worker")

    def for_lane(self, lane: str) -> int:
        if lane not in VALID_LANES:
            raise ValueError(f"unsupported work lane: {lane}")
        return int(getattr(self, lane))


@dataclass(frozen=True)
class ScheduledWork(Generic[T]):
    lane: str
    call: Callable[[], T]

    def __post_init__(self) -> None:
        if self.lane not in VALID_LANES:
            raise ValueError(f"unsupported work lane: {self.lane}")
        if not callable(self.call):
            raise TypeError("scheduled work must provide a callable")


@dataclass(frozen=True)
class ScheduleResult(Generic[T]):
    values: tuple[T, ...]
    max_global_concurrency: int
    max_lane_concurrency: Mapping[str, int]


@dataclass(frozen=True)
class EtaStage:
    stage_id: str
    dependencies: tuple[str, ...]
    p50_seconds: float | None
    p90_seconds: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.stage_id, str) or not _STAGE_ID_RE.fullmatch(self.stage_id):
            raise ValueError("stage_id must be a stable lowercase token")
        if any(not isinstance(value, str) or not _STAGE_ID_RE.fullmatch(value) for value in self.dependencies):
            raise ValueError("dependencies must contain stable lowercase stage tokens")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must not contain duplicates")
        if (self.p50_seconds is None) != (self.p90_seconds is None):
            raise ValueError("p50 and p90 estimates must both be present or both be unknown")
        if self.p50_seconds is not None:
            if not _finite_non_negative(self.p50_seconds) or not _finite_non_negative(self.p90_seconds):
                raise ValueError("ETA durations must be finite non-negative numbers")
            if float(self.p90_seconds) < float(self.p50_seconds):
                raise ValueError("p90 duration must be greater than or equal to p50")


def classify_work_lane(source: str | Path) -> str:
    """Classify a source without opening it or performing network I/O."""
    raw = str(source).strip()
    parsed = urlparse(raw)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
        return "network"
    suffix = Path(raw).suffix.casefold()
    if suffix in _IMAGE_SUFFIXES:
        return "ocr"
    if suffix in _MEDIA_SUFFIXES:
        return "asr"
    return "convert"


def lane_limits_for_memory(
    total_memory_bytes: int | None,
    *,
    requested_workers: int | None = None,
) -> LaneLimits:
    """Return small machine-aware caps; explicit overrides may only reduce them."""
    if total_memory_bytes is None or type(total_memory_bytes) is not int or total_memory_bytes <= 16 * GIB:
        automatic = 1
    elif total_memory_bytes <= 24 * GIB:
        automatic = 2
    else:
        automatic = HARD_MAX_WORKERS

    workers = automatic
    if requested_workers is not None:
        if (
            type(requested_workers) is not int
            or requested_workers < 1
            or requested_workers > HARD_MAX_WORKERS
        ):
            raise ValueError(f"requested_workers must be an integer from 1 to {HARD_MAX_WORKERS}")
        if requested_workers > automatic:
            raise ValueError("requested_workers cannot exceed the machine-aware limit")
        workers = requested_workers

    return LaneLimits(
        global_workers=workers,
        convert=workers,
        network=workers,
        ocr=1,
        asr=1,
        model=1,
    )


def run_bounded(
    work: Sequence[ScheduledWork[T]],
    limits: LaneLimits,
) -> ScheduleResult[T]:
    """Run work within global and per-lane caps while preserving result order."""
    items = tuple(work)
    if not items:
        return ScheduleResult((), 0, {lane: 0 for lane in sorted(VALID_LANES)})

    if limits.global_workers == 1:
        values: list[T] = []
        max_by_lane = {lane: 0 for lane in sorted(VALID_LANES)}
        for item in items:
            values.append(item.call())
            max_by_lane[item.lane] = 1
        return ScheduleResult(tuple(values), 1, max_by_lane)

    semaphores = {
        lane: threading.BoundedSemaphore(limits.for_lane(lane))
        for lane in VALID_LANES
    }
    lock = threading.Lock()
    active_global = 0
    active_by_lane = {lane: 0 for lane in sorted(VALID_LANES)}
    max_global = 0
    max_by_lane = dict(active_by_lane)

    def invoke(item: ScheduledWork[T]) -> T:
        nonlocal active_global, max_global
        semaphore = semaphores[item.lane]
        with semaphore:
            with lock:
                active_global += 1
                active_by_lane[item.lane] += 1
                max_global = max(max_global, active_global)
                max_by_lane[item.lane] = max(
                    max_by_lane[item.lane],
                    active_by_lane[item.lane],
                )
            try:
                return item.call()
            finally:
                with lock:
                    active_global -= 1
                    active_by_lane[item.lane] -= 1

    executor = ThreadPoolExecutor(
        max_workers=limits.global_workers,
        thread_name_prefix="omd-work",
    )
    futures: list[Future[T]] = []
    try:
        futures = [executor.submit(invoke, item) for item in items]
        values = tuple(future.result() for future in futures)
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return ScheduleResult(values, max_global, max_by_lane)


def critical_path_eta(stages: Sequence[EtaStage]) -> tuple[float, float] | None:
    """Return the longest dependency path, or no estimate when any stage is unknown."""
    by_id: dict[str, EtaStage] = {}
    for stage in stages:
        if stage.stage_id in by_id:
            raise ValueError(f"duplicate ETA stage: {stage.stage_id}")
        by_id[stage.stage_id] = stage
    for stage in by_id.values():
        missing = [dependency for dependency in stage.dependencies if dependency not in by_id]
        if missing:
            raise ValueError(f"unknown ETA dependency: {missing[0]}")

    visiting: set[str] = set()
    completed: dict[str, tuple[float, float] | None] = {}

    def finish(stage_id: str) -> tuple[float, float] | None:
        if stage_id in completed:
            return completed[stage_id]
        if stage_id in visiting:
            raise ValueError("ETA stage plan contains a cycle")
        visiting.add(stage_id)
        stage = by_id[stage_id]
        dependency_ranges = [finish(dependency) for dependency in stage.dependencies]
        visiting.remove(stage_id)
        if stage.p50_seconds is None or any(value is None for value in dependency_ranges):
            result = None
        else:
            prior_p50 = max((value[0] for value in dependency_ranges if value is not None), default=0.0)
            prior_p90 = max((value[1] for value in dependency_ranges if value is not None), default=0.0)
            result = (
                prior_p50 + float(stage.p50_seconds),
                prior_p90 + float(stage.p90_seconds),
            )
        completed[stage_id] = result
        return result

    ranges = [finish(stage_id) for stage_id in by_id]
    if not ranges:
        return (0.0, 0.0)
    if any(value is None for value in ranges):
        return None
    return (
        max(value[0] for value in ranges if value is not None),
        max(value[1] for value in ranges if value is not None),
    )


def _finite_non_negative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )
