from __future__ import annotations

import sys

import pytest


def test_xhs_markdown_includes_detected_transcript_language():
    from omd.xhs import compose_markdown

    note = {
        "title": "Demo",
        "desc": "",
        "id": "note-1",
        "type": "video",
        "uploader": "Author",
        "uploader_id": "author-1",
        "upload_time": "",
        "like_count": 0,
        "collect_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "tags": [],
        "comments": [],
    }

    markdown = compose_markdown(
        "https://www.xiaohongshu.com/explore/note-1",
        note,
        [],
        [],
        "你好",
        "",
        [],
        include_comments=False,
        transcript_language="zh",
    )

    assert "- **Language**: zh" in markdown


def test_xhs_markdown_does_not_persist_access_query_parameters():
    from omd.xhs import compose_markdown

    note = {
        "title": "Demo",
        "desc": "",
        "id": "note-1",
        "type": "normal",
        "uploader": "Author",
        "uploader_id": "author-1",
        "upload_time": "",
        "like_count": 0,
        "collect_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "tags": [],
        "comments": [],
    }

    markdown = compose_markdown(
        "https://www.rednote.com/discovery/item/note-1?xsec_token=private&source=web#detail",
        note,
        [],
        [],
        "",
        "",
        [],
        include_comments=False,
    )

    assert "- **URL**: https://www.rednote.com/discovery/item/note-1" in markdown
    assert "xsec_token" not in markdown
    assert "private" not in markdown


def test_podcast_markdown_includes_detected_transcript_language():
    from omd.podcast import compose_markdown

    markdown = compose_markdown(
        "https://podcasts.apple.com/podcast/id123?i=456",
        "Demo Show",
        {"title": "Episode", "description": ""},
        "Hello",
        "",
        [],
        transcript_language="en",
    )

    assert "- **Language**: en" in markdown


def test_xhs_explicit_inherit_mode_suppresses_environment_hint(
    monkeypatch,
    tmp_path,
    capsys,
):
    from omd import xhs
    from omd import _audio, _transcript

    seen: dict[str, object] = {}
    note = {
        "title": "Demo",
        "desc": "",
        "id": "abcdef",
        "type": "video",
        "uploader": "Author",
        "uploader_id": "author-1",
        "upload_time": "",
        "like_count": 0,
        "collect_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "tags": [],
        "comments": [],
        "images": [],
        "video_url": "https://example.com/video.mp4",
    }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            "https://www.xiaohongshu.com/explore/abcdef",
            "--spoken-language-mode",
            "inherit-adapter-default",
        ],
    )
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("OMD_SPOKEN_LANGUAGE_MODE", "explicit")
    monkeypatch.setenv("OMD_SPOKEN_LANGUAGE_HINT", "de")
    monkeypatch.setattr(xhs, "require_cookie_header", lambda *_args, **_kwargs: "session=demo")
    monkeypatch.setattr(
        xhs,
        "_http_get",
        lambda url, *_args, **_kwargs: (200, {}, b"", url),
    )
    monkeypatch.setattr(xhs, "parse_initial_state", lambda _html: {})
    monkeypatch.setattr(xhs, "extract_note", lambda _state, _note_id: note)
    monkeypatch.setattr(xhs, "download_to", lambda _url, _path: None)
    monkeypatch.setattr(xhs, "video_to_audio", lambda path: path.with_suffix(".mp3"))

    def fake_transcribe(_audio, _workdir, _model, language, _backend):
        seen["language"] = language
        return {"text": "你好", "segments": [], "language": "zh"}

    monkeypatch.setattr(xhs, "transcribe", fake_transcribe)
    monkeypatch.setattr(_audio, "duration_seconds", lambda _path: 1.0)
    monkeypatch.setattr(_transcript, "apply_transcript_quality", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(_transcript, "report_transcript_quality", lambda *_args, **_kwargs: None)

    assert xhs.main() == 0
    assert seen["language"] == "zh"
    assert "- **Language**: zh" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mode", "extra_args", "expected_hint"),
    [
        ("auto-detect", [], None),
        ("explicit", ["--whisper-lang", "fr"], "fr"),
    ],
)
def test_podcast_spoken_mode_controls_whisper_hint(
    mode,
    extra_args,
    expected_hint,
    monkeypatch,
    tmp_path,
    capsys,
):
    from omd import podcast
    from omd import _transcript

    seen: dict[str, object] = {}
    item = {
        "title": "Episode",
        "description": "",
        "audio_url": "https://example.com/audio.mp3",
        "audio_type": "audio/mpeg",
        "duration": "1",
    }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-podcast",
            "https://podcasts.apple.com/demo/id123?i=456",
            "--spoken-language-mode",
            mode,
            *extra_args,
        ],
    )
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("OMD_SPOKEN_LANGUAGE_MODE", "explicit")
    monkeypatch.setenv("OMD_SPOKEN_LANGUAGE_HINT", "de")
    monkeypatch.setattr(podcast, "lookup_feed_url", lambda _show_id: "https://example.com/feed.xml")
    monkeypatch.setattr(
        podcast,
        "http_get",
        lambda _url: b"<rss><channel><title>Demo Show</title></channel></rss>",
    )
    monkeypatch.setattr(podcast, "parse_feed", lambda _xml: [item])
    monkeypatch.setattr(podcast, "find_episode", lambda *_args: item)
    monkeypatch.setattr(podcast, "download_to", lambda _url, _path: None)

    def fake_transcribe(_audio, _workdir, _model, language, _backend):
        seen["language"] = language
        return {"text": "Bonjour", "segments": [], "language": "fr"}

    monkeypatch.setattr(podcast, "transcribe", fake_transcribe)
    monkeypatch.setattr(_transcript, "apply_transcript_quality", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(_transcript, "report_transcript_quality", lambda *_args, **_kwargs: None)

    assert podcast.main() == 0
    assert seen["language"] == expected_hint
    assert "- **Language**: fr" in capsys.readouterr().out


@pytest.mark.parametrize("adapter", ["xhs", "podcast"])
def test_direct_adapter_explicit_mode_requires_whisper_language(
    adapter,
    monkeypatch,
    tmp_path,
):
    module = __import__(f"omd.{adapter}", fromlist=["main"])
    url = (
        "https://www.xiaohongshu.com/explore/abcdef"
        if adapter == "xhs"
        else "https://podcasts.apple.com/demo/id123?i=456"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [f"omd-{adapter}", url, "--spoken-language-mode", "explicit"],
    )
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.delenv("OMD_SPOKEN_LANGUAGE_MODE", raising=False)
    monkeypatch.delenv("OMD_SPOKEN_LANGUAGE_HINT", raising=False)

    with pytest.raises(SystemExit, match="spoken_language_hint is required"):
        module.main()
