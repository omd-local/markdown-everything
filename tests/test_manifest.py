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


def test_manifest_preserves_recognition_when_refreshed_after_postprocessing(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "mixed.md"
    output.write_text("# Mixed\n", encoding="utf-8")
    recognition = {
        "ocr": {
            "requested": "chi_sim+eng",
            "effective": "chi_sim+eng",
            "source": "explicit",
        },
        "asr": {"requested": "auto", "detected": "zh"},
        "generation": {"language_policy": "same-as-source"},
        "content_languages_detected": ["zh", "en"],
        "detected_script": "Hans",
        "confidence": 0.98,
    }

    first = write_manifest_for_output(
        output,
        source="https://example.com/mixed",
        backend="reel",
        transcript_language="zh",
        recognition=recognition,
    )
    output.write_text("# Mixed\n\npolished\n", encoding="utf-8")
    refreshed = write_manifest_for_output(
        output,
        source="https://example.com/mixed",
        backend="reel",
    )

    assert first["recognition"] == recognition
    assert refreshed["recognition"] == recognition


def test_manifest_does_not_reuse_identity_or_recognition_for_a_different_source(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "capture.md"
    output.write_text("# First source\n", encoding="utf-8")
    first = write_manifest_for_output(
        output,
        source="https://example.com/first",
        backend="podcast",
        transcript_language="en",
        recognition={"asr": {"requested": "auto", "detected": "en"}},
        now=datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc),
    )

    output.write_text("# Replacement source\n", encoding="utf-8")
    replacement = write_manifest_for_output(
        output,
        source="https://example.com/replacement",
        backend="podcast",
        now=datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc),
    )

    assert replacement["created_at"] == "2026-05-28T11:00:00Z"
    assert replacement["capture_id"] != first["capture_id"]
    assert replacement["transcript_language"] is None
    assert replacement["recognition"]["asr"] == {
        "requested": None,
        "detected": None,
    }


def test_manifest_distinguishes_same_relative_filename_from_different_working_dirs(
    tmp_path, monkeypatch
):
    from omd._manifest import write_manifest_for_output

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "report.pdf").write_bytes(b"first")
    (second_dir / "report.pdf").write_bytes(b"second")
    output = tmp_path / "capture.md"
    output.write_text("# First\n", encoding="utf-8")

    monkeypatch.chdir(first_dir)
    first = write_manifest_for_output(
        output,
        source="report.pdf",
        backend="markitdown",
        recognition={"asr": {"requested": "auto", "detected": "en"}},
        now=datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc),
    )
    output.write_text("# Second\n", encoding="utf-8")
    monkeypatch.chdir(second_dir)
    second = write_manifest_for_output(
        output,
        source="report.pdf",
        backend="markitdown",
        now=datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc),
    )

    assert first["source"] == str((first_dir / "report.pdf").resolve())
    assert second["source"] == str((second_dir / "report.pdf").resolve())
    assert first["raw_source"] == "report.pdf"
    assert second["raw_source"] == "report.pdf"
    assert second["source_hash"] != first["source_hash"]
    assert second["capture_id"] != first["capture_id"]
    assert second["created_at"] == "2026-05-28T11:00:00Z"
    assert second["recognition"]["asr"] == {
        "requested": None,
        "detected": None,
    }


def test_manifest_reads_old_sidecar_and_adds_default_recognition_shape(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "legacy.md"
    output.write_text("# Legacy\n", encoding="utf-8")
    output.with_suffix(".omd.json").write_text(
        '{"manifest_version":2,"source":"https://example.com/legacy",'
        '"created_at":"2024-01-02T03:04:05Z"}\n',
        encoding="utf-8",
    )

    manifest = write_manifest_for_output(
        output,
        source="https://example.com/legacy",
        backend="markitdown",
    )

    assert manifest["created_at"] == "2024-01-02T03:04:05Z"
    assert manifest["recognition"] == {
        "ocr": {"requested": None, "effective": None, "source": None},
        "asr": {"requested": None, "detected": None},
        "generation": {"language_policy": "same-as-source"},
        "content_languages_detected": [],
        "detected_script": None,
        "confidence": None,
    }


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


def test_manifest_strips_rednote_access_query_from_source_and_raw_share_text(tmp_path):
    from omd._manifest import canonical_source_for, write_manifest_for_output

    private_url = (
        "https://www.rednote.com/discovery/item/note123"
        "?xsec_token=private-value&xsec_source=pc_search"
    )
    safe_url = "https://www.rednote.com/discovery/item/note123"
    share_text = f"Open this Rednote post: {private_url} 复制后查看"
    output = tmp_path / "rednote.md"
    output.write_text("# Rednote\n", encoding="utf-8")

    manifest = write_manifest_for_output(output, source=share_text, backend="xhs")

    assert canonical_source_for(private_url) == safe_url
    assert (
        canonical_source_for("https://xhslink.com/a/demo?xsec_token=shortlink-secret")
        == "https://xhslink.com/a/demo"
    )
    assert manifest["source"] == safe_url
    assert manifest["source_url"] == safe_url
    assert manifest["raw_source"] == f"Open this Rednote post: {safe_url} 复制后查看"
    assert "xsec_token" not in json.dumps(manifest)
    assert "private-value" not in json.dumps(manifest)


def test_manifest_refresh_sanitizes_legacy_rednote_source_without_changing_identity(tmp_path):
    from omd._manifest import write_manifest_for_output

    private_url = "https://www.rednote.com/discovery/item/note123?xsec_token=legacy-secret"
    safe_url = "https://www.rednote.com/discovery/item/note123"
    output = tmp_path / "legacy.md"
    output.write_text("# Updated\n", encoding="utf-8")
    output.with_suffix(".omd.json").write_text(
        json.dumps(
            {
                "source": private_url,
                "raw_source": f"Shared post {private_url}",
                "created_at": "2024-01-02T03:04:05Z",
                "capture_id": "cap_existing",
                "output": str(output),
            }
        ),
        encoding="utf-8",
    )

    manifest = write_manifest_for_output(output, source=safe_url, backend="xhs")
    serialized = json.dumps(manifest)

    assert manifest["source"] == safe_url
    assert manifest["raw_source"] == f"Shared post {safe_url}"
    assert manifest["created_at"] == "2024-01-02T03:04:05Z"
    assert manifest["capture_id"] == "cap_existing"
    assert "xsec_token" not in serialized
    assert "legacy-secret" not in serialized


def test_manifest_preserves_functional_queries_for_other_sources(tmp_path):
    from omd._manifest import write_manifest_for_output

    source = "https://podcasts.apple.com/podcast/id123?i=456"
    output = tmp_path / "podcast.md"
    output.write_text("# Podcast\n", encoding="utf-8")

    manifest = write_manifest_for_output(output, source=source, backend="podcast")

    assert manifest["source"] == source
    assert manifest["source_url"] == source


def test_write_manifest_for_output_preserves_old_sidecar_capture_id(tmp_path):
    from omd._manifest import write_manifest_for_output

    output = tmp_path / "note.md"
    output.write_text("# Updated\n", encoding="utf-8")
    output.with_suffix(".omd.json").write_text(
        '{"source":"https://example.com/source",'
        '"created_at":"2024-01-02T03:04:05Z","capture_id":"cap_existing"}\n',
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
