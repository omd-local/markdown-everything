from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


SAMPLE_STATUS = {
    "id": "114000000000000001",
    "uri": "https://mastodon.social/users/alice/statuses/114000000000000001",
    "url": "https://mastodon.social/@alice/114000000000000001",
    "created_at": "2026-06-01T10:00:00.000Z",
    "content": "<p>Hello <a href=\"https://example.com\">world</a><br />Second line</p>",
    "spoiler_text": "Research note",
    "visibility": "public",
    "language": "en",
    "favourites_count": 12,
    "reblogs_count": 3,
    "replies_count": 2,
    "in_reply_to_id": None,
    "application": {"name": "Web"},
    "account": {
        "display_name": "Alice",
        "username": "alice",
        "acct": "alice",
        "url": "https://mastodon.social/@alice",
    },
    "media_attachments": [
        {
            "type": "image",
            "url": "https://files.mastodon.social/media/demo.jpg",
            "preview_url": "https://files.mastodon.social/media/demo-small.jpg",
            "description": "Architecture diagram",
        }
    ],
    "card": {
        "title": "Example story",
        "url": "https://example.com/story",
        "description": "A linked story",
        "provider_name": "Example",
        "image": "https://example.com/story.jpg",
    },
    "mentions": [{"acct": "bob@example.social", "url": "https://example.social/@bob"}],
    "tags": [{"name": "AI", "url": "https://mastodon.social/tags/AI"}],
}


def test_parse_mastodon_url_supports_common_paths():
    from omd import mastodon

    assert mastodon.parse_mastodon_url("https://mastodon.social/@alice/114000000000000001") == (
        "mastodon.social",
        "114000000000000001",
    )
    assert mastodon.parse_mastodon_url(
        "https://mastodon.social/users/alice/statuses/114000000000000001"
    ) == (
        "mastodon.social",
        "114000000000000001",
    )
    assert mastodon.parse_mastodon_url("https://mastodon.social/web/statuses/114000000000000001") == (
        "mastodon.social",
        "114000000000000001",
    )


def test_html_to_text_keeps_readable_line_breaks():
    from omd import mastodon

    assert mastodon.html_to_text("<p>Hello <b>there</b><br />Next</p><p>Again</p>") == (
        "Hello there\nNext\n\nAgain"
    )
    assert mastodon.html_to_text("a&amp;b &lt;c&gt;") == "a&b <c>"


def test_parse_status_and_markdown():
    from omd import mastodon

    post = mastodon.parse_status(
        SAMPLE_STATUS,
        "https://mastodon.social/@alice/114000000000000001",
        "https://mastodon.social/api/v1/statuses/114000000000000001",
    )
    md = mastodon.markdown_for_post(post)

    assert md.startswith("# Mastodon post by Alice (@alice)\n")
    assert "- API: https://mastodon.social/api/v1/statuses/114000000000000001" in md
    assert "- Visibility: public" in md
    assert "- Favorites: 12" in md
    assert "## Content warning\n\nResearch note" in md
    assert "Hello world\nSecond line" in md
    assert "- image: [Architecture diagram](https://files.mastodon.social/media/demo.jpg)" in md
    assert "- [Example story](https://example.com/story)" in md
    assert "- [@bob@example.social](https://example.social/@bob)" in md
    assert "- [#AI](https://mastodon.social/tags/AI)" in md


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import mastodon

    monkeypatch.setattr(
        mastodon,
        "fetch_status",
        lambda _url: (SAMPLE_STATUS, "https://mastodon.social/api/v1/statuses/114000000000000001"),
    )
    out = tmp_path / "mastodon.md"

    rc = mastodon.convert_url("https://mastodon.social/@alice/114000000000000001", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Mastodon post by Alice")


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import mastodon

    class FakeResponse:
        headers = SimpleNamespace(
            get=lambda _name: str(2 * 1024 * 1024),
            get_content_charset=lambda: "utf-8",
        )

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setenv("OMD_MAX_DOWNLOAD_MB", "1")
    monkeypatch.setattr(mastodon.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        mastodon.fetch_status("https://mastodon.social/@alice/114000000000000001")


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import mastodon

    class FakeResponse:
        headers = SimpleNamespace(
            get=lambda _name: None,
            get_content_charset=lambda: "utf-8",
        )
        _done = False

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _n):
            if self._done:
                return b""
            self._done = True
            return json.dumps(SAMPLE_STATUS).encode()

        def geturl(self):
            return "https://mastodon.social/api/v1/statuses/114000000000000001"

    monkeypatch.setattr(mastodon.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    payload, final_url = mastodon.fetch_status("https://mastodon.social/@alice/114000000000000001")
    assert payload["id"] == "114000000000000001"
    assert final_url == "https://mastodon.social/api/v1/statuses/114000000000000001"
