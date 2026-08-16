"""Bridge structured CLI progress into truthful UI state and local ETA history."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .eta_calibration import EtaCalibrationSample
from .eta_history import (
    EtaHistoryStore,
    EtaObservation,
    detect_device_tier,
    live_stage_estimate,
    privacy_safe_identity,
)
from .stage_progress import (
    ProgressView,
    StageProgress,
    StructuredProgressTracker,
    display_stage,
)


_AUDIO_SUFFIXES = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"})
_VIDEO_SUFFIXES = frozenset({".avi", ".mkv", ".mov", ".mp4", ".mpeg", ".webm"})
_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
_MEDIA_HOSTS = ("youtube.com", "youtu.be", "douyin.com", "tiktok.com", "bilibili.com", "podcasts.apple.com")


@dataclass(frozen=True)
class TelemetryContext:
    source_class: str
    device_tier: str = field(default_factory=detect_device_tier)
    runtimes: Mapping[str, str] = field(default_factory=dict, repr=False)
    models: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name, value in (("source_class", self.source_class), ("device_tier", self.device_tier)):
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", value):
                raise ValueError(f"{name} must be a coarse privacy-safe token")
        object.__setattr__(
            self,
            "runtimes",
            {str(key): _safe_runtime(value) for key, value in self.runtimes.items()},
        )
        object.__setattr__(
            self,
            "models",
            {str(key): privacy_safe_identity(value) for key, value in self.models.items()},
        )

    def runtime_for(self, stage_id: str) -> str:
        if stage_id in self.runtimes:
            return self.runtimes[stage_id]
        return {
            "convert": "markitdown",
            "download": "network",
            "fetch": "network",
            "memory_cards": "ollama",
            "ocr": "tesseract",
            "polish": "ollama",
            "transcribe": "mlx",
        }.get(stage_id, "builtin")

    def model_for(self, stage_id: str) -> str:
        return self.models.get(stage_id, "none")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_class": self.source_class,
            "device_tier": self.device_tier,
            "runtimes": dict(self.runtimes),
            "models": dict(self.models),
        }


@dataclass(frozen=True)
class RunTelemetryUpdate:
    state: str
    label: str
    detail: str
    percent: int | None
    eta_label: str
    item_summary: str
    succeeded: int
    failed: int
    recorded_observation: bool = False


class RunTelemetrySession:
    """Consume one run's machine events; presentation logs remain a separate surface."""

    def __init__(
        self,
        history: EtaHistoryStore,
        context: TelemetryContext,
        *,
        calibration_store: Any | None = None,
    ) -> None:
        self.history = history
        self.context = context
        self.calibration_store = calibration_store
        self.tracker = StructuredProgressTracker()
        self._recorded: set[tuple[object, ...]] = set()
        self._shadow_predictions: dict[
            tuple[object, ...],
            tuple[float, float, float],
        ] = {}
        self._cold_start_by_stage: dict[tuple[object, ...], bool | None] = {}
        self._seen_runtime_models: set[tuple[str, str]] = set()

    def consume(self, event: Mapping[str, Any]) -> RunTelemetryUpdate:
        view = self.tracker.apply(event)
        progress = _progress_from_event(event)
        recorded = False
        if progress is not None:
            self._capture_shadow_prediction(progress)
            if _is_terminal_measurement(progress):
                recorded = self._record(progress)
                self._record_shadow_outcome(progress)
        eta_label = self._eta_label(progress, view)
        percent = view.percent
        if progress is not None and progress.state == "completed":
            if progress.total and progress.completed == progress.total:
                percent = 100
        return RunTelemetryUpdate(
            state=view.state,
            label=view.label,
            detail=view.detail,
            percent=percent,
            eta_label=eta_label,
            item_summary=view.item_summary,
            succeeded=view.succeeded,
            failed=view.failed,
            recorded_observation=recorded,
        )

    def _record(self, progress: StageProgress) -> bool:
        signature = self._stage_signature(progress)
        if signature in self._recorded:
            return False
        units = progress.total or progress.completed
        if progress.unit is None or units is None or units <= 0 or progress.elapsed_seconds <= 0:
            return False
        outcome = "success" if progress.state in {"completed", "determinate"} else progress.state
        try:
            recorded = self.history.record(
                EtaObservation(
                    stage_id=progress.stage_id,
                    source_class=self.context.source_class,
                    device_tier=self.context.device_tier,
                    runtime=self.context.runtime_for(progress.stage_id),
                    model_key=self.context.model_for(progress.stage_id),
                    cold_start=self._cold_start(progress),
                    unit=progress.unit,
                    work_units=float(units),
                    duration_seconds=progress.elapsed_seconds,
                    outcome=outcome,
                    observed_at=time.time(),
                    attempt=progress.attempt,
                    queue_depth=self._queue_depth(progress),
                    throughput_per_second=(
                        float(units) / progress.elapsed_seconds
                        if progress.elapsed_seconds > 0
                        else None
                    ),
                )
            )
        except (OSError, TypeError, ValueError):
            return False
        if recorded:
            self._recorded.add(signature)
        return recorded

    def _capture_shadow_prediction(self, progress: StageProgress) -> None:
        if self.calibration_store is None or progress.state != "determinate":
            return
        if progress.completed is None or progress.total is None:
            return
        signature = self._stage_signature(progress)
        if signature in self._shadow_predictions:
            return
        baseline_remaining = live_stage_estimate(
            completed=progress.completed,
            total=progress.total,
            elapsed_seconds=progress.elapsed_seconds,
        )
        if baseline_remaining is None:
            return
        shadow_estimate = getattr(self.history, "shadow_estimate", None)
        if not callable(shadow_estimate):
            return
        try:
            shadow = shadow_estimate(self._query(progress))
        except (OSError, TypeError, ValueError):
            return
        if shadow.p50_seconds is None or shadow.p90_seconds is None:
            return
        self._shadow_predictions[signature] = (
            progress.elapsed_seconds + baseline_remaining,
            float(shadow.p50_seconds),
            float(shadow.p90_seconds),
        )

    def _record_shadow_outcome(self, progress: StageProgress) -> bool:
        if self.calibration_store is None:
            return False
        prediction = self._shadow_predictions.pop(self._stage_signature(progress), None)
        if prediction is None or progress.unit is None or progress.elapsed_seconds <= 0:
            return False
        baseline_seconds, shadow_p50_seconds, shadow_p90_seconds = prediction
        try:
            return bool(
                self.calibration_store.record(
                    EtaCalibrationSample(
                        stage=progress.stage_id,
                        source=self.context.source_class,
                        device=self.context.device_tier,
                        runtime=self.context.runtime_for(progress.stage_id),
                        model=self.context.model_for(progress.stage_id),
                        cold=bool(self._cold_start(progress)),
                        unit=progress.unit,
                        actual_seconds=progress.elapsed_seconds,
                        baseline_seconds=baseline_seconds,
                        shadow_p50_seconds=shadow_p50_seconds,
                        shadow_p90_seconds=shadow_p90_seconds,
                    )
                )
            )
        except (OSError, TypeError, ValueError):
            return False

    def _eta_label(self, progress: StageProgress | None, view: ProgressView) -> str:
        if progress is None:
            return f"ETA: {view.eta_state}"
        if progress.state == "needs_action":
            return "ETA: paused; action needed"
        if progress.state == "retrying":
            return "ETA: paused during retry"
        if progress.state == "failed":
            return "ETA: unavailable"
        if progress.state == "cancelled":
            return "ETA: cancelled"
        if progress.state == "completed":
            return "ETA: stage complete"

        total = progress.total
        if progress.unit is not None and total is not None and total > 0:
            live = None
            if progress.completed is not None:
                live = live_stage_estimate(
                    completed=progress.completed,
                    total=total,
                    elapsed_seconds=progress.elapsed_seconds,
                )
            if live is not None:
                return f"Stage ETA: about {_format_duration(live)} from measured {progress.unit}"
            estimate = self.history.estimate(self._query(progress))
            if estimate.p50_seconds is not None and estimate.p90_seconds is not None:
                p50_remaining = max(0.0, estimate.p50_seconds - progress.elapsed_seconds)
                p90_remaining = max(p50_remaining, estimate.p90_seconds - progress.elapsed_seconds)
                qualifier = "high confidence" if estimate.confidence == "high" else "low confidence"
                return (
                    f"Stage ETA: usually {_format_duration(p50_remaining)}-"
                    f"{_format_duration(p90_remaining)}; {qualifier}"
                )
            if estimate.source == "uncalibrated" and estimate.sample_count > 0:
                return f"ETA: collecting timings for {display_stage(progress.stage_id)}"
            if progress.state == "determinate" and progress.completed is not None:
                return (
                    f"Stage ETA: measuring {progress.completed:g}/{total:g} "
                    f"{progress.unit.replace('_', ' ')}"
                )
        return f"ETA: estimating after {display_stage(progress.stage_id)} starts"

    def _query(self, progress: StageProgress) -> EtaObservation:
        return EtaObservation(
            stage_id=progress.stage_id,
            source_class=self.context.source_class,
            device_tier=self.context.device_tier,
            runtime=self.context.runtime_for(progress.stage_id),
            model_key=self.context.model_for(progress.stage_id),
            cold_start=self._cold_start(progress),
            unit=str(progress.unit),
            work_units=float(progress.total),
            duration_seconds=1.0,
            outcome="success",
            observed_at=time.time(),
            attempt=progress.attempt,
            queue_depth=self._queue_depth(progress),
            throughput_per_second=(
                float(progress.completed) / progress.elapsed_seconds
                if progress.completed is not None and progress.completed > 0 and progress.elapsed_seconds > 0
                else None
            ),
        )

    def _queue_depth(self, progress: StageProgress) -> int:
        item_total = progress.item_total or self.tracker.item_total or 1
        item_index = progress.item_index or self.tracker.active_item or 1
        return max(0, item_total - item_index)

    def _cold_start(self, progress: StageProgress) -> bool | None:
        signature = self._stage_signature(progress)
        if signature in self._cold_start_by_stage:
            return self._cold_start_by_stage[signature]
        runtime = self.context.runtime_for(progress.stage_id)
        model_key = self.context.model_for(progress.stage_id)
        if model_key == "none":
            self._cold_start_by_stage[signature] = None
            return None
        runtime_model = (runtime, model_key)
        cold_start = runtime_model not in self._seen_runtime_models
        self._seen_runtime_models.add(runtime_model)
        self._cold_start_by_stage[signature] = cold_start
        return cold_start

    def _stage_signature(self, progress: StageProgress) -> tuple[object, ...]:
        item_index = progress.item_index or self.tracker.active_item
        item_total = progress.item_total or self.tracker.item_total
        return (progress.stage_id, item_index, item_total, progress.attempt)


def telemetry_context_from_argv(argv: Sequence[str]) -> TelemetryContext:
    source_class = _source_class(argv)
    runtimes: dict[str, str] = {}
    models: dict[str, str] = {}
    if "--whisper-backend" in argv:
        runtimes["transcribe"] = _flag_value(argv, "--whisper-backend") or "mlx"
    if model := _flag_value(argv, "--model"):
        models["transcribe"] = model
    for flag, stage_id in (
        ("--polish-md-model", "polish"),
        ("--memory-model", "memory_cards"),
        ("--polish", "polish"),
    ):
        if model := _flag_value(argv, flag):
            models[stage_id] = model
    return TelemetryContext(source_class=source_class, runtimes=runtimes, models=models)


def parse_json_event(line: str) -> dict[str, Any] | None:
    if not line.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("event"), str) else None


def event_log_line(event: Mapping[str, Any]) -> str | None:
    event_type = str(event.get("event") or "")
    if event_type == "stage":
        return f"→ {display_stage(str(event.get('stage_id') or event.get('name') or 'work')).capitalize()}"
    if event_type == "stage_state":
        state = str(event.get("state") or "working").replace("_", " ")
        return f"→ {display_stage(str(event.get('stage_id') or 'work')).capitalize()}: {state}"
    if event_type == "progress":
        return None
    if event_type == "batch_started":
        return f"→ Batch accepted: {_event_counter(event.get('total'))} items"
    if event_type == "batch_item_started":
        return (
            f"→ Processing item {_event_counter(event.get('index'))} "
            f"of {_event_counter(event.get('total'))}"
        )
    if event_type == "batch_item_succeeded":
        return (
            f"✓ Item {_event_counter(event.get('index'))} "
            f"of {_event_counter(event.get('total'))} converted"
        )
    if event_type == "batch_item_failed":
        return (
            f"warn: Item {_event_counter(event.get('index'))} "
            f"of {_event_counter(event.get('total'))} failed"
        )
    if event_type == "batch_item_retry":
        return (
            f"warn: Retrying item {_event_counter(event.get('index'))} "
            f"of {_event_counter(event.get('total'))}"
        )
    if event_type == "batch_completed":
        return (
            f"→ Batch complete: {_event_counter(event.get('succeeded'), allow_zero=True)} succeeded, "
            f"{_event_counter(event.get('failed'), allow_zero=True)} failed"
        )
    if event_type == "warn":
        return f"warn: {str(event.get('message') or 'conversion warning')}"
    if event_type == "error":
        return f"error: {str(event.get('message') or event.get('kind') or 'conversion failed')}"
    if event_type == "done":
        return "✓ Output written"
    return None


def _event_counter(value: object, *, allow_zero: bool = False) -> str:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        return "?"
    return str(value)


def _progress_from_event(event: Mapping[str, Any]) -> StageProgress | None:
    if event.get("event") not in {"progress", "stage", "stage_state"}:
        return None
    try:
        return StageProgress.from_event(event)
    except (TypeError, ValueError):
        return None


def _is_terminal_measurement(progress: StageProgress) -> bool:
    if progress.state == "completed":
        return True
    return (
        progress.state == "determinate"
        and progress.completed is not None
        and progress.total is not None
        and progress.completed >= progress.total
    )


def _source_class(argv: Sequence[str]) -> str:
    lowered = [str(value).casefold() for value in argv]
    if "batch" in lowered or "--batch" in lowered:
        return "batch"
    candidates = [value for value in argv if isinstance(value, str) and not value.startswith("-")]
    joined = " ".join(candidates).casefold()
    if any(host in joined for host in _MEDIA_HOSTS):
        return "media"
    for candidate in candidates:
        suffix = Path(urlparse(candidate).path).suffix.casefold()
        if suffix in _AUDIO_SUFFIXES or suffix in _VIDEO_SUFFIXES:
            return "media"
        if suffix == ".pdf":
            return "pdf"
        if suffix in _IMAGE_SUFFIXES:
            return "image"
    if any(value.startswith(("http://", "https://")) for value in lowered):
        return "web"
    return "file"


def _flag_value(argv: Sequence[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
        value = argv[index + 1]
    except (ValueError, IndexError):
        return None
    return str(value) if value else None


def _safe_runtime(value: object) -> str:
    raw = str(value or "unknown").casefold()
    token = re.sub(r"[^a-z0-9._-]+", "_", raw).strip("_")[:96]
    return token or "unknown"


def _format_duration(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    if rounded < 60:
        return f"{rounded}s"
    minutes, seconds = divmod(rounded, 60)
    return f"{minutes}m {seconds:02d}s"
