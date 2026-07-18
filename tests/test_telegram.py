from __future__ import annotations

from types import SimpleNamespace

import pytest


SAMPLE_HTML = """
<html><body>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="demo/42">
    <div class="tgme_widget_message_user">
      <a href="https://t.me/demo"><i class="tgme_widget_message_user_photo"><img src="https://cdn.example/avatar.jpg"></i></a>
    </div>
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_author accent_color">
        <a class="tgme_widget_message_owner_name" href="https://t.me/demo"><span dir="auto">Demo Channel</span></a>
      </div>
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Hello <b>Telegram</b><br/><br/>Read <a href="https://example.com/story">the story</a>.
      </div>
      <a class="tgme_widget_message_link_preview" href="https://example.com/story">
        <div class="link_preview_site_name accent_color" dir="auto">Example</div>
        <div class="link_preview_title" dir="auto">Story title</div>
        <div class="link_preview_description" dir="auto">Story description</div>
      </a>
      <div class="tgme_widget_message_reactions js-message_reactions">
        <span class="tgme_reaction"><i class="emoji"><b>👍</b></i>10</span>
        <span class="tgme_reaction"><i class="emoji"><b>❤</b></i>5</span>
      </div>
      <div class="tgme_widget_message_info short js-message_info">
        <span class="tgme_widget_message_views">12.3K</span><span class="copyonly"> views</span>
        <span class="tgme_widget_message_meta">
          <a class="tgme_widget_message_date" href="https://t.me/demo/42">
            <time datetime="2026-06-01T10:00:00+00:00" class="time">10:00</time>
          </a>
        </span>
      </div>
    </div>
  </div>
</div>
</body></html>
"""


def test_parse_telegram_url_supports_public_post_paths():
    from omd import telegram

    assert telegram.parse_telegram_url("https://t.me/demo/42") == ("demo", 42)
    assert telegram.parse_telegram_url("https://t.me/s/demo/42") == ("demo", 42)
    assert telegram.parse_telegram_url("https://telegram.me/demo/42") == ("demo", 42)


def test_parse_telegram_url_rejects_private_or_invalid_paths():
    from omd import telegram

    with pytest.raises(SystemExit):
        telegram.parse_telegram_url("https://t.me/c/123/42")
    with pytest.raises(SystemExit):
        telegram.parse_telegram_url("https://t.me/+privateinvite")


def test_parse_page_and_markdown():
    from omd import telegram

    post = telegram.parse_page(
        SAMPLE_HTML,
        channel="demo",
        message_id=42,
        source_url="https://t.me/demo/42",
        page_url="https://t.me/s/demo/42",
    )
    md = telegram.markdown_for_post(post)

    assert post.author == "Demo Channel"
    assert post.text == "Hello Telegram\n\nRead the story."
    assert post.views == "12.3K"
    assert post.posted_at == "2026-06-01T10:00:00+00:00"
    assert post.links == ["https://example.com/story"]
    assert post.reactions == ["👍10", "❤5"]
    assert post.preview is not None
    assert post.preview.title == "Story title"
    assert md.startswith("# Telegram post by Demo Channel\n")
    assert "- Channel: @demo" in md
    assert "## Link Preview" in md
    assert "- [Story title](https://example.com/story)" in md


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import telegram

    monkeypatch.setattr(telegram, "fetch_page", lambda _channel, _message_id: (SAMPLE_HTML, "https://t.me/s/demo/42"))
    out = tmp_path / "telegram.md"

    rc = telegram.convert_url("https://t.me/demo/42", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Telegram post by Demo Channel")


def test_fetch_text_rejects_oversized_content_length(monkeypatch):
    from omd import telegram

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
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        telegram.fetch_page("demo", 42)


def test_fetch_text_decodes_payload(monkeypatch):
    from omd import telegram

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
            return SAMPLE_HTML.encode()

        def geturl(self):
            return "https://t.me/s/demo/42"

    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    body, final_url = telegram.fetch_page("demo", 42)
    assert "data-post=\"demo/42\"" in body
    assert final_url == "https://t.me/s/demo/42"
