from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest


SAMPLE_STORY = {
    "by": "alice",
    "descendants": 2,
    "id": 12345,
    "kids": [200, 201],
    "score": 42,
    "time": 1717200000,
    "title": "Show HN: OMD",
    "type": "story",
    "url": "https://example.com/omd",
}

SAMPLE_COMMENT = {
    "by": "bob",
    "id": 200,
    "kids": [300],
    "parent": 12345,
    "text": "<p>Great project<br>Works well</p>",
    "time": 1717200300,
    "type": "comment",
}

SAMPLE_REPLY = {
    "by": "carol",
    "id": 300,
    "parent": 200,
    "text": "Thanks &amp; agreed",
    "time": 1717200600,
    "type": "comment",
}


def test_item_id_from_url_supports_hn_item_url():
    from omd import hn

    assert hn.item_id_from_url("https://news.ycombinator.com/item?id=12345") == 12345
    assert hn.item_id_from_url("https://news.ycombinator.com/item?id=12345&p=2") == 12345


def test_html_to_text_decodes_hn_html():
    from omd import hn

    assert hn.html_to_text("<p>Hello <b>there</b><br>Next</p><p>Again</p>") == (
        "Hello there\nNext\n\nAgain"
    )
    assert hn.html_to_text("a&amp;b &lt;c&gt;") == "a&b <c>"


def test_parse_item_and_markdown():
    from omd import hn

    comment = hn.HNComment(
        item_id=200,
        author="bob",
        text="Great project\nWorks well",
        created_at="2024-06-01T00:05:00Z",
        children=[
            hn.HNComment(
                item_id=300,
                author="carol",
                text="Thanks & agreed",
                created_at="2024-06-01T00:10:00Z",
            )
        ],
    )
    item = hn.parse_item(
        SAMPLE_STORY,
        source_url="https://news.ycombinator.com/item?id=12345",
        api_url="https://hacker-news.firebaseio.com/v0/item/12345.json",
        comments=[comment],
        fetched_comments=2,
    )
    md = hn.markdown_for_item(item)

    assert md.startswith("# Hacker News story: Show HN: OMD\n")
    assert "- API: https://hacker-news.firebaseio.com/v0/item/12345.json" in md
    assert "- Author: alice" in md
    assert "- Posted: 2024-06-01T00:00:00Z" in md
    assert "- Score: 42" in md
    assert "- Link: https://example.com/omd" in md
    assert "- bob - https://news.ycombinator.com/item?id=200" in md
    assert "  Great project\n  Works well" in md
    assert "  - carol - https://news.ycombinator.com/item?id=300" in md


def test_fetch_hn_item_fetches_bounded_comment_tree(monkeypatch):
    from omd import hn

    payloads = {
        12345: SAMPLE_STORY,
        200: SAMPLE_COMMENT,
        201: {"id": 201, "by": "dave", "text": "Second", "time": 1717200900, "type": "comment"},
        300: SAMPLE_REPLY,
    }

    def fake_fetch(item_id, *, timeout=30):
        return payloads[item_id], hn.api_url_for(item_id)

    monkeypatch.setattr(hn, "fetch_item", fake_fetch)

    item = hn.fetch_hn_item(12345, max_comments=2, max_depth=2)

    assert item.fetched_comments == 2
    assert len(item.comments) == 1
    assert item.comments[0].author == "bob"
    assert item.comments[0].children[0].author == "carol"
    assert item.skipped_comment_ids == 1


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import hn

    item = hn.parse_item(
        SAMPLE_STORY,
        source_url="https://news.ycombinator.com/item?id=12345",
        api_url="https://hacker-news.firebaseio.com/v0/item/12345.json",
    )
    monkeypatch.setattr(hn, "fetch_hn_item", lambda _item_id: item)
    out = tmp_path / "hn.md"

    rc = hn.convert_url("https://news.ycombinator.com/item?id=12345", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Hacker News story: Show HN: OMD")


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import hn

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
    monkeypatch.setattr(hn.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        hn.fetch_item(12345)


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import hn

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
            return json.dumps(SAMPLE_STORY).encode()

        def geturl(self):
            return "https://hacker-news.firebaseio.com/v0/item/12345.json"

    monkeypatch.setattr(hn.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    payload, final_url = hn.fetch_item(12345)
    assert payload["id"] == 12345
    assert final_url == "https://hacker-news.firebaseio.com/v0/item/12345.json"


def test_hn_timestamp_formatting_uses_python_310_datetime_api(monkeypatch):
    from omd import hn

    monkeypatch.setattr(hn, "_dt", SimpleNamespace(datetime=dt.datetime, timezone=dt.timezone))

    assert hn._format_time(1700000000).endswith("Z")
