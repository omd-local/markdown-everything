from __future__ import annotations

import importlib.util
import json
import os
from datetime import date
from pathlib import Path
import sys
import time
import zipfile

import pytest

from omd import ui


@pytest.fixture(autouse=True)
def _stable_public_dns(monkeypatch):
    monkeypatch.setattr(
        "omd._network_policy.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


def build_gradio_config_or_skip():
    try:
        import gradio  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"Gradio UI dependencies unavailable: {exc}")
    return ui.build_app().get_config_file()


def load_hf_smoke_module():
    smoke_path = Path(__file__).resolve().parents[1] / "demo" / "huggingface-space" / "smoke.py"
    spec = importlib.util.spec_from_file_location("omd_hf_smoke", smoke_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_cookie_paths_use_user_data_dir():
    assert Path(ui.DEFAULT_COOKIES).name == "douyin_cookies.txt"
    assert Path(ui.DEFAULT_COOKIES).parent == ui.COOKIES_STAGING
    assert ".local/share/omd/cookies" in str(ui.COOKIES_STAGING)


def test_source_quick_start_mentions_douyin_and_xhs_examples():
    assert "https://v.douyin.com/yGWf39cCbCE/" in ui.SOURCE_PLACEHOLDER
    assert "http://xhslink.com/a/abcDEF/" in ui.SOURCE_PLACEHOLDER
    assert "https://www.reddit.com/r/.../comments/..." in ui.SOURCE_PLACEHOLDER
    assert "/Users/me/Downloads/report.pdf" in ui.SOURCE_PLACEHOLDER
    assert "Drop one or more files" in ui.SOURCE_QUICK_START
    assert "Inspect source / cookies" in ui.SOURCE_QUICK_START
    assert "Capture to vault note" in ui.SOURCE_QUICK_START
    assert "for Obsidian" in ui.SOURCE_QUICK_START
    assert "Typed text wins over the uploaded file" in ui.SOURCE_QUICK_START
    assert "Batch .txt" not in ui.SOURCE_QUICK_START
    assert f"ollama pull {ui.STARTUP_RECOMMENDED_TEXT_MODEL}" in ui.LOCAL_MODEL_NOTICE
    assert "Open Terminal" in ui.LOCAL_MODEL_NOTICE
    assert "Ollama host" in ui.LOCAL_MODEL_NOTICE
    assert "Personal note use only" in ui.PROCESS_LOG_DISCLAIMER
    assert "not a legal, compliance, evidentiary, or archival system" in ui.PROCESS_LOG_DISCLAIMER
    assert "Output may omit, reorder, or reformat source content" in ui.PROCESS_LOG_DISCLAIMER
    assert "does not bypass paywalls, access controls, or platform restrictions" in ui.PROCESS_LOG_DISCLAIMER
    assert "#### Use and access" in ui.PROCESS_LOG_DISCLAIMER
    assert "authorised to access and store" in ui.PROCESS_LOG_DISCLAIMER
    assert "VPN or proxy IP blocking" in ui.PROCESS_LOG_DISCLAIMER
    assert "data-centre IP filtering" in ui.PROCESS_LOG_DISCLAIMER
    assert "workplace or organisation network restrictions" in ui.PROCESS_LOG_DISCLAIMER
    assert "rate limits after repeated requests" in ui.PROCESS_LOG_DISCLAIMER
    assert "private, age- or NSFW-gated, quarantined, deleted, or requires sign-in" in ui.PROCESS_LOG_DISCLAIMER
    assert "does not necessarily mean the link is invalid" in ui.PROCESS_LOG_DISCLAIMER
    assert "#### Check what was saved" in ui.PROCESS_LOG_DISCLAIMER
    assert ".omd-legal-note h3" in ui.CUSTOM_CSS
    assert ".omd-legal-note h4" in ui.CUSTOM_CSS


def test_eta_detail_estimates_remaining_time():
    assert ui._eta_detail(0.0, None, now=10.0) == "ETA: working"
    assert ui._eta_detail(0.0, 25, now=3.0) == "ETA: working"
    assert ui._eta_detail(0.0, 25, now=10.0) == "ETA: ~30s"
    assert ui._eta_detail(0.0, 95, now=125.0) == "ETA: finishing"
    assert ui._eta_detail(0.0, 100, now=125.0) == "ETA: done"


def test_total_elapsed_detail_formats_completed_run_duration():
    assert ui._total_elapsed_detail(100.0, now=225.0) == "Total: 2m 05s"


def test_eta_estimator_waits_for_stable_progress_before_estimating():
    eta = ui._EtaEstimator(started_at=0.0, initial_range=(60, 180))

    assert eta.label(None, now=0.0) == "ETA: ~1m 00s-3m 00s"
    assert eta.label(20, now=10.0) == "ETA: ~50s-2m 50s"
    assert eta.label(55, now=70.0) == "ETA: ≤1m 50s"
    assert eta.label(82, now=100.0) == "ETA: ~27s"
    assert eta.label(95, now=101.0) == "ETA: finishing"


def test_initial_eta_range_uses_task_type_and_batch_size(tmp_path):
    batch = tmp_path / "items.txt"
    batch.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert ui._initial_eta_range(["omd", "batch", str(batch), "--polish-md"]) == (180, 540)
    assert ui._initial_eta_range(["omd", "/tmp/report.pdf"]) == (10, 60)
    assert ui._initial_eta_range(["omd", "https://youtu.be/abc"]) == (60, 240)


def test_initial_eta_range_counts_capture_batch_items(tmp_path):
    batch = tmp_path / "capture-items.txt"
    batch.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert ui._initial_eta_range(
        ["omd", "capture", str(batch), "--batch", "--memory-cards"]
    ) == (180, 540)


def test_warning_log_lines_raise_warning_status():
    assert ui._status_state_for_log_line("warn: no generated tags were returned") == "warn"
    assert ui._status_state_for_log_line("→ OMD will not download models automatically") == "warn"
    assert ui._status_state_for_log_line("→ Recommended explicit pulls: qwen3:4b") == "warn"
    assert ui._status_state_for_log_line("→ Fetching Reddit post") == "running"
    assert ".omd-status-warn" in ui.CUSTOM_CSS


def test_error_log_lines_raise_error_status():
    assert ui._status_state_for_log_line("error: converter failed") == "err"
    assert ui._status_state_for_log_line("Traceback (most recent call last):") == "err"
    assert ".omd-status-err" in ui.CUSTOM_CSS


def test_run_status_keeps_error_label_during_following_traceback_lines(tmp_path, monkeypatch):
    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "convert"], tmp_path / "missing.md"))
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter([
            ("err", "Traceback (most recent call last):"),
            ("err", '  File "converter.py", line 1, in convert'),
            ("rc", "1"),
        ]),
    )

    updates = list(ui.run_with_status("unused"))

    traceback_detail_status = updates[2][3]
    assert 'class="omd-status-err"' in traceback_detail_status
    assert '<span class="omd-status-label">error</span>' in traceback_detail_status


def test_run_status_consumes_structured_progress_without_showing_raw_json(tmp_path, monkeypatch):
    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    output = tmp_path / "result.md"
    output.write_text("# Result\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setenv("OMD_ETA_HISTORY_PATH", str(tmp_path / "eta-history.json"))
    monkeypatch.setattr(
        ui,
        "_build_argv",
        lambda *_args: (["omd", "https://example.com/private", "--json-events"], output),
    )
    events = [
        {
            "v": 1,
            "event": "stage",
            "name": "converting",
            "work_v": 2,
            "stage_id": "convert",
            "state": "indeterminate",
            "elapsed_s": 0,
            "attempt": 1,
        },
        {
            "v": 1,
            "event": "progress",
            "label": "Convert",
            "cur": 1,
            "total": 2,
            "percent": 50.0,
            "elapsed_s": 2.0,
            "eta_s": 2.0,
            "work_v": 2,
            "stage_id": "convert",
            "state": "determinate",
            "unit": "items",
            "completed": 1,
            "attempt": 1,
        },
        {"v": 1, "event": "done", "output": "/Users/private/result.md"},
    ]
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter(
            [("err", json.dumps(event)) for event in events] + [("rc", "0")]
        ),
    )

    updates = list(ui.run_with_status("unused"))
    final_log = updates[-1][0]

    assert any("--omd-progress:50%" in update[3] for update in updates)
    assert "Converting" in final_log
    assert "Output written" in final_log
    assert '"work_v"' not in final_log
    assert "/Users/private/result.md" not in final_log


def test_ocr_language_defaults_to_english_with_chinese_example():
    cfg = build_gradio_config_or_skip()
    ocr = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Text-in-image language"
    )

    assert ocr["props"]["value"] == "eng"
    assert "screenshots" in ocr["props"]["info"]
    assert "chi_sim+eng" in ocr["props"]["info"]


def test_browser_cookie_dropdown_explains_supported_scope():
    cfg = build_gradio_config_or_skip()
    dropdown = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Read cookies from browser"
    )

    assert [choice[1] for choice in dropdown["props"]["choices"]] == ["(none)", "chrome", "edge", "firefox", "brave"]
    assert "Safari is not offered here" in dropdown["props"]["info"]
    assert "public-only sources do not consume browser cookies" in dropdown["props"]["info"]
    assert "Douyin and XHS use the cookies.txt fields" in dropdown["props"]["info"]


def test_cookie_panel_explains_separate_douyin_and_xhs_files():
    cfg = build_gradio_config_or_skip()
    visible_text = "\n".join(
        str(component.get("props", {}).get(key, ""))
        for component in cfg["components"]
        for key in ("label", "info", "value", "placeholder")
    )

    assert "Douyin and XHS / Rednote use separate cookies fields" in visible_text
    assert "Upload the Douyin cookies.txt file" in visible_text
    assert "missing its matching cookie file" in visible_text
    assert "when a batch mixes" not in visible_text


def test_visible_ui_omits_instagram_cookie_controls():
    cfg = build_gradio_config_or_skip()
    visible_text = "\n".join(
        str(component.get("props", {}).get(key, ""))
        for component in cfg["components"]
        for key in ("label", "info", "value", "placeholder")
    )

    assert "Instagram" not in visible_text
    assert "instagram" not in visible_text


def test_source_file_drop_accepts_multiple_files():
    cfg = build_gradio_config_or_skip()
    source_file = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Drop source files"
    )

    assert source_file["type"] == "file"
    assert source_file["props"]["file_count"] == "multiple"
    assert source_file["props"]["type"] == "filepath"
    assert source_file["props"].get("allow_reordering", False) is False
    assert ui.SOURCE_FILE_LIMIT == 5


def test_source_file_queue_accumulates_separate_uploads(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    queued, _summary, visible_files = ui._merge_source_file_queue_for_ui([], [str(first)])
    first.unlink()
    queued, summary, visible_files = ui._merge_source_file_queue_for_ui(
        queued,
        [*visible_files, str(second)],
    )
    second.unlink()

    queued_paths = [Path(path) for path in ui._uploaded_file_paths(queued)]
    assert len(queued_paths) == 2
    assert all(source_staging in path.parents for path in queued_paths)
    assert [path.name for path in queued_paths] == ["first.pdf", "second.pdf"]
    assert visible_files == [str(path) for path in queued_paths]
    assert [path.read_bytes() for path in queued_paths] == [b"first", b"second"]
    assert "2 of 5 files queued" in summary


def test_source_file_queue_keeps_explicit_repeat_from_new_upload_path(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    first_upload = tmp_path / "first-upload" / "report.pdf"
    second_upload = tmp_path / "second-upload" / "report.pdf"
    first_upload.parent.mkdir()
    second_upload.parent.mkdir()
    first_upload.write_bytes(b"same report")
    second_upload.write_bytes(b"same report")

    queued, _summary, visible_files = ui._merge_source_file_queue_for_ui(
        [],
        [str(first_upload)],
    )
    queued, summary, visible_files = ui._merge_source_file_queue_for_ui(
        queued,
        [*visible_files, str(second_upload)],
    )

    assert len(queued) == 2
    assert len(visible_files) == 2
    assert "2 of 5 files queued" in summary


def test_source_file_queue_keeps_different_content_with_same_name(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    first_upload = tmp_path / "first-upload" / "report.pdf"
    second_upload = tmp_path / "second-upload" / "report.pdf"
    first_upload.parent.mkdir()
    second_upload.parent.mkdir()
    first_upload.write_bytes(b"first report")
    second_upload.write_bytes(b"second report")

    queued, _summary, visible_files = ui._merge_source_file_queue_for_ui(
        [],
        [str(first_upload)],
    )
    queued, summary, visible_files = ui._merge_source_file_queue_for_ui(
        queued,
        [*visible_files, str(second_upload)],
    )

    assert len(queued) == 2
    assert len(visible_files) == 2
    assert "2 of 5 files queued" in summary


def test_source_file_queue_keeps_same_content_with_different_names(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    first_upload = tmp_path / "alpha.pdf"
    second_upload = tmp_path / "beta.pdf"
    first_upload.write_bytes(b"shared template")
    second_upload.write_bytes(b"shared template")

    queued, _summary, visible_files = ui._merge_source_file_queue_for_ui(
        [],
        [str(first_upload)],
    )
    queued, summary, visible_files = ui._merge_source_file_queue_for_ui(
        queued,
        [*visible_files, str(second_upload)],
    )

    assert [entry["display_name"] for entry in queued] == ["alpha.pdf", "beta.pdf"]
    assert len(visible_files) == 2
    assert "2 of 5 files queued" in summary


def test_source_file_queue_removes_only_deleted_file(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    queued, _summary = ui._merge_source_file_queue([], [str(first), str(second)])
    staged_before = [Path(path) for path in ui._uploaded_file_paths(queued)]

    queued, summary = ui._remove_source_file_from_queue(
        queued,
        str(staged_before[0]),
    )

    queued_paths = [Path(path) for path in ui._uploaded_file_paths(queued)]
    assert len(queued_paths) == 1
    assert queued_paths[0].read_bytes() == b"second"
    assert not staged_before[0].exists()
    assert staged_before[1].exists()
    assert "1 of 5 files queued" in summary


def test_source_file_queue_clear_removes_staged_files(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    source = tmp_path / "report.pdf"
    source.write_bytes(b"report")
    queued, _summary = ui._merge_source_file_queue([], [str(source)])
    staged = Path(ui._uploaded_file_paths(queued)[0])

    cleared, summary = ui._clear_source_file_queue(queued)

    assert cleared == []
    assert "0 of 5 files queued" in summary
    assert not staged.exists()


def test_source_file_queue_rejects_files_above_five(tmp_path, monkeypatch):
    source_staging = tmp_path / "staged-sources"
    monkeypatch.setattr(ui, "SOURCE_STAGING", source_staging)
    sources = []
    for index in range(6):
        source = tmp_path / f"source-{index}.pdf"
        source.write_bytes(str(index).encode())
        sources.append(str(source))

    queued, summary, visible_files = ui._merge_source_file_queue_for_ui([], sources)

    assert len(queued) == 5
    queued_paths = [Path(path) for path in ui._uploaded_file_paths(queued)]
    assert len(queued_paths) == 5
    assert visible_files == [str(path) for path in queued_paths]
    assert all(path.is_file() for path in queued_paths)
    assert [path.name for path in queued_paths] == [f"source-{index}.pdf" for index in range(5)]
    assert "5 of 5 files queued" in summary
    assert "1 extra file was not added" in summary
    assert len(list(source_staging.iterdir())) == 5


def test_source_file_controls_are_compact_and_do_not_stretch_file_rows():
    assert ".omd-drop-grid .icon-button-wrapper.top-panel" in ui.CUSTOM_CSS
    assert ".omd-drop-grid tr.file" in ui.CUSTOM_CSS
    assert 'content: "+ Add files" !important' in ui.CUSTOM_CSS
    assert 'content: "× Clear" !important' in ui.CUSTOM_CSS
    assert ".omd-file-queue blockquote" in ui.CUSTOM_CSS
    assert "transform: translate(-50%, -50%)" in ui.CUSTOM_CSS


def test_vault_folder_keeps_long_paths_horizontally_scrollable(tmp_path, monkeypatch):
    selector = "#omd-vault-folder textarea"

    assert selector in ui.CUSTOM_CSS
    rule = ui.CUSTOM_CSS.split(selector, 1)[1].split("}", 1)[0]
    assert "overflow-x: auto !important" in rule
    assert "overflow-y: hidden !important" in rule
    assert "white-space: pre !important" in rule
    assert "min-height: 48px !important" in rule
    assert "height: 48px !important" in rule
    assert "scrollbar-width: thin" in rule
    assert "#omd-vault-folder textarea::-webkit-scrollbar" in ui.CUSTOM_CSS
    scrollbar_rule = ui.CUSTOM_CSS.split(
        "#omd-vault-folder textarea::-webkit-scrollbar", 1
    )[1].split("}", 1)[0]
    assert "height: 8px" in scrollbar_rule

    monkeypatch.setenv("OMD_ETA_HISTORY_PATH", str(tmp_path / "eta-history.json"))
    cfg = build_gradio_config_or_skip()
    vault_folder = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Vault folder"
    )
    assert vault_folder["props"]["elem_id"] == "omd-vault-folder"


def test_source_file_add_button_has_a_readable_accessible_name():
    kwargs = ui.build_launch_kwargs()

    assert ui.UI_TRANSLATIONS["common.upload"] == "Add files"
    assert kwargs["i18n"].translations["en"]["common.upload"] == "Add files"


def test_ui_help_does_not_build_or_launch_app(monkeypatch, capsys):
    monkeypatch.setattr(
        ui,
        "build_app",
        lambda: pytest.fail("--help must not build the Gradio application"),
    )

    with pytest.raises(SystemExit) as exc:
        ui.main(["--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Launch the local OMD browser UI" in captured.out
    assert "--port" in captured.out
    assert "OMD_UI_PORT" in captured.out
    assert captured.err == ""


def test_ui_main_forwards_requested_port(monkeypatch):
    launched = {}

    class FakeApp:
        def queue(self):
            return self

        def launch(self, **kwargs):
            launched.update(kwargs)

    monkeypatch.setattr(ui, "build_app", FakeApp)

    assert ui.main(["--port", "8123"]) == 0
    assert launched["server_port"] == 8123


def test_ui_main_preserves_environment_port_override(monkeypatch):
    launched = {}

    class FakeApp:
        def queue(self):
            return self

        def launch(self, **kwargs):
            launched.update(kwargs)

    monkeypatch.setenv("OMD_UI_PORT", "8124")
    monkeypatch.setattr(ui, "build_app", FakeApp)

    assert ui.main([]) == 0
    assert launched["server_port"] == 8124


def test_ui_port_conflict_is_actionable_without_traceback(monkeypatch, capsys):
    class FakeApp:
        def queue(self):
            return self

        def launch(self, **kwargs):
            raise OSError("Cannot find empty port in range: 7860-7860")

    monkeypatch.setattr(ui, "build_app", FakeApp)

    assert ui.main(["--port", "7860"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "port 7860 is already in use" in captured.err
    assert "omd-ui --port 7861" in captured.err
    assert "OMD_UI_PORT=7861 omd-ui" in captured.err
    assert "no vault files were changed" in captured.err
    assert "Traceback" not in captured.err


def test_ui_invalid_host_configuration_is_actionable_without_traceback(monkeypatch, capsys):
    class FakeApp:
        def queue(self):
            return self

    monkeypatch.setattr(ui, "build_app", FakeApp)
    monkeypatch.setenv("OMD_UI_HOST", "0.0.0.0")
    monkeypatch.delenv("OMD_PUBLIC_DEMO", raising=False)

    assert ui.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not start" in captured.err
    assert "omd doctor" in captured.err
    assert "no vault files were changed" in captured.err
    assert "Traceback" not in captured.err


def test_local_ui_rejects_non_loopback_bind_address(monkeypatch):
    monkeypatch.delenv("OMD_PUBLIC_DEMO", raising=False)

    with pytest.raises(ValueError, match="loopback"):
        ui.build_launch_kwargs(server_name="0.0.0.0")


def test_local_ui_accepts_ipv6_loopback_bind_address(monkeypatch):
    monkeypatch.delenv("OMD_PUBLIC_DEMO", raising=False)

    kwargs = ui.build_launch_kwargs(server_name="::1")

    assert kwargs["server_name"] == "::1"


def test_public_demo_allows_non_loopback_bind_address(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")

    kwargs = ui.build_launch_kwargs(server_name="0.0.0.0")

    assert kwargs["server_name"] == "0.0.0.0"


def test_command_buttons_share_control_height_and_action_column_width():
    assert "--omd-control-height: 36px" in ui.CUSTOM_CSS
    assert "--omd-command-width: 144px" in ui.CUSTOM_CSS
    assert "grid-template-columns: minmax(0, 1fr) var(--omd-command-width)" in ui.CUSTOM_CSS


def test_top_navigation_buttons_keep_visible_retro_chrome():
    assert "#omd-menubar button.omd-menu-btn" in ui.CUSTOM_CSS


def test_run_actions_share_one_aligned_control_height():
    assert "--omd-run-height: 46px" in ui.CUSTOM_CSS
    assert "#omd-stop-btn" in ui.CUSTOM_CSS
    assert "height: var(--omd-run-height) !important" in ui.CUSTOM_CSS


def test_run_status_uses_full_width_row_below_action_buttons():
    assert "grid-template-columns: minmax(0, 1fr) var(--omd-command-width)" in ui.CUSTOM_CSS
    assert ".omd-run-dock .omd-action-row > :nth-child(3)" in ui.CUSTOM_CSS
    assert "grid-column: 1 / -1 !important" in ui.CUSTOM_CSS


def test_run_panel_reserves_space_for_primary_status_output():
    assert "--omd-status-height: 104px" in ui.CUSTOM_CSS
    assert "min-height: var(--omd-status-height)" in ui.CUSTOM_CSS
    desktop_css = ui.CUSTOM_CSS.split("@media (max-width: 860px)", 1)[0]
    assert ".omd-status-detail" in desktop_css
    assert "grid-column: 1 / -1" in desktop_css


def test_status_detail_wraps_instead_of_ellipsising():
    assert ".omd-status-detail {" in ui.CUSTOM_CSS
    assert "white-space: normal;" in ui.CUSTOM_CSS
    assert "overflow-wrap: anywhere;" in ui.CUSTOM_CSS
    assert "#omd-menubar .omd-menu-btn button.primary" in ui.CUSTOM_CSS


def test_primary_panels_use_action_oriented_labels():
    cfg = build_gradio_config_or_skip()
    accordion_labels = [
        component.get("props", {}).get("label")
        for component in cfg["components"]
        if component.get("type") == "accordion"
    ]

    for label in ("Add source", "Choose output", "Convert", "Result and download"):
        assert label in accordion_labels


def test_result_download_controls_appear_before_process_log():
    cfg = build_gradio_config_or_skip()
    labels = [component.get("props", {}).get("label") for component in cfg["components"]]

    assert labels.index("Output path") < labels.index("Process log")


def test_primary_grid_keeps_task_order_when_columns_collapse():
    desktop_css = ui.CUSTOM_CSS.split("@media (max-width: 860px)", 1)[0]
    assert "display: contents" not in desktop_css
    assert ".omd-panel-source { grid-area: source; }" in ui.CUSTOM_CSS
    assert '"source"\n            "output"\n            "run"\n            "result"' in ui.CUSTOM_CSS


def test_source_panel_has_no_duplicate_clear_queue_button():
    cfg = build_gradio_config_or_skip()
    button_labels = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "Clear file queue" not in button_labels


def test_result_panel_keeps_markdown_download_option():
    cfg = build_gradio_config_or_skip()
    download = next(
        component
        for component in cfg["components"]
        if component.get("type") == "downloadbutton"
        and component.get("props", {}).get("label") == "Download Markdown"
    )

    assert download["props"]["visible"] is True
    assert download["props"]["interactive"] is False
    assert "omd-download-slot" in download["props"].get("elem_classes", [])
    assert ".omd-download-slot" in ui.CUSTOM_CSS
    assert "Generated Markdown" in "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
    )


def test_menubar_links_to_project_feedback_instead_of_static_ready_text():
    cfg = build_gradio_config_or_skip()
    visible_html = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
        if component.get("type") == "html"
    )

    assert ui.PROJECT_URL in visible_html
    assert "GitHub / feedback" in visible_html
    assert "Ready for local vault capture" not in visible_html


def test_ui_visible_copy_avoids_em_dash():
    cfg = build_gradio_config_or_skip()
    visible_text = "\n".join(
        str(component.get("props", {}).get(key, ""))
        for component in cfg["components"]
        for key in ("label", "info", "value", "placeholder")
    )

    assert "—" not in visible_text
    assert "–" not in visible_text
    assert "—" not in ui.CUSTOM_CSS
    assert "–" not in ui.CUSTOM_CSS


def test_reddit_adapter_scope_is_user_selectable():
    cfg = build_gradio_config_or_skip()
    reddit_scope = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Reddit content"
    )

    assert reddit_scope["type"] == "radio"
    assert reddit_scope["props"]["value"] == "OP only"
    assert [choice[1] for choice in reddit_scope["props"]["choices"]] == ["OP only", "OP + Top comments"]
    assert "comment authors, timestamps, nesting, permalinks" in reddit_scope["props"]["info"]


def test_top_menu_tabs_are_clickable_view_controls():
    cfg = build_gradio_config_or_skip()
    labels = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    for label in ("All", "Source", "Inbox / review", "Output", "Advanced settings"):
        assert label in labels


def test_top_menu_view_updates_collapse_instead_of_hiding_panels():
    def open_flags(updates):
        return [update["open"] for update in updates[:7]]

    def variants(updates):
        return [update["variant"] for update in updates[7:12]]

    def layout_classes(updates):
        return updates[12]["elem_classes"]

    for updates in (
        ui._menu_view_updates("all"),
        ui._menu_view_updates("source"),
        ui._menu_view_updates("inbox"),
        ui._menu_view_updates("output"),
        ui._menu_view_updates("advanced"),
    ):
        assert [update["visible"] for update in updates[:7]] == [True] * 7

    assert open_flags(ui._menu_view_updates("all")) == [True, False, True, True, True, False, False]
    assert open_flags(ui._menu_view_updates("source")) == [True, False, False, False, False, False, False]
    assert open_flags(ui._menu_view_updates("inbox")) == [False, True, False, False, False, False, False]
    assert open_flags(ui._menu_view_updates("output")) == [False, False, True, True, True, False, False]
    assert open_flags(ui._menu_view_updates("advanced")) == [False, False, False, False, False, True, True]
    assert variants(ui._menu_view_updates("all")) == ["primary", "secondary", "secondary", "secondary", "secondary"]
    assert variants(ui._menu_view_updates("source")) == ["secondary", "primary", "secondary", "secondary", "secondary"]
    assert variants(ui._menu_view_updates("inbox")) == ["secondary", "secondary", "primary", "secondary", "secondary"]
    assert variants(ui._menu_view_updates("output")) == ["secondary", "secondary", "secondary", "primary", "secondary"]
    assert variants(ui._menu_view_updates("advanced")) == ["secondary", "secondary", "secondary", "secondary", "primary"]
    assert layout_classes(ui._menu_view_updates("all")) == ["omd-grid"]
    assert layout_classes(ui._menu_view_updates("source")) == ["omd-grid", "omd-grid-primary-only"]
    assert layout_classes(ui._menu_view_updates("inbox")) == ["omd-grid", "omd-grid-primary-only"]
    assert layout_classes(ui._menu_view_updates("output")) == ["omd-grid"]
    assert layout_classes(ui._menu_view_updates("advanced")) == ["omd-grid", "omd-grid-secondary-only"]


def test_inbox_review_panel_is_secondary_and_privacy_explicit():
    cfg = build_gradio_config_or_skip()
    labels = [component.get("props", {}).get("label") for component in cfg["components"]]
    visible_text = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
    )
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "Inbox and review" in labels
    assert "Save to Inbox" in buttons
    assert "Saving is local and does not use AI" in visible_text
    assert "The original text remains in Inbox" in visible_text


def test_inbox_review_selection_loads_raw_source_and_provenance(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Quoted note",
        raw_content="Exact quoted source text.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)

    provenance, raw_source = ui._load_inbox_review(str(tmp_path), item.item_id)

    assert "Highlight" in provenance
    assert "Current status: inbox" in provenance
    assert "2026-07-19T10:11:12Z" in provenance
    assert raw_source == "Exact quoted source text."


def test_switching_inbox_items_clears_item_specific_unsaved_context():
    (
        consent,
        include_ai,
        draft,
        grant,
        source_update,
        source_preview,
        source_status,
        action_status,
        tags,
        personal_notes,
        retrieval_results,
        search_query,
    ) = ui._reset_inbox_review_context()

    assert consent is False
    assert include_ai is False
    assert draft == ""
    assert grant is None
    assert source_update["choices"] == []
    assert source_update["value"] is None
    assert source_preview == ""
    assert "Inbox original" in source_status
    assert "No review action yet" in action_status
    assert tags == ""
    assert personal_notes == ""
    assert retrieval_results == "_No local search run yet._"
    assert search_query == ""


def test_inbox_review_ui_exposes_read_only_raw_source_pane_and_specific_accept_labels():
    cfg = build_gradio_config_or_skip()
    components = {
        component.get("props", {}).get("label"): component
        for component in cfg["components"]
        if component.get("props", {}).get("label")
    }
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "Saved details" in components
    assert components["Original text (kept unchanged)"]["props"]["interactive"] is False
    assert "Markdown source for AI and new-note link (optional)" in components
    assert components["Selected Markdown source (kept unchanged)"]["props"]["interactive"] is False
    assert "Add your note (optional)" in components
    assert "Tags for new note (editable)" in components
    assert "AI draft (edit before including)" in components
    assert "Create note in Notes" in buttons
    assert "Keep original · mark as not needed" in buttons
    assert "Suggest sources" in buttons
    assert "Load converted Markdown from Sources" not in buttons
    assert "Find related to review item" not in buttons
    assert "Refresh Inbox" not in buttons
    assert "Accept Voice note" in buttons


def test_inbox_ai_and_tag_controls_follow_the_unchanged_original():
    cfg = build_gradio_config_or_skip()

    def component_position(text: str) -> int:
        for position, component in enumerate(cfg["components"]):
            props = component.get("props", {})
            if props.get("label") == text or props.get("value") == text:
                return position
        raise AssertionError(f"component not found: {text}")

    original = component_position("Original text (kept unchanged)")
    ai_panel = component_position("Draft a takeaway with AI (optional)")
    tags = component_position("Tags for new note (editable)")
    personal_note = component_position("Add your note (optional)")

    assert original < ai_panel < tags < personal_note


def test_inbox_review_ui_explains_the_three_step_workflow_and_ai_scope():
    cfg = build_gradio_config_or_skip()
    visible_text = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
    )
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "1. Save a thought or highlight" in visible_text
    assert "2. Review one Inbox item" in visible_text
    assert "3. Decide what to keep" in visible_text
    assert "does not open links or read any unselected file" in visible_text
    assert "Generate draft from selected text" in buttons
    assert "Review cloud request" in buttons


def test_inbox_queue_uses_readable_labels_instead_of_raw_item_ids(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Readable highlight",
        raw_content="An exact passage worth keeping.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)

    summary, choices = ui._inbox_queue_state(str(tmp_path))

    assert "Readable highlight" in summary
    assert choices == [("Readable highlight · needs review", item.item_id)]


def test_linkable_search_results_exclude_inbox_case_insensitively():
    from types import SimpleNamespace

    hits = [
        SimpleNamespace(title="Upper", path="Inbox/upper.md"),
        SimpleNamespace(title="Lower", path="inbox/lower.md"),
        SimpleNamespace(title="Note", path="Notes/kept.md"),
    ]

    assert ui._linkable_hit_choices(hits) == [
        ("Note · Notes/kept.md", "Notes/kept.md")
    ]


def test_vault_markdown_source_choices_are_safe_bounded_and_sources_only(tmp_path):
    sources = tmp_path / "Sources" / "Web"
    sources.mkdir(parents=True)
    newest = sources / "newest article.md"
    older = sources / "older.md"
    newest.write_text("# Newest\n\nConverted source text.\n", encoding="utf-8")
    older.write_text("# Older\n\nOlder source text.\n", encoding="utf-8")
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox" / "not-a-source.md").write_text("Inbox", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (sources / "linked.md").symlink_to(outside)

    choices = ui._vault_markdown_source_choices(str(tmp_path))

    assert ("Sources/Web/newest article.md", "Sources/Web/newest article.md") in choices
    assert ("Sources/Web/older.md", "Sources/Web/older.md") in choices
    assert all("Inbox/" not in value for _, value in choices)
    assert all(value != "Sources/Web/linked.md" for _, value in choices)


def test_selected_vault_markdown_source_is_read_only_and_previewed(tmp_path):
    source = tmp_path / "Sources" / "Web" / "converted.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Converted\n\nExact converted passage.\n", encoding="utf-8")
    before = source.read_bytes()

    preview, status = ui._load_vault_markdown_source(
        str(tmp_path), "Sources/Web/converted.md"
    )

    assert "Exact converted passage." in preview
    assert "Sources/Web/converted.md" in status
    assert source.read_bytes() == before


def test_inbox_final_action_returns_visible_success_and_failure_status(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Visible action",
        raw_content="Keep this source.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)

    _, _, success = ui._review_inbox_note(
        str(tmp_path), item.item_id, "accept", "", "", False, ""
    )
    _, _, failure = ui._review_inbox_note(
        str(tmp_path), "missing-item", "accept", "", "", False, ""
    )

    assert "omd-model-status-ok" in success
    assert "Created in Notes" in success
    assert "original remains in Inbox" in success
    assert "omd-model-status-warn" in failure
    assert "No vault files were changed" in failure


def test_inbox_final_actions_confirm_ai_link_and_safe_not_needed_state(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    accepted = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Accepted with AI",
        raw_content="Keep the original accepted text.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    rejected = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Not needed safely",
        raw_content="Keep the original rejected text too.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:12:12Z",
    )
    accepted_path = save_inbox_item(tmp_path, accepted)
    rejected_path = save_inbox_item(tmp_path, rejected)
    linked = tmp_path / "Sources" / "Web" / "converted.md"
    linked.parent.mkdir(parents=True)
    linked.write_text("# Converted\n\nExact source.\n", encoding="utf-8")
    source_before = {
        accepted_path: accepted_path.read_bytes(),
        rejected_path: rejected_path.read_bytes(),
        linked: linked.read_bytes(),
    }

    _, _, accepted_status = ui._review_inbox_note(
        str(tmp_path),
        accepted.item_id,
        "accept",
        "My addition.",
        "AI addition with exact evidence.",
        True,
        "Sources/Web/converted.md",
        "agents, #knowledge-management",
    )
    notes_after_accept = list((tmp_path / "Notes").glob("*.md"))
    _, _, rejected_status = ui._review_inbox_note(
        str(tmp_path), rejected.item_id, "reject", "Unsaved addition."
    )

    assert len(notes_after_accept) == 1
    note_text = notes_after_accept[0].read_text(encoding="utf-8")
    assert "My addition." in note_text
    assert "AI addition with exact evidence." in note_text
    assert "[[Sources/Web/converted]]" in note_text
    assert '  - "agents"' in note_text
    assert '  - "knowledge-management"' in note_text
    assert "The edited AI draft was included" in accepted_status
    assert "Linked source: Sources/Web/converted.md" in accepted_status
    assert "Tags: agents, knowledge-management" in accepted_status
    assert "Marked as not needed" in rejected_status
    assert "no Note was created" in rejected_status
    assert list((tmp_path / "Notes").glob("*.md")) == notes_after_accept
    for path, before in source_before.items():
        assert path.read_bytes() == before


def test_inbox_final_actions_do_not_replay_or_reverse_completed_decisions(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    accepted = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Completed accept",
        raw_content="Keep this accepted source.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    rejected = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Completed reject",
        raw_content="Keep this rejected source.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:12:12Z",
    )
    save_inbox_item(tmp_path, accepted)
    save_inbox_item(tmp_path, rejected)

    ui._review_inbox_note(
        str(tmp_path), accepted.item_id, "accept", "First note", "", False, "", "first-tag"
    )
    ui._review_inbox_note(str(tmp_path), rejected.item_id, "reject", "")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    _, _, repeated_accept = ui._review_inbox_note(
        str(tmp_path),
        accepted.item_id,
        "accept",
        "Replacement that must not be written",
        "Replacement AI draft",
        True,
        "",
        "replacement-tag",
    )
    _, _, reverse_accept = ui._review_inbox_note(
        str(tmp_path), accepted.item_id, "reject", ""
    )
    _, _, reverse_reject = ui._review_inbox_note(
        str(tmp_path), rejected.item_id, "accept", "Must not create a Note"
    )

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert "Review already completed" in repeated_accept
    assert "current edits were not applied again" in repeated_accept
    assert "cannot be changed here" in reverse_accept
    assert "cannot be changed here" in reverse_reject
    assert "No vault files were changed" in reverse_accept
    assert "No vault files were changed" in reverse_reject
    assert "Replacement that must not be written" not in next(
        (tmp_path / "Notes").glob("*.md")
    ).read_text(encoding="utf-8")


def test_local_ai_hides_cloud_review_controls():
    updates = ui._ai_provider_ui_updates("Local Ollama", "qwen3:4b-instruct")

    preview_button, preview_panel, generate_button, ai_panel = updates[5:]
    assert preview_button["visible"] is False
    assert preview_panel["visible"] is False
    assert generate_button["value"] == "Generate draft from selected text"
    assert ai_panel["visible"] is True


def test_no_ai_hides_the_complete_inbox_ai_panel():
    updates = ui._ai_provider_ui_updates("No AI", "qwen3:4b-instruct")

    assert updates[7]["visible"] is False
    assert updates[8]["visible"] is False


def test_inbox_local_ai_uses_extended_context_without_changing_global_default():
    from omd._models import LOCAL_TEXT_CONTEXT_TOKENS

    task = ui._ai_note_task(
        "Local Ollama",
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert LOCAL_TEXT_CONTEXT_TOKENS == 4096
    assert task.context_window_tokens == 32 * 1024


def test_inbox_context_error_explains_extended_limit_and_preserved_state(
    tmp_path, monkeypatch
):
    from omd.ai_service import AIServiceError
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Long note",
        raw_content="A sufficiently meaningful local note.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)

    def reject_context(*args, **kwargs):
        raise AIServiceError("ollama", "context_limit_exceeded", "old detail")

    monkeypatch.setattr(ui, "execute_text_task", reject_context)

    output, status = ui._run_inbox_ai_suggestion(
        str(tmp_path),
        item.item_id,
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "Keep this draft",
    )

    assert output == "Keep this draft"
    assert "32768-token Inbox AI window" in status
    assert "local models still have context and memory limits" in status
    assert "current draft, editable tags, raw Inbox item, and selected source are unchanged" in status


def test_inbox_ai_rejects_ungrounded_missing_evidence_result(tmp_path, monkeypatch):
    from omd.ai_service import AITextResult
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Short highlight",
        raw_content="A short exact highlight with enough words to review.",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)
    monkeypatch.setattr(
        ui,
        "execute_text_task",
        lambda *args, **kwargs: AITextResult(
            provider="ollama",
            requested_model="qwen3:4b-instruct",
            actual_model="qwen3:4b-instruct",
            capability="note_organisation",
            privacy_mode="local_only",
            destination_domain="localhost",
            text="No evidence provided to support an organisation structure.",
            usage={},
            timing={"elapsed_seconds": 0.1},
            structured={
                "suggestion": "No evidence provided to support an organisation structure.",
                "evidence": [],
                "tags": ["missing-evidence", "incomplete-input", "no-source-claims"],
            },
        ),
    )

    output, status = ui._run_inbox_ai_suggestion(
        str(tmp_path),
        item.item_id,
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "Keep my existing draft",
    )

    assert output == "Keep my existing draft"
    assert "could not create a grounded draft" in status
    assert "select or save the exact passage" in status
    assert "Inbox original" in status
    assert "ready for review" not in status


def test_inbox_ai_does_not_call_model_for_url_only_item(tmp_path, monkeypatch):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Article link",
        raw_content="https://www.towardsdeeplearning.com/example-article",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("URL-only Inbox items must not be sent to a model")

    monkeypatch.setattr(ui, "execute_text_task", unexpected_model_call)

    output, status = ui._run_inbox_ai_suggestion(
        str(tmp_path),
        item.item_id,
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "",
    )

    assert output == ""
    assert "needs text, not only a link" in status
    assert "Nothing was sent" in status


def test_inbox_ai_accepts_only_exact_source_evidence(tmp_path, monkeypatch):
    from omd.ai_service import AITextResult
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    source = "Transformers predict the next token using a bounded context."
    item = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Grounded highlight",
        raw_content=source,
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)
    monkeypatch.setattr(
        ui,
        "execute_text_task",
        lambda *args, **kwargs: AITextResult(
            provider="ollama",
            requested_model="qwen3:4b-instruct",
            actual_model="qwen3:4b-instruct",
            capability="note_organisation",
            privacy_mode="local_only",
            destination_domain="localhost",
            text="Keep this as a concise model-mechanics claim.",
            usage={},
            timing={"elapsed_seconds": 0.1},
            structured={
                "suggestion": "Keep this as a concise model-mechanics claim.",
                "evidence": ["predict the next token"],
                "tags": ["transformers", "language-models"],
            },
        ),
    )

    output, status = ui._run_inbox_ai_suggestion(
        str(tmp_path),
        item.item_id,
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "",
    )

    assert "Keep this as a concise model-mechanics claim." in output
    assert "- predict the next token" in output
    assert "transformers, language-models" in output
    assert "ready for review" in status


def test_inbox_ai_ui_moves_suggested_tags_into_editable_tag_field(monkeypatch):
    monkeypatch.setattr(
        ui,
        "_run_inbox_ai_suggestion",
        lambda *args, **kwargs: (
            "Keep the claim.\n\nEvidence from selected text\n- exact claim"
            "\n\nSuggested tags\nagents, #knowledge-management",
            "ready",
        ),
    )

    draft, status, tags = ui._run_inbox_ai_suggestion_for_ui(
        "vault",
        "item",
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "",
        "personal",
        None,
        "",
    )

    assert "Suggested tags" not in draft
    assert "ready" in status
    assert "agents" in status
    assert "knowledge-management" in status
    assert "Review or remove them" in status
    assert tags == "personal, agents, knowledge-management"


def test_inbox_ai_can_use_selected_converted_markdown_without_rewriting_it(
    tmp_path, monkeypatch
):
    from omd.ai_service import AITextResult
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Question about a conversion",
        raw_content="What should I retain from the converted article?",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T10:11:12Z",
    )
    save_inbox_item(tmp_path, item)
    converted = tmp_path / "Sources" / "Web" / "converted.md"
    converted.parent.mkdir(parents=True)
    converted_text = "# Converted\n\nA deterministic loop keeps agent behavior inspectable.\n"
    converted.write_text(converted_text, encoding="utf-8")
    before = converted.read_bytes()
    seen = {}

    def execute(_task, *, source_text, **_kwargs):
        seen["source_text"] = source_text
        return AITextResult(
            provider="ollama",
            requested_model="qwen3:4b-instruct",
            actual_model="qwen3:4b-instruct",
            capability="note_organisation",
            privacy_mode="local_only",
            destination_domain="localhost",
            text="Keep the inspectability claim.",
            usage={},
            timing={"elapsed_seconds": 0.1},
            structured={
                "suggestion": "Keep the inspectability claim.",
                "evidence": ["deterministic loop keeps agent behavior inspectable"],
                "tags": ["agents"],
            },
        )

    monkeypatch.setattr(ui, "execute_text_task", execute)

    output, status = ui._run_inbox_ai_suggestion(
        str(tmp_path),
        item.item_id,
        "Local Ollama",
        "qwen3:4b-instruct",
        "",
        "http://localhost:11434",
        False,
        "",
        None,
        "Sources/Web/converted.md",
    )

    assert seen["source_text"] == converted_text
    assert "deterministic loop keeps agent behavior inspectable" in output
    assert "Sources/Web/converted.md" in status
    assert converted.read_bytes() == before


def test_ai_provider_choices_are_direct_and_exclude_openrouter():
    assert ui.AI_PROVIDER_CHOICES == (
        "No AI",
        "Local Ollama",
        "OpenAI API",
        "Anthropic API",
        "DeepSeek API",
    )
    assert ui._ai_provider_key("OpenAI API") == "openai"

    with pytest.raises(ValueError, match="provider"):
        ui._ai_provider_key("OpenRouter")


def test_cloud_request_preview_names_destination_without_echoing_source(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    source = "Private source text must not appear in the preview."
    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Private note",
        raw_content=source,
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)

    preview = ui._preview_inbox_ai_request(
        str(tmp_path),
        item.item_id,
        "OpenAI API",
        "gpt-selected",
        "http://localhost:11434",
        as_of=date(2026, 7, 19),
    )

    assert "api.openai.com" in preview
    assert "gpt-selected" in preview
    assert f"{len(source)} characters" in preview
    assert source not in preview
    assert "consumer ChatGPT login" in preview


def test_cloud_request_preview_for_ui_creates_source_bound_unchecked_grant(tmp_path):
    from omd.ai_service import AIConsentGrant
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    source = "Private source for one exact preview."
    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Private note",
        raw_content=source,
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)

    preview, grant, consent = ui._preview_inbox_ai_request_for_ui(
        str(tmp_path),
        item.item_id,
        "OpenAI API",
        "gpt-selected",
        "http://localhost:11434",
    )

    assert "api.openai.com" in preview
    assert isinstance(grant, AIConsentGrant)
    assert source not in repr(grant)
    assert consent is False


def test_provider_connection_check_does_not_replace_unavailable_model(monkeypatch):
    from omd.provider_models import ProviderModelCatalog

    monkeypatch.setattr(
        ui,
        "discover_provider_models",
        lambda *args, **kwargs: ProviderModelCatalog(
            provider="openai",
            destination_domain="api.openai.com",
            models=("gpt-a", "gpt-b"),
            elapsed_seconds=0.2,
        ),
    )

    update, status = ui._check_ai_provider_connection(
        "OpenAI API",
        "retired-model",
        "session-secret",
        "http://localhost:11434",
    )

    assert update["value"] == "retired-model"
    assert [choice for choice in update["choices"]] == ["gpt-a", "gpt-b"]
    assert "not available" in status
    assert "retired-model" in status
    assert "session-secret" not in status


def test_ai_provider_controls_are_in_advanced_settings():
    cfg = build_gradio_config_or_skip()
    labels = [component.get("props", {}).get("label") for component in cfg["components"]]
    visible_text = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
    )
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "AI provider for Inbox review" in labels
    assert "Provider model" in labels
    assert "Session API key" in labels
    assert "Cloud consent for this request" in labels
    assert "Leave blank to use a supported environment variable" in visible_text
    assert "Check models" in buttons
    assert "Load models / check connection" not in buttons
    assert "OpenRouter" not in visible_text


def test_inbox_queue_escapes_markdown_in_user_title(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="[load remote image](https://tracker.example/pixel)",
        raw_content="Private note",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)

    summary, choices = ui._inbox_queue_state(str(tmp_path))

    assert choices == [(f"{item.title} · needs review", item.item_id)]
    assert "[load remote image](" not in summary
    assert r"\[load remote image\]\(" in summary


def test_local_search_ui_returns_path_and_bounded_evidence(tmp_path, monkeypatch):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "retrieval.md").write_text(
        "# Retrieval note\n\nA local lexical marker for this vault.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMD_PREFERENCES_PATH", str(tmp_path / ".state" / "preferences.json"))

    result = ui._search_vault_notes(str(tmp_path), "lexical marker")

    assert "Retrieval note" in result
    assert "Notes/retrieval.md" in result
    assert "local lexical marker" in result


def test_related_notes_ui_does_not_rewrite_vault_files(tmp_path, monkeypatch):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    (tmp_path / "Notes").mkdir()
    related = tmp_path / "Notes" / "related.md"
    related.write_text("# Related\n\nDurable context receipt design.\n", encoding="utf-8")
    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Receipt thought",
        raw_content="Durable context receipt",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setenv("OMD_PREFERENCES_PATH", str(tmp_path / ".state" / "preferences.json"))

    result = ui._related_inbox_notes(str(tmp_path), item.item_id)

    assert "Notes/related.md" in result
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_related_notes_can_populate_an_explicit_source_selection(tmp_path, monkeypatch):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "related.md").write_text(
        "# Related\n\nDurable context receipt design.\n", encoding="utf-8"
    )
    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Receipt thought",
        raw_content="Durable context receipt",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)
    monkeypatch.setenv("OMD_PREFERENCES_PATH", str(tmp_path / "preferences.json"))

    result, choices = ui._related_inbox_notes_with_choices(str(tmp_path), item.item_id)

    assert "Notes/related.md" in result
    assert choices == [("Related · Notes/related.md", "Notes/related.md")]


def test_source_suggestions_unify_recent_conversions_and_related_notes(tmp_path):
    from omd.inbox import InboxItem
    from omd.inbox_workflow import save_inbox_item

    converted = tmp_path / "Sources" / "Web" / "converted article.md"
    converted.parent.mkdir(parents=True)
    converted.write_text("# Converted article\n\nA separate converted source.\n", encoding="utf-8")
    related = tmp_path / "Notes" / "related.md"
    related.parent.mkdir(parents=True)
    related.write_text("# Related\n\nDurable context receipt design.\n", encoding="utf-8")
    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Receipt thought",
        raw_content="Durable context receipt",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )
    save_inbox_item(tmp_path, item)

    result, choices = ui._suggest_vault_sources_with_choices(str(tmp_path), item.item_id)

    assert "Related note" in result
    assert "Converted source" in result
    assert ("Related note · Related · Notes/related.md", "Notes/related.md") in choices
    assert (
        "Converted source · converted article · Sources/Web/converted article.md",
        "Sources/Web/converted article.md",
    ) in choices


def test_preference_feedback_changes_state_only_after_explicit_action(tmp_path, monkeypatch):
    preference_path = tmp_path / "preferences.json"
    monkeypatch.setenv("OMD_PREFERENCES_PATH", str(preference_path))

    initial = ui._inspect_local_preferences()
    updated, status = ui._record_local_preference_feedback(
        "accept",
        "bullet_list",
        "bullet_list",
    )

    assert preference_path.exists()
    assert "bullet_list" in updated
    assert "+1" in updated
    assert "saved locally" in status
    assert "No explicit preferences" in initial


def test_reset_local_preferences_removes_saved_state(tmp_path, monkeypatch):
    preference_path = tmp_path / "preferences.json"
    monkeypatch.setenv("OMD_PREFERENCES_PATH", str(preference_path))
    ui._record_local_preference_feedback("accept", "outline", "outline")

    profile, status = ui._reset_local_preferences()

    assert not preference_path.exists()
    assert "No explicit preferences" in profile
    assert "reset" in status.lower()


def test_retrieval_ui_has_one_source_entry_and_hides_unused_preference_controls():
    cfg = build_gradio_config_or_skip()
    labels = [component.get("props", {}).get("label") for component in cfg["components"]]
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]

    assert "Search this vault" in labels
    assert "Explicit style feedback" not in labels
    assert "Search notes" in buttons
    assert "Suggest sources" in buttons
    assert "Find related to review item" not in buttons
    assert "Load converted Markdown from Sources" not in buttons
    assert "Find duplicates" not in buttons
    assert "Reset preferences" not in buttons


def test_stop_conversion_button_configures_cancel_dependency():
    cfg = build_gradio_config_or_skip()
    stop_buttons = [
        component
        for component in cfg["components"]
        if component.get("type") == "button" and component.get("props", {}).get("elem_id") == "omd-stop-btn"
    ]

    assert stop_buttons
    stop_id = stop_buttons[0]["id"]
    stop_deps = [
        dep
        for dep in cfg["dependencies"]
        if any(target[0] == stop_id for target in dep.get("targets", []))
    ]

    assert any(dep.get("types", {}).get("cancel") and dep.get("cancels") for dep in stop_deps)
    assert any(dep.get("backend_fn") and dep.get("outputs") for dep in stop_deps)


def test_stop_conversion_status_is_terminal_and_readable():
    status = ui._stop_conversion_status()

    assert "cancelled" in status
    assert "stopped by user" in status
    assert "ETA: stopping" not in status
    assert "omd-status-indeterminate" not in status
    assert ".omd-status-label" in ui.CUSTOM_CSS
    assert "color: var(--omd-ink)" in ui.CUSTOM_CSS


def test_format_inspect_result_includes_cookie_strategy():
    text = ui._format_inspect_result(
        "https://v.douyin.com/abc/",
        {
            "probable_backend": "reel",
            "detected_type": "douyin_url",
            "needs_network": True,
            "needs_cookies": True,
            "needs_tools": ["f2"],
            "metadata": {
                "cookie_strategy": "cookies_txt_required",
                "cookie_domains": ["douyin.com", "v.douyin.com"],
            },
        },
    )

    assert "Cookie strategy" in text
    assert "cookies_txt_required" in text
    assert "douyin.com, v.douyin.com" in text


def test_open_output_button_configures_backend_dependency():
    cfg = build_gradio_config_or_skip()
    buttons = [
        component
        for component in cfg["components"]
        if component.get("type") == "button" and component.get("props", {}).get("value") == "Open folder"
    ]

    assert buttons
    button_id = buttons[0]["id"]
    deps = [
        dep
        for dep in cfg["dependencies"]
        if any(target[0] == button_id for target in dep.get("targets", []))
    ]

    assert any(dep.get("backend_fn") and dep.get("outputs") for dep in deps)


def test_run_button_defaults_to_markdown_and_updates_with_action():
    cfg = build_gradio_config_or_skip()
    buttons = [
        component
        for component in cfg["components"]
        if component.get("type") == "button" and component.get("props", {}).get("elem_id") == "omd-run-btn"
    ]
    actions = [
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Action"
    ]

    assert buttons
    assert buttons[0]["props"].get("value") == "Convert to Markdown"
    assert actions
    action_id = actions[0]["id"]
    run_id = buttons[0]["id"]
    assert any(
        any(target[0] == action_id for target in dep.get("targets", []))
        and run_id in dep.get("outputs", [])
        for dep in cfg["dependencies"]
    )


def test_run_button_label_helper_matches_workflow():
    assert ui._run_button_label("Convert to .md file") == "Convert to Markdown"
    assert ui._run_button_label("Capture to vault note") == "Capture to vault"


def test_open_output_button_hidden_in_public_demo(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    cfg = build_gradio_config_or_skip()
    buttons = [
        component
        for component in cfg["components"]
        if component.get("type") == "button" and component.get("props", {}).get("value") == "Open folder"
    ]

    assert buttons
    assert buttons[0]["props"].get("visible") is False


def test_public_demo_exposes_only_the_bounded_conversion_api(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")

    cfg = build_gradio_config_or_skip()
    exposed = {
        dependency.get("api_name")
        for dependency in cfg["dependencies"]
        if dependency.get("backend_fn")
        and dependency.get("api_visibility") == "public"
        and dependency.get("api_name") is not None
    }

    assert exposed == {"run_with_status"}
    assert not any(
        str(dependency.get("api_name") or "").startswith("false")
        for dependency in cfg["dependencies"]
    )
    assert all(
        dependency.get("api_visibility") == "private"
        for dependency in cfg["dependencies"]
        if dependency.get("backend_fn") and dependency.get("api_name") != "run_with_status"
    )


def test_public_demo_rejects_direct_inbox_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")

    with pytest.raises(ValueError, match="local OMD app"):
        ui._save_inbox_note(str(tmp_path), "My note", "Private", "Do not write")

    assert list(tmp_path.iterdir()) == []


def test_public_demo_rejects_direct_output_folder_open(tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")

    with pytest.raises(ValueError, match="local OMD app"):
        ui._open_output_path(str(tmp_path))


def test_public_demo_rejects_unstaged_server_file_as_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    monkeypatch.setattr(ui, "SOURCE_STAGING", tmp_path / "staged")
    server_file = tmp_path / "server-secret.txt"
    server_file.write_text("private", encoding="utf-8")

    with pytest.raises(ValueError, match="staged by this upload session"):
        ui._validate_public_demo_uploaded_file(str(server_file))


def test_public_demo_rejects_saved_list_before_reading_server_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    server_file = tmp_path / "server-list.txt"
    server_file.write_text("/private/server/path\n", encoding="utf-8")

    with pytest.raises(ValueError, match="local OMD app"):
        ui._inspect_source("", [], str(server_file))


def test_json_events_checkbox_is_not_exposed_in_ui_config():
    cfg = build_gradio_config_or_skip()
    labels = {
        component.get("props", {}).get("label"): component["id"]
        for component in cfg["components"]
        if component.get("type") == "checkbox"
    }

    assert "Verbose log" in labels
    assert "JSON events" not in labels


def test_ui_uses_internal_structured_events_unless_verbose_is_enabled():
    cfg = build_gradio_config_or_skip()
    components = {component["id"]: component for component in cfg["components"]}
    verbose = next(
        component
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Verbose log"
    )
    dependencies = [
        dependency
        for dependency in cfg["dependencies"]
        if any(target[0] == verbose["id"] for target in dependency.get("targets", []))
    ]

    assert ui.INTERNAL_JSON_EVENTS_DEFAULT is True
    assert any(
        any(components[output_id].get("type") == "state" for output_id in dependency["outputs"])
        for dependency in dependencies
    )


def test_eta_history_calibration_is_automatic_without_management_controls():
    cfg = build_gradio_config_or_skip()
    labels = [component.get("props", {}).get("label") for component in cfg["components"]]
    buttons = [
        component.get("props", {}).get("value")
        for component in cfg["components"]
        if component.get("type") == "button"
    ]
    visible_text = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in cfg["components"]
    )

    assert "Collect local timings for calibrated ETA" not in labels
    assert "Inspect ETA history" not in buttons
    assert "Reset ETA history" not in buttons
    assert "Progress and ETA history" not in visible_text


def test_ui_css_keeps_helper_text_readable_and_action_labels_on_one_line():
    assert ".omd-field-help" in ui.CUSTOM_CSS
    assert "color: var(--omd-muted) !important" in ui.CUSTOM_CSS
    assert ".omd-no-wrap-btn" in ui.CUSTOM_CSS
    assert "white-space: nowrap !important" in ui.CUSTOM_CSS


def test_reset_eta_history_also_removes_shadow_calibration_samples(tmp_path, monkeypatch):
    from omd.eta_calibration import EtaCalibrationSample, EtaCalibrationStore
    from omd.eta_history import EtaHistoryStore, EtaObservation

    history_path = tmp_path / "eta-history.json"
    calibration_path = tmp_path / "eta-calibration-samples.json"
    monkeypatch.setenv("OMD_ETA_HISTORY_PATH", str(history_path))
    monkeypatch.setenv("OMD_ETA_CALIBRATION_PATH", str(calibration_path))
    EtaHistoryStore(history_path).record(
        EtaObservation(
            stage_id="convert",
            source_class="file",
            device_tier="arm64-16gb-8core",
            runtime="markitdown",
            model_key="none",
            cold_start=None,
            unit="items",
            work_units=1,
            duration_seconds=5,
            outcome="success",
            observed_at=time.time(),
        )
    )
    EtaCalibrationStore(calibration_path).record(
        EtaCalibrationSample(
            stage="convert",
            source="file",
            device="arm64-16gb-8core",
            runtime="markitdown",
            model="none",
            cold=False,
            unit="items",
            actual_seconds=5,
            baseline_seconds=8,
            shadow_p50_seconds=5,
            shadow_p90_seconds=7,
        )
    )

    ui._reset_eta_history()

    assert EtaHistoryStore(history_path).summary()["observation_count"] == 0
    assert EtaCalibrationStore(calibration_path).summary()["sample_count"] == 0


def test_advanced_options_explain_ocr_and_verbose_log_scope():
    cfg = build_gradio_config_or_skip()
    controls = {
        component.get("props", {}).get("label"): component
        for component in cfg["components"]
        if component.get("type") == "checkbox"
    }

    assert "video cover image contains useful title text" in controls["Read text from video thumbnail"]["props"]["info"]
    assert "screenshots, charts, scanned pages" in controls["Read text from article images"]["props"]["info"]
    assert "Extra lines appear in Process log" in controls["Verbose log"]["props"]["info"]
    assert "not saved into your Obsidian vault" in controls["Verbose log"]["props"]["info"]


def test_ui_polish_defaults_follow_initial_convert_mode():
    cfg = build_gradio_config_or_skip()
    controls = {
        component.get("props", {}).get("label"): component
        for component in cfg["components"]
        if component.get("type") == "checkbox"
    }

    assert controls["Polish Markdown"]["props"].get("value") is True
    assert controls["Polish for Obsidian"]["props"].get("value") is False


def test_workflow_mode_change_updates_mutually_exclusive_polish_controls():
    cfg = build_gradio_config_or_skip()
    components = {component["id"]: component for component in cfg["components"]}
    action_id = next(
        component["id"]
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Action"
    )
    run_id = next(
        component["id"]
        for component in cfg["components"]
        if component.get("props", {}).get("elem_id") == "omd-run-btn"
    )
    polish_md_id = next(
        component["id"]
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Polish Markdown"
    )
    obsidian_polish_id = next(
        component["id"]
        for component in cfg["components"]
        if component.get("props", {}).get("label") == "Polish for Obsidian"
    )
    deps = [
        dep
        for dep in cfg["dependencies"]
        if any(target[0] == action_id for target in dep.get("targets", []))
    ]

    assert any(
        dep.get("outputs") == [run_id, polish_md_id, obsidian_polish_id]
        for dep in deps
    )
    assert components[polish_md_id]["props"]["value"] is True
    assert components[obsidian_polish_id]["props"]["value"] is False


def test_default_polish_model_prefers_installed_ollama_model(monkeypatch):
    class Proc:
        stdout = "NAME ID SIZE MODIFIED\nqwen2.5:14b-instruct abc 9 GB today\n"

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "48")
    monkeypatch.delenv("OMD_POLISH_MODEL", raising=False)
    monkeypatch.setattr(ui.shutil, "which", lambda name: "/usr/local/bin/ollama" if name == "ollama" else None)
    monkeypatch.setattr(ui.subprocess, "run", lambda *_a, **_kw: Proc())

    assert ui._default_polish_model() == "qwen2.5:14b-instruct"


def test_default_polish_model_keeps_explicit_environment_override(monkeypatch):
    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")
    monkeypatch.setenv("OMD_POLISH_MODEL", "custom:20b-instruct")

    assert ui._default_polish_model() == "custom:20b-instruct"


def test_default_polish_model_rejects_installed_model_above_memory_tier(monkeypatch):
    class Proc:
        stdout = "NAME ID SIZE MODIFIED\nqwen2.5:14b-instruct abc 9 GB today\n"

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.delenv("OMD_POLISH_MODEL", raising=False)
    monkeypatch.setattr(ui.shutil, "which", lambda name: "/usr/local/bin/ollama" if name == "ollama" else None)
    monkeypatch.setattr(ui.subprocess, "run", lambda *_a, **_kw: Proc())

    assert ui._default_polish_model() == "qwen3:4b-instruct"


def test_default_polish_model_falls_back_to_recommended_qwen(monkeypatch):
    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.delenv("OMD_POLISH_MODEL", raising=False)
    monkeypatch.setattr(ui.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ui.Path, "is_file", lambda _path: False)

    assert ui._default_polish_model() == "qwen3:4b-instruct"


def test_default_polish_model_does_not_auto_select_thinking_only_qwen(monkeypatch):
    class Proc:
        stdout = "NAME ID SIZE MODIFIED\nqwen3:4b 359d7dd4bcda 2.5 GB today\n"

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.delenv("OMD_POLISH_MODEL", raising=False)
    monkeypatch.setattr(ui.shutil, "which", lambda name: "/usr/local/bin/ollama" if name == "ollama" else None)
    monkeypatch.setattr(ui.subprocess, "run", lambda *_a, **_kw: Proc())

    assert ui._default_polish_model() == "qwen3:4b-instruct"


def test_local_model_status_explains_huggingface_demo_has_no_auto_llm():
    status = ui._local_model_status_html(
        "qwen3:4b-instruct",
        "qwen3:4b-instruct",
        "http://localhost:11434",
        public_demo=True,
    )

    assert "Hosted demo LLM" in status
    assert "does not auto-load" in status
    assert "does not auto-download" in status
    assert "Local model polish is disabled" in status


def test_local_model_status_warns_when_ollama_unreachable(monkeypatch):
    def fail_urlopen(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(ui.urllib.request, "urlopen", fail_urlopen)

    status = ui._local_model_status_html(
        "qwen3:4b-instruct",
        "qwen3:4b-instruct",
        "http://localhost:11434",
        public_demo=False,
    )

    assert "omd-model-status-warn" in status
    assert "Ollama is not reachable" in status
    assert "ollama pull qwen3:4b-instruct" in status


def test_local_model_notice_describes_a_memory_sized_recommendation():
    assert "recommended for this Mac based on available memory" in ui.LOCAL_MODEL_NOTICE
    assert "default recommendation for local Obsidian polish" not in ui.LOCAL_MODEL_NOTICE


def test_local_model_status_uses_memory_sized_recommendation_without_showing_memory(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models":[]}'

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    status = ui._local_model_status_html("", "", "http://localhost:11434", public_demo=False)

    assert "ollama pull qwen2.5:1.5b-instruct" in status
    assert "8 GB" not in status


def test_local_model_status_reports_missing_and_installed_models(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models":[{"name":"qwen3:4b-instruct"}]}'

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    ready = ui._local_model_status_html(
        "qwen3:4b-instruct",
        "qwen3:4b-instruct",
        "http://localhost:11434",
        public_demo=False,
    )
    missing = ui._local_model_status_html(
        "qwen3:4b-instruct",
        "gemma3:4b",
        "http://localhost:11434",
        public_demo=False,
    )

    assert "omd-model-status-ok" in ready
    assert "qwen3:4b-instruct" in ready
    assert "omd-model-status-warn" in missing
    assert "missing gemma3:4b" in missing
    assert "ollama pull gemma3:4b" in missing
    assert "optional AI polish" in missing
    assert "Markdown conversion still works" in missing


def test_local_model_status_warns_when_installed_model_is_thinking_only(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models":[{"name":"qwen3:4b"}]}'

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    status = ui._local_model_status_html(
        "qwen3:4b",
        "qwen3:4b",
        "http://localhost:11434",
        public_demo=False,
    )

    assert "omd-model-status-warn" in status
    assert "thinking-only" in status
    assert "ollama pull qwen3:4b-instruct" in status


def test_local_model_status_warns_when_installed_model_exceeds_machine_tier(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models":[{"name":"qwen2.5:14b-instruct"}]}'

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    status = ui._local_model_status_html(
        "qwen2.5:14b-instruct",
        "qwen2.5:14b-instruct",
        "http://localhost:11434",
        public_demo=False,
    )

    assert "omd-model-status-warn" in status
    assert "above this machine" in status
    assert "conservative" in status
    assert "qwen3:4b-instruct" in status
    assert "16 GB" not in status


@pytest.mark.parametrize(
    "host",
    [
        "file:///tmp/ollama",
        "http://169.254.169.254:11434",
        "ollama.example.com:11434",
        "http://localhost:11434/custom-path",
    ],
)
def test_ollama_model_probe_rejects_non_loopback_hosts_before_network(monkeypatch, host):
    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("an invalid Ollama host must not be requested")

    monkeypatch.setattr(ui.urllib.request, "urlopen", fail_urlopen)

    names, error = ui._ollama_model_names(host)

    assert names is None
    assert "loopback" in error or "http://localhost" in error


def test_ollama_model_probe_normalises_scheme_less_loopback_host(monkeypatch):
    requested_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"models":[]}'

    def fake_urlopen(request, **_kwargs):
        requested_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(ui.urllib.request, "urlopen", fake_urlopen)

    names, error = ui._ollama_model_names("localhost:11434")

    assert names == set()
    assert error == ""
    assert requested_urls == ["http://localhost:11434/api/tags"]


def build_argv(tmp_path: Path, **overrides):
    params = {
        "text_input": "https://example.com/watch/abc",
        "file_input": None,
        "batch_file_input": None,
        "workflow_mode": "Convert to .md file",
        "out_dir": str(tmp_path),
        "vault_dir": str(tmp_path / "vault"),
        "filename": "",
        "output_format": "Markdown (.md)",
        "polish_md": False,
        "polish_md_keep_raw": False,
        "polish_md_model": "",
        "memory_cards": False,
        "memory_model": "",
        "reel_polish": False,
        "reel_polish_model": "",
        "ocr_thumbnail": False,
        "ocr_article_images": False,
        "keep_video": False,
        "cookies_file": "",
        "cookies_browser": "(none)",
        "lang": "",
        "preferred_languages": "",
        "whisper_model": "",
        "ollama_host": "",
        "verbose": False,
        "json_events": False,
        "reddit_comment_scope": "OP only",
        "instagram_cookies_file": "",
    }
    params.update(overrides)
    return ui._build_argv(**params)


def make_receipt(
    *,
    state: str = "queued",
    recovery_action: str | None = None,
    source_state: str = "referenced",
) -> ui.ContextReceipt:
    kwargs = {
        "job_id": "job_1234567890abcdef",
        "source_type": "source",
        "source_state": source_state,
        "destination": "Markdown folder",
        "privacy_mode": "local_only",
        "accepted_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
        "state": state,
        "recovery_action": recovery_action,
    }
    if source_state in {"secured", "missing"}:
        kwargs["content_hash"] = "a" * 64
        kwargs["secured_source"] = "sources/job_1234567890abcdef/source.bin"
    return ui.ContextReceipt(**kwargs)


def test_build_argv_rejects_verbose_plus_json_events(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_argv(tmp_path, verbose=True, json_events=True)


def test_build_argv_rejects_missing_cookies_file(tmp_path):
    with pytest.raises(ValueError, match="Cookie file not found"):
        build_argv(tmp_path, cookies_file=str(tmp_path / "missing-cookies.txt"))


def test_build_argv_adds_existing_cookies_file(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    argv, _ = build_argv(tmp_path, cookies_file=str(cookies))

    assert "--cookies" not in argv
    assert argv[argv.index("--douyin-cookies") + 1] == str(cookies)


def test_build_argv_adds_separate_xhs_cookies_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "BATCH_STAGING", tmp_path / "batch-lists")
    douyin = tmp_path / "douyin_cookies.txt"
    xhs = tmp_path / "xhs_cookies.txt"
    douyin.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    xhs.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    argv, _ = build_argv(
        tmp_path,
        text_input="https://v.douyin.com/a/\nhttp://xhslink.com/a/b/",
        cookies_file=str(douyin),
        xhs_cookies_file=str(xhs),
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert argv[argv.index("--douyin-cookies") + 1] == str(douyin)
    assert argv[argv.index("--xhs-cookies") + 1] == str(xhs)


def test_build_argv_adds_reddit_top_comments_mode(tmp_path):
    argv, _ = build_argv(
        tmp_path,
        text_input="https://www.reddit.com/r/test/comments/abc/demo/",
        reddit_comment_scope="OP + Top comments",
    )

    assert argv[argv.index("--reddit-comments") + 1] == "top"


def test_build_argv_does_not_add_reddit_comment_scope_to_non_reddit_source(tmp_path):
    argv, _ = build_argv(
        tmp_path,
        text_input="https://youtu.be/abc123",
        reddit_comment_scope="OP + Top comments",
    )

    assert "--comments" not in argv
    assert "--reddit-comments" not in argv


def test_build_argv_defaults_reddit_to_op_only(tmp_path):
    argv, _ = build_argv(
        tmp_path,
        text_input="https://www.reddit.com/r/test/comments/abc/demo/",
    )

    assert "--comments" not in argv


def test_build_argv_rejects_missing_xhs_cookies_file(tmp_path):
    with pytest.raises(ValueError, match="XHS cookie file not found"):
        build_argv(tmp_path, xhs_cookies_file=str(tmp_path / "missing-xhs.txt"))


def test_build_argv_blocks_douyin_without_matching_cookie_file(tmp_path):
    with pytest.raises(ValueError, match="Default / Douyin cookies.txt path is empty"):
        build_argv(tmp_path, text_input="https://v.douyin.com/abc123/")


def test_build_argv_blocks_xhs_without_matching_cookie_file(tmp_path):
    with pytest.raises(ValueError, match="XHS / Rednote cookies.txt path is empty"):
        build_argv(tmp_path, text_input="http://xhslink.com/a/abcDEF/")


def test_build_argv_allows_public_only_sources_without_cookie_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "BATCH_STAGING", tmp_path / "batch-lists")
    argv, _ = build_argv(
        tmp_path,
        text_input="https://x.com/openai/status/1234567890\nhttps://www.reddit.com/r/test/comments/abc/demo/",
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]


def test_build_argv_prefers_typed_text_over_stale_uploaded_file(tmp_path):
    old_upload = tmp_path / "old.pdf"
    old_upload.write_text("old", encoding="utf-8")

    argv, output = build_argv(
        tmp_path,
        text_input="https://mp.weixin.qq.com/s/newArticle",
        file_input=str(old_upload),
    )

    assert argv[3] == "https://mp.weixin.qq.com/s/newArticle"
    assert str(old_upload) not in argv
    assert output.name == "mp-newArticle.md"


def test_build_argv_accepts_multiple_source_files_as_batch(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    first = tmp_path / "paper.pdf"
    second = tmp_path / "scan.png"
    first.write_text("pdf", encoding="utf-8")
    second.write_text("png", encoding="utf-8")

    argv, output = build_argv(
        tmp_path,
        text_input="",
        file_input=[str(first), str(second)],
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert Path(argv[4]).read_text(encoding="utf-8") == f"{first}\n{second}\n"
    assert argv[5:7] == ["-o", str(tmp_path)]
    assert output == tmp_path


def test_build_argv_capture_accepts_multiple_source_files_as_batch(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    first = tmp_path / "doc.pdf"
    second = tmp_path / "photo.jpg"
    first.write_text("pdf", encoding="utf-8")
    second.write_text("jpg", encoding="utf-8")
    vault = tmp_path / "vault"

    argv, output = build_argv(
        tmp_path,
        text_input="",
        file_input=[str(first), str(second)],
        workflow_mode="Capture to vault note",
        vault_dir=str(vault),
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "capture"]
    assert Path(argv[4]).read_text(encoding="utf-8") == f"{first}\n{second}\n"
    assert argv[5:7] == ["--vault", str(vault)]
    assert "--batch" in argv
    assert output == vault


def test_build_argv_adds_article_image_ocr_flag(tmp_path):
    argv, _ = build_argv(tmp_path, ocr_article_images=True)

    assert "--ocr-article-images" in argv


def test_build_argv_adds_polish_flags(tmp_path):
    argv, _ = build_argv(
        tmp_path,
        polish_md=True,
        polish_md_keep_raw=True,
        polish_md_model="gemma3:4b",
        ollama_host="http://localhost:11434",
    )

    assert "--polish-md" in argv
    assert argv[argv.index("--polish-md-model") + 1] == "gemma3:4b"
    assert "--polish-md-keep-raw" in argv
    assert argv[argv.index("--polish-md-host") + 1] == "http://localhost:11434"


def test_build_argv_rejects_remote_ollama_host(tmp_path):
    with pytest.raises(ValueError, match="explicit opt-in"):
        build_argv(
            tmp_path,
            polish_md=True,
            ollama_host="https://models.example.com",
        )


def test_build_argv_capture_to_vault_adds_memory_flags(tmp_path):
    vault = tmp_path / "AI-Memory"

    argv, output = build_argv(
        tmp_path,
        workflow_mode="Capture to vault note",
        vault_dir=str(vault),
        memory_cards=True,
        memory_model="qwen3:4b",
        ollama_host="http://localhost:11434",
    )

    assert argv[:5] == [ui.sys.executable, "-m", "omd.cli", "capture", "https://example.com/watch/abc"]
    assert argv[argv.index("--vault") + 1] == str(vault)
    assert "--format" not in argv
    assert "--memory-cards" in argv
    assert argv[argv.index("--memory-model") + 1] == "qwen3:4b"
    assert argv[argv.index("--memory-timeout") + 1] == str(ui.UI_MEMORY_TIMEOUT_SECONDS)
    assert argv[argv.index("--memory-host") + 1] == "http://localhost:11434"
    assert output == vault


def test_build_argv_capture_to_vault_can_disable_obsidian_polish(tmp_path):
    vault = tmp_path / "AI-Memory"

    argv, output = build_argv(
        tmp_path,
        workflow_mode="Capture to vault note",
        vault_dir=str(vault),
        memory_cards=False,
        ollama_host="http://localhost:11434",
    )

    assert "--memory-cards" not in argv
    assert "--memory-timeout" not in argv
    assert "--memory-host" not in argv
    assert output == vault


def test_build_argv_capture_to_vault_batch_uses_capture_batch(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    vault = tmp_path / "AI-Memory"

    argv, output = build_argv(
        tmp_path,
        workflow_mode="Capture to vault note",
        vault_dir=str(vault),
        text_input="https://example.com/a\nhttps://example.com/b\n",
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "capture"]
    assert Path(argv[4]).is_file()
    assert argv[argv.index("--vault") + 1] == str(vault)
    assert "--batch" in argv
    assert output == vault


def test_build_argv_memory_cards_require_capture_mode(tmp_path):
    with pytest.raises(ValueError, match="Polish for Obsidian requires Capture to vault"):
        build_argv(tmp_path, memory_cards=True)


def test_build_argv_rejects_local_video_file(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")

    with pytest.raises(ValueError, match="Local video file capture is not supported"):
        build_argv(tmp_path, file_input=str(video), text_input="")


def test_build_argv_adds_preferred_languages(tmp_path):
    argv, _ = build_argv(tmp_path, preferred_languages="zh,en")

    assert argv[argv.index("--preferred-languages") + 1] == "zh,en"


def test_build_argv_defaults_to_markdown_format_and_suffix(tmp_path):
    argv, output = build_argv(tmp_path)

    assert "--format" not in argv
    assert output.suffix == ".md"


def test_build_argv_handles_messy_douyin_share_text(tmp_path):
    share_text = (
        "8.92 复制打开抖音，看看【艾丽的无废话财经的作品】6.27 终于 贝森特道出了核心目的 "
        "# 沃什 #... https://v.douyin.com/yGWf39cCbCE/ :4pm z"
    )
    cookies = tmp_path / "douyin_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    argv, output = build_argv(tmp_path, text_input=share_text, cookies_file=str(cookies))

    assert argv[3] == share_text
    assert output.name == "v-yGWf39cCbCE.md"


def test_public_demo_uses_temp_output_for_public_url(tmp_path, monkeypatch):
    public_out = tmp_path / "public-out"
    monkeypatch.setenv("OMD_PUBLIC_DEMO_OUTPUT_DIR", str(public_out))

    argv, output = build_argv(
        tmp_path,
        text_input="https://example.com/page",
        out_dir="",
        output_format="Markdown (.md)",
        public_demo=True,
    )

    assert argv[argv.index("-o") + 1].startswith(str(public_out))
    assert output.parent == public_out


def test_public_demo_allows_uploaded_file(tmp_path, monkeypatch):
    public_out = tmp_path / "public-out"
    upload = tmp_path / "report.html"
    upload.write_text("<h1>Hello</h1>", encoding="utf-8")
    monkeypatch.setenv("OMD_PUBLIC_DEMO_OUTPUT_DIR", str(public_out))

    argv, output = build_argv(
        tmp_path,
        text_input="",
        file_input=str(upload),
        out_dir="",
        output_format="Markdown (.md)",
        public_demo=True,
    )

    assert argv[3] == str(upload)
    assert output.parent == public_out


def test_public_demo_rejects_uploaded_video_file(tmp_path, monkeypatch):
    public_out = tmp_path / "public-out"
    upload = tmp_path / "clip.mp4"
    upload.write_bytes(b"fake video")
    monkeypatch.setenv("OMD_PUBLIC_DEMO_OUTPUT_DIR", str(public_out))

    with pytest.raises(ValueError, match="video file uploads are not supported"):
        build_argv(
            tmp_path,
            text_input="",
            file_input=str(upload),
            out_dir="",
            output_format="Markdown (.md)",
            public_demo=True,
        )


def test_public_demo_rejects_loopback_url_before_inspection(monkeypatch):
    def fail_inspect(_target):
        raise AssertionError("private URL must be rejected before source inspection")

    monkeypatch.setattr("omd._preflight.inspect_target", fail_inspect)

    with pytest.raises(ValueError, match="public internet"):
        ui._validate_public_demo_target(
            "http://127.0.0.1:7860/private",
            uploaded_paths=set(),
        )


def test_public_demo_accepts_share_text_containing_public_url(monkeypatch):
    inspected = []

    def fake_inspect(target):
        inspected.append(target)
        return {"needs_cookies": False, "needs_tools": []}

    monkeypatch.setattr("omd._preflight.inspect_target", fake_inspect)

    ui._validate_public_demo_target(
        "Shared from my browser: https://example.com/article Read this later",
        uploaded_paths=set(),
    )

    assert inspected == ["https://example.com/article"]


def test_public_demo_rejects_typed_local_path(tmp_path):
    local_file = tmp_path / "report.html"
    local_file.write_text("<h1>Hello</h1>", encoding="utf-8")

    with pytest.raises(ValueError, match="local paths require Full Power Demo"):
        build_argv(
            tmp_path,
            text_input=str(local_file),
            out_dir="",
            public_demo=True,
        )


def test_public_demo_rejects_cookie_gated_sources(tmp_path):
    with pytest.raises(ValueError, match="cookie-gated sources"):
        build_argv(
            tmp_path,
            text_input="https://v.douyin.com/abc123/",
            out_dir="",
            public_demo=True,
        )


def test_public_demo_rejects_browser_cookie_extraction(tmp_path):
    with pytest.raises(ValueError, match="browser cookie extraction"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            out_dir="",
            cookies_browser="chrome",
            public_demo=True,
        )


def test_public_demo_rejects_cookie_files_by_default(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with pytest.raises(ValueError, match="disables cookie files"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            out_dir="",
            cookies_file=str(cookies),
            public_demo=True,
        )


def test_public_demo_rejects_staged_cookie_symlink(tmp_path, monkeypatch):
    staging = tmp_path / "cookies"
    staging.mkdir()
    outside = tmp_path / "private-cookies.txt"
    outside.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    staged_link = staging / "uploaded-cookies.txt"
    staged_link.symlink_to(outside)
    monkeypatch.setattr(ui, "COOKIES_STAGING", staging)
    monkeypatch.setenv("OMD_PUBLIC_DEMO_ALLOW_COOKIE_UPLOAD", "1")

    with pytest.raises(ValueError, match="uploaded/staged cookie files"):
        build_argv(
            tmp_path,
            cookies_file=str(staged_link),
            public_demo=True,
        )


def test_public_demo_rejects_cookie_path_that_resolves_outside_staging(tmp_path, monkeypatch):
    staging = tmp_path / "cookies"
    staging.mkdir()
    outside = tmp_path / "private-cookies.txt"
    outside.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    traversal_path = staging / ".." / outside.name
    monkeypatch.setattr(ui, "COOKIES_STAGING", staging)
    monkeypatch.setenv("OMD_PUBLIC_DEMO_ALLOW_COOKIE_UPLOAD", "1")

    with pytest.raises(ValueError, match="uploaded/staged cookie files"):
        build_argv(
            tmp_path,
            cookies_file=str(traversal_path),
            public_demo=True,
        )


def test_public_demo_accepts_regular_cookie_file_inside_staging(tmp_path, monkeypatch):
    staging = tmp_path / "cookies"
    staging.mkdir()
    staged_cookie = staging / "uploaded-cookies.txt"
    staged_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(ui, "COOKIES_STAGING", staging)
    monkeypatch.setenv("OMD_PUBLIC_DEMO_ALLOW_COOKIE_UPLOAD", "1")

    argv, _output = build_argv(
        tmp_path,
        cookies_file=str(staged_cookie),
        public_demo=True,
    )

    assert argv[argv.index("--douyin-cookies") + 1] == str(staged_cookie)


def test_public_demo_rejects_polish_and_ollama(tmp_path):
    with pytest.raises(ValueError, match="disables local Ollama"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            out_dir="",
            polish_md=True,
            public_demo=True,
        )

    with pytest.raises(ValueError, match="disables local Ollama"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            out_dir="",
            ollama_host="http://localhost:11434",
            public_demo=True,
        )


def test_public_demo_rejects_capture_to_vault(tmp_path):
    with pytest.raises(ValueError, match="cannot write to a local vault"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            workflow_mode="Capture to vault",
            out_dir="",
            public_demo=True,
        )


def test_public_demo_rejects_kept_media(tmp_path):
    with pytest.raises(ValueError, match="keeping media files is disabled"):
        build_argv(
            tmp_path,
            text_input="https://example.com/page",
            out_dir="",
            keep_video=True,
            public_demo=True,
        )


def test_public_demo_rejects_large_upload(tmp_path, monkeypatch):
    upload = tmp_path / "large.html"
    upload.write_bytes(b"x" * 2048)
    monkeypatch.setenv("OMD_PUBLIC_DEMO_MAX_UPLOAD_MB", "1")
    monkeypatch.setattr(ui, "_public_demo_max_upload_bytes", lambda: 1024)

    with pytest.raises(ValueError, match="upload limit"):
        build_argv(
            tmp_path,
            text_input="",
            file_input=str(upload),
            out_dir="",
            public_demo=True,
        )


def test_public_demo_media_mode_selects_faster_whisper(tmp_path, monkeypatch):
    upload = tmp_path / "clip.mp3"
    upload.write_bytes(b"not real audio")
    monkeypatch.setenv("OMD_PUBLIC_DEMO_ALLOW_MEDIA", "1")
    monkeypatch.setattr(ui, "_media_duration_seconds", lambda _path: 30.0)

    argv, _ = build_argv(
        tmp_path,
        text_input="",
        file_input=str(upload),
        out_dir="",
        whisper_model="small",
        public_demo=True,
    )

    assert argv[argv.index("--whisper-backend") + 1] == "faster-whisper"
    assert argv[argv.index("--max-duration") + 1] == "600"
    assert argv[argv.index("--model") + 1] == "small"


def test_build_argv_allows_markdown_output(tmp_path):
    argv, output = build_argv(tmp_path, output_format="Markdown (.md)")

    assert "--format" not in argv
    assert output.suffix == ".md"


def test_build_argv_keeps_explicit_rmarkdown_filename(tmp_path):
    argv, output = build_argv(
        tmp_path,
        filename="custom.Rmd",
        output_format="RMarkdown (.Rmd)",
    )

    assert argv[argv.index("-o") + 1].endswith("custom.Rmd")
    assert output.name == "custom.Rmd"


def test_build_argv_rejects_filename_paths(tmp_path):
    for filename in ["/tmp/escape.md", "../escape.md", "nested/output.md"]:
        with pytest.raises(ValueError, match="Filename must be a file name only"):
            build_argv(tmp_path, filename=filename)


def test_build_argv_rejects_empty_output_folder(tmp_path):
    with pytest.raises(ValueError, match="Choose an output folder"):
        build_argv(tmp_path, out_dir=" ")


def test_build_argv_rejects_file_output_folder(tmp_path):
    out_file = tmp_path / "out"
    out_file.write_text("not a folder", encoding="utf-8")

    with pytest.raises(ValueError, match="Output folder must be a folder"):
        build_argv(tmp_path, out_dir=str(out_file))


def test_build_argv_rejects_output_folder_with_file_parent(tmp_path):
    blocked = tmp_path / "notdir"
    blocked.write_text("not a folder", encoding="utf-8")

    with pytest.raises(ValueError, match="Output folder parent must be a folder"):
        build_argv(tmp_path, out_dir=str(blocked / "out"))


def test_build_argv_selected_format_rewrites_mismatched_filename_suffix(tmp_path):
    argv, output = build_argv(
        tmp_path,
        filename="custom.md",
        output_format="RMarkdown (.Rmd)",
    )

    assert argv[argv.index("-o") + 1].endswith("custom.Rmd")
    assert output.name == "custom.Rmd"
    assert argv[argv.index("--format") + 1] == "rmd"

    argv, output = build_argv(
        tmp_path,
        filename="custom.Rmd",
        output_format="Markdown (.md)",
    )

    assert argv[argv.index("-o") + 1].endswith("custom.md")
    assert output.name == "custom.md"
    assert "--format" not in argv


def test_build_argv_omits_none_cookies_browser(tmp_path):
    argv, _ = build_argv(tmp_path, cookies_browser="(none)")

    assert "--cookies-from-browser" not in argv


def test_stage_cookies_chmods_uploaded_copy(tmp_path, monkeypatch):
    staging = tmp_path / "staged"
    monkeypatch.setattr(ui, "COOKIES_STAGING", staging)
    source = tmp_path / "cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    source.chmod(0o644)

    staged = Path(ui._stage_cookies(str(source)))

    assert staged.parent == staging
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert staged.stat().st_mode & 0o777 == 0o600


def test_stage_cookies_uses_unique_names_for_fast_reuploads(tmp_path, monkeypatch):
    staging = tmp_path / "staged"
    monkeypatch.setattr(ui, "COOKIES_STAGING", staging)
    source = tmp_path / "cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    values = iter([111, 222])
    monkeypatch.setattr(ui.time, "time_ns", lambda: next(values))

    first = Path(ui._stage_cookies(str(source)))
    second = Path(ui._stage_cookies(str(source)))

    assert first != second
    assert first.name.startswith("uploaded_111_")
    assert second.name.startswith("uploaded_222_")
    assert first.exists()
    assert second.exists()


def test_stage_cookies_rejects_uploaded_directory(tmp_path):
    with pytest.raises(ValueError, match="Cookie file must be a file"):
        ui._stage_cookies(str(tmp_path))


def test_stage_cookies_reports_staging_failure(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(ui, "COOKIES_STAGING", blocked / "cookies")
    source = tmp_path / "cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Could not stage cookie file"):
        ui._stage_cookies(str(source))


def test_stage_batch_list_chmods_uploaded_copy(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    source = tmp_path / "urls.txt"
    source.write_text("https://example.com/a\n", encoding="utf-8")
    source.chmod(0o644)

    staged = Path(ui._stage_batch_list(str(source)))

    assert staged.parent == staging
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert staged.stat().st_mode & 0o777 == 0o600


def test_stage_batch_list_rejects_uploaded_directory(tmp_path):
    with pytest.raises(ValueError, match="Batch list must be a file"):
        ui._stage_batch_list(str(tmp_path))


def test_stage_batch_list_reports_staging_failure(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(ui, "BATCH_STAGING", blocked / "batch-lists")
    source = tmp_path / "urls.txt"
    source.write_text("https://example.com/a\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Could not stage batch list"):
        ui._stage_batch_list(str(source))


def test_write_pasted_batch_list_uses_unique_names(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    values = iter([333, 444])
    monkeypatch.setattr(ui.time, "time_ns", lambda: next(values))

    first = ui._write_pasted_batch_list(["https://example.com/a"])
    second = ui._write_pasted_batch_list(["https://example.com/b"])

    assert first != second
    assert first.name == "pasted_333.txt"
    assert second.name == "pasted_444.txt"
    assert first.read_text(encoding="utf-8") == "https://example.com/a\n"
    assert second.read_text(encoding="utf-8") == "https://example.com/b\n"


def test_build_argv_uses_batch_for_pasted_multiline_list(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)

    argv, output = build_argv(
        tmp_path,
        text_input="https://example.com/a\n# comment\nhttps://example.com/b\n",
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert Path(argv[4]).is_file()
    assert Path(argv[4]).read_text(encoding="utf-8") == "https://example.com/a\nhttps://example.com/b\n"
    assert argv[5:7] == ["-o", str(tmp_path)]
    assert output == tmp_path


def test_build_argv_uses_rmarkdown_format_for_batch(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)

    argv, output = build_argv(
        tmp_path,
        text_input="https://example.com/a\nhttps://example.com/b\n",
        output_format="RMarkdown (.Rmd)",
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert argv[argv.index("--format") + 1] == "rmd"
    assert output == tmp_path


def test_build_argv_uses_batch_for_multiple_urls_on_one_line(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)
    cookies = tmp_path / "douyin_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    argv, output = build_argv(
        tmp_path,
        text_input="first https://v.douyin.com/a/ middle second https://v.douyin.com/b/ tail",
        cookies_file=str(cookies),
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert Path(argv[4]).read_text(encoding="utf-8") == (
        "https://v.douyin.com/a\nhttps://v.douyin.com/b\n"
    )
    assert output == tmp_path


def test_build_argv_uses_batch_for_list_file(tmp_path):
    list_file = tmp_path / "urls.txt"
    list_file.write_text("https://example.com/a\n", encoding="utf-8")

    argv, output = build_argv(tmp_path, batch_file_input=str(list_file))

    assert argv[:5] == [ui.sys.executable, "-m", "omd.cli", "batch", str(list_file)]
    assert output == tmp_path


def test_build_argv_allows_polish_md_for_batch(tmp_path, monkeypatch):
    staging = tmp_path / "batch-lists"
    monkeypatch.setattr(ui, "BATCH_STAGING", staging)

    argv, output = build_argv(
        tmp_path,
        text_input="https://example.com/a\nhttps://example.com/b\n",
        polish_md=True,
        polish_md_model="gemma3:4b",
    )

    assert argv[:4] == [ui.sys.executable, "-m", "omd.cli", "batch"]
    assert "--polish-md" in argv
    assert argv[argv.index("--polish-md-model") + 1] == "gemma3:4b"
    assert output == tmp_path


def test_stream_subprocess_terminates_child_when_generator_closes():
    gen = ui._stream_subprocess([
        ui.sys.executable,
        "-c",
        "import os, time; print(os.getpid(), flush=True); time.sleep(30)",
    ])
    tag, line = next(gen)
    assert tag == "out"
    pid = int(line)

    gen.close()

    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("UI subprocess was still alive after stream generator closed")


def test_stream_subprocess_emits_heartbeat_while_child_is_quiet():
    gen = ui._stream_subprocess([
        ui.sys.executable,
        "-c",
        "import time; time.sleep(1.2)",
    ])

    assert next(gen) == ("tick", "")
    assert list(gen)[-1] == ("rc", "0")


def test_stream_subprocess_terminates_process_group_when_generator_closes():
    gen = ui._stream_subprocess([
        ui.sys.executable,
        "-c",
        (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid, flush=True); "
            "time.sleep(30)"
        ),
    ])
    tag, line = next(gen)
    assert tag == "out"
    child_pid = int(line)

    gen.close()

    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("UI subprocess grandchild was still alive after stream generator closed")


def test_inspect_source_reports_single_backend():
    report = ui._inspect_source("https://t.me/demo/42", None, None)

    assert "Items detected: **1**" in report
    assert "Backend: `telegram`" in report
    assert "Type: `telegram_post_url`" in report
    assert "Cookies: `False`" in report
    assert "Tools: `none`" in report
    assert "Ready: `True`" in report
    assert "Missing tools: `none`" in report


def test_inspect_source_reports_missing_douyin_tool(monkeypatch):
    from omd import doctor

    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("f2", False, "tool", "not on PATH", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ])

    report = ui._inspect_source("https://v.douyin.com/abc123/", None, None)

    assert "Backend: `reel`" in report
    assert "Type: `douyin_url`" in report
    assert "Ready: `False`" in report
    assert "Missing tools: `f2`" in report
    assert "Cookies needed: `True`" in report
    assert "Missing auth: `cookies_file`" in report
    assert "Default / Douyin cookies.txt path is empty" in report


def test_inspect_source_reports_cookie_file_ready_for_required_auth(monkeypatch, tmp_path):
    from omd import doctor

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("f2", True, "tool", "/bin/f2", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ])

    report = ui._inspect_source("https://v.douyin.com/abc123/", None, None, str(cookies), "(none)")

    assert "Ready: `True`" in report
    assert "Cookie file: `found:" in report
    assert "Missing auth: `none`" in report


def test_inspect_source_uses_xhs_cookie_file_for_xhs_items(monkeypatch, tmp_path):
    from omd import doctor

    douyin = tmp_path / "douyin_cookies.txt"
    xhs = tmp_path / "xhs_cookies.txt"
    douyin.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    xhs.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("tesseract", True, "tool", "/bin/tesseract", "OCR"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ])

    report = ui._inspect_source(
        "http://xhslink.com/a/abc/",
        None,
        None,
        str(douyin),
        "(none)",
        str(xhs),
    )

    assert "Type: `xhs_url`" in report
    assert f"Cookie file: `found: {xhs}`" in report
    assert str(douyin) not in report
    assert "XHS / Rednote cookies.txt path is empty" not in report


def test_inspect_source_warns_when_xhs_cookie_file_is_missing(monkeypatch):
    from omd import doctor

    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("tesseract", True, "tool", "/bin/tesseract", "OCR"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ])

    report = ui._inspect_source("http://xhslink.com/a/abc/", None, None)

    assert "Type: `xhs_url`" in report
    assert "Cookies needed: `True`" in report
    assert "Missing auth: `cookies_file`" in report
    assert "XHS / Rednote cookies.txt path is empty" in report


def test_inspect_source_reports_public_auth_warnings():
    report = ui._inspect_source(
        "https://www.reddit.com/r/test/comments/abc/demo/\n"
        "https://x.com/openai/status/1234567890\n"
        "https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS",
        None,
        None,
    )

    assert "Reddit conversion supports public posts only" in report
    assert "X/Twitter conversion uses public embed endpoints" in report
    assert "Threads conversion reads public page metadata" in report


def test_inspect_source_reports_pasted_batch():
    report = ui._inspect_source(
        "https://news.ycombinator.com/item?id=12345\nhttps://bsky.app/profile/alice.bsky.social/post/abc",
        None,
        None,
    )

    assert "Items detected: **2**" in report
    assert "Backend: `hn`" in report
    assert "Backend: `bsky`" in report


def test_inspect_source_reports_threads_backend():
    report = ui._inspect_source("https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS", None, None)

    assert "Items detected: **1**" in report
    assert "Backend: `threads`" in report
    assert "Type: `threads_post_url`" in report


def test_inspect_source_does_not_show_cookies_for_public_only_social(tmp_path):
    cookies = tmp_path / "douyin_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    report = ui._inspect_source(
        "https://www.reddit.com/r/test/comments/abc/demo/\n"
        "https://x.com/openai/status/1234567890\n"
        "https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS",
        None,
        None,
        str(cookies),
        "(none)",
    )

    assert "Cookie strategy: `public_only_no_cookie_passthrough`" in report
    assert str(cookies) not in report
    assert "Cookie file: `not needed`" in report
    assert "optional:" not in report


def test_inspect_source_reads_batch_file(tmp_path):
    batch = tmp_path / "urls.txt"
    batch.write_text("https://t.me/demo/42\nhttps://news.ycombinator.com/item?id=12345\n", encoding="utf-8")

    report = ui._inspect_source("", None, str(batch))

    assert "Items detected: **2**" in report
    assert "Backend: `telegram`" in report
    assert "Backend: `hn`" in report


def test_inspect_source_prefers_typed_text_over_stale_file_input(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"not really a png")

    report = ui._inspect_source("https://t.me/demo/42", str(image), None)

    assert "Items detected: **1**" in report
    assert "Backend: `telegram`" in report
    assert "Type: `telegram_post_url`" in report


def test_inspect_source_uses_file_input_when_text_is_empty(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"not really a png")

    report = ui._inspect_source("", str(image), None)

    assert "Items detected: **1**" in report
    assert "Backend: `tesseract`" in report
    assert "Type: `image_file`" in report


def test_status_for_batch_lines_advances_progress():
    total, index, pct, label, detail = ui._status_for_log_line(
        "→ batch: 2 items",
        batch_total=None,
        batch_index=0,
        percent=None,
    )
    assert (total, index, pct, label, detail) == (2, 0, 2, "batch", "2 items")

    total, index, pct, label, detail = ui._status_for_log_line(
        "→ [2/2] https://v.douyin.com/abc",
        batch_total=total,
        batch_index=index,
        percent=pct,
    )
    assert (total, index, pct, label, detail) == (2, 2, 50, "running...", "current 2/2")

    total, index, pct, label, detail = ui._status_for_log_line(
        "→ Transcribing audio (whisper)",
        batch_total=total,
        batch_index=index,
        percent=pct,
    )
    assert (total, index, label, detail) == (2, 2, "transcribing", "current 2/2")
    assert pct > 50


def test_status_html_contains_progress_bar():
    html = ui._status_html(
        "running",
        "transcribing",
        detail="item 1/2",
        percent=42,
        summary=(("total", "1/2 processed"), ("ok", "1 succeeded")),
    )

    assert 'class="omd-status-run"' in html
    assert "omd-progress-bar" in html
    assert "--omd-progress:42%" in html
    assert "1/2 processed" in html
    assert "1 succeeded" in html


def test_status_html_exposes_live_region_and_determinate_progress_semantics():
    html = ui._status_html(
        "running",
        "transcribing",
        detail="item 1/2",
        percent=42,
    )

    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-atomic="true"' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuemin="0"' in html
    assert 'aria-valuemax="100"' in html
    assert 'aria-valuenow="42"' in html


def test_status_html_does_not_claim_progress_value_when_indeterminate():
    html = ui._status_html("running", "fetching", percent=None)

    assert 'role="progressbar"' in html
    assert 'aria-valuenow=' not in html


def test_run_counts_distinguish_queued_current_and_finished_items():
    counts = ui._RunCounts(total=5)

    assert counts.summary() == (("total", "5 queued"),)
    ui._update_counts_from_log_line(counts, "✓ [1/5] converted: one.md", 5)

    assert counts.summary() == (("total", "1/5 processed"), ("ok", "1 succeeded"))


def test_download_value_stages_markdown_file_for_gradio(tmp_path, monkeypatch):
    download_staging = tmp_path / "downloads"
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", download_staging)
    source = tmp_path / "output" / "note.md"
    source.parent.mkdir()
    source.write_text("# ok\n", encoding="utf-8")

    staged = Path(ui._download_value_for_output(source) or "")

    assert staged.parent == download_staging
    assert staged != source
    assert staged.read_text(encoding="utf-8") == "# ok\n"


def test_download_staging_retention_uses_staging_time_not_source_mtime(tmp_path, monkeypatch):
    download_staging = tmp_path / "downloads"
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", download_staging)
    source = tmp_path / "old-note.md"
    source.write_text("# old but downloadable\n", encoding="utf-8")
    os.utime(source, (1_700_000_000.0, 1_700_000_000.0))

    staged = Path(ui._download_value_for_output(source) or "")

    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == "# old but downloadable\n"


def test_download_value_packages_batch_markdown_as_zip(tmp_path, monkeypatch):
    download_staging = tmp_path / "downloads"
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", download_staging)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "one.md").write_text("# One\n", encoding="utf-8")
    (output_dir / "two.Rmd").write_text("# Two\n", encoding="utf-8")
    (output_dir / "ignore.txt").write_text("ignore\n", encoding="utf-8")

    staged = Path(ui._download_value_for_output(output_dir) or "")

    assert staged.parent == download_staging
    assert staged.suffix == ".zip"
    with zipfile.ZipFile(staged) as archive:
        assert archive.namelist() == ["one.md", "two.Rmd"]


def test_download_value_excludes_markdown_from_before_current_run(tmp_path, monkeypatch):
    download_staging = tmp_path / "downloads"
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", download_staging)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    old_note = output_dir / "old.md"
    current_note = output_dir / "current.md"
    old_note.write_text("# Old\n", encoding="utf-8")
    current_note.write_text("# Current\n", encoding="utf-8")
    os.utime(old_note, (1_700_000_000.0, 1_700_000_000.0))
    os.utime(current_note, (1_700_000_300.0, 1_700_000_300.0))

    staged = Path(ui._download_value_for_output(output_dir, modified_since=1_700_000_200.0) or "")

    with zipfile.ZipFile(staged) as archive:
        assert archive.namelist() == ["current.md"]


def test_download_value_limits_vault_archive_to_recent_source_notes(tmp_path, monkeypatch):
    download_staging = tmp_path / "downloads"
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", download_staging)
    vault = tmp_path / "vault"
    sources = vault / "Sources" / "Web"
    index = vault / "Index"
    sources.mkdir(parents=True)
    index.mkdir()
    (index / "OMD Captures.md").write_text("# Index\n", encoding="utf-8")
    old_note = sources / "old.md"
    current_note = sources / "current.md"
    unrelated = vault / "private-note.md"
    old_note.write_text("# Old\n", encoding="utf-8")
    current_note.write_text("# Current\n", encoding="utf-8")
    unrelated.write_text("# Private\n", encoding="utf-8")
    os.utime(old_note, (1_700_000_000.0, 1_700_000_000.0))
    os.utime(current_note, (1_700_000_300.0, 1_700_000_300.0))
    os.utime(unrelated, (1_700_000_300.0, 1_700_000_300.0))

    staged = Path(ui._download_value_for_output(vault, modified_since=1_700_000_200.0) or "")

    with zipfile.ZipFile(staged) as archive:
        assert archive.namelist() == ["Sources/Web/current.md"]


def test_download_staging_prunes_expired_and_excess_artifacts(tmp_path):
    staging = tmp_path / "downloads"
    staging.mkdir()
    expired = staging / "expired.md"
    retained = [staging / f"recent-{index}.md" for index in range(4)]
    expired.write_text("old", encoding="utf-8")
    for index, path in enumerate(retained):
        path.write_text(str(index), encoding="utf-8")
        os.utime(path, (1_700_000_100.0 + index, 1_700_000_100.0 + index))
    os.utime(expired, (1_699_990_000.0, 1_699_990_000.0))

    ui._prune_staging_directory(
        staging,
        now=1_700_000_200.0,
        max_age_seconds=1_000.0,
        max_files=2,
    )

    assert not expired.exists()
    assert sorted(path.name for path in staging.iterdir()) == ["recent-2.md", "recent-3.md"]


def test_stale_ui_staging_cleanup_keeps_active_process_roots(tmp_path, monkeypatch):
    stale = tmp_path / "omd-ui-101"
    active = tmp_path / "omd-ui-202"
    unrelated = tmp_path / "other-temp-data"
    for folder in (stale, active, unrelated):
        folder.mkdir()
        (folder / "note.md").write_text("private", encoding="utf-8")
    monkeypatch.setattr(ui, "_pid_is_running", lambda pid: pid == 202)

    ui._prune_stale_ui_staging_roots(temp_root=tmp_path, current_pid=303)

    assert not stale.exists()
    assert active.is_dir()
    assert unrelated.is_dir()


def test_preview_output_summarizes_batch_directory(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "b.Rmd").write_text("# B\n", encoding="utf-8")

    preview, label = ui._preview_output(tmp_path)

    assert label == str(tmp_path)
    assert "Markdown/RMarkdown files: 2" in preview
    assert "`a.md`" in preview
    assert "`b.Rmd`" in preview


def test_preview_output_focuses_vault_capture_on_three_recent_files(tmp_path):
    captures = tmp_path / "Sources" / "Web"
    captures.mkdir(parents=True)
    for index in range(5):
        path = captures / f"source-{index}.md"
        path.write_text(f"# Source {index}\n", encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    vault_index = tmp_path / "Index" / "OMD Captures.md"
    vault_index.parent.mkdir()
    vault_index.write_text("INDEX-BODY-MUST-NOT-APPEAR\n" * 20, encoding="utf-8")

    preview, label = ui._preview_output(tmp_path)

    assert label == str(tmp_path)
    assert "# Saved to vault" in preview
    assert "External sources are stored under `Sources/`" in preview
    assert "`Inbox/` is unchanged" in preview
    assert "`Sources/Web/source-4.md`" in preview
    assert "`Sources/Web/source-3.md`" in preview
    assert "`Sources/Web/source-2.md`" in preview
    assert "source-1.md" not in preview
    assert "source-0.md" not in preview
    assert "INDEX-BODY-MUST-NOT-APPEAR" not in preview


def test_preview_output_rejects_empty_file(tmp_path):
    target = tmp_path / "empty.md"
    target.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="output file is empty"):
        ui._preview_output(target)


def test_preview_output_rejects_empty_batch_directory(tmp_path):
    with pytest.raises(ValueError, match="no Markdown/RMarkdown files"):
        ui._preview_output(tmp_path)


def test_preview_output_rejects_empty_batch_markdown_file(tmp_path):
    (tmp_path / "empty.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty Markdown/RMarkdown files"):
        ui._preview_output(tmp_path)


def test_run_with_status_reports_failed_when_success_has_empty_output(tmp_path, monkeypatch):
    target = tmp_path / "empty.md"
    target.write_text("", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    times = iter((100.0, 225.0))
    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "convert"], target))
    monkeypatch.setattr(ui, "_stream_subprocess", lambda _argv: iter([("rc", "0")]))
    monkeypatch.setattr(ui.time, "monotonic", lambda: next(times))

    updates = list(ui.run_with_status("unused"))

    assert len(updates) == 2
    log, preview_update, output_update, status_html, download_update = updates[-1]
    assert "output file is empty" in log
    assert preview_update["value"].startswith("_failed:")
    assert output_update["value"] == ""
    assert 'class="omd-status-err"' in status_html
    assert "Total: 2m 05s" in status_html
    assert download_update["value"] is None


def test_run_with_status_shows_total_duration_when_complete(tmp_path, monkeypatch):
    target = tmp_path / "complete.md"
    target.write_text("# Complete\n", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    times = iter((100.0, 225.0))
    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "convert"], target))
    monkeypatch.setattr(ui, "_stream_subprocess", lambda _argv: iter([("rc", "0")]))
    monkeypatch.setattr(ui.time, "monotonic", lambda: next(times))

    updates = list(ui.run_with_status("unused"))

    assert "Total: 2m 05s" in updates[-1][3]


def test_run_with_status_counts_successful_batch_items_once(tmp_path, monkeypatch):
    (tmp_path / "one.md").write_text("# One\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("# Two\n", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", tmp_path / "downloads")
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "batch"], tmp_path))
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter([
            ("err", "→ batch: 2 items"),
            ("err", "→ [1/2] /tmp/one.pdf"),
            ("err", "✓ wrote /tmp/out/one.md"),
            ("err", "→ [2/2] /tmp/two.pdf"),
            ("err", "✓ wrote /tmp/out/two.md"),
            ("err", f"✓ wrote {tmp_path}"),
            ("rc", "0"),
        ]),
    )

    updates = list(ui.run_with_status("unused"))

    _log, preview_update, output_update, status_html, download_update = updates[-1]
    assert "Markdown/RMarkdown files: 2" in preview_update["value"]
    assert output_update["value"] == str(tmp_path)
    assert "2/2 processed" in status_html
    assert "2 succeeded" in status_html
    assert "3 succeeded" not in status_html
    assert Path(download_update["value"]).suffix == ".zip"
    assert download_update["interactive"] is True


def test_run_with_status_exposes_partial_batch_output_on_failure(tmp_path, monkeypatch):
    (tmp_path / "ok.md").write_text("# OK\n", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    first_time = True

    def fake_monotonic():
        nonlocal first_time
        if first_time:
            first_time = False
            return 100.0
        return 225.0

    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", tmp_path / "downloads")
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "batch"], tmp_path))
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter([
            ("err", "→ batch: 3 items"),
            ("err", "warn: [2/3] failed: https://example.invalid: converter returned 1"),
            ("err", "warn: batch complete with failures: 2/3 succeeded"),
            ("rc", "1"),
        ]),
    )
    monkeypatch.setattr(ui.time, "monotonic", fake_monotonic)

    updates = list(ui.run_with_status("unused"))

    log, preview_update, output_update, status_html, download_update = updates[-1]
    assert "partial output available despite exit 1" in log
    assert "Markdown/RMarkdown files: 1" in preview_update["value"]
    assert output_update["value"] == str(tmp_path)
    assert "partial failure" in status_html
    assert "3/3 processed" in status_html
    assert "2 succeeded" in status_html
    assert "1 failed" in status_html
    assert "Total: 2m 05s" in status_html
    assert Path(download_update["value"]).suffix == ".zip"
    assert download_update["interactive"] is True


def test_run_with_status_counts_optional_polish_fallback_as_one_partial_item(tmp_path, monkeypatch):
    target = tmp_path / "partial.md"
    target.write_text("# Converted Markdown\n", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", tmp_path / "downloads")
    monkeypatch.setattr(
        ui,
        "_build_argv",
        lambda *_args: (["omd", "source.pdf", "-o", str(target), "--polish-md"], target),
    )
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter([
            ("err", f"✓ wrote {target}"),
            (
                "err",
                "warn: Markdown polish failed for partial.md; "
                "keeping the converted Markdown. timed out",
            ),
            ("rc", "0"),
        ]),
    )
    monkeypatch.setattr(ui.time, "monotonic", lambda: 100.0)

    updates = list(ui.run_with_status("unused"))

    status_html = updates[-1][3]
    assert "saved with warning" in status_html
    assert "1/1 processed" in status_html
    assert "1 partial" in status_html
    assert "2 items" not in status_html
    assert "1 succeeded" not in status_html
    assert "1 failed" not in status_html


def test_run_with_status_keeps_successful_warning_visible(tmp_path, monkeypatch):
    target = tmp_path / "warning.md"
    target.write_text("# Converted Markdown\n", encoding="utf-8")

    class FakeGradio:
        class Error(Exception):
            pass

        @staticmethod
        def update(**kwargs):
            return kwargs

    monkeypatch.setitem(sys.modules, "gradio", FakeGradio)
    monkeypatch.setattr(ui, "DOWNLOAD_STAGING", tmp_path / "downloads")
    monkeypatch.setattr(
        ui,
        "_build_argv",
        lambda *_args: (["omd", "source.pdf", "-o", str(target), "--polish-md"], target),
    )
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter([
            ("err", "warn: source metadata did not include an author"),
            ("rc", "0"),
        ]),
    )
    monkeypatch.setattr(ui.time, "monotonic", lambda: 100.0)

    updates = list(ui.run_with_status("unused"))

    status_html = updates[-1][3]
    assert 'class="omd-status-warn"' in status_html
    assert "done with warning" in status_html
    assert "1/1 processed" in status_html
    assert "1 succeeded" in status_html


def test_context_run_queues_one_privacy_minimised_receipt_per_input(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")

    context_run = ui._queue_context_run(
        "https://example.com/one\nhttps://example.com/two",
        [],
        "",
        "Convert to .md file",
        str(tmp_path / "output"),
        "",
    )

    assert context_run is not None
    assert len(context_run.receipts) == 2
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "omd-data" / "context-outbox" / "jobs").glob("*.json")
    )
    assert "https://example.com/one" not in persisted
    assert "https://example.com/two" not in persisted
    assert str(tmp_path) not in persisted


def test_context_run_secures_local_file_before_processing(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"private local PDF bytes")
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")

    context_run = ui._queue_context_run(
        "",
        [str(source)],
        "",
        "Convert to .md file",
        str(tmp_path / "output"),
        "",
    )
    assert context_run is not None

    assert context_run.receipts[0].state == "queued"
    context_run.secure_sources()
    assert context_run.receipts[0].state == "source_secured"
    assert context_run.receipts[0].source_state == "secured"
    context_run.start_processing()
    assert context_run.receipts[0].state == "processing"


def test_run_with_status_keeps_batch_receipt_outcomes_independent(tmp_path, monkeypatch):
    target = tmp_path / "partial.md"
    target.write_text("# Partial\n", encoding="utf-8")
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    monkeypatch.setattr(
        ui,
        "_build_argv",
        lambda *_args: (["omd", "batch", "--json-events"], target),
    )
    events = [
        {"event": "batch_started", "total": 2},
        {"event": "batch_item_started", "index": 1, "total": 2},
        {"event": "batch_item_succeeded", "index": 1, "total": 2},
        {"event": "batch_item_started", "index": 2, "total": 2},
        {"event": "batch_item_failed", "index": 2, "total": 2},
        {"event": "batch_completed", "succeeded": 1, "failed": 1, "total": 2},
    ]
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter(
            [("err", json.dumps(event)) for event in events] + [("rc", "1")]
        ),
    )
    monkeypatch.setattr(ui.time, "monotonic", lambda: 100.0)

    list(
        ui.run_with_status(
            "https://example.com/one\nhttps://example.com/two",
            [],
            "",
            "Convert to .md file",
            str(tmp_path),
            "",
        )
    )

    receipts = ui.ContextOutbox(tmp_path / "omd-data" / "context-outbox").list_receipts()
    assert [receipt.state for receipt in receipts] == ["complete", "needs_action"]


def test_interrupted_batch_does_not_claim_unprocessed_item_has_partial_output(
    tmp_path, monkeypatch
):
    target = tmp_path / "batch-output"
    target.mkdir()
    (target / "one.md").write_text("# One\n", encoding="utf-8")
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    monkeypatch.setattr(
        ui,
        "_build_argv",
        lambda *_args: (["omd", "batch", "--json-events"], target),
    )
    events = [
        {"event": "batch_started", "total": 2},
        {"event": "batch_item_started", "index": 1, "total": 2},
        {"event": "batch_item_succeeded", "index": 1, "total": 2},
        {"event": "batch_item_started", "index": 2, "total": 2},
    ]
    monkeypatch.setattr(
        ui,
        "_stream_subprocess",
        lambda _argv: iter(
            [("err", json.dumps(event)) for event in events] + [("rc", "1")]
        ),
    )
    monkeypatch.setattr(ui.time, "monotonic", lambda: 100.0)

    list(
        ui.run_with_status(
            "https://example.com/one\nhttps://example.com/two",
            [],
            "",
            "Convert to .md file",
            str(tmp_path),
            "",
        )
    )

    receipts = ui.ContextOutbox(tmp_path / "omd-data" / "context-outbox").list_receipts()
    assert [receipt.state for receipt in receipts] == ["complete", "needs_action"]


def test_context_batch_receipt_failure_is_reported_without_raising():
    class BrokenContextRun:
        def apply_batch_event(self, _event):
            raise OSError("disk unavailable")

    warning = ui._transition_context_batch_event(
        BrokenContextRun(),
        {"event": "batch_item_succeeded", "index": 1, "total": 1},
    )

    assert warning == "receipt update failed for batch item: disk unavailable"


def test_receipt_status_summary_surfaces_retry_action():
    context_run = ui._ContextRun(
        outbox=None,
        receipts=[make_receipt(state="needs_action", recovery_action="retry")],
        local_sources=[],
    )

    kind, text = context_run.status_summary()

    assert kind == "receipt"
    assert "needs action" in text
    assert "next: retry" in text


def test_receipt_status_summary_surfaces_resume_for_recovered_receipt():
    context_run = ui._ContextRun(
        outbox=None,
        receipts=[
            make_receipt(
                state="source_secured",
                recovery_action="resume",
                source_state="secured",
            )
        ],
        local_sources=[],
    )

    _kind, text = context_run.status_summary()

    assert "source secured" in text
    assert "next: resume" in text


def test_receipt_status_summary_surfaces_keep_raw_for_failed_receipt():
    context_run = ui._ContextRun(
        outbox=None,
        receipts=[make_receipt(state="failed", recovery_action="keep_raw")],
        local_sources=[],
    )

    _kind, text = context_run.status_summary()

    assert "failed" in text
    assert "next: keep raw" in text


def test_receipt_status_summary_keeps_action_visible_for_mixed_batch():
    context_run = ui._ContextRun(
        outbox=None,
        receipts=[
            make_receipt(state="complete"),
            make_receipt(state="needs_action", recovery_action="retry"),
        ],
        local_sources=[],
    )

    _kind, text = context_run.status_summary()

    assert "mixed states" in text
    assert "next: retry" in text


def test_context_run_cancel_preserves_item_that_already_needs_action(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    context_run = ui._queue_context_run(
        "https://example.com/one",
        [],
        "",
        "Convert to .md file",
        str(tmp_path),
        "",
    )
    assert context_run is not None
    context_run.start_processing()
    receipt = context_run.receipts[0]
    context_run.receipts[0] = context_run.outbox.fail_stage(
        receipt.job_id,
        error_code="conversion_failed",
        retryable=True,
    )

    context_run.cancel()

    assert context_run.receipts[0].state == "needs_action"


def test_context_run_cancel_preserves_item_with_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    context_run = ui._queue_context_run(
        "https://example.com/one",
        [],
        "",
        "Convert to .md file",
        str(tmp_path),
        "",
    )
    assert context_run is not None
    context_run.start_processing()
    context_run.mark_partial_output()

    context_run.cancel()

    assert context_run.receipts[0].state == "partial_output"


def test_run_with_status_exposes_and_completes_context_receipt(tmp_path, monkeypatch):
    target = tmp_path / "complete.md"
    target.write_text("# Complete\n", encoding="utf-8")
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "convert"], target))
    monkeypatch.setattr(ui, "_stream_subprocess", lambda _argv: iter([("rc", "0")]))
    monkeypatch.setattr(ui.time, "monotonic", lambda: 100.0)

    updates = list(
        ui.run_with_status(
            "https://example.com/article",
            [],
            "",
            "Convert to .md file",
            str(tmp_path),
            "",
        )
    )

    assert "receipt" in updates[0][0].lower()
    assert "job_" in updates[0][3]
    receipts = ui.ContextOutbox(tmp_path / "omd-data" / "context-outbox").list_receipts()
    assert [receipt.state for receipt in receipts] == ["complete"]


def test_public_demo_does_not_persist_local_context_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")

    context_run = ui._queue_context_run(
        "https://example.com/article",
        [],
        "",
        "Convert to .md file",
        str(tmp_path),
        "",
    )

    assert context_run is None
    assert not (tmp_path / "omd-data").exists()


def test_closing_running_ui_generator_cancels_context_receipt(tmp_path, monkeypatch):
    target = tmp_path / "pending.md"
    monkeypatch.setattr(ui, "OMD_DATA_DIR", tmp_path / "omd-data")
    monkeypatch.setattr(ui, "_build_argv", lambda *_args: (["omd", "convert"], target))

    def ticks(_argv):
        while True:
            yield "tick", ""

    monkeypatch.setattr(ui, "_stream_subprocess", ticks)
    generator = ui.run_with_status(
        "https://example.com/article",
        [],
        "",
        "Convert to .md file",
        str(tmp_path),
        "",
    )

    next(generator)
    next(generator)
    next(generator)
    generator.close()

    receipts = ui.ContextOutbox(tmp_path / "omd-data" / "context-outbox").list_receipts()
    assert [receipt.state for receipt in receipts] == ["cancelled"]


def test_save_voice_attachment_uses_filename_when_title_is_blank(tmp_path):
    source = tmp_path / "walking-note.m4a"
    source.write_bytes(b"audio")

    _summary, selector_update, status, upload_update = ui._save_voice_attachment(
        str(tmp_path),
        str(source),
        "",
        "A note I typed first.",
    )

    record_id = selector_update["value"]
    record = ui.VoiceInboxStore(tmp_path).load(record_id)
    assert record.title == "walking-note"
    assert record.my_notes == "A note I typed first."
    assert "audio saved" in status.lower()
    assert upload_update["value"] is None


def test_begin_voice_transcription_uses_existing_local_model_controls(tmp_path):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"audio")
    store = ui.VoiceInboxStore(tmp_path)
    record = store.create(source, title="Memo")

    status = ui._begin_voice_transcription(
        str(tmp_path),
        record.record_id,
        "My note",
        "mlx-community/whisper-large-v3-turbo",
        "en,zh",
    )

    updated = store.load(record.record_id)
    assert updated.transcription_state == "transcribing"
    assert updated.transcript_model == "mlx-community/whisper-large-v3-turbo"
    assert updated.transcript_language == "en"
    assert "started locally" in status.lower()


def test_voice_transcription_failure_keeps_audio_and_note(tmp_path, monkeypatch):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"audio")
    store = ui.VoiceInboxStore(tmp_path)
    record = store.create(source, title="Memo", my_notes="Keep me")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    def fail(*_args, **_kwargs):
        raise RuntimeError("mlx_whisper missing at /private/path")

    monkeypatch.setattr("omd.reel.transcribe", fail)

    transcript, notes, _suggestion, quality, status, _queue = ui._finish_voice_transcription(
        str(tmp_path), record.record_id
    )

    failed = store.load(record.record_id)
    assert transcript == ""
    assert notes == "Keep me"
    assert failed.transcription_state == "failed"
    assert (tmp_path / failed.attachment_path).exists()
    assert "/private/path" not in status
    assert "kept" in status.lower()
    assert "failed" in quality.lower()


def test_voice_transcription_success_surfaces_quality_review(tmp_path, monkeypatch):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"audio")
    store = ui.VoiceInboxStore(tmp_path)
    record = store.create(source, title="Memo")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    monkeypatch.setattr(
        "omd.reel.transcribe",
        lambda *_args, **_kwargs: {"text": "Repeated words.", "confidence": 0.2},
    )

    transcript, _notes, _suggestion, quality, status, _queue = ui._finish_voice_transcription(
        str(tmp_path), record.record_id
    )

    assert transcript == "Repeated words."
    assert "confidence" in quality.lower()
    assert "review" in status.lower()


def test_voice_ui_is_attachment_only_and_does_not_offer_recording():
    cfg = build_gradio_config_or_skip()
    labels = [
        str(component.get("props", {}).get("label") or "")
        for component in cfg["components"]
    ]

    assert "Voice attachment" in labels
    assert not any("microphone" in label.lower() or "record voice" in label.lower() for label in labels)


def test_huggingface_smoke_args_match_current_ui_contract(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    smoke = load_hf_smoke_module()
    cfg = build_gradio_config_or_skip()
    run_dep = next(dep for dep in cfg["dependencies"] if dep.get("api_name") == "run_with_status")
    components = {component["id"]: component for component in cfg["components"]}
    input_signature = [
        (components[component_id]["type"], components[component_id].get("props", {}).get("label"))
        for component_id in run_dep["inputs"]
    ]

    args = smoke.run_args(text_input="https://example.com/")
    argv, output = ui._build_argv(*args)

    assert len(args) == 28
    assert len(run_dep["inputs"]) == len(args)
    assert input_signature == [
        ("textbox", "Paste URLs, share text, or local file paths"),
        ("state", None),
        ("textbox", None),
        ("radio", "Action"),
        ("textbox", "Folder"),
        ("textbox", "Vault folder"),
        ("textbox", "Filename"),
        ("state", None),
        ("checkbox", "Polish Markdown"),
        ("checkbox", "Keep raw copy"),
        ("textbox", "Markdown model"),
        ("checkbox", "Polish for Obsidian"),
        ("textbox", "Memory model"),
        ("checkbox", "Polish transcript"),
        ("textbox", "Transcript model"),
        ("checkbox", "Read text from video thumbnail"),
        ("checkbox", "Read text from article images"),
        ("checkbox", "Keep media"),
        ("textbox", "Default / Douyin cookies.txt path"),
        ("dropdown", "Read cookies from browser"),
        ("textbox", "Text-in-image language"),
        ("textbox", "Spoken language hint"),
        ("textbox", "Whisper model"),
        ("textbox", "Ollama host"),
        ("checkbox", "Verbose log"),
        ("state", None),
        ("radio", "Reddit content"),
        ("textbox", "XHS / Rednote cookies.txt path"),
    ]
    assert argv[:4] == [sys.executable, "-m", "omd.cli", "https://example.com/"]
    assert output.parent == ui._public_demo_output_dir()
    assert output.suffix == ".md"


def test_huggingface_smoke_args_keep_public_demo_cookie_rejection(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    smoke = load_hf_smoke_module()

    with pytest.raises(ValueError, match="disables cookie files"):
        ui._build_argv(*smoke.run_args(text_input="https://example.com/", cookies_file="/tmp/cookies.txt"))

    with pytest.raises(ValueError, match="disables cookie files"):
        ui._build_argv(*smoke.run_args(text_input="https://example.com/", xhs_cookies_file="/tmp/xhs.txt"))


def test_huggingface_smoke_api_args_omit_gradio_state_inputs(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    smoke = load_hf_smoke_module()
    cfg = build_gradio_config_or_skip()
    run_dep = next(dep for dep in cfg["dependencies"] if dep.get("api_name") == "run_with_status")
    components = {component["id"]: component for component in cfg["components"]}
    public_inputs = [
        component_id
        for component_id in run_dep["inputs"]
        if components[component_id]["type"] != "state"
    ]

    args = smoke.api_args(text_input="https://example.com/")

    assert len(args) == len(public_inputs) == 25
    assert args[0] == "https://example.com/"
    assert args[2] == "Convert to .md file"
    assert args[-2:] == ["OP only", ""]


def test_public_demo_launch_caps_upload_before_conversion(monkeypatch):
    monkeypatch.setenv("OMD_PUBLIC_DEMO", "1")
    monkeypatch.setenv("OMD_PUBLIC_DEMO_MAX_UPLOAD_MB", "25")

    kwargs = ui.build_launch_kwargs()

    assert kwargs["max_file_size"] == "25mb"


def test_choose_output_dir_uses_native_picker_on_macos(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    calls = {}

    def fake_run(cmd, capture_output, text, check):
        calls["cmd"] = cmd
        return type("Proc", (), {"returncode": 0, "stdout": str(chosen) + "/\n"})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    assert ui._choose_output_dir(str(tmp_path)) == str(chosen)
    assert calls["cmd"][:2] == ["osascript", "-e"]


def test_choose_output_dir_keeps_current_when_native_picker_cancelled(tmp_path, monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        return type("Proc", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    assert ui._choose_output_dir(str(tmp_path)) == str(tmp_path)


def test_choose_output_dir_non_macos_keeps_current(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.sys, "platform", "linux")

    assert ui._choose_output_dir(str(tmp_path)) == str(tmp_path)


def test_open_output_path_opens_file_parent_on_macos(tmp_path, monkeypatch):
    output = tmp_path / "out.md"
    output.write_text("# ok\n", encoding="utf-8")
    calls = {}

    def fake_run(cmd, check):
        calls["cmd"] = cmd
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    status = ui._open_output_path(str(output))

    assert calls["cmd"] == ["open", str(tmp_path)]
    assert 'class="omd-status-ok"' in status


def test_open_output_path_reports_open_failure(tmp_path, monkeypatch):
    output = tmp_path / "out.md"
    output.write_text("# ok\n", encoding="utf-8")

    def fake_run(cmd, check):
        return type("Proc", (), {"returncode": 2})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    status = ui._open_output_path(str(output))

    assert 'class="omd-status-err"' in status
    assert "open exited 2" in status


def test_open_output_path_reports_missing_path(tmp_path):
    status = ui._open_output_path(str(tmp_path / "missing"))

    assert 'class="omd-status-err"' in status
    assert "missing:" in status


def test_choose_cookies_file_uses_native_picker_on_macos(tmp_path, monkeypatch):
    chosen = tmp_path / "douyin_cookies.txt"
    chosen.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    calls = {}

    def fake_run(cmd, capture_output, text, check):
        calls["cmd"] = cmd
        return type("Proc", (), {"returncode": 0, "stdout": str(chosen) + "\n"})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    assert ui._choose_cookies_file("") == str(chosen)
    assert calls["cmd"][:2] == ["osascript", "-e"]
    assert "choose file" in calls["cmd"][2]


def test_choose_cookies_file_keeps_current_when_cancelled(tmp_path, monkeypatch):
    current = tmp_path / "existing.txt"

    def fake_run(cmd, capture_output, text, check):
        return type("Proc", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)

    assert ui._choose_cookies_file(str(current)) == str(current)


def test_choose_cookies_file_non_macos_keeps_current(tmp_path, monkeypatch):
    current = tmp_path / "cookies.txt"
    monkeypatch.setattr(ui.sys, "platform", "linux")

    assert ui._choose_cookies_file(str(current)) == str(current)
