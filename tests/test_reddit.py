from __future__ import annotations

import datetime as dt
import json
import urllib.error
from types import SimpleNamespace

import pytest


SAMPLE_REDDIT_JSON = [
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "title": "Demo Reddit Post",
                        "author": "alice",
                        "subreddit_name_prefixed": "r/test",
                        "selftext": "Post **body**",
                        "score": 42,
                        "num_comments": 2,
                        "created_utc": 1700000000,
                        "edited": False,
                        "permalink": "/r/test/comments/abc/demo/",
                        "url": "https://example.com/article",
                    },
                }
            ]
        },
    },
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "author": "bob",
                        "body": "Top comment",
                        "score": 7,
                        "created_utc": 1700000100,
                        "edited": 1700000200,
                        "permalink": "/r/test/comments/abc/demo/c1/",
                        "replies": {
                            "kind": "Listing",
                            "data": {
                                "children": [
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "author": "carol",
                                            "body": "Nested reply",
                                            "score": 3,
                                            "created_utc": 1700000300,
                                            "edited": False,
                                            "permalink": "/r/test/comments/abc/demo/c2/",
                                            "replies": "",
                                        },
                                    }
                                ]
                            },
                        },
                    },
                },
                {"kind": "more", "data": {"count": 10}},
            ]
        },
    },
]

SAMPLE_OLD_REDDIT_HTML = """
<!doctype html>
<html><head>
<title>Demo Reddit Post : u/alice</title>
<meta property="og:title" content="Demo Reddit Post">
</head><body>
<div class=" thing id-t3_abc link self" id="thing_t3_abc"
  data-fullname="t3_abc" data-type="link" data-author="alice"
  data-subreddit-prefixed="r/test" data-comments-count="2"
  data-score="42" data-timestamp="1700000000000"
  data-permalink="/r/test/comments/abc/demo/"
  data-url="/r/test/comments/abc/demo/">
  <p class="title"><a class="title may-blank" href="/r/test/comments/abc/demo/">Demo Reddit Post</a></p>
  <div class="usertext-body md-container"><div class="md">
    <p>Post <strong>body</strong> from old reddit.</p>
    <h1>Heading</h1>
    <p>More text with <a href="https://example.com">a link</a>.</p>
  </div></div>
</div>
<div class=" thing id-t1_c1 comment" id="thing_t1_c1"
  data-fullname="t1_c1" data-type="comment" data-author="bob"
  data-permalink="/r/test/comments/abc/demo/c1/">
  <span class="score unvoted" title="7">7 points</span>
  <div class="usertext-body md-container"><div class="md">
    <p>Top comment</p>
  </div></div>
</div>
</body></html>
"""


def test_json_url_for_reddit_comments_url():
    from omd import reddit

    url = reddit.json_url_for("https://old.reddit.com/r/test/comments/abc/demo/?utm_source=share")

    assert url.startswith("https://www.reddit.com/r/test/comments/abc/demo.json?")
    assert "limit=50" in url
    assert "raw_json=1" in url


def test_json_url_for_redd_it_shortlink():
    from omd import reddit

    assert reddit.json_url_for("https://redd.it/abc") == (
        "https://www.reddit.com/comments/abc.json?limit=50&raw_json=1"
    )


def test_resolve_share_url_uses_canonical_reddit_post_redirect(monkeypatch):
    from omd import reddit

    canonical = (
        "https://www.reddit.com/r/LocalLLM/comments/1utsblw/"
        "glm_52_744b_on_25_gb_ram_consumer_machine/"
    )

    class FakeOpener:
        def open(self, request, **_kwargs):
            raise urllib.error.HTTPError(
                request.full_url,
                301,
                "Moved Permanently",
                {"Location": canonical + "?utm_source=share"},
                None,
            )

    monkeypatch.setattr(reddit.urllib.request, "build_opener", lambda *_handlers: FakeOpener())

    assert reddit.resolve_share_url("https://www.reddit.com/r/LocalLLM/s/9fC4efLJQB") == canonical


def test_resolve_share_url_rejects_redirect_outside_reddit(monkeypatch):
    from omd import reddit

    class FakeResponse:
        status = 301
        headers = {"Location": "https://example.com/comments/abc"}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def geturl(self):
            return "https://www.reddit.com/r/test/s/share-token"

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(reddit.urllib.request, "build_opener", lambda *_handlers: FakeOpener())

    with pytest.raises(ValueError, match="unsupported target"):
        reddit.resolve_share_url("https://www.reddit.com/r/test/s/share-token")


def test_resolve_share_url_retries_with_get_when_head_is_rejected(monkeypatch):
    from omd import reddit

    share_url = "https://www.reddit.com/r/test/s/share-token"
    canonical = "https://www.reddit.com/r/test/comments/abc/demo/"
    methods = []

    class FakeOpener:
        def open(self, request, **_kwargs):
            methods.append(request.get_method())
            if request.get_method() == "HEAD":
                raise urllib.error.HTTPError(request.full_url, 405, "Method Not Allowed", {}, None)
            raise urllib.error.HTTPError(
                request.full_url,
                301,
                "Moved Permanently",
                {"Location": canonical},
                None,
            )

    monkeypatch.setattr(reddit.urllib.request, "build_opener", lambda *_handlers: FakeOpener())

    assert reddit.resolve_share_url(share_url) == canonical
    assert methods == ["HEAD", "GET"]


def test_old_reddit_url_for_strips_tracking_query():
    from omd import reddit

    url = reddit.old_reddit_url_for(
        "https://www.reddit.com/user/Bushbuck_NZ/comments/1su2g56/demo/?p=1&impressionid=123"
    )

    assert url == "https://old.reddit.com/user/Bushbuck_NZ/comments/1su2g56/demo/"


def test_parse_listing_and_markdown_include_comments():
    from omd import reddit

    post = reddit.parse_listing(SAMPLE_REDDIT_JSON)
    md = reddit.markdown_for_post(
        post,
        "https://www.reddit.com/r/test/comments/abc/demo/",
        include_comments=True,
    )

    assert md.startswith("# Demo Reddit Post\n")
    assert "- Subreddit: r/test" in md
    assert "- Author: u/alice" in md
    assert "Post **body**" in md
    assert "- u/bob (7 points) - 2023-11-14T22:15:00Z - edited - https://www.reddit.com/r/test/comments/abc/demo/c1/" in md
    assert "  Top comment" in md
    assert "  - u/carol (3 points) - 2023-11-14T22:18:20Z - https://www.reddit.com/r/test/comments/abc/demo/c2/" in md
    assert "    Nested reply" in md


def test_markdown_for_post_defaults_to_op_only():
    from omd import reddit

    post = reddit.parse_listing(SAMPLE_REDDIT_JSON)
    md = reddit.markdown_for_post(post, "https://www.reddit.com/r/test/comments/abc/demo/")

    assert "## Post" in md
    assert "## Top comments" not in md
    assert "Skipped by default" in md
    assert "  Top comment" not in md
    assert "Nested reply" not in md


def test_parse_old_html_extracts_post_and_comments():
    from omd import reddit

    post = reddit.parse_old_html(SAMPLE_OLD_REDDIT_HTML, "https://old.reddit.com/r/test/comments/abc/demo/")
    md = reddit.markdown_for_post(
        post,
        "https://old.reddit.com/r/test/comments/abc/demo/",
        include_comments=True,
    )

    assert post.title == "Demo Reddit Post"
    assert post.author == "alice"
    assert post.subreddit == "r/test"
    assert post.score == 42
    assert post.num_comments == 2
    assert post.created_utc == 1700000000
    assert "Post **body** from old reddit." in md
    assert "### Heading" in md
    assert "[a link](https://example.com)" in md
    assert "- u/bob (7 points) - https://www.reddit.com/r/test/comments/abc/demo/c1/" in md
    assert "Top comment" in md


def test_extract_url_from_reddit_share_blob():
    from omd import reddit

    blob = "check this https://www.reddit.com/r/test/comments/abc/demo/ more text"
    assert reddit.extract_url(blob) == "https://www.reddit.com/r/test/comments/abc/demo"


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import reddit

    monkeypatch.setattr(
        reddit,
        "fetch_json",
        lambda _url: (SAMPLE_REDDIT_JSON, "https://www.reddit.com/r/test/comments/abc/demo/.json"),
    )
    out = tmp_path / "reddit.md"

    rc = reddit.convert_url("https://www.reddit.com/r/test/comments/abc/demo/", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Demo Reddit Post\n")


def test_convert_url_resolves_reddit_share_link_before_fetch(monkeypatch, tmp_path):
    from omd import reddit

    share_url = "https://www.reddit.com/r/test/s/share-token"
    canonical = "https://www.reddit.com/r/test/comments/abc/demo/"
    fetched = []
    monkeypatch.setattr(reddit, "resolve_share_url", lambda _url: canonical)
    monkeypatch.setattr(
        reddit,
        "fetch_json",
        lambda url: (fetched.append(url) or SAMPLE_REDDIT_JSON, canonical + ".json"),
    )
    out = tmp_path / "reddit.md"

    rc = reddit.convert_url(share_url, out)

    assert rc == 0
    assert fetched == [canonical]


def test_convert_url_falls_back_to_old_html_when_json_is_blocked(monkeypatch, tmp_path):
    from omd import reddit

    def blocked_json(_url):
        raise urllib.error.HTTPError(
            "https://www.reddit.com/comments/abc.json",
            403,
            "Blocked",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(reddit, "fetch_json", blocked_json)
    monkeypatch.setattr(
        reddit,
        "fetch_old_html",
        lambda _url: (SAMPLE_OLD_REDDIT_HTML, "https://old.reddit.com/r/test/comments/abc/demo/"),
    )
    out = tmp_path / "reddit.md"

    rc = reddit.convert_url("https://www.reddit.com/r/test/comments/abc/demo/", out)

    assert rc == 0
    assert "Post **body** from old reddit." in out.read_text(encoding="utf-8")


def test_fetch_json_rejects_oversized_content_length(monkeypatch):
    from omd import reddit

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
    monkeypatch.setattr(reddit.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        reddit.fetch_json("https://www.reddit.com/r/test/comments/abc/demo/")


def test_fetch_json_decodes_payload(monkeypatch):
    from omd import reddit

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
            return json.dumps(SAMPLE_REDDIT_JSON).encode()

        def geturl(self):
            return "https://www.reddit.com/r/test/comments/abc/demo/.json"

    monkeypatch.setattr(reddit.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    payload, final_url = reddit.fetch_json("https://www.reddit.com/r/test/comments/abc/demo/")

    assert payload[0]["data"]["children"][0]["data"]["title"] == "Demo Reddit Post"
    assert final_url.endswith(".json")


def test_reddit_timestamp_formatting_uses_python_310_datetime_api(monkeypatch):
    from omd import reddit

    monkeypatch.setattr(reddit, "_dt", SimpleNamespace(datetime=dt.datetime, timezone=dt.timezone))

    assert reddit._format_utc(1700000000).endswith("Z")
