"""Machine-readable OMD feature and bounded local readiness capabilities."""
from __future__ import annotations

import json
import os
from datetime import date

from omd import __version__
from omd.config import CONFIG_SCHEMA_VERSION, SUPPORTED_CONFIG_SCHEMA_VERSIONS
from omd._network_policy import LOCAL_OLLAMA_TRANSPORT_VERSION
from omd.provider_disclosure import disclosure_readiness

try:
    from omd._build_info import BUILD_REVISION
except ImportError:
    BUILD_REVISION = None

PROTOCOL_VERSION = 1

# This table describes behaviour implemented by OMD, rather than the tools
# installed on the current machine.  A missing backend is therefore not a
# negative auto-detection result: availability belongs to ``omd doctor``.
_ASR_AUTO_DETECTION_MATRIX = (
    ("local-audio", "mlx"),
    ("local-audio", "faster-whisper"),
    ("reel", "mlx"),
    ("reel", "faster-whisper"),
    ("xhs", "mlx"),
    ("xhs", "faster-whisper"),
    ("podcast", "mlx"),
    ("podcast", "faster-whisper"),
)


def _build_revision() -> str | None:
    value = os.environ.get("OMD_BUILD_REVISION", "").strip() or BUILD_REVISION or ""
    printable = "".join(character for character in value if character.isprintable())
    return printable[:128] or None



def _ocr_readiness() -> dict[str, object]:
    """Expose a bounded local OCR-pack probe without leaking executable paths.

    The doctor implementation already owns Tesseract probing, timeout handling,
    and language validation.  Keeping this narrow projection lets integrations
    decide whether a selected OCR preset is usable without treating a missing
    local dependency as an unsupported protocol feature.
    """
    from omd.doctor import tesseract_readiness

    status = tesseract_readiness()
    return {
        "available": bool(status.get("available")),
        "ready": bool(status.get("ready")),
        "installed_packs": list(status.get("languages") or []),
        "effective_language": status.get("effective_language"),
        "missing_packs": list(status.get("missing_languages") or []),
        "error": status.get("error"),
    }


def _asr_auto_detection_matrix() -> list[dict[str, object]]:
    return [
        {
            "adapter": adapter,
            "backend": backend,
            "supported": True,
        }
        for adapter, backend in _ASR_AUTO_DETECTION_MATRIX
    ]


def capabilities_payload(*, as_of: date | None = None) -> dict[str, object]:
    return {
        "package_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "build_revision": _build_revision(),
        "browser_ui": {"supported": False, "reason": "dependency_security_update_pending"},
        "hosted_ai_disclosure": disclosure_readiness(as_of=as_of),
        "config_schema": {
            "current_version": CONFIG_SCHEMA_VERSION,
            "supported_versions": list(SUPPORTED_CONFIG_SCHEMA_VERSIONS),
        },
        "capture_language_options": {
            "ocr": {
                "argument": "--ocr-language",
                "aliases": ["--ocr-lang", "--lang"],
                "composite": True,
                "separator": "+",
                "presets": [
                    {"id": "english", "label": "English", "value": "eng"},
                    {
                        "id": "simplified-chinese-english",
                        "label": "简体中文 + English",
                        "value": "chi_sim+eng",
                    },
                    {
                        "id": "traditional-chinese-english",
                        "label": "繁體中文 + English",
                        "value": "chi_tra+eng",
                    },
                ],
                "readiness": _ocr_readiness(),
            },
            "asr": {
                "argument": "--whisper-lang",
                "modes": ["inherit-adapter-default", "auto-detect", "explicit"],
                "auto_detection_matrix": _asr_auto_detection_matrix(),
            },
        },
        "capture_auth_options": {
            "share_text": {
                "supported": True,
                "requires_single_http_url": True,
            },
            "platform_cookie_flags": {
                "douyin": "--douyin-cookies",
                "xhs": "--xhs-cookies",
            },
        },
        "local_ollama_transport": {"version": LOCAL_OLLAMA_TRANSPORT_VERSION},
        "enrich_note": {"supported": True, "schema_versions": [1]},
    }


def capabilities_json(*, as_of: date | None = None) -> str:
    return json.dumps(
        capabilities_payload(as_of=as_of),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
