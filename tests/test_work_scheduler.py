from __future__ import annotations

import threading
import time

import pytest

from omd._models import GIB
from omd.work_scheduler import (
    EtaStage,
    ScheduledWork,
    classify_work_lane,
    critical_path_eta,
    lane_limits_for_memory,
    run_bounded,
)


def test_classify_work_lane_uses_network_for_urls():
    assert classify_work_lane("https://example.com/article") == "network"


def test_classify_work_lane_uses_ocr_for_local_images():
    assert classify_work_lane("/tmp/scan.PNG") == "ocr"


def test_classify_work_lane_uses_asr_for_local_media():
    assert classify_work_lane("/tmp/interview.m4a") == "asr"


def test_classify_work_lane_defaults_documents_to_convert():
    assert classify_work_lane("/tmp/report.pdf") == "convert"


def test_lane_limits_keep_sixteen_gib_machines_sequential():
    limits = lane_limits_for_memory(16 * GIB)

    assert limits.global_workers == 1
    assert limits.model == 1


def test_lane_limits_bound_larger_machines_and_keep_heavy_lanes_single():
    limits = lane_limits_for_memory(32 * GIB)

    assert limits.global_workers == 3
    assert limits.ocr == 1
    assert limits.asr == 1
    assert limits.model == 1


def test_lane_limits_allow_only_a_smaller_explicit_override():
    limits = lane_limits_for_memory(32 * GIB, requested_workers=2)

    assert limits.global_workers == 2


@pytest.mark.parametrize("requested", [0, -1, 4, True])
def test_lane_limits_reject_invalid_worker_override(requested):
    with pytest.raises(ValueError):
        lane_limits_for_memory(32 * GIB, requested_workers=requested)


def test_run_bounded_uses_sequential_fallback_without_threads():
    caller = threading.get_ident()
    seen: list[int] = []
    work = [
        ScheduledWork("convert", lambda value=value: seen.append(threading.get_ident()) or value)
        for value in (1, 2, 3)
    ]

    result = run_bounded(work, lane_limits_for_memory(16 * GIB))

    assert result.values == (1, 2, 3)
    assert seen == [caller, caller, caller]
    assert result.max_global_concurrency == 1


def test_run_bounded_preserves_input_order_when_tasks_finish_out_of_order():
    release_first = threading.Event()
    second_finished = threading.Event()

    def first():
        assert second_finished.wait(1)
        release_first.set()
        return "first"

    def second():
        second_finished.set()
        return "second"

    limits = lane_limits_for_memory(32 * GIB, requested_workers=2)
    result = run_bounded(
        [ScheduledWork("convert", first), ScheduledWork("network", second)],
        limits,
    )

    assert release_first.is_set()
    assert result.values == ("first", "second")


def test_run_bounded_enforces_global_worker_limit():
    active = 0
    observed = 0
    lock = threading.Lock()

    def task():
        nonlocal active, observed
        with lock:
            active += 1
            observed = max(observed, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return "ok"

    limits = lane_limits_for_memory(32 * GIB, requested_workers=2)
    result = run_bounded(
        [ScheduledWork("convert", task) for _ in range(4)],
        limits,
    )

    assert observed == 2
    assert result.max_global_concurrency == 2


def test_run_bounded_enforces_single_asr_worker():
    active = 0
    observed = 0
    lock = threading.Lock()

    def task():
        nonlocal active, observed
        with lock:
            active += 1
            observed = max(observed, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return "ok"

    limits = lane_limits_for_memory(32 * GIB)
    result = run_bounded([ScheduledWork("asr", task) for _ in range(3)], limits)

    assert observed == 1
    assert result.max_lane_concurrency["asr"] == 1


def test_run_bounded_propagates_task_exceptions():
    def fail():
        raise RuntimeError("conversion failed")

    with pytest.raises(RuntimeError, match="conversion failed"):
        run_bounded(
            [ScheduledWork("convert", fail)],
            lane_limits_for_memory(32 * GIB),
        )


def test_critical_path_eta_sums_sequential_stages():
    result = critical_path_eta(
        [
            EtaStage("convert", (), 10.0, 20.0),
            EtaStage("polish", ("convert",), 30.0, 50.0),
        ]
    )

    assert result == (40.0, 70.0)


def test_critical_path_eta_uses_longest_parallel_branch():
    result = critical_path_eta(
        [
            EtaStage("download", (), 5.0, 8.0),
            EtaStage("transcribe", ("download",), 30.0, 40.0),
            EtaStage("metadata", ("download",), 3.0, 4.0),
            EtaStage("compose", ("transcribe", "metadata"), 2.0, 3.0),
        ]
    )

    assert result == (37.0, 51.0)


def test_critical_path_eta_returns_none_when_a_required_estimate_is_unknown():
    result = critical_path_eta(
        [EtaStage("convert", (), None, None), EtaStage("polish", ("convert",), 2.0, 3.0)]
    )

    assert result is None


def test_critical_path_eta_rejects_cycles():
    with pytest.raises(ValueError, match="cycle"):
        critical_path_eta(
            [
                EtaStage("first", ("second",), 1.0, 2.0),
                EtaStage("second", ("first",), 1.0, 2.0),
            ]
        )
