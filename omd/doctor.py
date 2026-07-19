"""Environment diagnostics for OMD."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    kind: str
    detail: str
    required_for: str


@dataclass
class Capability:
    name: str
    ok: bool
    required: list[str]
    optional: list[str]
    missing_required: list[str]
    missing_optional: list[str]
    detail: str


TOOLS = [
    ("ffmpeg", "audio/video extraction"),
    ("ffprobe", "audio duration and progress estimates"),
    ("tesseract", "image OCR"),
    ("yt-dlp", "YouTube/TikTok/Instagram/Bilibili downloads"),
    ("f2", "Douyin downloads"),
    ("ollama", "local transcript/Markdown polish"),
    ("mlx_whisper", "Apple Silicon transcription"),
    ("markitdown", "document/web/data conversion"),
]

CAPABILITY_REQUIREMENTS: list[tuple[str, list[str], list[str], str]] = [
    ("rmarkdown", [], [], "RMarkdown output wrapper"),
    ("public_social_posts", [], [], "WeChat, Reddit, X, Bluesky, Mastodon, Threads, Hacker News, and Telegram public posts"),
    ("documents_web_data", ["markitdown"], [], "Document, web, archive, and data-file conversion"),
    ("image_ocr", ["tesseract"], [], "Image OCR conversion"),
    ("audio_video_transcription", ["ffmpeg", "mlx_whisper"], ["ffprobe"], "Audio/video transcription"),
    ("douyin", ["f2", "ffmpeg", "mlx_whisper"], [], "Douyin downloads and transcription"),
    ("xhs", ["tesseract", "ffmpeg", "mlx_whisper"], [], "Xiaohongshu image OCR and video transcription"),
    ("ui", ["gradio"], [], "Gradio UI"),
    ("polish", ["ollama"], [], "Local Ollama polish"),
    ("mcp", [], [], "MCP stdio server"),
]

PYTHON_PACKAGES = [
    ("markitdown", "document/web/data conversion"),
    ("pip_audit", "dependency audit"),
    ("gradio", "omd-ui"),
]


def _which_tool(tool: str) -> str | None:
    path = shutil.which(tool)
    if path:
        return path
    candidates = [
        Path(sys.executable).parent,
        Path(sys.executable).resolve().parent,
        *[
            Path(p).expanduser()
            for p in os.environ.get("OMD_TOOL_PATH", "").split(os.pathsep)
            if p
        ],
        Path.home() / ".local/share/omd/toolenv-py312/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    for directory in candidates:
        candidate = directory / tool
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def run_checks() -> list[Check]:
    checks: list[Check] = []
    for tool, required_for in TOOLS:
        path = _which_tool(tool)
        checks.append(
            Check(
                name=tool,
                ok=path is not None,
                kind="tool",
                detail=path or "not on PATH",
                required_for=required_for,
            )
        )
    for package, required_for in PYTHON_PACKAGES:
        spec = importlib.util.find_spec(package)
        checks.append(
            Check(
                name=package,
                ok=spec is not None,
                kind="python",
                detail="importable" if spec else "not importable",
                required_for=required_for,
            )
        )
    return checks


def checks_by_name(checks: list[Check]) -> dict[str, Check]:
    """Return a best-status lookup by check name.

    Some capabilities can be satisfied by either a tool or Python package with
    the same name. Prefer an available check when names collide.
    """
    by_name: dict[str, Check] = {}
    for check in checks:
        existing = by_name.get(check.name)
        if existing is None or (check.ok and not existing.ok):
            by_name[check.name] = check
    return by_name


def _requirement_ok(requirement: str, checks: list[Check]) -> bool:
    return any(check.name == requirement and check.ok for check in checks)


def _missing(requirements: list[str], checks: list[Check]) -> list[str]:
    return [requirement for requirement in requirements if not _requirement_ok(requirement, checks)]


def capability_status(checks: list[Check]) -> list[Capability]:
    capabilities: list[Capability] = []
    for name, required, optional, detail in CAPABILITY_REQUIREMENTS:
        missing_required = _missing(required, checks)
        missing_optional = _missing(optional, checks)
        capabilities.append(
            Capability(
                name=name,
                ok=not missing_required,
                required=list(required),
                optional=list(optional),
                missing_required=missing_required,
                missing_optional=missing_optional,
                detail=detail,
            )
        )
    return capabilities


def capabilities_as_dicts(capabilities: list[Capability]) -> list[dict]:
    return [asdict(capability) for capability in capabilities]


def readiness_for_preflight(
    preflight: dict[str, object],
    checks: list[Check] | None = None,
    *,
    cookies_file: str | Path | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, object]:
    checks = run_checks() if checks is None else checks
    needs_tools = [str(tool) for tool in preflight.get("needs_tools", []) or []]
    available_tools = [tool for tool in needs_tools if _requirement_ok(tool, checks)]
    missing_tools = [tool for tool in needs_tools if tool not in available_tools]
    risks = [str(risk) for risk in preflight.get("risks", []) or []]
    warnings = [str(warning) for warning in preflight.get("warnings", []) or []]
    blocking_risks = sorted(set(risks) & {"missing_input", "unsupported_input"})
    cookie_status = _cookie_file_status(cookies_file)
    missing_auth: list[str] = []
    if bool(preflight.get("needs_cookies")) and cookie_status["status"] != "found":
        missing_auth.append("cookies_file")
    return {
        "ready": not missing_tools and not blocking_risks and not missing_auth,
        "missing_tools": missing_tools,
        "available_tools": available_tools,
        "needs_cookies": bool(preflight.get("needs_cookies")),
        "cookies_file": cookie_status,
        "cookies_from_browser": (cookies_from_browser or "").strip() or None,
        "missing_auth": missing_auth,
        "warnings": warnings,
        "risks": risks,
        "blocking_risks": blocking_risks,
    }


def _cookie_file_status(cookies_file: str | Path | None) -> dict[str, object]:
    if cookies_file is None or not str(cookies_file).strip():
        return {"path": None, "status": "not_provided"}
    path = Path(cookies_file).expanduser()
    return {
        "path": str(path),
        "status": "found" if path.is_file() else "missing",
    }


def checks_as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(check) for check in checks]


def render_text(checks: list[Check]) -> str:
    lines = ["OMD doctor\n"]
    for check in checks:
        mark = "ok" if check.ok else "missing"
        lines.append(f"- {mark:7} {check.name:14} {check.detail} ({check.required_for})")
    missing = [check for check in checks if not check.ok]
    lines.append("")
    lines.append(
        "All checks passed." if not missing else f"{len(missing)} optional/required capability checks missing."
    )
    lines.extend(["", "Capabilities"])
    for capability in capability_status(checks):
        mark = "ready" if capability.ok else "missing"
        missing_required = ", ".join(capability.missing_required) or "none"
        lines.append(f"- {mark:7} {capability.name:26} missing required: {missing_required}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check OMD local toolchain availability.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    checks = run_checks()
    if args.json:
        print(
            json.dumps(
                {
                    "checks": checks_as_dicts(checks),
                    "capabilities": capabilities_as_dicts(capability_status(checks)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_text(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
