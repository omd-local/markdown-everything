from __future__ import annotations

import subprocess

import pytest

from omd import doctor
from omd.config import CaptureConfig, OMDConfig


def test_doctor_reports_tool_presence(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}" if name == "ffmpeg" else None)
    monkeypatch.setattr(doctor.Path, "is_file", lambda _self: False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "pip_audit" else None)

    checks = doctor.run_checks()

    by_name = {check.name: check for check in checks}
    assert by_name["ffmpeg"].ok is True
    assert by_name["ffmpeg"].detail == "/bin/ffmpeg"
    assert by_name["yt-dlp"].ok is False
    assert by_name["pip_audit"].ok is True


def test_resolve_tool_executable_honors_path_before_omd_tool_path(tmp_path):
    path_dir = tmp_path / "path-bin"
    omd_dir = tmp_path / "omd-bin"
    path_dir.mkdir()
    omd_dir.mkdir()
    path_tool = path_dir / "tesseract"
    omd_tool = omd_dir / "tesseract"
    path_tool.write_text("#!/bin/sh\n", encoding="utf-8")
    omd_tool.write_text("#!/bin/sh\n", encoding="utf-8")
    path_tool.chmod(0o755)
    omd_tool.chmod(0o755)

    resolved = doctor.resolve_tool_executable(
        "tesseract",
        environ={"PATH": str(path_dir), "OMD_TOOL_PATH": str(omd_dir)},
        python_executable=tmp_path / "python-bin" / "python",
        home=tmp_path,
    )

    assert resolved == str(path_tool)


def test_resolve_tool_executable_falls_back_to_omd_tool_path(tmp_path):
    omd_dir = tmp_path / "tools"
    omd_dir.mkdir()
    tool = omd_dir / "ffmpeg"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)

    resolved = doctor.resolve_tool_executable(
        "ffmpeg",
        environ={"PATH": "", "OMD_TOOL_PATH": str(omd_dir)},
        python_executable=tmp_path / "python-bin" / "python",
        home=tmp_path,
    )

    assert resolved == str(tool)


def test_resolve_tool_executable_rejects_paths_and_non_executable_files(tmp_path):
    tool_name = "omd-non-executable-probe"
    tool = tmp_path / tool_name
    tool.write_text("not executable", encoding="utf-8")

    assert doctor.resolve_tool_executable(
        "../ffmpeg",
        environ={"PATH": "", "OMD_TOOL_PATH": str(tmp_path)},
    ) is None
    assert doctor.resolve_tool_executable(
        tool_name,
        environ={"PATH": "", "OMD_TOOL_PATH": str(tmp_path)},
        python_executable=tmp_path / "python-bin" / "python",
        home=tmp_path,
    ) is None


def test_doctor_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction")
    ])
    monkeypatch.setattr(
        doctor,
        "tesseract_readiness",
        lambda: {"ready": False, "available": False, "error": "not found"},
    )

    rc = doctor.main(["--json"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"checks"' in out
    assert '"capabilities"' in out
    assert '"tesseract"' in out
    assert '"ffmpeg"' in out


def test_tesseract_readiness_reports_version_languages_and_presets(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n leptonica", "")
        return subprocess.CompletedProcess(
            argv,
            0,
            "List of available languages in /tmp/tessdata/ (3):\neng\nchi_sim\nosd\n",
            "",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness("chi_sim+eng", timeout=2)

    assert readiness["ready"] is True
    assert readiness["available"] is True
    assert readiness["version"] == "tesseract 5.5.1"
    assert readiness["languages"] == ["chi_sim", "eng", "osd"]
    assert readiness["requested_language"] == "chi_sim+eng"
    assert readiness["effective_language"] == "chi_sim+eng"
    assert readiness["language_source"] == "explicit"
    assert readiness["missing_languages"] == []
    assert readiness["presets"]["chi_sim+eng"] is True
    assert readiness["presets"]["chi_tra+eng"] is False


def test_tesseract_readiness_defaults_to_the_builtin_english_pack(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(argv, 0, "List of available languages (1):\nosd\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness()

    assert readiness["ready"] is False
    assert readiness["requested_language"] == "eng"
    assert readiness["missing_languages"] == ["eng"]


def test_tesseract_readiness_reports_missing_requested_packs(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(argv, 0, "List of available languages (1):\neng\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness("chi_sim+eng")

    assert readiness["ready"] is False
    assert readiness["missing_languages"] == ["chi_sim"]
    assert "brew install tesseract-lang" in readiness["error"]


def test_tesseract_readiness_resolves_environment_over_config(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")
    configured = OMDConfig(capture=CaptureConfig(ocr_language="deu"))

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(
            argv,
            0,
            "List of available languages (2):\nchi_sim\neng\n",
            "",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness(
        environ={"OMD_OCR_LANGUAGE": " chi_sim + eng "},
        config=configured,
    )

    assert readiness["requested_language"] == " chi_sim + eng "
    assert readiness["effective_language"] == "chi_sim+eng"
    assert readiness["language_source"] == "environment"
    assert readiness["ready"] is True


def test_tesseract_readiness_loads_the_active_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"schema_version": 1, "capture": {"ocr_language": "chi_sim+eng"}}',
        encoding="utf-8",
    )

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(argv, 0, "List of available languages (1):\neng\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness(
        executable="/bin/tesseract",
        environ={"OMD_CONFIG": str(config_path), "PATH": ""},
    )

    assert readiness["requested_language"] == "chi_sim+eng"
    assert readiness["effective_language"] == "chi_sim+eng"
    assert readiness["language_source"] == "config"
    assert readiness["missing_languages"] == ["chi_sim"]
    assert readiness["ready"] is False


def test_tesseract_readiness_reports_an_invalid_active_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"schema_version": 1, "capture": {"unknown": true}}', encoding="utf-8")
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    readiness = doctor.tesseract_readiness(
        environ={"OMD_CONFIG": str(config_path), "PATH": ""},
        executable="/bin/tesseract",
    )

    assert readiness["ready"] is False
    assert readiness["effective_language"] is None
    assert readiness["language_source"] == "config"
    assert "unknown capture field" in readiness["error"]


def test_tesseract_readiness_can_preserve_a_caller_resolved_source(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(argv, 0, "List of available languages (1):\neng\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    readiness = doctor.tesseract_readiness("eng", language_source="config")

    assert readiness["effective_language"] == "eng"
    assert readiness["language_source"] == "config"


def test_tesseract_readiness_contains_timeouts_at_the_boundary(monkeypatch):
    monkeypatch.setattr(doctor, "_which_tool", lambda name: "/bin/tesseract")

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["tesseract"], 0.1)

    monkeypatch.setattr(doctor.subprocess, "run", timed_out)

    readiness = doctor.tesseract_readiness("eng", timeout=0.1)

    assert readiness["ready"] is False
    assert readiness["available"] is True
    assert readiness["error"] == "tesseract probe timed out"


def test_render_text_includes_tesseract_version_and_pack_count():
    rendered = doctor.render_text(
        [],
        tesseract={
            "available": True,
            "version": "tesseract 5.5.1",
            "languages": ["chi_sim", "eng"],
            "error": None,
        },
    )

    assert "tesseract 5.5.1" in rendered
    assert "language packs (2): chi_sim, eng" in rendered


def test_capability_status_reports_missing_required_tools():
    checks = [
        doctor.Check("yt-dlp", False, "tool", "not on PATH", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ]

    by_name = {capability.name: capability for capability in doctor.capability_status(checks)}

    assert by_name["rmarkdown"].ok is True
    assert by_name["douyin"].ok is False
    assert by_name["douyin"].missing_required == ["yt-dlp"]


def test_capability_status_does_not_claim_ocr_ready_when_packs_are_missing():
    checks = [
        doctor.Check("tesseract", True, "tool", "/bin/tesseract", "image OCR"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "transcription"),
    ]
    tesseract = {
        "ready": False,
        "available": True,
        "missing_languages": ["chi_sim"],
    }

    by_name = {
        capability.name: capability
        for capability in doctor.capability_status(checks, tesseract=tesseract)
    }

    assert by_name["image_ocr"].ok is False
    assert by_name["image_ocr"].missing_required == ["tesseract-language-packs"]
    assert by_name["xhs"].ok is False
    assert by_name["xhs"].missing_required == ["tesseract-language-packs"]


def test_doctor_json_capabilities_reflect_tesseract_pack_readiness(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda: [
            doctor.Check("tesseract", True, "tool", "/bin/tesseract", "image OCR"),
        ],
    )
    monkeypatch.setattr(
        doctor,
        "tesseract_readiness",
        lambda: {
            "ready": False,
            "available": True,
            "missing_languages": ["chi_sim"],
            "error": "missing chi_sim",
        },
    )

    rc = doctor.main(["--json"])

    assert rc == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    image_ocr = next(item for item in payload["capabilities"] if item["name"] == "image_ocr")
    assert image_ocr["ok"] is False
    assert image_ocr["missing_required"] == ["tesseract-language-packs"]


def test_readiness_for_preflight_marks_missing_tools():
    preflight = {
        "needs_tools": ["yt-dlp", "ffmpeg", "mlx_whisper"],
        "needs_cookies": True,
        "warnings": ["cookies required"],
        "risks": ["auth_required"],
    }
    checks = [
        doctor.Check("yt-dlp", False, "tool", "not on PATH", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ]

    readiness = doctor.readiness_for_preflight(preflight, checks)

    assert readiness["ready"] is False
    assert readiness["missing_tools"] == ["yt-dlp"]
    assert readiness["available_tools"] == ["ffmpeg", "mlx_whisper"]
    assert readiness["needs_cookies"] is True
    assert readiness["missing_auth"] == ["cookies_file"]
    assert readiness["cookies_file"]["status"] == "not_provided"


def test_ytdlp_douyin_readiness_requires_advertised_extractor(monkeypatch):
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["yt-dlp", "--list-extractors"],
            returncode=0,
            stdout="Youtube\nTikTok\n",
            stderr="",
        ),
    )

    result = doctor.ytdlp_douyin_readiness("yt-dlp")

    assert result["ready"] is False
    assert result["available"] is True
    assert "Upgrade yt-dlp" in str(result["action"])


def test_douyin_readiness_blocks_old_ytdlp_even_when_tool_exists(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    preflight = {
        "detected_type": "douyin_url",
        "needs_tools": ["yt-dlp"],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["auth_required"],
        "metadata": {"cookie_domains": ["douyin.com"]},
    }
    checks = [
        doctor.Check("yt-dlp", True, "tool", "/bin/yt-dlp", "Douyin downloads"),
    ]

    readiness = doctor.readiness_for_preflight(
        preflight,
        checks,
        cookies_file=cookies,
        douyin_extractor={
            "ready": False,
            "available": True,
            "error": "installed yt-dlp has no Douyin extractor",
            "action": "Upgrade yt-dlp, then run Check setup again.",
        },
    )

    assert readiness["ready"] is False
    assert readiness["missing_tools"] == ["yt-dlp:douyin-extractor"]
    assert readiness["douyin_extractor"]["ready"] is False


def test_doctor_capability_blocks_old_ytdlp_douyin_extractor():
    checks = [
        doctor.Check("yt-dlp", True, "tool", "/bin/yt-dlp", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "transcription"),
    ]
    by_name = {
        capability.name: capability
        for capability in doctor.capability_status(
            checks,
            douyin_extractor={"ready": False},
        )
    }

    assert by_name["douyin"].ok is False
    assert by_name["douyin"].missing_required == ["yt-dlp:douyin-extractor"]


def test_readiness_accepts_markitdown_tool_or_package():
    preflight = {"needs_tools": ["markitdown"], "needs_cookies": False, "warnings": [], "risks": []}
    checks = [
        doctor.Check("markitdown", False, "tool", "not on PATH", "document conversion"),
        doctor.Check("markitdown", True, "python", "importable", "document conversion"),
    ]

    readiness = doctor.readiness_for_preflight(preflight, checks)

    assert readiness["ready"] is True
    assert readiness["missing_tools"] == []


def test_readiness_requires_cookie_file_when_preflight_needs_cookies(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    preflight = {
        "needs_tools": ["yt-dlp", "ffmpeg", "mlx_whisper"],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["auth_required"],
        "metadata": {"cookie_domains": ["douyin.com"]},
    }
    checks = [
        doctor.Check("yt-dlp", True, "tool", "/bin/yt-dlp", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ]

    missing = doctor.readiness_for_preflight(preflight, checks)
    ready = doctor.readiness_for_preflight(preflight, checks, cookies_file=cookies)

    assert missing["ready"] is False
    assert missing["missing_auth"] == ["cookies_file"]
    assert ready["ready"] is True
    assert ready["missing_auth"] == []
    assert ready["cookies_file"]["status"] == "found"
    assert ready["cookies_file"]["required_domains"] == ["douyin.com"]
    assert str(cookies) not in str(ready["cookies_file"])


def test_readiness_reports_cookie_format_domain_and_expiry_statuses(tmp_path):
    checks = [
        doctor.Check("yt-dlp", True, "tool", "/bin/yt-dlp", "Douyin downloads"),
    ]
    preflight = {
        "needs_tools": ["yt-dlp"],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["auth_required"],
        "metadata": {"cookie_domains": ["douyin.com"]},
    }
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("not a cookie row\n", encoding="utf-8")
    wrong_domain = tmp_path / "wrong.txt"
    wrong_domain.write_text(
        ".example.com\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    expired = tmp_path / "expired.txt"
    expired.write_text(
        ".douyin.com\tTRUE\t/\tTRUE\t1\tsessionid\tdemo\n",
        encoding="utf-8",
    )

    expected = {
        invalid: "invalid_format",
        wrong_domain: "wrong_domain",
        expired: "expired",
    }
    for cookie_file, status in expected.items():
        readiness = doctor.readiness_for_preflight(
            preflight,
            checks,
            cookies_file=cookie_file,
        )
        assert readiness["ready"] is False
        assert readiness["cookies_file"]["status"] == status
        assert readiness["missing_auth"] == ["cookies_file"]
        assert str(cookie_file) not in str(readiness["cookies_file"])


def test_xhs_direct_readiness_matches_actual_request_url(tmp_path):
    cookies = tmp_path / "xhs.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".rednote.com\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    preflight = {
        "needs_tools": [],
        "needs_cookies": True,
        "warnings": [],
        "risks": [],
        "metadata": {
            "url": "https://www.xiaohongshu.com/explore/abc",
            "cookie_domains": ["xiaohongshu.com", "rednote.com"],
            "cookie_validation_scope": "request_url",
            "cookie_runtime_recheck": False,
        },
    }

    wrong_host = doctor.readiness_for_preflight(preflight, [], cookies_file=cookies)
    preflight["metadata"]["url"] = "https://www.rednote.com/explore/abc"
    matching_host = doctor.readiness_for_preflight(preflight, [], cookies_file=cookies)

    assert wrong_host["ready"] is False
    assert wrong_host["cookies_file"]["status"] == "wrong_domain"
    assert wrong_host["missing_auth"] == ["cookies_file"]
    assert matching_host["ready"] is True
    assert matching_host["cookies_file"]["status"] == "found"
    assert matching_host["cookie_runtime_recheck"] is False


def test_xhs_shortlink_readiness_accepts_platform_cookie_and_marks_recheck(tmp_path):
    cookies = tmp_path / "xhs.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".rednote.com\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    preflight = {
        "needs_tools": [],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["redirect_resolution"],
        "metadata": {
            "url": "https://xhslink.com/a/abc",
            "cookie_domains": ["xiaohongshu.com", "rednote.com"],
            "cookie_validation_scope": "platform_until_redirect",
            "cookie_runtime_recheck": True,
        },
    }

    readiness = doctor.readiness_for_preflight(preflight, [], cookies_file=cookies)

    assert readiness["ready"] is True
    assert readiness["cookies_file"]["status"] == "found"
    assert readiness["cookie_runtime_recheck"] is True


@pytest.mark.parametrize("domain", ["xhslink.com", "xhscdn.com"])
def test_xhs_shortlink_readiness_rejects_non_auth_cookie_domains(tmp_path, domain):
    cookies = tmp_path / "xhs.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        f".{domain}\tTRUE\t/\tTRUE\t0\tsessionid\tdemo\n",
        encoding="utf-8",
    )
    preflight = {
        "needs_tools": [],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["redirect_resolution"],
        "metadata": {
            "url": "https://xhslink.com/a/abc",
            "cookie_domains": ["xiaohongshu.com", "rednote.com"],
            "cookie_validation_scope": "platform_until_redirect",
            "cookie_runtime_recheck": True,
        },
    }

    readiness = doctor.readiness_for_preflight(preflight, [], cookies_file=cookies)

    assert readiness["ready"] is False
    assert readiness["cookies_file"]["status"] == "wrong_domain"
    assert readiness["missing_auth"] == ["cookies_file"]
    assert readiness["cookie_runtime_recheck"] is True


def test_readiness_for_preflight_blocks_missing_tesseract_language_packs():
    preflight = {
        "needs_tools": ["tesseract"],
        "needs_cookies": False,
        "warnings": [],
        "risks": [],
    }
    checks = [
        doctor.Check("tesseract", True, "tool", "/bin/tesseract", "image OCR"),
    ]
    tesseract = {
        "ready": False,
        "available": True,
        "executable": "/bin/tesseract",
        "requested_language": "chi_sim+eng",
        "effective_language": "chi_sim+eng",
        "language_source": "config",
        "languages": ["eng"],
        "missing_languages": ["chi_sim"],
        "error": "missing chi_sim",
    }

    readiness = doctor.readiness_for_preflight(
        preflight,
        checks,
        tesseract=tesseract,
    )

    assert readiness["ready"] is False
    assert readiness["missing_tools"] == []
    assert readiness["tesseract"] is tesseract


def test_readiness_for_preflight_probes_the_resolved_tesseract_path(monkeypatch):
    preflight = {
        "needs_tools": ["tesseract"],
        "needs_cookies": False,
        "warnings": [],
        "risks": [],
    }
    checks = [
        doctor.Check("tesseract", True, "tool", "/custom/bin/tesseract", "image OCR"),
    ]
    seen = []

    def fake_readiness(*, executable):
        seen.append(executable)
        return {"ready": True, "executable": executable}

    monkeypatch.setattr(doctor, "tesseract_readiness", fake_readiness)

    readiness = doctor.readiness_for_preflight(preflight, checks)

    assert readiness["ready"] is True
    assert readiness["tesseract"]["executable"] == "/custom/bin/tesseract"
    assert seen == ["/custom/bin/tesseract"]


@pytest.mark.parametrize("gradio_installed", [False, True])
def test_doctor_reports_deferred_ui_even_with_leftover_gradio(gradio_installed):
    checks = [doctor.Check("gradio", gradio_installed, "python", "fixture", "legacy UI")]
    ui = next(item for item in doctor.capability_status(checks) if item.name == "ui")

    assert ui.ok is False
    assert ui.required == ui.missing_required == []
    assert "deferred" in ui.detail
    assert "deferred ui" in doctor.render_text(checks)
    assert all(name != "gradio" for name, _ in doctor.PYTHON_PACKAGES)
