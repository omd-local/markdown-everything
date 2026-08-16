"""Helpers for list-driven batch conversions.

These helpers stay stdlib-only so CLI wiring can reuse them without pulling in
any external conversion dependencies. Callers provide the actual `convert_one`
function, typically a thin wrapper around the existing route functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable
import re
from urllib.parse import urlparse

from .work_scheduler import LaneLimits, ScheduledWork, classify_work_lane, run_bounded


ConvertOne = Callable[[str, Path], int | None]
OutputPathFor = Callable[[str, Path, int], Path]
ProgressHook = Callable[[dict[str, object]], None]
FinishPending = Callable[[], None]

_URL_RE = re.compile(r"https?://\S+")
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class BatchItemResult:
    item: str
    output_path: Path
    item_index: int
    item_total: int
    status: str
    attempts: int
    return_code: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class BatchRunResult:
    out_dir: Path
    items: list[BatchItemResult]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.ok)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    @property
    def exit_code(self) -> int:
        if self.total == 0:
            return 1
        return 0 if self.failed == 0 else 1


ItemSucceeded = Callable[[BatchItemResult], None]


def load_batch_items(input_file: str | Path) -> list[str]:
    """Load a batch list, skipping blank lines and `#` comments."""
    return list(iter_batch_items(Path(input_file).read_text(encoding="utf-8").splitlines()))


def iter_batch_items(lines: Iterable[str]) -> Iterable[str]:
    """Yield normalized batch inputs, ignoring blank lines and comments."""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        yield line


def default_output_path(
    item: str,
    out_dir: str | Path,
    index: int = 0,
    *,
    suffix: str = ".md",
) -> Path:
    """Derive a deterministic Markdown filename for a batch item."""
    out_root = Path(out_dir)
    stem = _derive_stem(item) or f"item-{index + 1}"
    return out_root / f"{stem}{_normalize_suffix(suffix)}"


def reserve_output_path(
    item: str,
    out_dir: str | Path,
    index: int,
    reserved: set[str],
    output_path_for: OutputPathFor | None = None,
    *,
    output_suffix: str = ".md",
) -> Path:
    """Return a unique output path for this run, avoiding in-batch collisions."""
    suffix = _normalize_suffix(output_suffix)
    if output_path_for is None:
        base = default_output_path(item, Path(out_dir), index, suffix=suffix)
    else:
        base = output_path_for(item, Path(out_dir), index)
    candidate = Path(base)
    if candidate.suffix.lower() != suffix.lower():
        candidate = candidate.with_suffix(suffix)

    stem = candidate.stem
    suffix = candidate.suffix
    attempt = 2
    while candidate.name in reserved:
        candidate = candidate.with_name(f"{stem}-{attempt}{suffix}")
        attempt += 1
    reserved.add(candidate.name)
    return candidate


def run_batch(
    input_list: Iterable[str],
    out_dir: str | Path,
    convert_one: ConvertOne,
    *,
    retries: int = 0,
    output_path_for: OutputPathFor | None = None,
    output_suffix: str = ".md",
    progress_hook: ProgressHook | None = None,
    finish_pending: FinishPending | None = None,
    on_item_succeeded: ItemSucceeded | None = None,
    lane_limits: LaneLimits | None = None,
) -> BatchRunResult:
    """Convert every item in `input_list`, continuing through partial failures."""
    from omd import _progress

    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    items = list(iter_batch_items(input_list))
    reserved_names: set[str] = set()
    results: list[BatchItemResult] = []

    _emit(
        progress_hook,
        {
            "event": "batch_started",
            "out_dir": str(out_root),
            "total": len(items),
            "retries": max(0, retries),
            "worker_plan": _worker_plan(lane_limits),
        },
    )
    if not items:
        if finish_pending is not None:
            finish_pending()
        _progress.warn("batch: no items to convert")
        summary = BatchRunResult(out_dir=out_root, items=results)
        _emit(
            progress_hook,
            {
                "event": "batch_completed",
                "out_dir": str(out_root),
                "total": summary.total,
                "succeeded": summary.succeeded,
                "failed": summary.failed,
                "exit_code": summary.exit_code,
            },
        )
        return summary

    planned: list[tuple[int, str, Path]] = []
    for index, item in enumerate(items, 1):
        planned.append(
            (
                index,
                item,
                reserve_output_path(
                    item,
                    out_root,
                    index - 1,
                    reserved_names,
                    output_path_for,
                    output_suffix=output_suffix,
                ),
            )
        )

    _progress.info(f"batch: {len(items)} items")
    with _progress.ProgressBar("Batch", total=len(items)) as bar:
        progress_lock = Lock()

        def process(index: int, item: str, output_path: Path) -> BatchItemResult:
            with progress_lock:
                _progress.info(f"[{index}/{len(items)}] {item}")
            result = _run_one(
                item=item,
                output_path=output_path,
                index=index,
                total=len(items),
                retries=max(0, retries),
                convert_one=convert_one,
                progress_hook=progress_hook,
                label="batch",
            )
            with progress_lock:
                if result.ok:
                    if on_item_succeeded is not None:
                        on_item_succeeded(result)
                    _progress.item_result(
                        f"[{index}/{len(items)}] converted: {result.output_path.name}"
                    )
                bar.update()
            return result

        if lane_limits is None:
            results = [process(index, item, output_path) for index, item, output_path in planned]
        else:
            scheduled = [
                ScheduledWork(
                    classify_work_lane(item),
                    lambda index=index, item=item, output_path=output_path: process(
                        index,
                        item,
                        output_path,
                    ),
                )
                for index, item, output_path in planned
            ]
            results = list(run_bounded(scheduled, lane_limits).values)

    if finish_pending is not None:
        finish_pending()

    summary = BatchRunResult(out_dir=out_root, items=results)
    _emit(
        progress_hook,
        {
            "event": "batch_completed",
            "out_dir": str(out_root),
            "total": summary.total,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
            "exit_code": summary.exit_code,
        },
    )
    if summary.failed:
        _progress.warn(
            f"batch complete with failures: {summary.succeeded}/{summary.total} succeeded"
        )
    else:
        _progress.done(f"wrote {out_root}")
    return summary


def _worker_plan(limits: LaneLimits | None) -> dict[str, int]:
    if limits is None:
        return {
            "global": 1,
            "convert": 1,
            "network": 1,
            "ocr": 1,
            "asr": 1,
            "model": 1,
        }
    return {
        "global": limits.global_workers,
        "convert": limits.convert,
        "network": limits.network,
        "ocr": limits.ocr,
        "asr": limits.asr,
        "model": limits.model,
    }


def _run_one(
    *,
    item: str,
    output_path: Path,
    index: int,
    total: int,
    retries: int,
    convert_one: ConvertOne,
    progress_hook: ProgressHook | None,
    label: str,
) -> BatchItemResult:
    attempts_allowed = retries + 1
    last_error: str | None = None
    last_rc = 1
    output_existed_before = output_path.exists()
    output_backup = _output_backup(output_path)
    output_signature_before = _output_signature(output_path)

    for attempt in range(1, attempts_allowed + 1):
        _emit(
            progress_hook,
            {
                "event": f"{label}_item_started",
                "item": item,
                "output": str(output_path),
                "index": index,
                "total": total,
                "attempt": attempt,
            },
        )
        try:
            from omd import _events

            with _events.item_context(index=index, total=total, attempt=attempt):
                rc_raw = convert_one(item, output_path)
            last_rc = 0 if rc_raw is None else int(rc_raw)
            last_error = None if last_rc == 0 else f"converter returned {last_rc}"
        except KeyboardInterrupt:
            raise
        except SystemExit as exc:
            last_rc = exc.code if isinstance(exc.code, int) else 1
            last_error = str(exc.code)
        except Exception as exc:  # pragma: no cover - defensive, exercised in tests
            last_rc = 1
            last_error = str(exc)

        if last_rc == 0 and not output_path.exists():
            last_rc = 1
            last_error = f"converter did not create output: {output_path}"
        elif last_rc == 0 and output_signature_before is not None and _output_signature(output_path) == output_signature_before:
            last_rc = 1
            last_error = f"converter did not refresh output: {output_path}"
        elif last_rc == 0 and _output_file_is_blank(output_path):
            last_rc = 1
            last_error = f"converter created empty output: {output_path}"

        if last_rc == 0:
            _emit(
                progress_hook,
                {
                    "event": f"{label}_item_succeeded",
                    "item": item,
                    "output": str(output_path),
                    "index": index,
                    "total": total,
                    "attempts": attempt,
                    "return_code": 0,
                },
            )
            return BatchItemResult(
                item=item,
                output_path=output_path,
                item_index=index,
                item_total=total,
                status="succeeded",
                attempts=attempt,
                return_code=0,
            )

        _restore_failed_output(output_path, existed_before=output_existed_before, backup=output_backup)
        if attempt < attempts_allowed:
            _emit(
                progress_hook,
                {
                    "event": f"{label}_item_retry",
                    "item": item,
                    "output": str(output_path),
                    "index": index,
                    "total": total,
                    "attempt": attempt,
                    "return_code": last_rc,
                    "error": last_error,
                },
            )
            from omd import _progress

            _progress.warn(
                f"[{index}/{total}] retry {attempt + 1}/{attempts_allowed} for {item}: {last_error}"
            )

    _emit(
        progress_hook,
        {
            "event": f"{label}_item_failed",
            "item": item,
            "output": str(output_path),
            "index": index,
            "total": total,
            "attempts": attempts_allowed,
            "return_code": last_rc,
            "error": last_error,
        },
    )
    from omd import _progress

    _progress.warn(f"[{index}/{total}] failed: {item}: {last_error}")
    return BatchItemResult(
        item=item,
        output_path=output_path,
        item_index=index,
        item_total=total,
        status="failed",
        attempts=attempts_allowed,
        return_code=last_rc,
        error=last_error,
    )


def _restore_failed_output(
    output_path: Path,
    *,
    existed_before: bool,
    backup: tuple[bytes, int] | None,
) -> None:
    if existed_before and backup is not None:
        try:
            from omd._io import write_atomic_bytes

            write_atomic_bytes(output_path, backup[0])
            output_path.chmod(backup[1])
        except OSError:
            pass
        return
    if existed_before:
        return
    try:
        if output_path.is_file() or output_path.is_symlink():
            output_path.unlink()
    except FileNotFoundError:
        return


def _output_backup(path: Path) -> tuple[bytes, int] | None:
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        return (path.read_bytes(), stat.st_mode & 0o777)
    except OSError:
        return None


def _output_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _output_file_is_blank(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for chunk in iter(lambda: handle.read(8192), ""):
                if chunk and chunk.strip():
                    return False
    except OSError:
        return False
    return True


def _derive_stem(item: str) -> str:
    url = _extract_url(item)
    if url:
        parsed = urlparse(url)
        parts = [parsed.netloc, parsed.path, parsed.query]
        candidate = "-".join(part for part in parts if part)
        return _slugify(candidate)[:80]

    path_name = Path(item).name
    if path_name:
        return _slugify(Path(path_name).stem)[:80]
    return ""


def _extract_url(item: str) -> str | None:
    match = _URL_RE.search(item)
    return match.group(0).rstrip(".,);!?]") if match else None


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug or "item"


def _normalize_suffix(suffix: str) -> str:
    suffix = suffix.strip() or ".md"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _emit(progress_hook: ProgressHook | None, event: dict[str, object]) -> None:
    if progress_hook is not None:
        progress_hook(dict(event))
