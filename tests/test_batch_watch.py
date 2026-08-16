from __future__ import annotations

from pathlib import Path
import threading

from omd.batch import (
    default_output_path,
    iter_batch_items,
    load_batch_items,
    run_batch,
)
from omd.watch import run_watch
from omd.work_scheduler import lane_limits_for_memory
from omd._models import GIB


def test_load_batch_items_skips_blank_lines_and_comments(tmp_path):
    src = tmp_path / "urls.txt"
    src.write_text(
        "\n# comment\nhttps://example.com/one\n   \n  # also comment\n/tmp/report.pdf\n",
        encoding="utf-8",
    )

    assert load_batch_items(src) == ["https://example.com/one", "/tmp/report.pdf"]


def test_iter_batch_items_normalizes_lines():
    assert list(iter_batch_items(["  # skip  ", "", " https://example.com "])) == [
        "https://example.com"
    ]


def test_default_output_path_uses_stable_slug_for_urls(tmp_path):
    out = default_output_path(
        "share blob https://example.com/a/b?x=1 and more",
        tmp_path,
    )

    assert out == tmp_path / "example-com-a-b-x-1.md"


def test_default_output_path_can_use_rmarkdown_suffix(tmp_path):
    out = default_output_path("https://example.com/a", tmp_path, suffix=".Rmd")

    assert out == tmp_path / "example-com-a.Rmd"


def test_run_batch_retries_partial_failures_and_emits_events(tmp_path):
    attempts: dict[str, int] = {}
    events: list[dict[str, object]] = []

    def convert_one(item: str, output: Path) -> int:
        attempts[item] = attempts.get(item, 0) + 1
        if item == "first":
            output.write_text("ok\n", encoding="utf-8")
            return 0
        if item == "flaky" and attempts[item] == 1:
            raise RuntimeError("transient")
        if item == "flaky":
            output.write_text("recovered\n", encoding="utf-8")
            return 0
        return 7

    result = run_batch(
        ["# ignore", "first", "flaky", "bad"],
        tmp_path,
        convert_one,
        retries=1,
        progress_hook=events.append,
    )

    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1
    assert result.exit_code == 1
    assert [item.status for item in result.items] == ["succeeded", "succeeded", "failed"]
    assert [item.attempts for item in result.items] == [1, 2, 2]
    assert attempts == {"first": 1, "flaky": 2, "bad": 2}
    assert result.items[0].output_path == tmp_path / "first.md"
    assert result.items[1].output_path == tmp_path / "flaky.md"
    assert result.items[2].output_path == tmp_path / "bad.md"
    assert any(event["event"] == "batch_item_retry" for event in events)
    assert events[0]["event"] == "batch_started"
    assert events[-1]["event"] == "batch_completed"


def test_run_batch_parallel_lanes_preserve_result_order(tmp_path):
    second_finished = threading.Event()

    def convert_one(item: str, output: Path) -> int:
        if item == "first.pdf":
            assert second_finished.wait(1)
        else:
            second_finished.set()
        output.write_text(item, encoding="utf-8")
        return 0

    result = run_batch(
        ["first.pdf", "second.pdf"],
        tmp_path,
        convert_one,
        lane_limits=lane_limits_for_memory(32 * GIB, requested_workers=2),
    )

    assert [item.item for item in result.items] == ["first.pdf", "second.pdf"]
    assert [item.output_path.read_text(encoding="utf-8") for item in result.items] == [
        "first.pdf",
        "second.pdf",
    ]


def test_run_batch_reports_effective_worker_plan(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(_item: str, output: Path) -> int:
        output.write_text("ok\n", encoding="utf-8")
        return 0

    limits = lane_limits_for_memory(32 * GIB, requested_workers=2)
    run_batch(
        ["one.pdf"],
        tmp_path,
        convert_one,
        lane_limits=limits,
        progress_hook=events.append,
    )

    assert events[0]["worker_plan"] == {
        "global": 2,
        "convert": 2,
        "network": 2,
        "ocr": 1,
        "asr": 1,
        "model": 1,
    }


def test_run_batch_default_remains_sequential(tmp_path):
    active = 0
    observed = 0
    lock = threading.Lock()

    def convert_one(item: str, output: Path) -> int:
        nonlocal active, observed
        with lock:
            active += 1
            observed = max(observed, active)
        output.write_text(item, encoding="utf-8")
        with lock:
            active -= 1
        return 0

    run_batch(["first.pdf", "second.pdf"], tmp_path, convert_one)

    assert observed == 1


def test_run_batch_parallel_callback_runs_as_each_item_finishes(tmp_path):
    callback_seen = threading.Event()

    def convert_one(item: str, output: Path) -> int:
        if item == "second.pdf":
            assert callback_seen.wait(1)
        output.write_text(item, encoding="utf-8")
        return 0

    def on_succeeded(result):
        if result.item == "first.pdf":
            callback_seen.set()

    run_batch(
        ["first.pdf", "second.pdf"],
        tmp_path,
        convert_one,
        on_item_succeeded=on_succeeded,
        lane_limits=lane_limits_for_memory(32 * GIB, requested_workers=2),
    )

    assert callback_seen.is_set()


def test_batch_success_result_keeps_item_index_and_total(tmp_path):
    seen = []

    def convert_one(item: str, output: Path) -> int:
        output.write_text(item, encoding="utf-8")
        return 0

    run_batch(
        ["first.pdf", "second.pdf"],
        tmp_path,
        convert_one,
        on_item_succeeded=seen.append,
    )

    assert [(result.item_index, result.item_total) for result in seen] == [(1, 2), (2, 2)]


def test_run_batch_logs_each_success_as_a_result(tmp_path, capsys):
    def convert_one(_item: str, output: Path) -> int:
        output.write_text("ok\n", encoding="utf-8")
        return 0

    run_batch(["one"], tmp_path, convert_one)

    assert "[1/1] converted:" in capsys.readouterr().err


def test_batch_event_contract_includes_required_fields(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(item: str, output: Path) -> int:
        if item == "ok":
            output.write_text("ok\n", encoding="utf-8")
            return 0
        return 3

    run_batch(["ok", "bad"], tmp_path, convert_one, progress_hook=events.append)

    started = next(event for event in events if event["event"] == "batch_started")
    item_started = next(event for event in events if event["event"] == "batch_item_started")
    succeeded = next(event for event in events if event["event"] == "batch_item_succeeded")
    failed = next(event for event in events if event["event"] == "batch_item_failed")
    completed = next(event for event in events if event["event"] == "batch_completed")

    assert {"event", "out_dir", "total", "retries"} <= set(started)
    assert {"event", "item", "output", "index", "total", "attempt"} <= set(item_started)
    assert {"event", "item", "output", "index", "total", "attempts", "return_code"} <= set(succeeded)
    assert {"event", "item", "output", "index", "total", "attempts", "return_code", "error"} <= set(failed)
    assert {"event", "out_dir", "total", "succeeded", "failed", "exit_code"} <= set(completed)


def test_run_batch_fails_when_list_has_no_items(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(_item: str, _output: Path) -> int:
        raise AssertionError("empty batch should not convert")

    result = run_batch(["", "# comment", "   "], tmp_path, convert_one, progress_hook=events.append)

    assert result.total == 0
    assert result.succeeded == 0
    assert result.failed == 0
    assert result.exit_code == 1
    assert events[0]["event"] == "batch_started"
    assert events[-1]["event"] == "batch_completed"
    assert events[-1]["exit_code"] == 1


def test_run_batch_uses_rmarkdown_suffix(tmp_path):
    def convert_one(_item: str, output: Path) -> int:
        output.write_text("# ok\n", encoding="utf-8")
        return 0

    result = run_batch(["https://example.com/a"], tmp_path, convert_one, output_suffix=".Rmd")

    assert result.exit_code == 0
    assert result.items[0].output_path == tmp_path / "example-com-a.Rmd"


def test_run_batch_catches_system_exit_and_dedupes_outputs(tmp_path):
    def convert_one(item: str, output: Path) -> int:
        if item == "same.pdf":
            output.write_text(item, encoding="utf-8")
            return 0
        raise SystemExit("error: boom")

    result = run_batch(["same.pdf", "same.mp3"], tmp_path, convert_one)

    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[0].output_path == tmp_path / "same.md"
    assert result.items[1].output_path == tmp_path / "same-2.md"
    assert result.items[1].return_code == 1
    assert "boom" in (result.items[1].error or "")


def test_run_batch_dedupes_rmarkdown_outputs(tmp_path):
    def convert_one(item: str, output: Path) -> int:
        output.write_text(item, encoding="utf-8")
        return 0

    result = run_batch(["same.pdf", "same.mp3"], tmp_path, convert_one, output_suffix=".Rmd")

    assert result.items[0].output_path == tmp_path / "same.Rmd"
    assert result.items[1].output_path == tmp_path / "same-2.Rmd"


def test_run_batch_removes_new_partial_output_after_failure(tmp_path):
    def convert_one(_item: str, output: Path) -> int:
        output.write_text("partial\n", encoding="utf-8")
        return 9

    result = run_batch(["bad"], tmp_path, convert_one)

    assert result.failed == 1
    assert result.items[0].output_path == tmp_path / "bad.md"
    assert not (tmp_path / "bad.md").exists()


def test_run_batch_fails_when_converter_returns_success_without_output(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(_item: str, _output: Path) -> int:
        return 0

    result = run_batch(["missing-output"], tmp_path, convert_one, progress_hook=events.append)

    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert result.items[0].return_code == 1
    assert "did not create output" in (result.items[0].error or "")
    assert not any(event["event"] == "batch_item_succeeded" for event in events)


def test_run_batch_fails_when_converter_returns_success_with_empty_output(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(_item: str, output: Path) -> int:
        output.write_text("", encoding="utf-8")
        return 0

    result = run_batch(["empty-output"], tmp_path, convert_one, progress_hook=events.append)

    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "created empty output" in (result.items[0].error or "")
    assert not (tmp_path / "empty-output.md").exists()
    assert not any(event["event"] == "batch_item_succeeded" for event in events)


def test_run_batch_fails_when_converter_returns_success_with_whitespace_only_output(tmp_path):
    events: list[dict[str, object]] = []

    def convert_one(_item: str, output: Path) -> int:
        output.write_text("  \n\t\n", encoding="utf-8")
        return 0

    result = run_batch(["blank-output"], tmp_path, convert_one, progress_hook=events.append)

    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "created empty output" in (result.items[0].error or "")
    assert not (tmp_path / "blank-output.md").exists()
    assert not any(event["event"] == "batch_item_succeeded" for event in events)


def test_run_batch_fails_when_converter_returns_success_without_refreshing_existing_output(tmp_path):
    existing = tmp_path / "stale.md"
    existing.write_text("old output\n", encoding="utf-8")

    def convert_one(_item: str, _output: Path) -> int:
        return 0

    result = run_batch(["stale"], tmp_path, convert_one)

    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "did not refresh output" in (result.items[0].error or "")
    assert existing.read_text(encoding="utf-8") == "old output\n"


def test_run_batch_preserves_existing_output_after_failure(tmp_path):
    existing = tmp_path / "bad.md"
    existing.write_text("previous good output\n", encoding="utf-8")

    def convert_one(_item: str, _output: Path) -> int:
        return 9

    result = run_batch(["bad"], tmp_path, convert_one)

    assert result.failed == 1
    assert existing.read_text(encoding="utf-8") == "previous good output\n"


def test_run_batch_restores_existing_output_when_failed_converter_overwrites_it(tmp_path):
    existing = tmp_path / "bad.md"
    existing.write_text("previous good output\n", encoding="utf-8")

    def convert_one(_item: str, output: Path) -> int:
        output.write_text("failed replacement\n", encoding="utf-8")
        return 9

    result = run_batch(["bad"], tmp_path, convert_one)

    assert result.failed == 1
    assert existing.read_text(encoding="utf-8") == "previous good output\n"


def test_run_watch_waits_for_stability_and_processes_once(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    source = inbox / "report.pdf"
    source.write_text("payload", encoding="utf-8")
    calls: list[Path] = []

    def convert_one(src: Path, output: Path) -> int:
        calls.append(src)
        output.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        poll_interval=0,
        stable_polls=2,
        max_polls=4,
    )

    assert result.polls == 4
    assert result.processed == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert calls == [source]
    assert (out_dir / "report.md").read_text(encoding="utf-8") == "payload"


def test_run_watch_uses_rmarkdown_suffix(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    source = inbox / "report.pdf"
    source.write_text("payload", encoding="utf-8")

    def convert_one(src: Path, output: Path) -> int:
        output.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        poll_interval=0,
        stable_polls=1,
        max_polls=1,
        output_suffix=".Rmd",
    )

    assert result.succeeded == 1
    assert result.items[0].output_path == out_dir / "report.Rmd"
    assert (out_dir / "report.Rmd").read_text(encoding="utf-8") == "payload"


def test_run_watch_respects_filter_and_retries_failures(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    (inbox / "ignore.tmp").write_text("x", encoding="utf-8")
    source = inbox / "clip.mp3"
    source.write_text("audio", encoding="utf-8")
    attempts = {"clip.mp3": 0}
    events: list[dict[str, object]] = []

    def convert_one(src: Path, output: Path) -> int:
        attempts[src.name] += 1
        if attempts[src.name] == 1:
            return 9
        output.write_text("done", encoding="utf-8")
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        retries=1,
        poll_interval=0,
        stable_polls=2,
        max_polls=3,
        path_filter=lambda path: path.suffix == ".mp3",
        progress_hook=events.append,
    )

    assert result.processed == 1
    assert result.succeeded == 1
    assert attempts["clip.mp3"] == 2
    assert (out_dir / "clip.md").read_text(encoding="utf-8") == "done"
    assert any(event["event"] == "watch_item_retry" for event in events)
    assert not (out_dir / "ignore.md").exists()


def test_run_watch_fails_when_converter_returns_success_without_output(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    source = inbox / "report.pdf"
    source.write_text("payload", encoding="utf-8")
    events: list[dict[str, object]] = []

    def convert_one(_src: Path, _output: Path) -> int:
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        poll_interval=0,
        stable_polls=1,
        max_polls=1,
        progress_hook=events.append,
    )

    assert result.processed == 1
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "did not create output" in (result.items[0].error or "")
    assert not (out_dir / "report.md").exists()
    assert not any(event["event"] == "watch_item_succeeded" for event in events)


def test_run_watch_fails_when_converter_returns_success_with_empty_output(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    source = inbox / "report.pdf"
    source.write_text("payload", encoding="utf-8")
    events: list[dict[str, object]] = []

    def convert_one(_src: Path, output: Path) -> int:
        output.write_text("", encoding="utf-8")
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        poll_interval=0,
        stable_polls=1,
        max_polls=1,
        progress_hook=events.append,
    )

    assert result.processed == 1
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "created empty output" in (result.items[0].error or "")
    assert not (out_dir / "report.md").exists()
    assert not any(event["event"] == "watch_item_succeeded" for event in events)


def test_run_watch_fails_when_converter_returns_success_with_whitespace_only_output(tmp_path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "markdown"
    inbox.mkdir()
    source = inbox / "report.pdf"
    source.write_text("payload", encoding="utf-8")
    events: list[dict[str, object]] = []

    def convert_one(_src: Path, output: Path) -> int:
        output.write_text("  \n\t\n", encoding="utf-8")
        return 0

    result = run_watch(
        inbox,
        out_dir,
        convert_one,
        poll_interval=0,
        stable_polls=1,
        max_polls=1,
        progress_hook=events.append,
    )

    assert result.processed == 1
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.exit_code == 1
    assert result.items[0].status == "failed"
    assert "created empty output" in (result.items[0].error or "")
    assert not (out_dir / "report.md").exists()
    assert not any(event["event"] == "watch_item_succeeded" for event in events)
