"""Synthetic Phase 2 receipt benchmark; never reads a user's vault or notes."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import __version__
from ._io import write_atomic
from .context_receipt import ContextOutbox
from .eta_history import detect_device_tier
from .inbox import InboxJob


BENCHMARK_SCHEMA_VERSION = 1
CORPUS_VERSION = "phase2-synthetic-v1"
DEFAULT_FILE_SIZES = (1024, 1024 * 1024)


def run_receipt_benchmark(
    *,
    iterations: int = 30,
    file_sizes: Sequence[int] = DEFAULT_FILE_SIZES,
    work_root: str | Path | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    """Measure only the durable foreground receipt path using synthetic inputs."""
    if type(iterations) is not int or iterations < 2:
        raise ValueError("iterations must be an integer of at least 2")
    sizes = tuple(int(value) for value in file_sizes)
    if not sizes or any(value <= 0 for value in sizes):
        raise ValueError("file_sizes must contain positive integers")

    if work_root is None:
        with tempfile.TemporaryDirectory(prefix="omd-phase2-benchmark-") as temp_root:
            scenarios = _run_scenarios(Path(temp_root), iterations, sizes, clock)
    else:
        root = Path(work_root)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="run-", dir=root) as temp_root:
            scenarios = _run_scenarios(Path(temp_root), iterations, sizes, clock)

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "corpus_version": CORPUS_VERSION,
        "app_version": _app_version(),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "device_tier": detect_device_tier(),
        "iterations": iterations,
        "status": "pass" if all(item["passed"] for item in scenarios) else "fail",
        "scope": "durable_context_receipt_only",
        "scenarios": scenarios,
        "claim_boundary": (
            "This report does not benchmark conversion, download, OCR, transcription, "
            "or AI completion time."
        ),
    }


def _run_scenarios(
    root: Path,
    iterations: int,
    file_sizes: tuple[int, ...],
    clock: Callable[[], float],
) -> list[dict[str, object]]:
    outbox_root = root / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    scenarios = [
        _measure_referenced(
            outbox,
            scenario="authored_note_receipt",
            source_type="text",
            iterations=iterations,
            target_seconds=0.5,
            clock=clock,
        ),
        _measure_referenced(
            outbox,
            scenario="url_job_receipt",
            source_type="url",
            iterations=iterations,
            target_seconds=1.0,
            clock=clock,
        ),
    ]
    for size in file_sizes:
        source = root / f"synthetic-{size}.bin"
        source.write_bytes(_synthetic_bytes(size))
        samples = []
        for index in range(iterations):
            job = _benchmark_job(f"local-{size}-{index}")
            started = clock()
            receipt = outbox.queue(
                job,
                source_type="local_file",
                destination="benchmark-vault/Inbox",
                privacy_mode="local_only",
            )
            outbox.secure_local_source(receipt.job_id, source)
            samples.append(max(0.0, clock() - started))
        scenarios.append(
            _scenario_report(
                f"local_file_{size}b_receipt",
                samples,
                target_seconds=2.0,
                input_bytes=size,
            )
        )
    return scenarios


def _measure_referenced(
    outbox: ContextOutbox,
    *,
    scenario: str,
    source_type: str,
    iterations: int,
    target_seconds: float,
    clock: Callable[[], float],
) -> dict[str, object]:
    samples = []
    for index in range(iterations):
        started = clock()
        outbox.queue(
            _benchmark_job(f"{scenario}-{index}"),
            source_type=source_type,
            destination="benchmark-vault/Inbox",
            privacy_mode="local_only",
        )
        samples.append(max(0.0, clock() - started))
    return _scenario_report(scenario, samples, target_seconds=target_seconds)


def _benchmark_job(identity: str) -> InboxJob:
    return InboxJob(
        job_type="capture",
        payload={"source_identity": f"synthetic_{identity}"},
        source="benchmark",
    )


def _scenario_report(
    scenario: str,
    samples: Sequence[float],
    *,
    target_seconds: float,
    input_bytes: int | None = None,
) -> dict[str, object]:
    ordered = sorted(float(value) for value in samples)
    p50 = _nearest_rank(ordered, 0.50)
    p95 = _nearest_rank(ordered, 0.95)
    report: dict[str, object] = {
        "scenario": scenario,
        "sample_count": len(ordered),
        "p50_seconds": round(p50, 6),
        "p95_seconds": round(p95, 6),
        "max_seconds": round(ordered[-1], 6),
        "target_p95_seconds": target_seconds,
        "passed": p95 <= target_seconds,
    }
    if input_bytes is not None:
        report["input_bytes"] = input_bytes
    return report


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return float(values[index])


def _synthetic_bytes(size: int) -> bytes:
    pattern = b"OMD phase 2 synthetic receipt benchmark\n"
    repeats, remainder = divmod(size, len(pattern))
    return pattern * repeats + pattern[:remainder]


def _app_version() -> str:
    return __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the synthetic Phase 2 durable Context Receipt path."
    )
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument(
        "--file-size",
        type=int,
        action="append",
        dest="file_sizes",
        help="Synthetic local-file size in bytes; repeat for multiple sizes.",
    )
    parser.add_argument("--output", required=True, help="JSON report path.")
    args = parser.parse_args(argv)
    report = run_receipt_benchmark(
        iterations=args.iterations,
        file_sizes=args.file_sizes or DEFAULT_FILE_SIZES,
    )
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(output, json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
