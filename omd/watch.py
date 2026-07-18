"""Helpers for polling a folder and converting newly stable files once."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import time

from omd.batch import (
    ProgressHook,
    _run_one,
    _emit,
    reserve_output_path,
)


WatchConvertOne = Callable[[Path, Path], int | None]
WatchFilter = Callable[[Path], bool]


@dataclass(frozen=True)
class WatchItemResult:
    source_path: Path
    output_path: Path
    status: str
    attempts: int
    return_code: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class WatchRunResult:
    inbox: Path
    out_dir: Path
    items: list[WatchItemResult]
    polls: int

    @property
    def processed(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.ok)

    @property
    def failed(self) -> int:
        return self.processed - self.succeeded

    @property
    def exit_code(self) -> int:
        return 0 if self.failed == 0 else 1


def run_watch(
    inbox: str | Path,
    out_dir: str | Path,
    convert_one: WatchConvertOne,
    *,
    retries: int = 0,
    poll_interval: float = 1.0,
    stable_polls: int = 2,
    max_polls: int | None = None,
    output_path_for: Callable[[str, Path, int], Path] | None = None,
    output_suffix: str = ".md",
    progress_hook: ProgressHook | None = None,
    path_filter: WatchFilter | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> WatchRunResult:
    """Poll `inbox` and convert each newly stable file once."""
    from omd import _progress

    inbox_path = Path(inbox)
    out_path = Path(out_dir)
    inbox_path.mkdir(parents=True, exist_ok=True)
    out_path.mkdir(parents=True, exist_ok=True)

    reserved_names: set[str] = set()
    observations: dict[Path, tuple[int, int, int]] = {}
    processed: set[Path] = set()
    results: list[WatchItemResult] = []
    polls = 0

    _emit(
        progress_hook,
        {
            "event": "watch_started",
            "inbox": str(inbox_path),
            "out_dir": str(out_path),
            "retries": max(0, retries),
            "stable_polls": max(1, stable_polls),
        },
    )
    _progress.info(f"watch: {inbox_path}")

    try:
        while max_polls is None or polls < max_polls:
            polls += 1
            stable_now = _discover_stable_files(
                inbox_path=inbox_path,
                out_dir=out_path,
                observations=observations,
                processed=processed,
                stable_polls=max(1, stable_polls),
                path_filter=path_filter,
                progress_hook=progress_hook,
            )
            for file_path in stable_now:
                processed.add(file_path)
                output_path = reserve_output_path(
                    str(file_path),
                    out_path,
                    len(results),
                    reserved_names,
                    output_path_for,
                    output_suffix=output_suffix,
                )
                _progress.info(f"watch: converting {file_path.name}")
                result = _run_one(
                    item=str(file_path),
                    output_path=output_path,
                    index=len(results) + 1,
                    total=len(results) + 1,
                    retries=max(0, retries),
                    convert_one=lambda item, output: convert_one(Path(item), output),
                    progress_hook=progress_hook,
                    label="watch",
                )
                results.append(
                    WatchItemResult(
                        source_path=file_path,
                        output_path=result.output_path,
                        status=result.status,
                        attempts=result.attempts,
                        return_code=result.return_code,
                        error=result.error,
                    )
                )
            if max_polls is None or polls < max_polls:
                sleep_fn(poll_interval)
    except KeyboardInterrupt:
        _progress.warn("watch interrupted")

    summary = WatchRunResult(inbox=inbox_path, out_dir=out_path, items=results, polls=polls)
    _emit(
        progress_hook,
        {
            "event": "watch_completed",
            "inbox": str(inbox_path),
            "out_dir": str(out_path),
            "polls": summary.polls,
            "processed": summary.processed,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
            "exit_code": summary.exit_code,
        },
    )
    if summary.failed:
        _progress.warn(
            f"watch complete with failures: {summary.succeeded}/{summary.processed} succeeded"
        )
    elif max_polls is not None:
        _progress.done(f"wrote {out_path}")
    return summary


def _discover_stable_files(
    *,
    inbox_path: Path,
    out_dir: Path,
    observations: dict[Path, tuple[int, int, int]],
    processed: set[Path],
    stable_polls: int,
    path_filter: WatchFilter | None,
    progress_hook: ProgressHook | None,
) -> list[Path]:
    stable: list[Path] = []
    current_files: set[Path] = set()

    for file_path in sorted(inbox_path.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("."):
            continue
        if _is_under(file_path, out_dir):
            continue
        if path_filter is not None and not path_filter(file_path):
            continue
        current_files.add(file_path)
        if file_path in processed:
            continue

        stat = file_path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        prev = observations.get(file_path)
        if prev is None or prev[:2] != signature:
            observations[file_path] = (signature[0], signature[1], 1)
            _emit(
                progress_hook,
                {
                    "event": "watch_file_seen",
                    "path": str(file_path),
                    "size": stat.st_size,
                },
            )
            if stable_polls <= 1:
                stable.append(file_path)
                _emit(
                    progress_hook,
                    {
                        "event": "watch_file_stable",
                        "path": str(file_path),
                        "size": stat.st_size,
                        "stable_polls": 1,
                    },
                )
            continue

        seen_count = prev[2] + 1
        observations[file_path] = (signature[0], signature[1], seen_count)
        if seen_count >= stable_polls:
            stable.append(file_path)
            _emit(
                progress_hook,
                {
                    "event": "watch_file_stable",
                    "path": str(file_path),
                    "size": stat.st_size,
                    "stable_polls": seen_count,
                },
            )

    for path in list(observations):
        if path not in current_files and path not in processed:
            observations.pop(path, None)

    return stable


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
