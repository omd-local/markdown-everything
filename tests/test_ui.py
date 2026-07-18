from __future__ import annotations

import importlib.util
import os
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


def test_source_file_add_button_has_a_readable_accessible_name():
    kwargs = ui.build_launch_kwargs()

    assert ui.UI_TRANSLATIONS["common.upload"] == "Add files"
    assert kwargs["i18n"].translations["en"]["common.upload"] == "Add files"


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

    for label in ("All", "Source", "Output", "Advanced settings"):
        assert label in labels


def test_top_menu_view_updates_collapse_instead_of_hiding_panels():
    def open_flags(updates):
        return [update["open"] for update in updates]

    for updates in (
        ui._menu_view_updates("all"),
        ui._menu_view_updates("source"),
        ui._menu_view_updates("output"),
        ui._menu_view_updates("advanced"),
    ):
        assert [update["visible"] for update in updates] == [True, True, True, True, True, True]

    assert open_flags(ui._menu_view_updates("all")) == [True, True, True, True, False, False]
    assert open_flags(ui._menu_view_updates("source")) == [True, False, False, False, False, False]
    assert open_flags(ui._menu_view_updates("output")) == [False, True, True, True, False, False]
    assert open_flags(ui._menu_view_updates("advanced")) == [False, False, False, False, True, True]


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


def test_json_events_checkbox_is_not_exposed_in_ui_config():
    cfg = build_gradio_config_or_skip()
    labels = {
        component.get("props", {}).get("label"): component["id"]
        for component in cfg["components"]
        if component.get("type") == "checkbox"
    }

    assert "Verbose log" in labels
    assert "JSON events" not in labels


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


@pytest.mark.parametrize(
    "host",
    [
        "file:///tmp/ollama",
        "http://169.254.169.254:11434",
        "ollama.example.com:11434",
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

    argv, output = build_argv(tmp_path, text_input=share_text)

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

    argv, output = build_argv(
        tmp_path,
        text_input="first https://v.douyin.com/a/ middle second https://v.douyin.com/b/ tail",
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
