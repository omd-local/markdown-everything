"""Environment diagnostics for OMD."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from omd._language import (
    DEFAULT_OCR_LANGUAGE,
    MissingOcrLanguagePacks,
    OCR_LANGUAGE_ENV,
    OcrLanguageError,
    OcrLanguageProbeError,
    probe_tesseract_languages,
    validate_ocr_language_packs,
)


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
    ("yt-dlp", "YouTube/TikTok/Instagram/Bilibili/Douyin downloads"),
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
    ("douyin", ["yt-dlp", "ffmpeg", "mlx_whisper"], [], "Douyin downloads and transcription"),
    ("xhs", ["tesseract", "ffmpeg", "mlx_whisper"], [], "Xiaohongshu image OCR and video transcription"),
    ("ui", [], [], "Browser UI deferred pending compatible security updates; use OMD Home or the CLI"),
    ("polish", ["ollama"], [], "Local Ollama polish"),
    ("mcp", [], [], "MCP stdio server"),
]

PYTHON_PACKAGES = [
    ("markitdown", "document/web/data conversion"),
    ("pip_audit", "dependency audit"),
]

TESSERACT_LANGUAGE_PRESETS = (
    DEFAULT_OCR_LANGUAGE,
    "chi_sim+eng",
    "chi_tra+eng",
)
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _safe_probe_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return 5.0
    if not math.isfinite(value):
        return 5.0
    return min(max(value, 0.1), 30.0)


def resolve_tool_executable(
    tool: str,
    *,
    environ: Mapping[str, str] | None = None,
    python_executable: str | Path | None = None,
    home: str | Path | None = None,
) -> str | None:
    """Resolve a local executable without invoking a shell.

    PATH retains normal precedence. ``OMD_TOOL_PATH`` then supports OMD's
    managed tools before the Python environment and established install paths.
    Only a plain executable name is accepted so configured directories cannot
    be combined with traversal or arbitrary paths.
    """
    if not isinstance(tool, str) or not _TOOL_NAME_RE.fullmatch(tool):
        return None

    environment = os.environ if environ is None else environ
    if environ is None:
        path = shutil.which(tool)
    else:
        path = shutil.which(tool, path=environment.get("PATH", ""))
    if path:
        return path

    interpreter = Path(sys.executable if python_executable is None else python_executable)
    home_path = Path.home() if home is None else Path(home).expanduser()
    directories = [
        *[
            Path(value).expanduser()
            for value in environment.get("OMD_TOOL_PATH", "").split(os.pathsep)
            if value
        ],
        interpreter.parent,
        interpreter.resolve().parent,
        home_path / ".local/share/omd/toolenv-py312/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    seen: set[Path] = set()
    for directory in directories:
        if directory in seen:
            continue
        seen.add(directory)
        candidate = directory / tool
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _which_tool(tool: str) -> str | None:
    """Backward-compatible alias for older callers and monkeypatches."""
    return resolve_tool_executable(tool)


def _active_capture_config(
    requested_language: str | None,
    *,
    environ: Mapping[str, str],
    configured: object | None,
) -> object | None:
    if configured is not None:
        return configured
    if requested_language is not None or OCR_LANGUAGE_ENV in environ:
        return None

    from omd.config import config_path, load_config

    path = config_path(environ=environ)
    return load_config(path, environ=environ) if path.is_file() else None


def tesseract_readiness(
    requested_language: str | None = None,
    *,
    executable: str | None = None,
    timeout: float = 5.0,
    environ: Mapping[str, str] | None = None,
    config: object | None = None,
    language_source: str | None = None,
) -> dict[str, object]:
    """Return bounded Tesseract readiness for the effective OCR configuration."""
    environment = os.environ if environ is None else environ
    tool = executable or (
        _which_tool("tesseract")
        if environ is None
        else resolve_tool_executable("tesseract", environ=environment)
    )

    configured: object | None = config
    raw_requested: object = requested_language
    source = language_source.strip() if language_source and language_source.strip() else None
    effective_language: str | None = None
    try:
        configured = _active_capture_config(
            requested_language,
            environ=environment,
            configured=config,
        )
        from omd.config import resolve_capture_config

        resolved = resolve_capture_config(
            configured,
            explicit_ocr_language=requested_language,
            environ=environment,
        )
        effective_language = resolved.ocr_language
        source = source or resolved.ocr_language_source
        if requested_language is not None:
            raw_requested = requested_language
        elif resolved.ocr_language_source == "environment":
            raw_requested = environment.get(OCR_LANGUAGE_ENV)
        elif resolved.ocr_language_source == "config":
            raw_requested = getattr(getattr(configured, "capture", None), "ocr_language", None)
        else:
            raw_requested = DEFAULT_OCR_LANGUAGE
    except (OcrLanguageError, OSError, ValueError) as exc:
        if source is None:
            if requested_language is not None:
                source = "explicit"
            elif OCR_LANGUAGE_ENV in environment:
                source = "environment"
                raw_requested = environment.get(OCR_LANGUAGE_ENV)
            elif configured is not None:
                source = "config"
            else:
                from omd.config import config_path

                source = (
                    "config"
                    if config_path(environ=environment).is_file()
                    else "default"
                )
        return {
            "ready": False,
            "available": tool is not None,
            "executable": tool,
            "version": None,
            "languages": [],
            "requested_language": raw_requested,
            "effective_language": None,
            "language_source": source,
            "missing_languages": [],
            "presets": {},
            "error": str(exc),
        }

    if tool is None:
        return {
            "ready": False,
            "available": False,
            "executable": None,
            "version": None,
            "languages": [],
            "requested_language": raw_requested,
            "effective_language": effective_language,
            "language_source": source,
            "missing_languages": [],
            "presets": {},
            "error": "tesseract executable not found",
        }

    probe_timeout = _safe_probe_timeout(timeout)
    deadline = time.monotonic() + probe_timeout
    try:
        version_result = subprocess.run(
            [tool, "--version"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=probe_timeout,
        )
        version_lines = (version_result.stdout or version_result.stderr).strip().splitlines()
        version = version_lines[0] if version_result.returncode == 0 and version_lines else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired([tool, "--version", "--list-langs"], probe_timeout)
        languages = probe_tesseract_languages(tool, timeout=remaining)
    except subprocess.TimeoutExpired:
        return {
            "ready": False,
            "available": True,
            "executable": tool,
            "version": None,
            "languages": [],
            "requested_language": raw_requested,
            "effective_language": effective_language,
            "language_source": source,
            "missing_languages": [],
            "presets": {},
            "error": "tesseract probe timed out",
        }
    except OcrLanguageProbeError as exc:
        return {
            "ready": False,
            "available": True,
            "executable": tool,
            "version": None,
            "languages": [],
            "requested_language": raw_requested,
            "effective_language": effective_language,
            "language_source": source,
            "missing_languages": [],
            "presets": {},
            "error": str(exc),
        }
    except (OSError, UnicodeError) as exc:
        return {
            "ready": False,
            "available": True,
            "executable": tool,
            "version": None,
            "languages": [],
            "requested_language": raw_requested,
            "effective_language": effective_language,
            "language_source": source,
            "missing_languages": [],
            "presets": {},
            "error": f"could not run tesseract probe: {exc}",
        }

    sorted_languages = sorted(languages)
    preset_status = {
        preset: all(part in languages for part in preset.split("+"))
        for preset in TESSERACT_LANGUAGE_PRESETS
    }
    missing: list[str] = []
    error: str | None = None
    try:
        validate_ocr_language_packs(
            effective_language,
            available_languages=languages,
        )
    except MissingOcrLanguagePacks as exc:
        missing = list(exc.missing)
        error = str(exc)
    return {
        "ready": error is None,
        "available": True,
        "executable": tool,
        "version": version,
        "languages": sorted_languages,
        "requested_language": raw_requested,
        "effective_language": effective_language,
        "language_source": source,
        "missing_languages": missing,
        "presets": preset_status,
        "error": error,
    }


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


def ytdlp_douyin_readiness(
    executable: str | None = None,
    *,
    timeout: float = 15,
) -> dict[str, object]:
    """Report whether the selected yt-dlp build advertises Douyin support."""
    tool = executable or resolve_tool_executable("yt-dlp")
    if not tool:
        return {
            "ready": False,
            "available": False,
            "error": "yt-dlp is not installed",
            "action": "Install or upgrade yt-dlp, then run Check setup again.",
        }
    try:
        result = subprocess.run(
            [tool, "--list-extractors"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=_safe_probe_timeout(timeout),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "ready": False,
            "available": True,
            "error": "yt-dlp could not report its installed extractors",
            "action": "Upgrade or reinstall yt-dlp, then run Check setup again.",
        }
    supports_douyin = result.returncode == 0 and any(
        line.strip().split(maxsplit=1)[0].casefold() == "douyin"
        for line in result.stdout.splitlines()
        if line.strip()
    )
    return {
        "ready": supports_douyin,
        "available": True,
        "error": None if supports_douyin else "installed yt-dlp has no Douyin extractor",
        "action": None
        if supports_douyin
        else "Upgrade yt-dlp, then run Check setup again.",
    }


def _douyin_extractor_status_for_checks(
    checks: list[Check],
) -> dict[str, object] | None:
    yt_dlp_check = next(
        (
            check
            for check in checks
            if check.name == "yt-dlp" and check.kind == "tool" and check.ok
        ),
        None,
    )
    if yt_dlp_check is None:
        return None
    return ytdlp_douyin_readiness(executable=yt_dlp_check.detail)


def capability_status(
    checks: list[Check],
    *,
    tesseract: Mapping[str, object] | None = None,
    douyin_extractor: Mapping[str, object] | None = None,
) -> list[Capability]:
    capabilities: list[Capability] = []
    for name, required, optional, detail in CAPABILITY_REQUIREMENTS:
        missing_required = _missing(required, checks)
        if (
            "tesseract" in required
            and "tesseract" not in missing_required
            and tesseract is not None
            and tesseract.get("ready") is False
        ):
            missing_required.append(
                "tesseract-language-packs"
                if tesseract.get("missing_languages")
                else "tesseract-readiness"
            )
        if (
            name == "douyin"
            and "yt-dlp" not in missing_required
            and douyin_extractor is not None
            and not bool(douyin_extractor.get("ready"))
        ):
            missing_required.append("yt-dlp:douyin-extractor")
        missing_optional = _missing(optional, checks)
        capabilities.append(
            Capability(
                name=name,
                ok=name != "ui" and not missing_required,
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
    tesseract: Mapping[str, object] | None = None,
    douyin_extractor: Mapping[str, object] | None = None,
) -> dict[str, object]:
    checks = run_checks() if checks is None else checks
    needs_tools = [str(tool) for tool in preflight.get("needs_tools", []) or []]
    available_tools = [tool for tool in needs_tools if _requirement_ok(tool, checks)]
    missing_tools = [tool for tool in needs_tools if tool not in available_tools]
    detected_type = str(preflight.get("detected_type") or "")
    douyin_extractor_status = douyin_extractor
    if detected_type == "douyin_url" and "yt-dlp" in available_tools:
        if douyin_extractor_status is None:
            douyin_extractor_status = _douyin_extractor_status_for_checks(checks)
        if douyin_extractor_status is None:
            douyin_extractor_status = {
                "ready": False,
                "available": True,
                "error": "yt-dlp executable could not be resolved for extractor inspection",
                "action": "Upgrade or reinstall yt-dlp, then run Check setup again.",
            }
        if not bool(douyin_extractor_status.get("ready")):
            missing_tools.append("yt-dlp:douyin-extractor")
    risks = [str(risk) for risk in preflight.get("risks", []) or []]
    warnings = [str(warning) for warning in preflight.get("warnings", []) or []]
    blocking_risks = sorted(
        set(risks) & {"missing_input", "unsupported_input", "unsafe_url"}
    )
    metadata = preflight.get("metadata")
    cookie_domains: list[str] = []
    cookie_request_url: str | None = None
    cookie_runtime_recheck = False
    if isinstance(metadata, Mapping):
        cookie_domains = [
            str(domain)
            for domain in metadata.get("cookie_domains", []) or []
            if str(domain).strip()
        ]
        cookie_scope = metadata.get("cookie_validation_scope")
        if cookie_scope == "request_url":
            metadata_url = metadata.get("url")
            if isinstance(metadata_url, str) and metadata_url.strip():
                cookie_request_url = metadata_url
        cookie_runtime_recheck = bool(metadata.get("cookie_runtime_recheck"))
    cookie_status = _cookie_file_status(
        cookies_file,
        cookie_domains,
        request_url=cookie_request_url,
    )
    missing_auth: list[str] = []
    if bool(preflight.get("needs_cookies")) and cookie_status["status"] != "found":
        missing_auth.append("cookies_file")
    needs_tesseract = "tesseract" in needs_tools
    tesseract_status = tesseract
    if needs_tesseract and tesseract_status is None:
        tool_check = next(
            (
                check
                for check in checks
                if check.name == "tesseract" and check.kind == "tool" and check.ok
            ),
            None,
        )
        tesseract_status = tesseract_readiness(
            executable=tool_check.detail if tool_check is not None else None,
        )
    tesseract_ready = (
        not needs_tesseract
        or (tesseract_status is not None and bool(tesseract_status.get("ready")))
    )
    return {
        "ready": (
            not missing_tools
            and not blocking_risks
            and not missing_auth
            and tesseract_ready
        ),
        "missing_tools": missing_tools,
        "available_tools": available_tools,
        "needs_cookies": bool(preflight.get("needs_cookies")),
        "cookies_file": cookie_status,
        "cookie_runtime_recheck": cookie_runtime_recheck,
        "cookies_from_browser": (cookies_from_browser or "").strip() or None,
        "missing_auth": missing_auth,
        "warnings": warnings,
        "risks": risks,
        "blocking_risks": blocking_risks,
        "tesseract": tesseract_status,
        "douyin_extractor": douyin_extractor_status,
    }


def _cookie_file_status(
    cookies_file: str | Path | None,
    cookie_domains: list[str] | tuple[str, ...] = (),
    *,
    request_url: str | None = None,
) -> dict[str, object]:
    from omd._cookies import inspect_cookie_file

    return inspect_cookie_file(
        cookies_file,
        cookie_domains,
        request_url=request_url,
    )


def checks_as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(check) for check in checks]


def render_text(
    checks: list[Check],
    *,
    tesseract: dict[str, object] | None = None,
    douyin_extractor: Mapping[str, object] | None = None,
) -> str:
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
    for capability in capability_status(
        checks,
        tesseract=tesseract,
        douyin_extractor=douyin_extractor,
    ):
        if capability.name == "ui":
            lines.append(f"- deferred {capability.name:26} {capability.detail}")
            continue
        mark = "ready" if capability.ok else "missing"
        missing_required = ", ".join(capability.missing_required) or "none"
        lines.append(f"- {mark:7} {capability.name:26} missing required: {missing_required}")
    if tesseract is not None:
        lines.extend(["", "Tesseract OCR"])
        if not tesseract.get("available"):
            lines.append("- missing tesseract executable")
        else:
            version = tesseract.get("version") or "version unknown"
            languages = tesseract.get("languages") or []
            lines.append(f"- version {version}")
            lines.append(
                f"- language packs ({len(languages)}): "
                + (", ".join(str(item) for item in languages) or "none detected")
            )
            requested = tesseract.get("requested_language")
            effective = tesseract.get("effective_language")
            source = tesseract.get("language_source")
            if requested is not None or effective is not None:
                lines.append(
                    f"- OCR language requested={requested or 'none'} "
                    f"effective={effective or 'none'} source={source or 'unknown'}"
                )
            if tesseract.get("error"):
                lines.append(f"- warning {tesseract['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check OMD local toolchain availability.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    checks = run_checks()
    tesseract = tesseract_readiness()
    douyin_extractor = _douyin_extractor_status_for_checks(checks)
    if args.json:
        print(
            json.dumps(
                {
                    "checks": checks_as_dicts(checks),
                    "capabilities": capabilities_as_dicts(
                        capability_status(
                            checks,
                            tesseract=tesseract,
                            douyin_extractor=douyin_extractor,
                        )
                    ),
                    "tesseract": tesseract,
                    "douyin_extractor": douyin_extractor,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            render_text(
                checks,
                tesseract=tesseract,
                douyin_extractor=douyin_extractor,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
