"""Audio metadata and progress helpers."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        value = float(proc.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def estimated_transcribe_seconds(path: Path) -> int | None:
    duration = duration_seconds(path)
    if duration is None:
        return None
    try:
        speed = float(os.environ.get("OMD_TRANSCRIBE_SPEED_HINT", "8"))
    except ValueError:
        speed = 8.0
    speed = max(0.1, speed)
    return max(1, int(duration / speed))


def run_with_estimated_progress(cmd: list[str], audio: Path, label: str) -> subprocess.CompletedProcess:
    """Run a blocking audio tool while rendering an estimated progress bar.

    mlx_whisper does not expose stable machine-readable progress. This estimates
    completion from media duration and an adjustable speed hint, then holds at
    95% until the subprocess exits.
    """
    from omd import _progress
    from omd import _events

    if _progress.is_verbose():
        return subprocess.run(cmd, check=True)

    if _events.is_enabled():
        duration = duration_seconds(audio)
        _events.stage(
            "transcribing",
            stage_id="transcribe",
            state="indeterminate",
            unit="audio_seconds",
            total=duration,
        )
        started = time.monotonic()
        try:
            result = _run_captured(cmd)
        except BaseException:
            _events.stage_state(
                "transcribe",
                "failed",
                elapsed_s=time.monotonic() - started,
                unit="audio_seconds",
                total=duration,
            )
            raise
        _events.stage_state(
            "transcribe",
            "completed",
            elapsed_s=time.monotonic() - started,
            unit="audio_seconds",
            completed=duration,
            total=duration,
        )
        return result

    estimate = estimated_transcribe_seconds(audio)
    if estimate is None:
        return _run_captured(cmd)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    start = time.monotonic()
    with _progress.ProgressBar(label, total=100) as bar:
        while proc.poll() is None:
            elapsed = time.monotonic() - start
            pct = min(95, int((elapsed / estimate) * 100))
            bar.set(pct)
            time.sleep(0.25)
        bar.set(100)
    stdout, stderr = proc.communicate()
    if proc.returncode:
        if stdout:
            sys.stderr.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _run_captured(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        if e.stdout:
            sys.stderr.write(e.stdout)
        if e.stderr:
            sys.stderr.write(e.stderr)
        raise
