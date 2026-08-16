from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest


SAMPLE_SYNDICATION = {
    "id_str": "1234567890",
    "text": "Hello from X https://t.co/demo",
    "created_at": "2024-06-01T12:00:00.000Z",
    "favorite_count": 10,
    "retweet_count": 2,
    "conversation_count": 3,
    "quote_count": 1,
    "user": {"name": "Alice Example", "screen_name": "alice"},
    "entities": {
        "urls": [
            {
                "display_url": "example.com/article",
                "expanded_url": "https://example.com/article",
            }
        ]
    },
    "mediaDetails": [
        {
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/demo.jpg",
        }
    ],
    "quoted_tweet": {
        "text": "Quoted post",
        "user": {"name": "Bob", "screen_name": "bob"},
    },
}


SAMPLE_OEMBED = {
    "url": "https://x.com/jack/status/20",
    "author_name": "jack",
    "author_url": "https://x.com/jack",
    "html": (
        '<blockquote class="twitter-tweet" data-dnt="true">'
        '<p lang="en" dir="ltr">just setting up my twttr</p>'
        '&mdash; jack (@jack) '
        '<a href="https://x.com/jack/status/20?ref_src=twsrc%5Etfw">March 21, 2006</a>'
        "</blockquote>"
    ),
}


SAMPLE_PUBLIC_PAGE = r'''
<!doctype html>
<html>
<head>
<meta name="title" content="Andrej Karpathy (@karpathy) on X" />
<meta name="twitter:creator" content="@karpathy" />
</head>
<body>
<script>
window.__INITIAL_DATA__={relevantPerson:{name:"Andrej Karpathy",screenName:"karpathy"},
"client:tweet:counts":{reply_count:2914,favorite_count:60498,retweet_count:7353,quote_count:2173},
"client:tweet:details":{created_at_ms:1775162541000,full_text:"First paragraph only"},
"client:tweet:note_tweet":{__typename:"NoteTweetData",is_expandable:!0},
"note":{__typename:"NoteTweet",text:"First paragraph only\n\nSecond paragraph from public SSR note tweet",entity_set:{}}
};
</script>
</body>
</html>
'''


def test_post_id_from_standard_and_web_urls():
    from omd import xpost

    assert xpost.post_id_from_url("https://x.com/openai/status/1234567890") == "1234567890"
    assert xpost.post_id_from_url("https://twitter.com/i/web/status/20") == "20"


def test_canonical_url_preserves_handle_when_known():
    from omd import xpost

    assert xpost.canonical_url("https://twitter.com/openai/status/123?x=y") == (
        "https://x.com/openai/status/123"
    )
    assert xpost.canonical_url("https://x.com/i/web/status/20") == "https://x.com/i/web/status/20"
    assert xpost.canonical_url("https://x.com/i/status/20") == "https://x.com/i/web/status/20"
    assert xpost.canonical_url("https://twitter.com/i/status/20") == "https://x.com/i/web/status/20"


def test_parse_syndication_and_markdown():
    from omd import xpost

    post = xpost.parse_syndication(SAMPLE_SYNDICATION, "https://x.com/alice/status/1234567890")

    assert post is not None
    md = xpost.markdown_for_post(post)
    assert md.startswith("# X post by Alice Example (@alice)\n")
    assert "- Likes: 10" in md
    assert "- Reposts: 2" in md
    assert "- Replies: 3" in md
    assert "Hello from X" in md
    assert "- [example.com/article](https://example.com/article)" in md
    assert "- photo: https://pbs.twimg.com/media/demo.jpg" in md
    assert "> Quoted post" in md
    assert "- Quoted author: Bob (@bob)" in md


def test_parse_syndication_prefers_full_note_tweet_text():
    from omd import xpost

    payload = {
        **SAMPLE_SYNDICATION,
        "text": "First paragraph only",
        "note_tweet": {
            "text": "First paragraph only\n\nSecond paragraph from the long-form post",
            "entities": {
                "urls": [
                    {
                        "display_url": "example.com/long",
                        "expanded_url": "https://example.com/long",
                    }
                ]
            },
        },
    }

    post = xpost.parse_syndication(payload, "https://x.com/alice/status/1234567890")

    assert post is not None
    assert post.text == "First paragraph only\n\nSecond paragraph from the long-form post"
    assert ("example.com/long", "https://example.com/long") in post.links


def test_parse_oembed_and_markdown():
    from omd import xpost

    post = xpost.parse_oembed(SAMPLE_OEMBED, "https://twitter.com/jack/status/20")

    assert post is not None
    assert post.handle == "jack"
    assert post.created_at == "March 21, 2006"
    md = xpost.markdown_for_post(post)
    assert "# X post by jack (@jack)" in md
    assert "just setting up my twttr" in md
    assert "## Links" not in md


def test_extract_url_from_x_share_blob():
    from omd import xpost

    blob = "look at this https://x.com/openai/status/1234567890?s=20 more text"
    assert xpost.extract_url(blob) == "https://x.com/openai/status/1234567890?s=20"


def test_convert_url_falls_back_to_oembed(monkeypatch, tmp_path):
    from omd import xpost

    monkeypatch.setattr(xpost, "fetch_syndication", lambda _post_id: ({}, ""))
    monkeypatch.setattr(xpost, "fetch_page", lambda _url: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(xpost, "fetch_oembed", lambda _url: (SAMPLE_OEMBED, ""))
    out = tmp_path / "x.md"

    rc = xpost.convert_url("https://twitter.com/jack/status/20", out)

    assert rc == 0
    assert "just setting up my twttr" in out.read_text(encoding="utf-8")


def test_convert_url_falls_back_when_syndication_request_fails(monkeypatch, tmp_path):
    from omd import xpost

    monkeypatch.setattr(
        xpost,
        "fetch_syndication",
        lambda _post_id: (_ for _ in ()).throw(OSError("syndication unavailable")),
    )
    monkeypatch.setattr(xpost, "fetch_page", lambda _url: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(xpost, "fetch_oembed", lambda _url: (SAMPLE_OEMBED, ""))
    out = tmp_path / "x.md"

    rc = xpost.convert_url("https://twitter.com/jack/status/20", out)

    assert rc == 0
    assert "just setting up my twttr" in out.read_text(encoding="utf-8")


def test_convert_url_normalizes_failure_when_all_sources_error(monkeypatch):
    from omd import xpost

    monkeypatch.setattr(
        xpost,
        "fetch_syndication",
        lambda _post_id: (_ for _ in ()).throw(OSError("syndication unavailable")),
    )
    monkeypatch.setattr(xpost, "fetch_page", lambda _url: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(xpost, "fetch_oembed", lambda _url: (_ for _ in ()).throw(OSError("blocked")))

    with pytest.raises(SystemExit, match="X/Twitter returned no public post text"):
        xpost.convert_url("https://twitter.com/jack/status/20")


def test_convert_url_uses_oembed_when_syndication_is_truncated_and_page_fails(monkeypatch, tmp_path):
    from omd import xpost

    truncated = {**SAMPLE_SYNDICATION, "text": "just setting up…"}
    monkeypatch.setattr(xpost, "fetch_syndication", lambda _post_id: (truncated, ""))
    monkeypatch.setattr(xpost, "fetch_page", lambda _url: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(xpost, "fetch_oembed", lambda _url: (SAMPLE_OEMBED, ""))
    out = tmp_path / "x.md"

    rc = xpost.convert_url("https://twitter.com/jack/status/20", out)

    assert rc == 0
    assert "just setting up my twttr" in out.read_text(encoding="utf-8")


def test_convert_url_uses_public_page_when_embed_is_truncated(monkeypatch, tmp_path):
    from omd import xpost

    truncated_oembed = {
        **SAMPLE_OEMBED,
        "url": "https://x.com/karpathy/status/2039805659525644595",
        "author_name": "Andrej Karpathy",
        "author_url": "https://x.com/karpathy",
        "html": (
            '<blockquote class="twitter-tweet" data-dnt="true">'
            '<p lang="en" dir="ltr">First paragraph only…</p>'
            '&mdash; Andrej Karpathy (@karpathy) '
            '<a href="https://x.com/karpathy/status/2039805659525644595?ref_src=twsrc%5Etfw">'
            "April 2, 2026</a></blockquote>"
        ),
    }
    monkeypatch.setattr(xpost, "fetch_syndication", lambda _post_id: ({}, ""))
    monkeypatch.setattr(xpost, "fetch_page", lambda _url: (SAMPLE_PUBLIC_PAGE, ""))
    monkeypatch.setattr(xpost, "fetch_oembed", lambda _url: (truncated_oembed, ""))
    out = tmp_path / "x.md"

    rc = xpost.convert_url("https://x.com/karpathy/status/2039805659525644595", out)

    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "# X post by Andrej Karpathy (@karpathy)" in md
    assert "Second paragraph from public SSR note tweet" in md
    assert "First paragraph only…" not in md


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import xpost

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
    monkeypatch.setattr(xpost.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        xpost.fetch_oembed("https://x.com/jack/status/20")


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import xpost

    class FakeResponse:
        headers = SimpleNamespace(
            get=lambda _name: None,
            get_content_charset=lambda: "utf-8",
        )

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _n):
            return json.dumps(SAMPLE_OEMBED).encode()

        def geturl(self):
            return "https://publish.twitter.com/oembed"

    monkeypatch.setattr(xpost.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    payload, final_url = xpost.fetch_oembed("https://x.com/jack/status/20")

    assert payload["author_name"] == "jack"
    assert final_url == "https://publish.twitter.com/oembed"


def test_xpost_timestamp_formatting_uses_python_310_datetime_api(monkeypatch):
    from omd import xpost

    monkeypatch.setattr(xpost, "_dt", SimpleNamespace(datetime=dt.datetime, timezone=dt.timezone))

    assert xpost._format_created_at(1700000000).endswith("Z")
