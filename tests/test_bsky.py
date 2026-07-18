from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


SAMPLE_THREAD = {
    "thread": {
        "post": {
            "uri": "at://did:plc:alice/app.bsky.feed.post/abc123",
            "cid": "bafyabc",
            "author": {
                "handle": "alice.bsky.social",
                "displayName": "Alice",
            },
            "record": {
                "$type": "app.bsky.feed.post",
                "text": "Hello Bluesky",
                "createdAt": "2026-06-01T10:00:00.000Z",
                "langs": ["en"],
                "facets": [
                    {
                        "features": [
                            {
                                "$type": "app.bsky.richtext.facet#link",
                                "uri": "https://example.com/story",
                            },
                            {
                                "$type": "app.bsky.richtext.facet#tag",
                                "tag": "omd",
                            },
                        ]
                    }
                ],
            },
            "embed": {
                "$type": "app.bsky.embed.images#view",
                "images": [
                    {
                        "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:alice/demo",
                        "alt": "demo image",
                    }
                ],
            },
            "likeCount": 12,
            "repostCount": 3,
            "replyCount": 2,
            "quoteCount": 1,
        },
        "replies": [
            {
                "post": {
                    "uri": "at://did:plc:bob/app.bsky.feed.post/reply1",
                    "author": {"handle": "bob.example", "displayName": "Bob"},
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": "Nice post",
                    },
                    "likeCount": 4,
                    "replyCount": 0,
                }
            }
        ],
    }
}


def test_parse_bsky_url_supports_handle_and_did():
    from omd import bsky

    assert bsky.parse_bsky_url("https://bsky.app/profile/alice.bsky.social/post/abc123") == (
        "alice.bsky.social",
        "abc123",
    )
    assert bsky.parse_bsky_url("https://bsky.app/profile/did%3Aplc%3Aalice/post/abc123") == (
        "did:plc:alice",
        "abc123",
    )


def test_at_uri_for_resolves_handles(monkeypatch):
    from omd import bsky

    monkeypatch.setattr(bsky, "resolve_handle", lambda handle: f"did:plc:{handle.split('.')[0]}")

    uri, actor, rkey = bsky.at_uri_for("https://bsky.app/profile/alice.bsky.social/post/abc123")

    assert uri == "at://did:plc:alice/app.bsky.feed.post/abc123"
    assert actor == "alice.bsky.social"
    assert rkey == "abc123"


def test_parse_thread_and_markdown():
    from omd import bsky

    post = bsky.parse_thread(SAMPLE_THREAD, "https://bsky.app/profile/alice.bsky.social/post/abc123")
    md = bsky.markdown_for_post(post)

    assert md.startswith("# Bluesky post by Alice (@alice.bsky.social)\n")
    assert "- AT URI: at://did:plc:alice/app.bsky.feed.post/abc123" in md
    assert "- Likes: 12" in md
    assert "Hello Bluesky" in md
    assert "- [https://example.com/story](https://example.com/story)" in md
    assert "- [#omd](https://bsky.app/hashtag/omd)" in md
    assert "- ![demo image](https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:alice/demo)" in md
    assert "- Bob (@bob.example) (4 likes, 0 replies) - https://bsky.app/profile/bob.example/post/reply1" in md
    assert "  Nice post" in md


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import bsky

    monkeypatch.setattr(
        bsky,
        "at_uri_for",
        lambda _url: ("at://did:plc:alice/app.bsky.feed.post/abc123", "alice.bsky.social", "abc123"),
    )
    monkeypatch.setattr(bsky, "fetch_thread", lambda _uri: (SAMPLE_THREAD, ""))
    out = tmp_path / "bsky.md"

    rc = bsky.convert_url("https://bsky.app/profile/alice.bsky.social/post/abc123", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Bluesky post by Alice")


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import bsky

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
    monkeypatch.setattr(bsky.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        bsky.resolve_handle("alice.bsky.social")


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import bsky

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
            return json.dumps({"did": "did:plc:alice"}).encode()

        def geturl(self):
            return "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"

    monkeypatch.setattr(bsky.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    assert bsky.resolve_handle("alice.bsky.social") == "did:plc:alice"
