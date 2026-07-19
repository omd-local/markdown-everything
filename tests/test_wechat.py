from __future__ import annotations

from types import SimpleNamespace

import pytest


SAMPLE_HTML = """
<html>
  <head><meta property="og:title" content="Demo &amp; Title" /></head>
  <body>
    <h1 id="activity-name">Ignored because og:title wins</h1>
    <span id="profileBt">APPSO</span>
    <div id="js_content" style="visibility: hidden">
      <section><span>第一段 <strong>bold</strong></span></section>
      <section style="font-weight: 600;font-size: 21px;"><span>小标题</span></section>
      <section>
        <img data-src="https://mmbiz.qpic.cn/demo/640?wx_fmt=jpeg&amp;from=appmsg" alt="cover" />
      </section>
      <section><a href="https://example.com">link</a></section>
    </div>
  </body>
</html>
"""


def test_html_to_markdown_extracts_wechat_content():
    from omd import wechat

    md = wechat.html_to_markdown(SAMPLE_HTML, "https://mp.weixin.qq.com/s/demo")

    assert md.startswith("# Demo & Title\n")
    assert "- Source: https://mp.weixin.qq.com/s/demo" in md
    assert "- Account: APPSO" in md
    assert "- Images: 1" in md
    assert "第一段 **bold**" in md
    assert "## 小标题" in md
    assert "![cover](https://mmbiz.qpic.cn/demo/640?wx_fmt=jpeg&from=appmsg)" in md
    assert "[link](https://example.com)" in md


def test_parse_article_requires_js_content():
    from omd import wechat

    with pytest.raises(SystemExit) as exc:
        wechat.parse_article("<html><body>No article body</body></html>")
    assert "login-gated" in str(exc.value)
    assert "mp.weixin.qq.com/s/..." in str(exc.value)


def test_extract_url_from_wechat_share_blob():
    from omd import wechat

    blob = "阅读原文 https://mp.weixin.qq.com/s/abc123 ，更多内容"
    assert wechat.extract_url(blob) == "https://mp.weixin.qq.com/s/abc123"


def test_is_wechat_article_url_supports_article_shapes_only():
    from omd import wechat

    assert wechat.is_wechat_article_url("https://mp.weixin.qq.com/s/abc123")
    assert wechat.is_wechat_article_url("https://mp.weixin.qq.com/s?__biz=demo&mid=1")
    assert not wechat.is_wechat_article_url("https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum")
    assert not wechat.is_wechat_article_url("https://weixin.qq.com/s/abc123")


def test_convert_url_rejects_non_article_wechat_shape():
    from omd import wechat

    with pytest.raises(SystemExit) as exc:
        wechat.convert_url("https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum")

    assert "Unsupported WeChat URL shape" in str(exc.value)
    assert "mp.weixin.qq.com/s/..." in str(exc.value)


def test_parse_article_reports_wechat_verification_page():
    from omd import wechat

    html = """
    <html>
      <script>var PAGE_MID='mmbizwap:secitptpage/verify.html';</script>
      <script src="https://captcha.gtimg.com/TCaptcha.js"></script>
    </html>
    """

    with pytest.raises(SystemExit) as exc:
        wechat.parse_article(html)

    assert "verification/captcha page" in str(exc.value)
    assert "Automated fetch cannot pass this challenge" in str(exc.value)


def test_verification_detector_matches_real_redirect_scaffold():
    from omd._wechat import looks_like_verification_page

    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <link rel="stylesheet" href="//res.wx.qq.com/mmbizwap/zh_CN/htmledition/style/page/secitptpage/verify7f296b.css">
        <script src="https://captcha.gtimg.com/TCaptcha.js"></script>
      </head>
      <body>
        <script>
        window.cgiData = {
          target_url : "https://mp.weixin.qq.com/s?__biz=demo&mid=1",
          poc_token : "HM8_TmqjfJo_AMfKqN9OnU1Sby8SujvK4gRNwWAQ"
        };
        </script>
      </body>
    </html>
    """

    assert looks_like_verification_page(html)


def test_convert_url_reports_wechat_verification_redirect(monkeypatch):
    from omd import wechat

    def fake_fetch_html(_url):
        return (
            "<html><script>var PAGE_MID='mmbizwap:secitptpage/verify.html';</script></html>",
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=demo",
        )

    monkeypatch.setattr(wechat, "fetch_html", fake_fetch_html)

    with pytest.raises(SystemExit) as exc:
        wechat.convert_url("https://mp.weixin.qq.com/s?__biz=demo&mid=1")

    assert "verification/captcha page" in str(exc.value)


def test_convert_url_writes_output(monkeypatch, tmp_path):
    from omd import wechat

    monkeypatch.setattr(
        wechat,
        "fetch_html",
        lambda _url: (SAMPLE_HTML, "https://mp.weixin.qq.com/s/demo"),
    )
    out = tmp_path / "article.md"

    rc = wechat.convert_url("https://mp.weixin.qq.com/s/demo", out)

    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("# Demo & Title\n")


def test_fetch_html_rejects_oversized_content_length(monkeypatch):
    from omd import wechat

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
    monkeypatch.setattr(wechat.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse())

    with pytest.raises(ValueError, match="above limit"):
        wechat.fetch_html("https://mp.weixin.qq.com/s/demo")
