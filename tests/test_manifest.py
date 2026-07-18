from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def test_manifest_path_uses_omd_json_sidecar():
    from omd._manifest import manifest_path_for_output

    assert manifest_path_for_output("/tmp/report.md") == Path("/tmp/report.omd.json")
    assert manifest_path_for_output("/tmp/report") == Path("/tmp/report.omd.json")


def test_write_manifest_for_output_writes_expected_shape(tmp_path):
    from omd._manifest import manifest_path_for_output, write_manifest_for_output

    output = tmp_path / "episode.md"
    output.write_text("# Hello\n", encoding="utf-8")

    manifest = write_manifest_for_output(
        output,
        source="https://example.com/episode",
        backend="podcast",
        transcript_language="en",
        untrusted=True,
        warnings=["feed lookup fallback"],
        metadata={"show_id": 42, "artifacts": [Path("/tmp/audio.mp3")]},
        now=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )

    manifest_path = manifest_path_for_output(output)
    written = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert written == manifest
    assert written["manifest_version"] == 2
    assert written["source_id"].startswith("src_")
    assert len(written["source_hash"]) == 64
    assert written["capture_id"].startswith("cap_")
    assert written["source"] == "https://example.com/episode"
    assert written["source_url"] == "https://example.com/episode"
    assert written["output"] == str(output)
    assert written["backend"] == "podcast"
    assert written["created_at"] == "2026-05-28T12:00:00Z"
    assert written["updated_at"] == "2026-05-28T12:00:00Z"
    assert written["content_checksum"] == "90f8ec5669cd34183b9b0fdf8b94f5efb4c3672876330f4aa76088c2b4ad17be"
    assert written["checksum"] == "90f8ec5669cd34183b9b0fdf8b94f5efb4c3672876330f4aa76088c2b4ad17be"
    assert written["transcript_language"] == "en"
    assert written["untrusted"] is True
    assert written["warnings"] == ["feed lookup fallback"]
    assert written["elements"] == [
        {
            "id": "el_0001",
            "type": "title",
            "markdown_start_line": 1,
            "markdown_end_line": 1,
            "page_number": None,
            "timestamp_start": None,
            "timestamp_end": None,
            "source_ref": "https://example.com/episode",
        }
    ]
    assert written["metadata"] == {"show_id": 42, "artifacts": ["/tmp/audio.mp3"]}


def test_write_manifest_for_output_preserves_created_at(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "note.md"
    output.write_text("first\n", encoding="utf-8")

    first = write_manifest_for_output(
        output,
        source="https://xhslink.com/a/demo",
        backend="xhs",
        now=datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc),
    )

    output.write_text("second\n", encoding="utf-8")
    second = write_manifest_for_output(
        output,
        source="https://xhslink.com/a/demo",
        backend="xhs",
        now=datetime(2026, 5, 28, 11, 30, tzinfo=timezone.utc),
    )

    assert first["created_at"] == "2026-05-28T10:00:00Z"
    assert second["created_at"] == "2026-05-28T10:00:00Z"
    assert second["updated_at"] == "2026-05-28T11:30:00Z"
    assert first["checksum"] != second["checksum"]
    assert first["capture_id"] == second["capture_id"]


def test_manifest_canonicalizes_share_text_url_identity(tmp_path):
    from omd._manifest import source_hash_for, write_manifest_for_output

    direct_url = "https://v.douyin.com/yGWf39cCbCE/"
    share_text = (
        "8.92 复制打开抖音，看看【艾丽的无废话财经的作品】6.27 终于 贝森特道出了核心目的 "
        f"# 沃什 #... {direct_url} :4pm z"
    )
    first_output = tmp_path / "share.md"
    second_output = tmp_path / "url.md"
    first_output.write_text("# Share\n", encoding="utf-8")
    second_output.write_text("# Url\n", encoding="utf-8")

    share_manifest = write_manifest_for_output(first_output, source=share_text, backend="reel")
    url_manifest = write_manifest_for_output(second_output, source=direct_url, backend="reel")

    assert source_hash_for(share_text) == source_hash_for(direct_url)
    assert share_manifest["source"] == direct_url
    assert share_manifest["source_url"] == direct_url
    assert "local_source_path" not in share_manifest
    assert share_manifest["raw_source"] == share_text
    assert share_manifest["source_id"] == url_manifest["source_id"]
    assert share_manifest["source_hash"] == url_manifest["source_hash"]
    assert share_manifest["elements"][0]["source_ref"] == direct_url


def test_write_manifest_for_output_preserves_old_sidecar_capture_id(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "note.md"
    output.write_text("# Updated\n", encoding="utf-8")
    output.with_suffix(".omd.json").write_text(
        '{"created_at":"2024-01-02T03:04:05Z","capture_id":"cap_existing"}\n',
        encoding="utf-8",
    )

    manifest = write_manifest_for_output(
        output,
        source="https://example.com/source",
        backend="markitdown",
        now=datetime(2026, 5, 28, 11, 30, tzinfo=timezone.utc),
    )

    assert manifest["created_at"] == "2024-01-02T03:04:05Z"
    assert manifest["capture_id"] == "cap_existing"
    assert manifest["manifest_version"] == 2
    assert manifest["source_hash"]


def test_manifest_v2_marks_local_source_path_and_paragraph_lines(tmp_path):
    from omd._manifest import write_manifest_for_output

    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    output = tmp_path / "note.md"
    output.write_text("# Title\n\nFirst paragraph\nstill paragraph\n\n- item\n- other\n", encoding="utf-8")

    manifest = write_manifest_for_output(output, source=str(source), backend="markitdown")

    assert manifest["local_source_path"] == str(source.resolve(strict=False))
    assert [item["type"] for item in manifest["elements"]] == ["title", "paragraph", "list"]
    assert manifest["elements"][1]["markdown_start_line"] == 3
    assert manifest["elements"][1]["markdown_end_line"] == 4
    assert manifest["elements"][2]["markdown_start_line"] == 6
    assert manifest["elements"][2]["markdown_end_line"] == 7


def test_manifest_element_classifier_avoids_plain_text_false_positives(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "note.md"
    output.write_text(
        "2026 roadmap\n\nA | B\n\n1. real item\n2) another item\n\n| A | B |\n| - | - |\n",
        encoding="utf-8",
    )

    manifest = write_manifest_for_output(output, source="https://example.com/source", backend="markitdown")

    assert [item["type"] for item in manifest["elements"]] == [
        "paragraph",
        "paragraph",
        "list",
        "table",
    ]
    assert manifest["elements"][0]["markdown_start_line"] == 1
    assert manifest["elements"][1]["markdown_start_line"] == 3
    assert manifest["elements"][2]["markdown_start_line"] == 5
    assert manifest["elements"][2]["markdown_end_line"] == 6
    assert manifest["elements"][3]["markdown_start_line"] == 8
    assert manifest["elements"][3]["markdown_end_line"] == 9
