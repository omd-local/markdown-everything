from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Headers(dict):
    def get_content_charset(self):
        return "utf-8"


class _Response:
    def __init__(self, body: str, url: str, *, content_type: str = "text/html") -> None:
        self._stream = io.BytesIO(body.encode("utf-8"))
        self._url = url
        self.headers = _Headers(
            {
                "Content-Length": str(len(body.encode("utf-8"))),
                "Content-Type": content_type,
            }
        )

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def _http_403(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 403, "Forbidden", {}, None)


def test_public_fallback_returns_browser_fetched_html_when_page_is_accessible():
    from omd.web_article import fetch_public_fallback

    url = "https://example.com/posts/demo"

    def fake_open(request, timeout):
        assert request.full_url == url
        assert "Mozilla/5.0" in request.headers["User-agent"]
        assert timeout == 30
        return _Response("<html><head><title>Demo</title></head><body>Body</body></html>", url)

    result = fetch_public_fallback(url, _open=fake_open)

    assert result.mode == "browser_html"
    assert result.partial is False
    assert "<base href=\"https://example.com/posts/demo\">" in result.html


def test_public_fallback_uses_matching_rss_excerpt_when_page_is_blocked():
    from omd.web_article import fetch_public_fallback

    url = "https://example.com/posts/demo"
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
      <channel><item>
        <title>Demo article</title>
        <link>https://example.com/posts/demo/</link>
        <dc:creator>Ada</dc:creator>
        <pubDate>Sun, 12 Jul 2026 10:00:00 +0000</pubDate>
        <description><![CDATA[<p>Public excerpt.</p>]]></description>
      </item></channel>
    </rss>"""

    def fake_open(request, timeout):
        if request.full_url == url:
            raise _http_403(url)
        assert request.full_url == "https://example.com/feed/"
        return _Response(feed, request.full_url, content_type="application/rss+xml")

    result = fetch_public_fallback(url, _open=fake_open)

    assert result.mode == "rss_excerpt"
    assert result.partial is True
    assert "Demo article" in result.html
    assert "Ada" in result.html
    assert "Public excerpt." in result.html
    assert "Only the public RSS excerpt was available" in result.html


def test_public_fallback_rejects_feed_without_matching_article():
    from omd.web_article import WebFallbackUnavailable, fetch_public_fallback

    url = "https://example.com/posts/missing"
    feed = """<rss version="2.0"><channel><item>
      <title>Different article</title>
      <link>https://example.com/posts/other/</link>
      <description>Other text</description>
    </item></channel></rss>"""

    def fake_open(request, timeout):
        if request.full_url == url:
            raise _http_403(url)
        return _Response(feed, request.full_url, content_type="application/rss+xml")

    with pytest.raises(WebFallbackUnavailable, match="matching article"):
        fetch_public_fallback(url, _open=fake_open)


def test_route_markitdown_retries_failed_url_with_local_fallback(monkeypatch, tmp_path):
    from omd import cli
    from omd.web_article import WebFallbackDocument

    url = "https://example.com/posts/demo"
    output = tmp_path / "demo.md"
    calls: list[list[str]] = []

    monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/local/bin/markitdown")
    monkeypatch.setattr(
        "omd.web_article.fetch_public_fallback",
        lambda _url: WebFallbackDocument(
            html="<html><body><h1>Demo</h1></body></html>",
            mode="browser_html",
            partial=False,
        ),
    )

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Traceback (most recent call last):\nrequests.exceptions.HTTPError: 403 Forbidden\n",
            )
        assert Path(cmd[1]).suffix == ".html"
        return SimpleNamespace(returncode=0, stdout="# Demo\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.route_markitdown(url, output)

    assert rc == 0
    assert output.read_text(encoding="utf-8") == "# Demo\n"
    assert calls[0] == ["/usr/local/bin/markitdown", url]
    assert len(calls) == 2


def test_route_markitdown_hides_converter_traceback_when_fallback_succeeds(
    monkeypatch, tmp_path, capsys
):
    from omd import cli
    from omd.web_article import WebFallbackDocument

    monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/local/bin/markitdown")
    monkeypatch.setattr(
        "omd.web_article.fetch_public_fallback",
        lambda _url: WebFallbackDocument(
            html="<html><body>Excerpt</body></html>",
            mode="rss_excerpt",
            partial=True,
        ),
    )
    results = iter(
        [
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Traceback (most recent call last):\nrequests.exceptions.HTTPError: 403 Forbidden\n",
            ),
            SimpleNamespace(returncode=0, stdout="# Excerpt\n", stderr=""),
        ]
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: next(results))

    rc = cli.route_markitdown("https://example.com/posts/demo", tmp_path / "demo.md")
    captured = capsys.readouterr()

    assert rc == 0
    assert "Traceback" not in captured.err
    assert "RSS excerpt" in captured.err


def test_route_one_records_partial_rss_fallback_in_manifest(monkeypatch, tmp_path):
    from omd import cli
    from omd._manifest import manifest_path_for_output
    from omd.web_article import WebFallbackDocument

    url = "https://example.com/posts/demo"
    output = tmp_path / "demo.md"
    monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/local/bin/markitdown")
    monkeypatch.setattr(
        "omd.web_article.fetch_public_fallback",
        lambda _url: WebFallbackDocument(
            html="<html><body><h1>Public excerpt</h1></body></html>",
            mode="rss_excerpt",
            partial=True,
        ),
    )
    results = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="HTTP Error 403: Blocked"),
            SimpleNamespace(returncode=0, stdout="# Public excerpt\n", stderr=""),
        ]
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: next(results))

    rc = cli.route_one(url, output, "eng", [])
    manifest = json.loads(
        manifest_path_for_output(output).read_text(encoding="utf-8")
    )

    assert rc == 0
    assert manifest["metadata"]["conversion"]["web_fallback"] == {
        "used": True,
        "mode": "rss_excerpt",
        "partial": True,
    }
    assert any("partial" in warning.lower() for warning in manifest["warnings"])


def test_manifest_does_not_reuse_web_provenance_for_a_different_source(monkeypatch, tmp_path):
    from omd import cli
    from omd._manifest import manifest_path_for_output, write_manifest_for_output

    output = tmp_path / "reused.md"
    output.write_text("# Old web page\n", encoding="utf-8")
    write_manifest_for_output(
        output,
        source="https://example.com/old",
        backend="markitdown",
        metadata={
            "conversion": {
                "web_fallback": {"used": True, "mode": "rss_excerpt", "partial": True}
            }
        },
    )

    def fake_wechat(_url, path):
        path.write_text("# New WeChat article\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_wechat", fake_wechat)

    rc = cli.route_one("https://mp.weixin.qq.com/s/new", output, "eng", [])
    manifest = json.loads(manifest_path_for_output(output).read_text(encoding="utf-8"))

    assert rc == 0
    assert "conversion" not in manifest["metadata"]
