from __future__ import annotations

import os
import socket
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest


def _dns_answer(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


def test_public_url_policy_rejects_loopback_ip():
    from omd._network_policy import validate_public_http_url

    with pytest.raises(ValueError, match="public internet"):
        validate_public_http_url("http://127.0.0.1:7860/private")


def test_public_url_policy_rejects_private_dns_answer():
    from omd._network_policy import validate_public_http_url

    with pytest.raises(ValueError, match="public internet"):
        validate_public_http_url(
            "https://example.test/private",
            resolver=lambda *_args, **_kwargs: _dns_answer("10.0.0.8"),
        )


def test_public_url_policy_accepts_public_dns_answer():
    from omd._network_policy import validate_public_http_url

    validate_public_http_url(
        "https://example.test/article",
        resolver=lambda *_args, **_kwargs: _dns_answer("93.184.216.34"),
    )


def test_public_url_policy_rejects_private_redirect():
    from omd._network_policy import PublicOnlyRedirectHandler

    handler = PublicOnlyRedirectHandler(
        resolver=lambda *_args, **_kwargs: _dns_answer("169.254.169.254")
    )

    with pytest.raises(ValueError, match="public internet"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://metadata.test/latest",
        )


def test_ai_transport_redirect_handler_refuses_all_redirects():
    from omd._network_policy import NoRedirectHandler

    handler = NoRedirectHandler()

    assert handler.redirect_request(None, None, 307, "Temporary Redirect", {}, "https://other.test") is None


def test_public_resolver_guard_rejects_dns_rebinding_to_private_address(monkeypatch):
    from omd import _network_policy

    monkeypatch.setattr(
        _network_policy,
        "_SYSTEM_GETADDRINFO",
        lambda *_args, **_kwargs: _dns_answer("169.254.169.254"),
        raising=False,
    )

    with pytest.raises(socket.gaierror, match="public internet"):
        _network_policy.public_only_getaddrinfo("example.test", 443)


def test_public_network_scope_restores_process_resolver():
    from omd._network_policy import public_network_policy_scope, public_only_getaddrinfo

    original = socket.getaddrinfo

    with public_network_policy_scope():
        assert socket.getaddrinfo is public_only_getaddrinfo

    assert socket.getaddrinfo is original


def test_ollama_policy_requires_explicit_opt_in_for_remote_host():
    from omd._network_policy import validate_ollama_host

    with pytest.raises(ValueError, match="explicit opt-in"):
        validate_ollama_host("https://models.example.com")


def test_ollama_policy_requires_https_for_opted_in_remote_host():
    from omd._network_policy import validate_ollama_host

    with pytest.raises(ValueError, match="HTTPS"):
        validate_ollama_host("http://models.example.com", allow_remote=True)


def test_ollama_policy_accepts_opted_in_https_remote_host():
    from omd._network_policy import validate_ollama_host

    validate_ollama_host("https://models.example.com", allow_remote=True)


def test_inbox_ai_task_rejects_remote_ollama_host_before_preview_or_execution():
    from omd.ui import _ai_note_task

    with pytest.raises(ValueError, match="explicit opt-in"):
        _ai_note_task(
            "Local Ollama",
            "qwen2.5:7b-instruct",
            "https://models.example.com",
        )


def test_xhs_shortlink_expansion_does_not_send_cookies(monkeypatch):
    from omd import xhs

    calls = []

    def fake_http_get(url: str, cookie_str: str = "", redirect: bool = True):
        calls.append((url, cookie_str, redirect))
        return 302, {"Location": "https://www.xiaohongshu.com/explore/abc123"}, b"", url

    monkeypatch.setattr(xhs, "_http_get", fake_http_get)

    expanded = xhs.expand_short_url("https://xhslink.com/a/abc")

    assert expanded == "https://www.xiaohongshu.com/explore/abc123"
    assert calls == [("https://xhslink.com/a/abc", "", False)]


def test_write_atomic_uses_unique_temps_and_cleans_failures(tmp_path, monkeypatch):
    from omd import _io

    target = tmp_path / "out.md"
    seen_temps: list[Path] = []

    def fail_replace(src, _dst):
        seen_temps.append(Path(src))
        raise OSError("simulated replace failure")

    monkeypatch.setattr(_io.os, "replace", fail_replace)

    for content in ("first\n", "second\n"):
        try:
            _io.write_atomic(target, content)
        except OSError:
            pass

    assert len(seen_temps) == 2
    assert seen_temps[0] != seen_temps[1]
    assert all(p.parent == tmp_path for p in seen_temps)
    assert not list(tmp_path.glob(".out.md.*.tmp"))
    assert not target.exists()


def test_write_atomic_bytes_uses_restrictive_permissions(tmp_path):
    from omd._io import write_atomic_bytes

    target = tmp_path / "out.bin"

    write_atomic_bytes(target, b"secret")

    assert target.read_bytes() == b"secret"
    assert oct(os.stat(target).st_mode & 0o777) == "0o600"


def test_f2_cookie_value_is_written_to_private_temp_config_not_argv(tmp_path):
    from omd.reel import _build_f2_douyin_command, _write_f2_cookie_config

    cfg = _write_f2_cookie_config(tmp_path, "sid_guard=secret")
    cmd = _build_f2_douyin_command(
        "f2",
        cfg,
        "https://v.douyin.com/abc/",
        tmp_path,
    )

    assert "-k" not in cmd
    assert "sid_guard=secret" not in cmd
    assert str(cfg) in cmd
    assert cfg.read_text(encoding="utf-8").count("sid_guard=secret") == 1
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"


def test_download_f2_does_not_leave_cookie_config_in_keep_dir(tmp_path, monkeypatch):
    from omd import reel

    seen_cmd = {}

    def fake_run(cmd, input, text, capture_output=False):
        seen_cmd["cmd"] = cmd
        user_dir = tmp_path / "creator"
        user_dir.mkdir()
        (user_dir / "clip_music.mp3").write_bytes(b"audio")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reel, "require", lambda cmd: cmd)
    monkeypatch.setattr(reel, "cookies_txt_to_string", lambda *_a, **_kw: "sid_guard=secret")
    monkeypatch.setattr(reel.subprocess, "run", fake_run)

    audio, info, cover = reel.download_f2(
        "https://v.douyin.com/abc/",
        tmp_path,
        str(tmp_path / "uploaded-cookies.txt"),
    )

    assert audio.name == "clip_music.mp3"
    assert info["extractor_key"] == "Douyin"
    assert cover is None
    assert "sid_guard=secret" not in seen_cmd["cmd"]
    assert not list(tmp_path.rglob("f2-dy-cookie.yaml"))


def test_download_f2_ignores_macos_appledouble_video_sidecars(tmp_path, monkeypatch):
    from omd import reel

    seen_ffmpeg = {}

    def fake_subprocess_run(cmd, input=None, text=None, capture_output=False, **_kw):
        if cmd[0] == "f2":
            user_dir = tmp_path / "creator"
            user_dir.mkdir()
            (user_dir / "._clip_video.mp4").write_bytes(b"sidecar")
            (user_dir / "clip_video.mp4").write_bytes(b"video")
            (user_dir / "._clip_desc.txt").write_text("sidecar desc", encoding="utf-8")
            (user_dir / "clip_desc.txt").write_text("real desc", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess.run: {cmd}")

    def fake_run(cmd, **_kw):
        seen_ffmpeg["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reel, "require", lambda cmd: cmd)
    monkeypatch.setattr(reel, "cookies_txt_to_string", lambda *_a, **_kw: "sid_guard=secret")
    monkeypatch.setattr(reel.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(reel, "run", fake_run)

    audio, info, cover = reel.download_f2(
        "https://v.douyin.com/abc/",
        tmp_path,
        str(tmp_path / "uploaded-cookies.txt"),
    )

    assert seen_ffmpeg["cmd"][3].endswith("clip_video.mp4")
    assert not Path(seen_ffmpeg["cmd"][3]).name.startswith("._")
    assert audio.name == "clip_audio.mp3"
    assert info["extractor_key"] == "Douyin"
    assert info["description"] == "real desc"
    assert cover is None


class FakeResponse:
    def __init__(self, chunks: list[bytes], content_length: int | None = None):
        self._chunks = list(chunks)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_podcast_download_rejects_oversized_content_length(tmp_path, monkeypatch):
    from omd import podcast

    monkeypatch.setenv("OMD_MAX_DOWNLOAD_MB", "1")
    monkeypatch.setattr(
        podcast.urllib.request,
        "urlopen",
        lambda *_a, **_kw: FakeResponse([], content_length=2 * 1024 * 1024),
    )
    dest = tmp_path / "episode.mp3"

    try:
        podcast.download_to("https://cdn.example.com/episode.mp3", dest)
    except ValueError as e:
        assert "above limit" in str(e)
    else:
        raise AssertionError("oversized podcast download was not rejected")

    assert not dest.exists()


def test_xhs_download_rejects_stream_that_exceeds_limit(tmp_path, monkeypatch):
    from omd import xhs

    monkeypatch.setenv("OMD_MAX_DOWNLOAD_MB", "1")
    chunks = [b"x" * (700 * 1024), b"y" * (700 * 1024)]
    monkeypatch.setattr(
        xhs.urllib.request,
        "urlopen",
        lambda *_a, **_kw: FakeResponse(chunks, content_length=None),
    )
    dest = tmp_path / "image.jpg"

    try:
        xhs.download_to("https://cdn.example.com/image.jpg", dest)
    except ValueError as e:
        assert "exceeded limit" in str(e)
    else:
        raise AssertionError("oversized XHS download stream was not rejected")

    assert not dest.exists()


def test_release_template_verifies_bundled_tarball_before_extracting():
    release = Path("packaging/stage2-templates/release.yml").read_text()

    assert "BUNDLED_CLI_SHA256" in release
    assert "shasum -a 256 -c -" in release
    assert "| tar xz" not in release


def test_markitdown_ocr_plugins_disabled_by_default(monkeypatch, capsys):
    from omd import markitdown_convert

    seen = {}
    markitdown_mod = ModuleType("markitdown")
    openai_mod = ModuleType("openai")

    class FakeMarkItDown:
        def __init__(self, **kwargs):
            seen["enable_plugins"] = kwargs["enable_plugins"]

        def convert(self, _input_path, llm_prompt):
            return SimpleNamespace(text_content="ocr text")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            pass

    markitdown_mod.MarkItDown = FakeMarkItDown
    openai_mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "markitdown", markitdown_mod)
    monkeypatch.setitem(sys.modules, "openai", openai_mod)

    rc = markitdown_convert.run_ocr(
        "scan.pdf",
        None,
        "http://localhost:11434/v1",
        "ollama",
        "gemma3:4b",
        None,
    )

    assert rc == 0
    assert seen["enable_plugins"] is False
    assert capsys.readouterr().out == "ocr text"


def test_markitdown_ocr_plugins_require_explicit_opt_in(monkeypatch):
    from omd import markitdown_convert

    seen = {}
    markitdown_mod = ModuleType("markitdown")
    openai_mod = ModuleType("openai")

    class FakeMarkItDown:
        def __init__(self, **kwargs):
            seen["enable_plugins"] = kwargs["enable_plugins"]

        def convert(self, _input_path, llm_prompt):
            return SimpleNamespace(text_content="")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            pass

    markitdown_mod.MarkItDown = FakeMarkItDown
    openai_mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "markitdown", markitdown_mod)
    monkeypatch.setitem(sys.modules, "openai", openai_mod)

    rc = markitdown_convert.run_ocr(
        "scan.pdf",
        None,
        "http://localhost:11434/v1",
        "ollama",
        "gemma3:4b",
        None,
        enable_plugins=True,
    )

    assert rc == 0
    assert seen["enable_plugins"] is True


def test_route_markitdown_cli_does_not_enable_plugins(monkeypatch, tmp_path):
    from omd import cli

    seen = {}

    def fake_which(cmd):
        assert cmd == "markitdown"
        return "/usr/local/bin/markitdown"

    def fake_run(cmd, capture_output, text):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="# doc\n", stderr="")

    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.route_markitdown("report.pdf", tmp_path / "report.md")

    assert rc == 0
    assert "--use-plugins" not in seen["cmd"]
    assert "-p" not in seen["cmd"]


def test_public_network_mode_does_not_pass_remote_url_to_markitdown(monkeypatch, tmp_path):
    from omd import cli, web_article

    seen = {}
    output = tmp_path / "article.md"
    monkeypatch.setenv("OMD_NETWORK_POLICY", "public")
    monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/local/bin/markitdown")
    monkeypatch.setattr(
        web_article,
        "fetch_public_fallback",
        lambda _url, _open: web_article.WebFallbackDocument(
            html="<h1>Public article</h1>",
            mode="browser_html",
            partial=False,
        ),
    )
    monkeypatch.setattr(cli, "_record_web_conversion", lambda *_args, **_kwargs: None)

    def fake_run(cmd, capture_output, text):
        seen["input"] = cmd[1]
        return SimpleNamespace(returncode=0, stdout="# Public article\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.route_markitdown("https://example.com/article", output)

    assert rc == 0
    assert not seen["input"].startswith(("http://", "https://"))
    assert output.read_text(encoding="utf-8") == "# Public article\n"
