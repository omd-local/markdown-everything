from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


SAMPLE_HTML = """<!doctype html>
<html>
<head>
  <meta property="og:title" content="Threads API Changelog (@threadsapi.changelog) on Threads" />
  <meta property="og:description" content="&#128226; Threads API update&#10;&#10;March 3, 2026&#10;&middot; You can now call Threads oEmbed API without an access token." />
  <meta property="og:url" content="https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS" />
  <meta property="og:image" content="https://example.com/profile.jpg" />
  <link rel="canonical" href="https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS" />
</head>
</html>
"""

SAMPLE_OEMBED = {
    "type": "rich",
    "version": "1.0",
    "provider_name": "Threads",
    "provider_url": "https://www.threads.com/",
    "width": 658,
    "html": "<blockquote>View on Threads</blockquote>",
}


def test_parse_threads_url_supports_profile_and_short_forms():
    from omd import threads

    assert threads.parse_threads_url("https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS") == (
        "threadsapi.changelog",
        "DVcNwt2jDZS",
    )
    assert threads.parse_threads_url("https://www.threads.net/@alice/post/abc_123") == ("alice", "abc_123")
    assert threads.parse_threads_url("https://www.threads.com/t/DVcNwt2jDZS") == ("", "DVcNwt2jDZS")


def test_parse_page_and_markdown():
    from omd import threads

    post = threads.parse_page(
        SAMPLE_HTML,
        source_url="https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS",
        page_url="https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS",
    )

    assert post.post_id == "DVcNwt2jDZS"
    assert post.author_name == "Threads API Changelog"
    assert post.handle == "threadsapi.changelog"
    assert "without an access token" in post.text
    md = threads.markdown_for_post(post)
    assert md.startswith("# Threads post by Threads API Changelog (@threadsapi.changelog)\n")
    assert "- Post ID: DVcNwt2jDZS" in md
    assert "## Post" in md
    assert "https://example.com/profile.jpg" in md


def test_parse_oembed_adds_provider_metadata():
    from omd import threads

    embed = threads.parse_oembed(SAMPLE_OEMBED)
    post = threads.ThreadsPost(
        post_id="DVcNwt2jDZS",
        source_url="https://www.threads.com/t/DVcNwt2jDZS",
        page_url="https://www.threads.com/t/DVcNwt2jDZS",
        text="Hello",
        embed=embed,
    )

    md = threads.markdown_for_post(post)

    assert "- Provider: Threads" in md
    assert "- Embed type: rich" in md
    assert "- Embed width: 658" in md


def test_extract_url_from_threads_share_blob():
    from omd import threads

    blob = "check this https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS?x=y now"
    assert threads.extract_url(blob) == "https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS?x=y"


def test_convert_url_writes_markdown(monkeypatch, tmp_path):
    from omd import threads

    monkeypatch.setattr(threads, "fetch_page", lambda _url: (SAMPLE_HTML, _url))
    monkeypatch.setattr(threads, "fetch_oembed", lambda _url: (SAMPLE_OEMBED, ""))
    out = tmp_path / "threads.md"

    rc = threads.convert_url("https://www.threads.com/@threadsapi.changelog/post/DVcNwt2jDZS", out)

    assert rc == 0
    output = out.read_text(encoding="utf-8")
    assert "# Threads post by Threads API Changelog (@threadsapi.changelog)" in output
    assert "- Provider: Threads" in output


def test_convert_url_fails_when_metadata_has_no_text(monkeypatch, tmp_path):
    from omd import threads

    monkeypatch.setattr(threads, "fetch_page", lambda _url: ("<html></html>", _url))

    with pytest.raises(SystemExit, match="did not expose post text"):
        threads.convert_url("https://www.threads.com/t/DVcNwt2jDZS", tmp_path / "threads.md")


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import threads

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
    monkeypatch.setattr(threads.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        threads.fetch_oembed("https://www.threads.com/t/DVcNwt2jDZS")


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import threads

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
            return "https://graph.threads.net/v1.0/oembed"

    monkeypatch.setattr(threads.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    payload, final_url = threads.fetch_oembed("https://www.threads.com/t/DVcNwt2jDZS")

    assert payload["provider_name"] == "Threads"
    assert final_url == "https://graph.threads.net/v1.0/oembed"
