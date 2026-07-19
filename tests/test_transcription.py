"""Regression tests for transcription isolation and quality reporting."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


def _fake_mlx_runner(seen_output_dirs: list[Path], *, text: str = "current transcript"):
    def run(cmd: list[str], _audio: Path, _label: str):
        input_path = Path(cmd[-1])
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        seen_output_dirs.append(output_dir)
        (output_dir / f"{input_path.stem}.json").write_text(
            json.dumps({"text": text, "segments": [], "language": "en"}),
            encoding="utf-8",
        )

    return run


def test_mlx_transcription_uses_current_audio_when_keep_dir_has_stale_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import _audio, reel

    keep_dir = tmp_path / "keep"
    whisper_root = keep_dir / "_whisper"
    whisper_root.mkdir(parents=True)
    stale_audio = tmp_path / "douyin.mp3"
    current_audio = tmp_path / "podcast.mp3"
    stale_audio.write_bytes(b"old")
    current_audio.write_bytes(b"new")
    (whisper_root / "audio.mp3").symlink_to(stale_audio)
    seen_inputs: list[Path] = []

    monkeypatch.setattr(reel, "require", lambda _name: "/fake/mlx_whisper")

    def run(cmd: list[str], _audio: Path, _label: str):
        input_path = Path(cmd[-1])
        seen_inputs.append(input_path.resolve())
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        (output_dir / f"{input_path.stem}.json").write_text(
            json.dumps({"text": "BBC transcript", "segments": [], "language": "en"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(_audio, "run_with_estimated_progress", run)

    result = reel._transcribe_mlx(current_audio, keep_dir, "model", "en")

    assert seen_inputs == [current_audio.resolve()]
    assert result["text"] == "BBC transcript"


def test_mlx_transcription_uses_a_distinct_output_directory_per_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import _audio, reel

    keep_dir = tmp_path / "keep"
    first_audio = tmp_path / "first.mp3"
    second_audio = tmp_path / "second.mp3"
    first_audio.write_bytes(b"first")
    second_audio.write_bytes(b"second")
    output_dirs: list[Path] = []
    monkeypatch.setattr(reel, "require", lambda _name: "/fake/mlx_whisper")
    monkeypatch.setattr(_audio, "run_with_estimated_progress", _fake_mlx_runner(output_dirs))

    reel._transcribe_mlx(first_audio, keep_dir, "model", "en")
    reel._transcribe_mlx(second_audio, keep_dir, "model", "en")

    assert len(set(output_dirs)) == 2


def test_mlx_transcription_rejects_stale_json_when_current_run_writes_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import _audio, _events, reel

    keep_dir = tmp_path / "keep"
    whisper_root = keep_dir / "_whisper"
    whisper_root.mkdir(parents=True)
    (whisper_root / "stale.json").write_text(
        json.dumps({"text": "stale Douyin transcript"}),
        encoding="utf-8",
    )
    current_audio = tmp_path / "podcast.mp3"
    current_audio.write_bytes(b"new")
    monkeypatch.setattr(reel, "require", lambda _name: "/fake/mlx_whisper")
    monkeypatch.setattr(_audio, "run_with_estimated_progress", lambda *_args: None)
    _events.configure(False)

    with pytest.raises(SystemExit, match="produced no JSON"):
        reel._transcribe_mlx(current_audio, keep_dir, "model", "en")


def test_podcast_audio_path_is_source_specific(tmp_path: Path):
    from omd import podcast

    first = podcast._episode_audio_path(
        tmp_path,
        "https://cdn.example.com/first.mp3",
        "audio/mpeg",
    )
    second = podcast._episode_audio_path(
        tmp_path,
        "https://cdn.example.com/second.mp3",
        "audio/mpeg",
    )

    assert first != second
    assert first.suffix == second.suffix == ".mp3"


def test_transcript_quality_warns_when_a_phrase_repeats_unusually():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": ("the US will be able to increase the US again " * 40).strip(),
        "segments": [],
    }

    warnings = assess_transcript_quality(transcript, expected_language="en")

    assert any("repeated phrases" in warning for warning in warnings)


def test_transcript_quality_warns_when_english_hint_contains_chinese():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": "这是一个关于美元政策和市场变化的中文节目内容。" * 20,
        "segments": [],
    }

    warnings = assess_transcript_quality(transcript, expected_language="en")

    assert any("English language hint" in warning for warning in warnings)


def test_transcript_quality_warns_when_reported_language_differs_from_hint():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": "Este episodio explica las decisiones de transporte de una ciudad.",
        "language": "es",
        "segments": [],
    }

    warnings = assess_transcript_quality(transcript, expected_language="en")

    assert any("reported language" in warning for warning in warnings)


def test_transcript_quality_warns_when_whisper_reports_unstable_compression():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": "A short transcript with otherwise ordinary wording.",
        "segments": [{"start": 0, "end": 5, "text": "ordinary", "compression_ratio": 6.3}],
    }

    warnings = assess_transcript_quality(transcript, expected_language="en")

    assert any("unstable repeated-text" in warning for warning in warnings)


def test_transcript_quality_warns_when_transcript_is_much_shorter_than_source():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": "A short but valid opening sentence.",
        "segments": [{"start": 0, "end": 20, "text": "opening"}],
    }

    warnings = assess_transcript_quality(
        transcript,
        expected_language="en",
        expected_duration=120,
    )

    assert any("source duration" in warning for warning in warnings)


def test_transcript_quality_accepts_normal_english_text():
    from omd._transcript import assess_transcript_quality

    transcript = {
        "text": (
            "Cycling can reduce congestion while improving public health. "
            "The programme compares protected lanes, safer junctions, secure parking, "
            "and practical policy choices for commuters in several different cities."
        ),
        "segments": [{"start": 0, "end": 115, "text": "Cycling can reduce congestion."}],
    }

    warnings = assess_transcript_quality(
        transcript,
        expected_language="en",
        expected_duration=120,
    )

    assert warnings == []


def test_faster_whisper_preserves_segment_compression_ratio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from omd import reel

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            segment = types.SimpleNamespace(
                start=0,
                end=5,
                text="repeated text",
                compression_ratio=6.3,
            )
            return [segment], types.SimpleNamespace(language="en")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")

    result = reel._transcribe_faster_whisper(audio, "small", "en")

    assert result["segments"][0]["compression_ratio"] == 6.3


def test_route_audio_applies_source_duration_quality_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import _audio, cli, reel

    audio = tmp_path / "episode.mp3"
    output = tmp_path / "episode.md"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(_audio, "duration_seconds", lambda _path: 120.0)
    monkeypatch.setattr(
        reel,
        "transcribe",
        lambda *_args, **_kwargs: {
            "text": "Only the opening was transcribed.",
            "language": "en",
            "segments": [{"start": 0, "end": 10, "text": "opening"}],
            "quality_warnings": [],
        },
    )

    result = cli.route_audio(audio, output, extra=["--whisper-lang", "en"])

    assert result == 0
    assert "source duration" in output.read_text(encoding="utf-8")


def test_xhs_video_applies_source_duration_quality_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import _audio, xhs

    url = "https://www.xiaohongshu.com/explore/note123"
    output = tmp_path / "note.md"
    keep_dir = tmp_path / "keep"
    note = {
        "title": "Video note",
        "desc": "Description",
        "id": "note123",
        "type": "video",
        "uploader": "Creator",
        "uploader_id": "creator1",
        "upload_time": "today",
        "like_count": 0,
        "collect_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "tags": [],
        "images": [],
        "comments": [],
        "video_url": "https://cdn.example.com/video.mp4",
    }
    monkeypatch.setattr(sys, "argv", ["omd.xhs", url, "-o", str(output), "--keep", str(keep_dir)])
    monkeypatch.setattr(xhs, "_http_get", lambda *_args, **_kwargs: (200, {}, b"html", url))
    monkeypatch.setattr(xhs, "parse_initial_state", lambda _html: {})
    monkeypatch.setattr(xhs, "extract_note", lambda *_args: note)

    def fake_download(_url: str, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")

    def fake_audio(_video: Path) -> Path:
        path = keep_dir / "audio.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(xhs, "download_to", fake_download)
    monkeypatch.setattr(xhs, "video_to_audio", fake_audio)
    monkeypatch.setattr(_audio, "duration_seconds", lambda _path: 120.0)
    monkeypatch.setattr(
        xhs,
        "transcribe",
        lambda *_args, **_kwargs: {
            "text": "Only the opening was transcribed.",
            "language": "zh",
            "segments": [{"start": 0, "end": 10, "text": "opening"}],
            "quality_warnings": [],
        },
    )

    result = xhs.main()

    assert result == 0
    assert "source duration" in output.read_text(encoding="utf-8")


def test_podcast_markdown_marks_suspect_transcript_for_review():
    from omd import podcast

    warning = "The transcript contains unusually repeated phrases and may be inaccurate."
    markdown = podcast.compose_markdown(
        "https://podcasts.apple.com/nz/podcast/demo/id1?i=2",
        "Demo Show",
        {
            "title": "Episode",
            "author": "Host",
            "pub_date": "Today",
            "duration": "120",
            "audio_url": "https://cdn.example.com/episode.mp3",
            "description": "Description",
        },
        "raw transcript",
        "",
        [],
        "local Whisper",
        transcript_warnings=[warning],
    )

    assert "Transcript status**: Needs review" in markdown
    assert f"Transcript warning**: {warning}" in markdown
    assert "raw transcript" in markdown


def test_manifest_records_transcript_warning_from_markdown(tmp_path: Path):
    from omd import cli
    from omd._manifest import manifest_path_for_output

    warning = "The transcript contains unusually repeated phrases and may be inaccurate."
    output = tmp_path / "episode.md"
    output.write_text(
        f"# Episode\n\n- **Transcript warning**: {warning}\n\n## Transcript\n\nraw\n",
        encoding="utf-8",
    )

    cli._write_manifest_if_possible(
        "https://podcasts.apple.com/nz/podcast/demo/id1?i=2",
        output,
        {"probable_backend": "podcast", "warnings": []},
    )

    manifest = json.loads(manifest_path_for_output(output).read_text(encoding="utf-8"))
    assert warning in manifest["warnings"]


def test_generated_markdown_polish_skips_transcript_that_needs_review(tmp_path: Path):
    from omd import cli

    output = tmp_path / "episode.md"
    output.write_text(
        "# Episode\n\n"
        "- **Transcript warning**: The transcript contains unusually repeated phrases.\n",
        encoding="utf-8",
    )

    reason = cli._generated_markdown_polish_skip_reason(output, force=False)

    assert reason is not None
    assert "transcript quality" in reason


def test_capture_skips_memory_cards_when_transcript_needs_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import capture, cli, memory_cards

    warning = "The transcript contains unusually repeated phrases and may be inaccurate."

    def fake_route_one(_source, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            f"# Episode\n\n## Metadata\n\n- **Transcript warning**: {warning}\n\n"
            "## Transcript\n\nraw transcript\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(
        memory_cards,
        "generate_memory_cards",
        lambda *_args, **_kwargs: pytest.fail("memory cards must not run for suspect transcripts"),
    )

    result = capture.capture_one(
        "https://podcasts.apple.com/nz/podcast/demo/id1?i=2",
        tmp_path / "vault",
        memory_cards=True,
    )

    manifest = json.loads(result.output_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert manifest["metadata"]["capture"]["memory_cards"] is False
    assert "transcript quality" in manifest["metadata"]["capture"]["memory_error"]


def test_capture_manifest_preserves_transcript_quality_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from omd import capture, cli

    warning = "The transcript contains unusually repeated phrases and may be inaccurate."

    def fake_route_one(_source, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            f"# Episode\n\n## Metadata\n\n- **Transcript warning**: {warning}\n\n"
            "## Transcript\n\nraw transcript\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)

    result = capture.capture_one(
        "https://podcasts.apple.com/nz/podcast/demo/id1?i=2",
        tmp_path / "vault",
    )

    manifest = json.loads(result.output_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert warning in manifest["warnings"]
