from __future__ import annotations

from omd import doctor


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


def test_doctor_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction")
    ])

    rc = doctor.main(["--json"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"checks"' in out
    assert '"capabilities"' in out
    assert '"ffmpeg"' in out


def test_capability_status_reports_missing_required_tools():
    checks = [
        doctor.Check("f2", False, "tool", "not on PATH", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ]

    by_name = {capability.name: capability for capability in doctor.capability_status(checks)}

    assert by_name["rmarkdown"].ok is True
    assert by_name["douyin"].ok is False
    assert by_name["douyin"].missing_required == ["f2"]


def test_readiness_for_preflight_marks_missing_tools():
    preflight = {
        "needs_tools": ["f2", "ffmpeg", "mlx_whisper"],
        "needs_cookies": True,
        "warnings": ["cookies required"],
        "risks": ["auth_required"],
    }
    checks = [
        doctor.Check("f2", False, "tool", "not on PATH", "Douyin downloads"),
        doctor.Check("ffmpeg", True, "tool", "/bin/ffmpeg", "audio/video extraction"),
        doctor.Check("mlx_whisper", True, "tool", "/bin/mlx_whisper", "Apple Silicon transcription"),
    ]

    readiness = doctor.readiness_for_preflight(preflight, checks)

    assert readiness["ready"] is False
    assert readiness["missing_tools"] == ["f2"]
    assert readiness["available_tools"] == ["ffmpeg", "mlx_whisper"]
    assert readiness["needs_cookies"] is True
    assert readiness["missing_auth"] == ["cookies_file"]
    assert readiness["cookies_file"]["status"] == "not_provided"


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
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    preflight = {
        "needs_tools": ["f2", "ffmpeg", "mlx_whisper"],
        "needs_cookies": True,
        "warnings": [],
        "risks": ["auth_required"],
    }
    checks = [
        doctor.Check("f2", True, "tool", "/bin/f2", "Douyin downloads"),
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
