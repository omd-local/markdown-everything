import pytest


def test_stage_progress_round_trips_determinate_work_units():
    from omd.stage_progress import StageProgress

    progress = StageProgress(
        stage_id="download",
        state="determinate",
        unit="bytes",
        completed=512,
        total=1024,
        elapsed_seconds=2.5,
        peak_memory_bytes=123456,
        item_index=1,
        item_total=3,
    )

    event = progress.to_event()
    restored = StageProgress.from_event(event)

    assert event["event"] == "progress"
    assert event["work_v"] == 2
    assert event["percent"] == 50.0
    assert event["peak_memory_bytes"] == 123456
    assert restored == progress


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"state": "determinate", "unit": "bytes", "completed": 1}, "total"),
        ({"state": "determinate", "unit": "bytes", "completed": 2, "total": 1}, "completed"),
        ({"state": "unknown"}, "state"),
        ({"state": "indeterminate", "unit": "percent"}, "unit"),
        ({"state": "indeterminate", "peak_memory_bytes": -1}, "peak_memory"),
    ],
)
def test_stage_progress_rejects_incoherent_work_state(kwargs, message):
    from omd.stage_progress import StageProgress

    with pytest.raises(ValueError, match=message):
        StageProgress(stage_id="convert", **kwargs)


def test_stage_progress_parses_legacy_progress_as_v1_fallback():
    from omd.stage_progress import StageProgress

    progress = StageProgress.from_event(
        {
            "v": 1,
            "event": "progress",
            "label": "Polish (md)",
            "cur": 3,
            "total": 12,
            "elapsed_s": 4.5,
        }
    )

    assert progress.stage_id == "polish"
    assert progress.unit == "items"
    assert progress.completed == 3
    assert progress.total == 12


def test_tracker_does_not_describe_active_item_as_completed():
    from omd.stage_progress import StructuredProgressTracker

    tracker = StructuredProgressTracker()
    tracker.apply({"event": "batch_started", "total": 5})
    view = tracker.apply({"event": "batch_item_started", "index": 1, "total": 5})

    assert view.detail == "item 1 of 5"
    assert view.item_summary == "5 queued"
    assert "0/5 done" not in f"{view.detail} {view.item_summary}"


def test_tracker_counts_only_terminal_item_events_as_processed():
    from omd.stage_progress import StructuredProgressTracker

    tracker = StructuredProgressTracker()
    tracker.apply({"event": "batch_started", "total": 5})
    tracker.apply({"event": "batch_item_started", "index": 1, "total": 5})
    view = tracker.apply({"event": "batch_item_succeeded", "index": 1, "total": 5})

    assert view.item_summary == "1/5 processed"
    assert view.succeeded == 1
    assert view.failed == 0


def test_tracker_resets_previous_item_percent_when_next_item_starts():
    from omd.stage_progress import StageProgress, StructuredProgressTracker

    tracker = StructuredProgressTracker()
    tracker.apply({"event": "batch_started", "total": 2})
    tracker.apply({"event": "batch_item_started", "index": 1, "total": 2})
    tracker.apply(
        StageProgress(
            stage_id="convert",
            state="determinate",
            unit="pages",
            completed=3,
            total=3,
            item_index=1,
            item_total=2,
        ).to_event()
    )
    tracker.apply({"event": "batch_item_succeeded", "index": 1, "total": 2})

    view = tracker.apply({"event": "batch_item_started", "index": 2, "total": 2})

    assert view.state == "indeterminate"
    assert view.percent is None
    assert view.detail == "item 2 of 2"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("indeterminate", "estimating"),
        ("retrying", "retrying"),
        ("needs_action", "paused"),
    ],
)
def test_tracker_never_fabricates_percent_for_non_determinate_state(state, expected):
    from omd.stage_progress import StageProgress, StructuredProgressTracker

    view = StructuredProgressTracker().apply(
        StageProgress(stage_id="fetch", state=state).to_event()
    )

    assert view.percent is None
    assert expected in view.eta_state


def test_structured_event_contains_no_source_locator_fields():
    from omd.stage_progress import StageProgress

    event = StageProgress(
        stage_id="ocr",
        state="determinate",
        unit="pages",
        completed=1,
        total=4,
    ).to_event()

    assert not {"url", "path", "filename", "title", "source_text"}.intersection(event)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_stage_progress_rejects_non_finite_work_values(value):
    from omd.stage_progress import StageProgress

    with pytest.raises(ValueError, match="completed"):
        StageProgress(
            stage_id="download",
            state="determinate",
            unit="bytes",
            completed=value,
            total=100,
        )


def test_tracker_ignores_non_finite_progress_event():
    from omd.stage_progress import StructuredProgressTracker

    view = StructuredProgressTracker().apply(
        {
            "event": "progress",
            "work_v": 2,
            "stage_id": "download",
            "state": "determinate",
            "unit": "bytes",
            "completed": float("nan"),
            "total": 100,
        }
    )

    assert view.state == "indeterminate"
    assert view.percent is None
