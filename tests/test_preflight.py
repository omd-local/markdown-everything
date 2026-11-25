from __future__ import annotations

import json

import pytest


def test_preflight_xhs_share_blob_is_serializable():
    from omd._preflight import inspect_target

    result = inspect_target(
        "32 复制本条信息，打开【小红书】App查看精彩内容！http://xhslink.com/a/abcDEF/ 8@s"
    )

    assert json.loads(json.dumps(result)) == result
    assert result["probable_backend"] == "xhs"
    assert result["detected_type"] == "xhs_url"
    assert result["needs_network"] is True
    assert result["needs_cookies"] is True
    assert result["needs_tools"] == ["tesseract", "ffmpeg", "mlx_whisper"]
    assert result["metadata"]["cookie_strategy"] == "cookies_txt_required"
    assert result["metadata"]["cookie_domains"] == [
        "xiaohongshu.com",
        "rednote.com",
    ]
    assert result["metadata"]["allowed_url_domains"] == [
        "xiaohongshu.com",
        "xhslink.com",
        "rednote.com",
        "xhscdn.com",
    ]
    assert result["metadata"]["cookie_browser_supported"] is False
    assert result["metadata"]["cookie_validation_scope"] == "platform_until_redirect"
    assert result["metadata"]["cookie_runtime_recheck"] is True
    assert "redirect_resolution" in result["risks"]
    assert any("Shortlink input" in warning for warning in result["warnings"])
    assert any("rechecks the resolved destination" in warning for warning in result["warnings"])
    assert any("advanced local-only route" in warning for warning in result["warnings"])
    assert any("hosted demo auth is disabled" in warning for warning in result["warnings"])


def test_preflight_douyin_uses_ytdlp_cookie_jar():
    from omd._preflight import inspect_target

    result = inspect_target("https://v.douyin.com/abc123/")

    assert result["probable_backend"] == "reel"
    assert result["detected_type"] == "douyin_url"
    assert result["needs_cookies"] is True
    assert result["needs_tools"] == ["yt-dlp", "ffmpeg", "mlx_whisper"]
    assert result["metadata"]["cookie_strategy"] == "cookies_txt_required"
    assert result["metadata"]["cookie_browser_supported"] is False
    assert result["metadata"]["cookie_validation_scope"] == "platform_cookie_jar"
    assert result["metadata"]["cookie_runtime_recheck"] is False
    assert result["metadata"]["redirect_cookie_policy"] == (
        "anonymous_bounded_then_scoped_cookie_jar"
    )
    assert "auth_required" in result["risks"]
    assert "redirect_resolution" in result["risks"]
    assert any("expanded anonymously" in warning for warning in result["warnings"])
    assert any("advanced local-only route" in warning for warning in result["warnings"])
    assert any("hosted demo auth is disabled" in warning for warning in result["warnings"])


def test_preflight_douyin_with_nondefault_port_stays_on_guarded_route():
    from omd._preflight import inspect_target

    result = inspect_target("https://v.douyin.com:444/abc123/")

    assert result["detected_type"] == "douyin_url"
    assert result["probable_backend"] == "reel"
    assert result["metadata"]["redirect_cookie_policy"] == (
        "anonymous_bounded_then_scoped_cookie_jar"
    )
    assert "unsafe_url" in result["risks"]


def test_preflight_blocks_unsafe_cookie_provider_transports():
    from omd._preflight import inspect_target
    from omd.doctor import readiness_for_preflight

    targets = [
        "http://www.douyin.com/video/123",
        "https://v.douyin.com:444/abc123/",
        "https://user:pass@www.douyin.com/video/123",
        "http://www.xiaohongshu.com/explore/abc123",
        "https://www.rednote.com:444/explore/abc123",
        "https://user:pass@www.xiaohongshu.com/explore/abc123",
    ]

    for target in targets:
        result = inspect_target(target)
        assert "unsafe_url" in result["risks"], target
        readiness = readiness_for_preflight(result, [])
        assert "unsafe_url" in readiness["blocking_risks"], target
        assert readiness["ready"] is False


@pytest.mark.parametrize(
    "target",
    [
        "http://xhslink.com/a/abc",
        "http://xhslink.com:80/a/abc",
        "https://xhslink.com/a/abc",
        "https://xhslink.com:443/a/abc",
    ],
)
def test_preflight_allows_anonymous_xhs_shortlink_default_transports(target):
    from omd._preflight import inspect_target

    assert "unsafe_url" not in inspect_target(target)["risks"]


@pytest.mark.parametrize(
    "target",
    [
        "http://xhslink.com:8080/a/abc",
        "https://xhslink.com:444/a/abc",
        "https://user:pass@xhslink.com/a/abc",
    ],
)
def test_preflight_blocks_unsafe_xhs_shortlink_transports(target):
    from omd._preflight import inspect_target

    assert "unsafe_url" in inspect_target(target)["risks"]


def test_preflight_direct_xhs_uses_request_url_cookie_scope():
    from omd._preflight import inspect_target

    result = inspect_target("https://www.xiaohongshu.com/explore/abc123")

    assert result["metadata"]["cookie_validation_scope"] == "request_url"
    assert result["metadata"]["cookie_runtime_recheck"] is False


def test_preflight_pdf_file_uses_markitdown(tmp_path):
    from omd._preflight import inspect_target

    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF-1.7\n")

    result = inspect_target(target)

    assert result["probable_backend"] == "markitdown"
    assert result["detected_type"] == "document_file"
    assert result["extension"] == ".pdf"
    assert result["needs_network"] is False
    assert result["needs_tools"] == ["markitdown"]
    assert result["metadata"]["path"] == str(target)


def test_preflight_missing_file_reports_warning(tmp_path):
    from omd._preflight import inspect_target

    target = tmp_path / "missing.docx"
    result = inspect_target(target)

    assert result["probable_backend"] is None
    assert result["detected_type"] == "missing_file"
    assert result["exists"] is False
    assert result["warnings"] == [f"Path does not exist: {target}"]


def test_preflight_marks_supported_social_hosts_with_invalid_post_paths_as_unsupported():
    from omd._preflight import inspect_target

    targets = [
        "https://x.com/openai",
        "https://reddit.com/r/Python/",
        "https://news.ycombinator.com/news",
        "https://t.me/demo",
        "https://bsky.app/profile/alice.bsky.social",
        "https://threads.com/@alice",
        "https://mastodon.social/@alice",
    ]

    for target in targets:
        result = inspect_target(target)
        assert "unsupported_input" in result["risks"], target


def test_preflight_keeps_supported_social_post_paths_convertible():
    from omd._preflight import inspect_target

    targets = [
        "https://x.com/openai/status/1234567890",
        "https://x.com/i/status/1234567890",
        "https://reddit.com/r/Python/comments/abc123/title/",
        "https://www.reddit.com/r/LocalLLM/s/9fC4efLJQB",
        "https://redd.it/abc123",
        "https://news.ycombinator.com/item?id=12345",
        "https://t.me/demo_channel/42",
        "https://bsky.app/profile/alice.bsky.social/post/abc123",
        "https://threads.com/@alice/post/abc123",
        "https://mastodon.social/@alice/114000000000000001",
    ]

    for target in targets:
        result = inspect_target(target)
        assert "unsupported_input" not in result["risks"], target


def test_preflight_reports_platform_cookie_strategy_boundaries():
    from omd._preflight import inspect_target

    instagram = inspect_target("https://www.instagram.com/reel/abc123/")
    reddit = inspect_target("https://www.reddit.com/r/Python/comments/abc123/title/")
    x_post = inspect_target("https://x.com/openai/status/1234567890")
    threads = inspect_target("https://threads.com/@alice/post/abc123")

    assert instagram["metadata"]["cookie_strategy"] == "browser_or_cookies_txt_optional"
    assert instagram["metadata"]["cookie_browser_supported"] is True
    assert reddit["metadata"]["cookie_strategy"] == "public_only_no_cookie_passthrough"
    assert reddit["metadata"]["cookie_browser_supported"] is False
    assert x_post["metadata"]["cookie_strategy"] == "public_only_no_cookie_passthrough"
    assert x_post["metadata"]["cookie_browser_supported"] is False
    assert threads["metadata"]["cookie_strategy"] == "public_only_no_cookie_passthrough"
    assert threads["metadata"]["cookie_browser_supported"] is False


def test_preflight_reddit_describes_op_only_default_scope():
    from omd._preflight import inspect_target

    result = inspect_target("https://www.reddit.com/r/Python/comments/abc123/title/")

    assert result["metadata"]["comment_scope"] == "op"
    assert any("OP only by default" in warning for warning in result["warnings"])
    assert not any("captures top comments" in warning for warning in result["warnings"])
