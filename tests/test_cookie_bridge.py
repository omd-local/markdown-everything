from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from omd._cookies import (
    DOUYIN_COOKIE_DOMAINS,
    XHS_COOKIE_DOMAINS,
    CookieFileError,
    inspect_cookie_file,
    require_cookie_header,
    write_scoped_cookie_jar,
)


def _cookie_line(
    domain: str,
    *,
    include_subdomains: bool = True,
    path: str = "/",
    secure: bool = True,
    expires: int = 0,
    name: str = "sessionid",
    value: str = "secret-value",
    http_only: bool = False,
) -> str:
    prefix = "#HttpOnly_" if http_only else ""
    include = "TRUE" if include_subdomains else "FALSE"
    secure_value = "TRUE" if secure else "FALSE"
    return (
        f"{prefix}{domain}\t{include}\t{path}\t{secure_value}\t"
        f"{expires}\t{name}\t{value}\n"
    )


def _write_cookies(path: Path, *rows: str) -> Path:
    path.write_text(
        "# Netscape HTTP Cookie File\n" + "".join(rows),
        encoding="utf-8",
    )
    return path


def test_cookie_inspection_distinguishes_missing_format_domain_and_expiry(tmp_path):
    missing = tmp_path / "missing.txt"
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("not a netscape row\n", encoding="utf-8")
    wrong = _write_cookies(
        tmp_path / "wrong.txt",
        _cookie_line("example.com"),
    )
    expired = _write_cookies(
        tmp_path / "expired.txt",
        _cookie_line(".douyin.com", expires=10),
    )

    assert inspect_cookie_file(None, DOUYIN_COOKIE_DOMAINS)["status"] == "not_provided"
    assert inspect_cookie_file(missing, DOUYIN_COOKIE_DOMAINS)["status"] == "missing"
    assert inspect_cookie_file(invalid, DOUYIN_COOKIE_DOMAINS)["status"] == "invalid_format"
    assert inspect_cookie_file(wrong, DOUYIN_COOKIE_DOMAINS)["status"] == "wrong_domain"
    assert inspect_cookie_file(expired, DOUYIN_COOKIE_DOMAINS, now=20)["status"] == "expired"


def test_cookie_inspection_accepts_unicode_path_and_http_only_rows(tmp_path):
    douyin = _write_cookies(
        tmp_path / "抖音 登录 cookies.txt",
        _cookie_line(".douyin.com", http_only=True),
    )
    xhs = _write_cookies(
        tmp_path / "小红书 登录 cookies.txt",
        _cookie_line("subdomain.rednote.com", name="a1", value="token"),
        _cookie_line(".xhscdn.com", name="cdn", value="media"),
    )

    assert inspect_cookie_file(douyin, DOUYIN_COOKIE_DOMAINS)["status"] == "found"
    assert inspect_cookie_file(xhs, XHS_COOKIE_DOMAINS)["status"] == "found"
    assert require_cookie_header(
        douyin,
        DOUYIN_COOKIE_DOMAINS,
        platform="Douyin",
    ) == "sessionid=secret-value"
    assert require_cookie_header(
        xhs,
        XHS_COOKIE_DOMAINS,
        platform="XHS / Rednote",
    ) == "a1=token"


@pytest.mark.parametrize("domain", ["xhslink.com", "xhscdn.com"])
def test_xhs_shortlink_and_cdn_cookies_do_not_satisfy_auth_readiness(tmp_path, domain):
    cookies = _write_cookies(
        tmp_path / f"{domain}.txt",
        _cookie_line(f".{domain}"),
    )

    assert inspect_cookie_file(cookies, XHS_COOKIE_DOMAINS)["status"] == "wrong_domain"


def test_cookie_header_can_be_scoped_to_the_authenticated_request_host(tmp_path):
    xhs = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".xiaohongshu.com", name="web", value="page"),
        _cookie_line(".rednote.com", name="other", value="do-not-send"),
    )

    assert require_cookie_header(
        xhs,
        XHS_COOKIE_DOMAINS,
        platform="XHS / Rednote",
        request_url="https://www.xiaohongshu.com/explore/abc",
    ) == "web=page"


def test_cookie_header_honors_domain_path_secure_and_specific_name_precedence(tmp_path):
    cookies = _write_cookies(
        tmp_path / "scoped.txt",
        _cookie_line(".xiaohongshu.com", path="/", name="sid", value="root"),
        _cookie_line(
            ".xiaohongshu.com",
            path="/explore",
            name="sid",
            value="specific",
        ),
        _cookie_line(
            "xiaohongshu.com",
            include_subdomains=False,
            name="exact_only",
            value="root-host",
        ),
        _cookie_line(
            ".xiaohongshu.com",
            path="/other",
            name="other_path",
            value="skip",
        ),
        _cookie_line(
            ".xiaohongshu.com",
            secure=False,
            name="plain_ok",
            value="included",
        ),
    )

    header = require_cookie_header(
        cookies,
        XHS_COOKIE_DOMAINS,
        platform="XHS / Rednote",
        request_url="https://www.xiaohongshu.com/explore/abc",
    )

    assert header == "sid=specific; plain_ok=included"
    assert "root-host" not in header
    assert "other_path" not in header


def test_cookie_parser_rejects_header_delimiters_and_malformed_names(tmp_path):
    bad_name = _write_cookies(
        tmp_path / "bad-name.txt",
        _cookie_line(".douyin.com", name="bad name"),
    )
    bad_value = _write_cookies(
        tmp_path / "bad-value.txt",
        _cookie_line(".douyin.com", value="first; forged=second"),
    )

    assert inspect_cookie_file(bad_name, DOUYIN_COOKIE_DOMAINS)["status"] == "invalid_format"
    assert inspect_cookie_file(bad_value, DOUYIN_COOKIE_DOMAINS)["status"] == "invalid_format"


def test_cookie_parser_ignores_malformed_rows_when_safe_active_rows_remain(tmp_path):
    mixed = _write_cookies(
        tmp_path / "mixed-export.txt",
        _cookie_line(".douyin.com", name="", value="ignored-empty-name"),
        "not\ta\tnetscape\trow\n",
        _cookie_line(
            ".douyin.com",
            name="usable_session",
            value="active-value",
        ),
    )

    assert inspect_cookie_file(mixed, DOUYIN_COOKIE_DOMAINS)["status"] == "found"
    assert require_cookie_header(
        mixed,
        DOUYIN_COOKIE_DOMAINS,
        platform="Douyin",
    ) == "usable_session=active-value"

    jar = write_scoped_cookie_jar(
        mixed,
        DOUYIN_COOKIE_DOMAINS,
        tmp_path / "scoped.txt",
        platform="Douyin",
    )
    jar_text = jar.read_text(encoding="utf-8")
    assert "usable_session\tactive-value" in jar_text
    assert "ignored-empty-name" not in jar_text


def test_cookie_parser_keeps_domain_and_expiry_results_after_skipping_bad_rows(tmp_path):
    wrong_domain = _write_cookies(
        tmp_path / "wrong-domain-with-bad-row.txt",
        _cookie_line(".douyin.com", name=""),
        _cookie_line("example.com"),
    )
    expired = _write_cookies(
        tmp_path / "expired-with-bad-row.txt",
        _cookie_line(".douyin.com", name=""),
        _cookie_line(".douyin.com", expires=10),
    )

    assert inspect_cookie_file(wrong_domain, DOUYIN_COOKIE_DOMAINS)["status"] == "wrong_domain"
    assert inspect_cookie_file(expired, DOUYIN_COOKIE_DOMAINS, now=20)["status"] == "expired"


def test_douyin_runtime_accepts_valid_cookie_export(tmp_path, monkeypatch):
    from omd import reel

    cookies = _write_cookies(
        tmp_path / "douyin.txt",
        _cookie_line(
            ".douyin.com",
            name="shared_session",
            value="allowed-value",
            http_only=True,
        ),
        _cookie_line(
            "iesdouyin.com",
            include_subdomains=False,
            name="other_host",
            value="must-not-flatten",
        ),
        _cookie_line(
            "v.douyin.com",
            include_subdomains=False,
            name="shortlink_host",
            value="must-not-flatten-shortlink",
        ),
        _cookie_line(
            ".douyin.com",
            path="/other",
            name="other_path",
            value="path-scoped-value",
        ),
        _cookie_line(".rednote.com", name="unrelated", value="never-copy-this"),
    )
    original = cookies.read_bytes()
    captured = {}

    def fake_run(cmd, text, capture_output, **_kwargs):
        if "--list-extractors" in cmd:
            return SimpleNamespace(returncode=0, stdout="Douyin\nYoutube\n", stderr="")
        jar = Path(cmd[cmd.index("--cookies") + 1])
        captured["cmd"] = cmd
        captured["jar"] = jar
        captured["jar_text"] = jar.read_text(encoding="utf-8")
        captured["jar_mode"] = jar.stat().st_mode & 0o777
        (tmp_path / "reel.mp3").write_bytes(b"audio")
        (tmp_path / "reel.info.json").write_text(
            json.dumps({"extractor_key": "Douyin", "title": "clip"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reel, "require", lambda command: command)
    monkeypatch.setattr(reel.subprocess, "run", fake_run)
    monkeypatch.setattr(
        reel,
        "expand_douyin_short_url",
        lambda _url: "https://www.douyin.com/video/123",
    )

    audio, info, cover = reel.download_douyin_ytdlp(
        "https://v.douyin.com/demo/",
        tmp_path,
        str(cookies),
    )

    assert audio.name == "reel.mp3"
    assert info["extractor_key"] == "Douyin"
    assert cover is None
    assert captured["jar_mode"] == 0o600
    assert "allowed-value" in captured["jar_text"]
    assert "must-not-flatten" in captured["jar_text"]
    assert "path-scoped-value" in captured["jar_text"]
    assert "never-copy-this" not in captured["jar_text"]
    assert str(cookies) not in captured["cmd"]
    assert "allowed-value" not in captured["cmd"]
    assert captured["cmd"][0] == "yt-dlp"
    assert "f2" not in captured["cmd"]
    assert captured["cmd"][-1] == "https://www.douyin.com/video/123"
    assert not captured["jar"].exists()
    assert cookies.read_bytes() == original


def test_ytdlp_download_limit_uses_a_parser_compatible_byte_count(monkeypatch):
    from omd import _download, reel

    monkeypatch.setenv("OMD_MAX_DOWNLOAD_MB", "500")

    assert _download.ytdlp_max_filesize_arg() == "524288000"
    command = reel._build_ytdlp_command(
        "yt-dlp",
        "https://www.douyin.com/video/123",
        Path("/tmp/output"),
        cookies_browser=None,
        cookies_file=None,
        max_duration=None,
    )
    assert command[command.index("--max-filesize") + 1] == "524288000"


def test_xhs_extract_note_accepts_current_ef_streams_and_upgrades_media_https():
    from omd import xhs

    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "video",
                        "video": {
                            "media": {
                                "stream": {
                                    "EF4": [
                                        {
                                            "masterUrl": (
                                                "http://sns-v28.rednotecdn.com/video/demo.mp4"
                                            )
                                        }
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }

    note = xhs.extract_note(state, "note123")

    assert note["type"] == "video"
    assert note["video_url"] == "https://sns-v28.rednotecdn.com/video/demo.mp4"
    assert xhs._validate_xhs_media_url(note["video_url"]) == note["video_url"]


def test_xhs_video_selection_skips_rejected_master_and_uses_safe_backup():
    from omd import xhs

    video = {
        "media": {
            "stream": {
                "EF4": [
                    {
                        "masterUrl": "https://[malformed",
                        "backupUrls": [
                            "https://media.example.invalid/private.mp4",
                            "javascript:alert(1)",
                            "http://sns-v28.rednotecdn.com/video/backup.mp4",
                        ],
                    }
                ]
            }
        }
    }

    assert xhs._extract_video_url(video) == (
        "https://sns-v28.rednotecdn.com/video/backup.mp4"
    )


def test_xhs_media_cdn_does_not_expand_the_authenticated_page_boundary():
    from omd import xhs

    media = "https://sns-v28.rednotecdn.com/video/demo.mp4"

    assert xhs._validate_xhs_media_url(media) == media
    with pytest.raises(xhs.XHSRedirectError, match="No cookies were sent"):
        xhs._validate_xhs_request_url(media, carries_cookies=True)


def test_xhs_video_without_a_supported_stream_fails_instead_of_writing_partial_note(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    source = "https://www.rednote.com/discovery/item/note123"
    output = tmp_path / "partial.md"
    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".rednote.com"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            source,
            "-o",
            str(output),
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_authenticated_http_get",
        lambda *_args, **_kwargs: (200, {}, b"html", source),
    )
    monkeypatch.setattr(xhs, "parse_initial_state", lambda _html: {})
    monkeypatch.setattr(
        xhs,
        "extract_note",
        lambda *_args: {"type": "video", "video_url": "", "images": []},
    )

    with pytest.raises(SystemExit):
        xhs.main()

    event = json.loads(capsys.readouterr().err)
    assert event["kind"] == "xhs_video_unavailable"
    assert "supported video stream" in event["message"]
    assert not output.exists()
    _events.configure(False)


def test_xhs_media_download_failure_has_cookie_independent_recovery_copy(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    source = "https://www.rednote.com/discovery/item/note123"
    output = tmp_path / "partial.md"
    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".rednote.com"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            source,
            "-o",
            str(output),
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_authenticated_http_get",
        lambda *_args, **_kwargs: (200, {}, b"html", source),
    )
    monkeypatch.setattr(xhs, "parse_initial_state", lambda _html: {})
    monkeypatch.setattr(
        xhs,
        "extract_note",
        lambda *_args: {
            "type": "video",
            "video_url": "https://sns-v28.rednotecdn.com/video/demo.mp4",
            "images": [],
        },
    )
    monkeypatch.setattr(xhs, "download_to", lambda *_args: (_ for _ in ()).throw(OSError()))

    with pytest.raises(SystemExit):
        xhs.main()

    event = json.loads(capsys.readouterr().err)
    assert event["kind"] == "xhs_media_download_failed"
    assert "network connection" in event["message"]
    assert "local storage" in event["message"]
    assert "cookies" not in event["message"].lower()
    assert not output.exists()
    _events.configure(False)


def test_xhs_empty_transcription_fails_instead_of_writing_video_without_transcript(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    source = "https://www.rednote.com/discovery/item/note123"
    output = tmp_path / "partial.md"
    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".rednote.com"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            source,
            "-o",
            str(output),
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_authenticated_http_get",
        lambda *_args, **_kwargs: (200, {}, b"html", source),
    )
    monkeypatch.setattr(xhs, "parse_initial_state", lambda _html: {})
    monkeypatch.setattr(
        xhs,
        "extract_note",
        lambda *_args: {
            "type": "video",
            "video_url": "https://sns-v28.rednotecdn.com/video/demo.mp4",
            "images": [],
        },
    )
    monkeypatch.setattr(xhs, "download_to", lambda *_args: None)
    monkeypatch.setattr(xhs, "video_to_audio", lambda video: video.with_suffix(".mp3"))
    monkeypatch.setattr(
        xhs,
        "transcribe",
        lambda *_args, **_kwargs: {"text": "", "segments": [], "language": ""},
    )

    with pytest.raises(SystemExit):
        xhs.main()

    event = json.loads(capsys.readouterr().err)
    assert event["kind"] == "xhs_transcription_failed"
    assert "no readable speech" in event["message"]
    assert not output.exists()
    _events.configure(False)


class _DouyinFatal(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


def test_douyin_failure_does_not_leak_cookie_path_value_or_tool_output(
    tmp_path,
    monkeypatch,
):
    from omd import reel

    cookies = _write_cookies(
        tmp_path / "private cookie location.txt",
        _cookie_line(".douyin.com", value="do-not-leak-cookie-value"),
    )

    def fake_run(cmd, text, capture_output, **_kwargs):
        if "--list-extractors" in cmd:
            return SimpleNamespace(returncode=0, stdout="Douyin\n", stderr="")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"internal failure {cookies} do-not-leak-cookie-value",
        )

    monkeypatch.setattr(reel, "require", lambda command: command)
    monkeypatch.setattr(reel.subprocess, "run", fake_run)
    monkeypatch.setattr(
        reel,
        "expand_douyin_short_url",
        lambda _url: "https://www.douyin.com/video/123",
    )
    monkeypatch.setattr(
        reel,
        "_fatal_douyin",
        lambda kind, message: (_ for _ in ()).throw(_DouyinFatal(kind, message)),
    )

    with pytest.raises(_DouyinFatal) as exc_info:
        reel.download_douyin_ytdlp(
            "https://v.douyin.com/demo/",
            tmp_path,
            str(cookies),
        )

    assert exc_info.value.kind == "douyin_fetch_failed"
    assert str(cookies) not in exc_info.value.message
    assert "do-not-leak-cookie-value" not in exc_info.value.message
    assert "internal failure" not in exc_info.value.message


def test_douyin_uses_cookies_rejected_only_for_explicit_auth_evidence(
    tmp_path,
    monkeypatch,
):
    from omd import reel

    cookies = _write_cookies(
        tmp_path / "cookies.txt",
        _cookie_line(".douyin.com"),
    )

    def fake_run(cmd, text, capture_output, **_kwargs):
        if "--list-extractors" in cmd:
            return SimpleNamespace(returncode=0, stdout="Douyin\n", stderr="")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: Login required; export fresh cookies and try again",
        )

    monkeypatch.setattr(reel, "require", lambda command: command)
    monkeypatch.setattr(reel.subprocess, "run", fake_run)
    monkeypatch.setattr(
        reel,
        "expand_douyin_short_url",
        lambda _url: "https://www.douyin.com/video/123",
    )
    monkeypatch.setattr(
        reel,
        "_fatal_douyin",
        lambda kind, message: (_ for _ in ()).throw(_DouyinFatal(kind, message)),
    )

    with pytest.raises(_DouyinFatal) as exc_info:
        reel.download_douyin_ytdlp(
            "https://v.douyin.com/demo/",
            tmp_path,
            str(cookies),
        )

    assert exc_info.value.kind == "cookies_rejected"
    assert "export fresh" in exc_info.value.message.lower()


def test_douyin_missing_ytdlp_extractor_has_upgrade_action(tmp_path, monkeypatch):
    from omd import reel

    calls = []

    def fake_run(cmd, text, capture_output, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="Youtube\nTikTok\n", stderr="")

    monkeypatch.setattr(reel, "require", lambda command: command)
    monkeypatch.setattr(reel.subprocess, "run", fake_run)
    monkeypatch.setattr(
        reel,
        "_fatal_douyin",
        lambda kind, message: (_ for _ in ()).throw(_DouyinFatal(kind, message)),
    )

    with pytest.raises(_DouyinFatal) as exc_info:
        reel.download_douyin_ytdlp(
            "https://v.douyin.com/demo/",
            tmp_path,
            str(tmp_path / "unused.txt"),
        )

    assert exc_info.value.kind == "douyin_fetch_failed"
    assert "upgrade" in exc_info.value.message.lower()
    assert "yt-dlp" in exc_info.value.message
    assert len(calls) == 1


def test_douyin_shortlink_expands_anonymously_to_approved_canonical_url(monkeypatch):
    from omd import reel

    calls = []

    def fake_http_get(url):
        calls.append(url)
        return (
            302,
            {"Location": "https://www.douyin.com/video/123"},
            url,
        )

    monkeypatch.setattr(reel, "_douyin_http_get", fake_http_get)

    resolved = reel.expand_douyin_short_url("https://v.douyin.com/demo/")

    assert resolved == "https://www.douyin.com/video/123"
    assert calls == ["https://v.douyin.com/demo/"]


def test_douyin_shortlink_rejects_cross_origin_redirect_before_cookie_use(monkeypatch):
    from omd import reel

    calls = []

    def fake_http_get(url):
        calls.append(url)
        return 302, {"Location": "https://attacker.example/collect"}, url

    monkeypatch.setattr(reel, "_douyin_http_get", fake_http_get)

    with pytest.raises(reel.DouyinRedirectError, match="No cookies were sent"):
        reel.expand_douyin_short_url("https://v.douyin.com/demo/")

    assert calls == ["https://v.douyin.com/demo/"]


def test_douyin_shortlink_redirect_chain_is_bounded(monkeypatch):
    from omd import reel

    calls = []

    def fake_http_get(url):
        calls.append(url)
        return 302, {"Location": "https://v.douyin.com/next/"}, url

    monkeypatch.setattr(reel, "_douyin_http_get", fake_http_get)

    with pytest.raises(reel.DouyinRedirectError, match="too many redirects"):
        reel.expand_douyin_short_url("https://v.douyin.com/demo/")

    assert len(calls) == reel.MAX_DOUYIN_REDIRECTS


@pytest.mark.parametrize(
    "url",
    [
        "http://v.douyin.com/demo/",
        "https://v.douyin.com:444/demo/",
        "https://user@example.com@v.douyin.com/demo/",
    ],
)
def test_douyin_shortlink_requires_https_default_port_and_no_userinfo(url):
    from omd import reel

    with pytest.raises(reel.DouyinRedirectError, match="No cookies were sent"):
        reel.expand_douyin_short_url(url)


def test_douyin_port_variants_still_use_guarded_adapter():
    from omd import cli, reel

    url = "https://v.douyin.com:444/demo/"

    assert cli.is_reel_url(url) is True
    assert cli.is_douyin_url(url) is True
    assert reel.is_douyin(url) is True


def test_guarded_douyin_routing_does_not_broaden_instagram_cookie_routing():
    from omd import cli

    url = "https://www.instagram.com:444/reel/abc/"

    assert cli.is_reel_url(url) is False
    assert cli.is_instagram_url(url) is False


def test_cookie_inspection_reports_unreadable_without_path(monkeypatch, tmp_path):
    cookies = _write_cookies(
        tmp_path / "private.txt",
        _cookie_line(".douyin.com"),
    )
    original_open = Path.open

    def deny_open(self, *args, **kwargs):
        if self == cookies:
            raise PermissionError("private location")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_open)
    result = inspect_cookie_file(cookies, DOUYIN_COOKIE_DOMAINS)

    assert result["status"] == "unreadable"
    assert str(cookies) not in json.dumps(result)


def test_cookie_status_and_errors_do_not_leak_value_or_full_path(tmp_path):
    cookies = _write_cookies(
        tmp_path / "private-cookie-location.txt",
        _cookie_line("example.com", value="do-not-leak-this"),
    )
    result = inspect_cookie_file(cookies, DOUYIN_COOKIE_DOMAINS)

    rendered = json.dumps(result)
    assert "do-not-leak-this" not in rendered
    assert str(cookies) not in rendered
    with pytest.raises(CookieFileError) as exc_info:
        require_cookie_header(cookies, DOUYIN_COOKIE_DOMAINS, platform="Douyin")
    assert exc_info.value.kind == "cookies_wrong_domain"
    assert "do-not-leak-this" not in str(exc_info.value)
    assert str(cookies) not in str(exc_info.value)


def test_cookie_inspection_bounds_file_size(tmp_path, monkeypatch):
    from omd import _cookies

    monkeypatch.setattr(_cookies, "MAX_COOKIE_FILE_BYTES", 16)
    cookies = tmp_path / "cookies.txt"
    cookies.write_bytes(b"x" * 17)

    assert inspect_cookie_file(cookies, DOUYIN_COOKIE_DOMAINS)["status"] == "invalid_format"


def test_xhs_shortlink_rejects_non_platform_redirect_before_cookie_use(monkeypatch):
    from omd import xhs

    calls = []

    def fake_http_get(
        url: str,
        cookie_str: str = "",
        redirect: bool = True,
        **_kwargs,
    ):
        calls.append((url, cookie_str, redirect))
        return 302, {"Location": "https://attacker.example/collect"}, b"", url

    monkeypatch.setattr(xhs, "_http_get", fake_http_get)

    with pytest.raises(xhs.XHSRedirectError, match="No cookies were sent"):
        xhs.expand_short_url("https://xhslink.com/a/demo")
    assert calls == [("https://xhslink.com/a/demo", "", False)]


def test_xhs_shortlink_rejects_cdn_destination_before_authenticated_fetch(monkeypatch):
    from omd import xhs

    calls = []

    def fake_http_get(
        url: str,
        cookie_str: str = "",
        redirect: bool = True,
        **_kwargs,
    ):
        calls.append((url, cookie_str, redirect))
        return 302, {"Location": "https://cdn.xhscdn.com/media/abc"}, b"", url

    monkeypatch.setattr(xhs, "_http_get", fake_http_get)

    with pytest.raises(xhs.XHSRedirectError, match="No cookies were sent"):
        xhs.expand_short_url("https://xhslink.com/a/demo")

    assert calls == [("https://xhslink.com/a/demo", "", False)]


def test_xhs_shortlink_probe_never_reads_response_body(monkeypatch):
    from omd import xhs

    class Response:
        status = 302
        headers = {"Location": "https://www.xiaohongshu.com/explore/abc"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://xhslink.com/a/demo"

        def read(self, *_args):
            raise AssertionError("shortlink probe must not read a response body")

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_args: Opener())

    assert xhs.expand_short_url("https://xhslink.com/a/demo") == (
        "https://www.xiaohongshu.com/explore/abc"
    )


def test_xhs_page_body_has_content_length_and_streaming_caps(monkeypatch):
    from omd import xhs

    monkeypatch.setattr(xhs, "MAX_XHS_PAGE_BYTES", 8)

    class TooLargeByHeader:
        headers = {"Content-Length": "9"}

        def read(self, *_args):
            raise AssertionError("known oversized body must not be read")

    with pytest.raises(xhs.XHSFetchError, match="safe HTML size limit"):
        xhs._bounded_page_body(TooLargeByHeader(), read_body=True)

    class TooLargeWhileReading:
        headers = {}
        requested = None

        def read(self, requested):
            self.requested = requested
            return b"x" * requested

    response = TooLargeWhileReading()
    with pytest.raises(xhs.XHSFetchError, match="safe HTML size limit"):
        xhs._bounded_page_body(response, read_body=True)
    assert response.requested == 9


@pytest.mark.parametrize("failure", [urllib.error.URLError("private URL"), OSError("private path")])
def test_xhs_network_failures_are_safe_and_structured(monkeypatch, failure):
    from omd import xhs

    class Opener:
        def open(self, *_args, **_kwargs):
            raise failure

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_args: Opener())

    with pytest.raises(xhs.XHSFetchError) as exc_info:
        xhs._http_get("https://www.xiaohongshu.com/explore/abc")
    message = str(exc_info.value)
    assert "private URL" not in message
    assert "private path" not in message
    assert "xiaohongshu.com/explore/abc" not in message


def test_xhs_network_failure_emits_fetch_error_without_secrets(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    cookies = _write_cookies(
        tmp_path / "private-cookie-path.txt",
        _cookie_line(".xiaohongshu.com", value="private-cookie-value"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            "https://www.xiaohongshu.com/explore/secret-note",
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_http_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            xhs.XHSFetchError("XHS / Rednote could not reach the platform. Check the network, then retry.")
        ),
    )

    with pytest.raises(SystemExit):
        xhs.main()
    event = json.loads(capsys.readouterr().err)
    rendered = json.dumps(event)
    assert event["kind"] == "xhs_fetch_failed"
    assert "secret-note" not in rendered
    assert str(cookies) not in rendered
    assert "private-cookie-value" not in rendered
    _events.configure(False)


def test_xhs_shortlink_network_failure_emits_redirect_error_without_secrets(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    cookies = _write_cookies(
        tmp_path / "private-cookie-path.txt",
        _cookie_line(".xiaohongshu.com", value="private-cookie-value"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            "https://xhslink.com/a/secret-shortlink",
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_http_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            xhs.XHSFetchError("XHS / Rednote could not reach the platform. Check the network, then retry.")
        ),
    )

    with pytest.raises(SystemExit):
        xhs.main()
    event = json.loads(capsys.readouterr().err)
    rendered = json.dumps(event)
    assert event["kind"] == "xhs_redirect_rejected"
    assert "secret-shortlink" not in rendered
    assert str(cookies) not in rendered
    assert "private-cookie-value" not in rendered
    _events.configure(False)


def _wait_for_path(path: Path, *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path.name}")


def _start_blocked_douyin_capture(tmp_path: Path, marker: Path) -> subprocess.Popen:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir(exist_ok=True)
    fake_ytdlp = tool_dir / "yt-dlp"
    script = (
        "import os, pathlib, sys, time\n"
        "if '--list-extractors' in sys.argv:\n"
        "    print('Douyin')\n"
        "    raise SystemExit(0)\n"
        "jar = pathlib.Path(sys.argv[sys.argv.index('--cookies') + 1])\n"
        "pathlib.Path(os.environ['OMD_TEST_JAR_MARKER']).write_text(str(jar))\n"
        "time.sleep(60)\n"
    )
    fake_ytdlp.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable)
        + " -c " + shlex.quote(script) + ' "$@"\n',
        encoding="utf-8",
    )
    fake_ytdlp.chmod(0o755)
    cookies = _write_cookies(
        tmp_path / "douyin-cookies.txt",
        _cookie_line(".douyin.com", value="private-cookie-value"),
    )
    temp_root = tmp_path / "private-temp"
    temp_root.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "OMD_TOOL_PATH": str(tool_dir),
            "OMD_TEST_JAR_MARKER": str(marker),
            "OMD_CONFIG": str(tmp_path / "missing-config.json"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "TMPDIR": str(temp_root),
            "PATH": str(tool_dir) + os.pathsep + environment.get("PATH", ""),
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "omd.reel",
            "https://www.douyin.com/video/123",
            "--cookies",
            str(cookies),
            "--json-events",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_douyin_private_cookie_jar_is_removed_after_sigterm(tmp_path):
    marker = tmp_path / "sigterm-jar.txt"
    process = _start_blocked_douyin_capture(tmp_path, marker)
    try:
        _wait_for_path(marker)
        jar = Path(marker.read_text(encoding="utf-8"))
        assert jar.is_file()
        assert jar.stat().st_mode & 0o777 == 0o600
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        assert process.returncode != 0
        assert not jar.parent.exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_douyin_startup_prunes_private_cookie_jar_left_by_sigkill(tmp_path):
    marker = tmp_path / "sigkill-jar.txt"
    process = _start_blocked_douyin_capture(tmp_path, marker)
    try:
        _wait_for_path(marker)
        jar = Path(marker.read_text(encoding="utf-8"))
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        assert jar.is_file()

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "TMPDIR": str(tmp_path / "private-temp"),
            }
        )
        cleanup = subprocess.run(
            [sys.executable, "-m", "omd.reel", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert cleanup.returncode == 0
        assert not jar.parent.exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


class _RedirectFixtureResponse:
    def __init__(self, url: str, status: int, headers: dict, payload: bytes = b""):
        self.url = url
        self.status = status
        self.headers = headers
        self.payload = payload
        self.read_sizes: list[int] = []
        self.closed = False

    def geturl(self):
        return self.url

    def read(self, size: int = -1):
        if size < 0:
            raise AssertionError("redirect/body reads must always be bounded")
        self.read_sizes.append(size)
        payload, self.payload = self.payload, b""
        return payload

    def close(self):
        self.closed = True


def test_xhs_manual_http_redirect_does_not_read_intermediate_or_forward_cookie(
    monkeypatch,
):
    from omd import xhs

    first = _RedirectFixtureResponse(
        "https://www.xiaohongshu.com/explore/abc",
        302,
        {"Location": "https://www.rednote.com/explore/abc"},
    )
    second = _RedirectFixtureResponse(
        "https://www.rednote.com/explore/abc",
        200,
        {},
        b"page",
    )
    responses = iter([first, second])
    requests = []

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request, timeout))
            return next(responses)

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_args: Opener())

    status, _, body, final = xhs._http_get(
        "https://www.xiaohongshu.com/explore/abc",
        "sessionid=do-not-forward",
    )

    assert (status, body, final) == (
        200,
        b"page",
        "https://www.rednote.com/explore/abc",
    )
    assert first.read_sizes == []
    assert second.read_sizes == [xhs.MAX_XHS_PAGE_BYTES + 1]
    assert requests[0][0].get_header("Cookie") == "sessionid=do-not-forward"
    assert requests[1][0].get_header("Cookie") is None
    assert first.closed is True
    assert second.closed is True


def test_xhs_manual_http_redirect_rejects_malicious_destination(monkeypatch):
    from omd import xhs

    first = _RedirectFixtureResponse(
        "https://www.xiaohongshu.com/explore/abc",
        302,
        {"Location": "https://xiaohongshu.com.attacker.example/collect"},
    )

    class Opener:
        def open(self, *_args, **_kwargs):
            return first

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_args: Opener())

    with pytest.raises(xhs.XHSRedirectError, match="No cookies were sent"):
        xhs._http_get("https://www.xiaohongshu.com/explore/abc")
    assert first.read_sizes == []
    assert first.closed is True


def test_xhs_media_redirect_is_manual_and_never_reads_intermediate_body(
    monkeypatch,
    tmp_path,
):
    from omd import xhs

    first = _RedirectFixtureResponse(
        "https://cdn.xhscdn.com/media/original",
        302,
        {"Location": "https://assets.xhscdn.com/media/final"},
    )
    second = _RedirectFixtureResponse(
        "https://assets.xhscdn.com/media/final",
        200,
        {"Content-Length": "5"},
        b"media",
    )
    responses = iter([first, second])
    requests = []

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request, timeout))
            return next(responses)

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_args: Opener())
    destination = tmp_path / "media.bin"

    xhs.download_to("https://cdn.xhscdn.com/media/original", destination)

    assert destination.read_bytes() == b"media"
    assert first.read_sizes == []
    assert second.read_sizes and all(size > 0 for size in second.read_sizes)
    assert all(request.get_header("Cookie") is None for request, _ in requests)
    assert [request.full_url for request, _ in requests] == [
        "https://cdn.xhscdn.com/media/original",
        "https://assets.xhscdn.com/media/final",
    ]
    assert first.closed is True
    assert second.closed is True


@pytest.mark.parametrize(
    "url",
    [
        "http://www.xiaohongshu.com/explore/abc",
        "https://www.xiaohongshu.com:444/explore/abc",
        "https://user@example.com@www.xiaohongshu.com/explore/abc",
        "https://cdn.xhscdn.com/media/abc",
    ],
)
def test_xhs_cookie_requests_require_auth_host_https_default_port_and_no_userinfo(url):
    from omd.xhs import XHSRedirectError, _validate_xhs_request_url

    with pytest.raises(XHSRedirectError, match="No cookies were sent"):
        _validate_xhs_request_url(url, carries_cookies=True)


def test_xhs_cdn_url_is_allowed_only_without_cookies():
    from omd.xhs import _validate_xhs_request_url

    url = "https://cdn.xhscdn.com/media/abc"

    assert _validate_xhs_request_url(url) == url


def test_xhs_authenticated_redirect_rejects_nondefault_port_before_target_request(
    tmp_path,
    monkeypatch,
):
    from omd import xhs

    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".xiaohongshu.com", name="xhs_session", value="xhs-secret"),
        _cookie_line(".rednote.com", name="red_session", value="red-secret"),
    )
    calls = []

    def fake_http_get(url: str, cookie_str: str = "", redirect: bool = True):
        calls.append((url, cookie_str, redirect))
        return (
            302,
            {"Location": "https://www.rednote.com:444/explore/abc"},
            b"",
            url,
        )

    monkeypatch.setattr(xhs, "_http_get", fake_http_get)

    with pytest.raises(xhs.XHSRedirectError, match="No cookies were sent"):
        xhs._authenticated_http_get(
            "https://www.xiaohongshu.com/explore/abc",
            str(cookies),
        )

    assert calls == [
        (
            "https://www.xiaohongshu.com/explore/abc",
            "xhs_session=xhs-secret",
            False,
        )
    ]


def test_xhs_authenticated_redirect_rebuilds_cookie_header_for_each_origin(
    tmp_path,
    monkeypatch,
):
    from omd import xhs

    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".xiaohongshu.com", name="xhs_session", value="xhs-secret"),
        _cookie_line(".rednote.com", name="red_session", value="red-secret"),
    )
    calls = []

    def fake_http_get(url: str, cookie_str: str = "", redirect: bool = True):
        calls.append((url, cookie_str, redirect))
        if len(calls) == 1:
            return 302, {"Location": "https://www.rednote.com/explore/abc"}, b"", url
        return 200, {}, b"ok", url

    monkeypatch.setattr(xhs, "_http_get", fake_http_get)

    status, _, body, final = xhs._authenticated_http_get(
        "https://www.xiaohongshu.com/explore/abc",
        str(cookies),
    )

    assert status == 200
    assert body == b"ok"
    assert final == "https://www.rednote.com/explore/abc"
    assert calls == [
        (
            "https://www.xiaohongshu.com/explore/abc",
            "xhs_session=xhs-secret",
            False,
        ),
        (
            "https://www.rednote.com/explore/abc",
            "red_session=red-secret",
            False,
        ),
    ]


def test_xhs_runtime_requires_cookies_instead_of_anonymous_fallback(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["omd-xhs", "https://www.xiaohongshu.com/explore/abc", "--json-events"],
    )

    with pytest.raises(SystemExit):
        xhs.main()
    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "error"
    assert event["kind"] == "cookies_not_provided"
    assert "anonymous" not in event["message"].lower()
    assert "choose" in event["message"].lower()
    _events.configure(False)


def test_xhs_runtime_reports_rejected_cookies_as_actionable_json(
    monkeypatch,
    capsys,
    tmp_path,
):
    from omd import _events, xhs

    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".xiaohongshu.com"),
    )
    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            "https://www.xiaohongshu.com/explore/abc",
            "--cookies",
            str(cookies),
            "--json-events",
        ],
    )
    monkeypatch.setattr(
        xhs,
        "_http_get",
        lambda *_args, **_kwargs: (403, {}, b"", "https://www.xiaohongshu.com/explore/abc"),
    )

    with pytest.raises(SystemExit):
        xhs.main()
    event = json.loads(capsys.readouterr().err)
    assert event["kind"] == "cookies_rejected"
    assert "sign in again" in event["message"].lower()
    assert str(cookies) not in event["message"]
    _events.configure(False)


def test_xhs_extract_note_never_substitutes_an_unrelated_feed_entry(monkeypatch):
    from omd import _events, xhs

    class FatalEvent(RuntimeError):
        def __init__(self, kind, message):
            self.kind = kind
            super().__init__(message)

    def fail(kind, message):
        raise FatalEvent(kind, message)

    monkeypatch.setattr(_events, "fatal", fail)
    state = {
        "note": {
            "noteDetailMap": {
                "unrelated": {"note": {"title": "Wrong post"}},
            }
        }
    }

    with pytest.raises(FatalEvent) as exc_info:
        xhs.extract_note(state, "requested")

    assert exc_info.value.kind == "xhs_note_unavailable"
    assert "requested post" in str(exc_info.value)



def test_xhs_main_retries_once_when_rednote_returns_placeholder_state(
    monkeypatch,
    tmp_path,
):
    from omd import _events, xhs

    source = "https://www.rednote.com/discovery/item/deadbeef"
    output = tmp_path / "captured.md"
    cookies = _write_cookies(
        tmp_path / "xhs.txt",
        _cookie_line(".rednote.com"),
    )
    placeholder = (
        b'<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":'
        b'{"undefined":{}}},"translate":{"originalCommentIds":new Set([])}}'
        b"</script>"
    )
    target = (
        b'<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":'
        b'{"deadbeef":{"note":{"type":"normal","title":"Target post",'
        b'"desc":"Body","user":{},"interactInfo":{},"imageList":[],'
        b'"tagList":[]}}}},"translate":{"originalCommentIds":new Set([])}}'
        b"</script>"
    )
    responses = iter(
        [
            (200, {}, placeholder, source),
            (200, {}, target, source),
        ]
    )
    calls = []

    def fetch(url, cookies_file):
        calls.append((url, cookies_file))
        return next(responses)

    _events.configure(False)
    monkeypatch.setenv("OMD_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omd-xhs",
            source,
            "-o",
            str(output),
            "--cookies",
            str(cookies),
        ],
    )
    monkeypatch.setattr(xhs, "_authenticated_http_get", fetch)

    assert xhs.main() == 0
    assert len(calls) == 2
    assert output.exists()
    rendered = output.read_text(encoding="utf-8")
    assert "# Target post" in rendered
    assert "**Note ID**: deadbeef" in rendered
    assert "Wrong post" not in rendered
    _events.configure(False)
