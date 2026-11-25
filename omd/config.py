"""Versioned, read-only OMD runtime configuration.

This module intentionally stores capture behavior only.  Credentials, cookies,
and learned note-organization preferences belong to their existing dedicated
surfaces and are rejected here as unknown fields.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from omd._language import (
    DEFAULT_OCR_LANGUAGE,
    OcrLanguageError,
    OCR_LANGUAGE_ENV,
    PREFERRED_LANGUAGES_ENV,
    SPOKEN_LANGUAGE_HINT_ENV,
    SPOKEN_LANGUAGE_MODE_ENV,
    normalize_ocr_language_set,
    normalize_spoken_language_hint,
    parse_preferred_languages,
)

CONFIG_ENV = "OMD_CONFIG"
XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"
CONFIG_SCHEMA_VERSION = 1
SUPPORTED_CONFIG_SCHEMA_VERSIONS = (CONFIG_SCHEMA_VERSION,)
MAX_CONFIG_BYTES = 64 * 1024
SPOKEN_LANGUAGE_MODES = (
    "inherit-adapter-default",
    "auto-detect",
    "explicit",
)
GENERATION_LANGUAGE_POLICY = "same-as-source"


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid or cannot be read safely."""


def _normalize_spoken_hint(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("capture.spoken_language_hint must be a string or null")
    try:
        return normalize_spoken_language_hint(value)
    except ValueError as exc:
        raise ConfigError(f"capture.spoken_language_hint: {exc}") from exc


def _validate_spoken_pair(mode: object, hint: object | None) -> tuple[str, str | None]:
    if not isinstance(mode, str) or mode not in SPOKEN_LANGUAGE_MODES:
        raise ConfigError(
            "capture.spoken_language_mode must be one of: "
            + ", ".join(SPOKEN_LANGUAGE_MODES)
        )
    normalized_hint = _normalize_spoken_hint(hint)
    if mode == "explicit" and normalized_hint is None:
        raise ConfigError(
            "capture.spoken_language_hint is required when spoken_language_mode is explicit"
        )
    if mode != "explicit" and normalized_hint is not None:
        raise ConfigError(
            "capture.spoken_language_hint must be null unless spoken_language_mode is explicit"
        )
    return mode, normalized_hint


@dataclass(frozen=True)
class CaptureConfig:
    ocr_language: str = DEFAULT_OCR_LANGUAGE
    spoken_language_mode: str = "inherit-adapter-default"
    spoken_language_hint: str | None = None

    def __post_init__(self) -> None:
        try:
            normalized_ocr = normalize_ocr_language_set(self.ocr_language)
        except ValueError as exc:
            raise ConfigError(f"capture.ocr_language: {exc}") from exc
        mode, hint = _validate_spoken_pair(
            self.spoken_language_mode,
            self.spoken_language_hint,
        )
        object.__setattr__(self, "ocr_language", normalized_ocr)
        object.__setattr__(self, "spoken_language_mode", mode)
        object.__setattr__(self, "spoken_language_hint", hint)


@dataclass(frozen=True)
class GenerationConfig:
    language_policy: str = GENERATION_LANGUAGE_POLICY

    def __post_init__(self) -> None:
        if self.language_policy != GENERATION_LANGUAGE_POLICY:
            raise ConfigError(
                "generation.language_policy must be 'same-as-source'"
            )


@dataclass(frozen=True)
class OMDConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version not in SUPPORTED_CONFIG_SCHEMA_VERSIONS
        ):
            raise ConfigError(
                f"schema_version must be one of {list(SUPPORTED_CONFIG_SCHEMA_VERSIONS)}"
            )
        if not isinstance(self.capture, CaptureConfig):
            raise ConfigError("capture must be a CaptureConfig")
        if not isinstance(self.generation, GenerationConfig):
            raise ConfigError("generation must be a GenerationConfig")


@dataclass(frozen=True)
class ResolvedCaptureConfig:
    ocr_language: str
    ocr_language_source: str
    spoken_language_mode: str
    spoken_language_hint: str | None
    spoken_language_source: str


def config_path(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: str | Path | None = None,
) -> Path:
    """Resolve OMD_CONFIG > XDG_CONFIG_HOME > platform convention."""
    environment = os.environ if environ is None else environ
    explicit = environment.get(CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()

    xdg_home = environment.get(XDG_CONFIG_HOME_ENV, "").strip()
    if xdg_home:
        return Path(xdg_home).expanduser() / "omd" / "config.json"

    home_path = Path.home() if home is None else Path(home).expanduser()
    current_platform = sys.platform if platform is None else platform
    if current_platform == "darwin":
        base = home_path / "Library" / "Application Support"
    elif current_platform.startswith("win"):
        appdata = environment.get("APPDATA", "").strip()
        base = Path(appdata).expanduser() if appdata else home_path / "AppData" / "Roaming"
    else:
        base = home_path / ".config"
    return base / "omd" / "config.json"


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"unknown {location} field(s): {', '.join(unknown)}")


def parse_config(payload: object) -> OMDConfig:
    """Validate a decoded schema-v1 configuration object."""
    if not isinstance(payload, Mapping):
        raise ConfigError("configuration root must be a JSON object")
    _reject_unknown(payload, {"schema_version", "capture", "generation"}, "configuration")
    if "schema_version" not in payload:
        raise ConfigError("schema_version is required")
    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version not in SUPPORTED_CONFIG_SCHEMA_VERSIONS:
        raise ConfigError(
            f"schema_version must be one of {list(SUPPORTED_CONFIG_SCHEMA_VERSIONS)}"
        )

    raw_capture = payload.get("capture", {})
    if not isinstance(raw_capture, Mapping):
        raise ConfigError("capture must be a JSON object")
    _reject_unknown(
        raw_capture,
        {"ocr_language", "spoken_language_mode", "spoken_language_hint"},
        "capture",
    )
    capture = CaptureConfig(
        ocr_language=raw_capture.get("ocr_language", DEFAULT_OCR_LANGUAGE),
        spoken_language_mode=raw_capture.get(
            "spoken_language_mode", "inherit-adapter-default"
        ),
        spoken_language_hint=raw_capture.get("spoken_language_hint"),
    )
    raw_generation = payload.get("generation", {})
    if not isinstance(raw_generation, Mapping):
        raise ConfigError("generation must be a JSON object")
    _reject_unknown(raw_generation, {"language_policy"}, "generation")
    generation = GenerationConfig(
        language_policy=raw_generation.get(
            "language_policy", GENERATION_LANGUAGE_POLICY
        )
    )
    return OMDConfig(
        schema_version=schema_version,
        capture=capture,
        generation=generation,
    )


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> OMDConfig:
    """Load and strictly validate configuration without creating or changing it."""
    resolved_path = config_path(environ=environ) if path is None else Path(path).expanduser()
    try:
        size = resolved_path.stat().st_size
    except FileNotFoundError:
        return OMDConfig()
    except OSError as exc:
        raise ConfigError(f"could not inspect config {resolved_path}: {exc}") from exc
    if size > MAX_CONFIG_BYTES:
        raise ConfigError(
            f"config file is too large ({size} bytes; maximum {MAX_CONFIG_BYTES})"
        )
    try:
        raw = resolved_path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"could not read config {resolved_path}: {exc}") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError(
            f"config file is too large ({len(raw)} bytes; maximum {MAX_CONFIG_BYTES})"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid JSON in config {resolved_path}: {exc}") from exc
    return parse_config(payload)


def config_to_dict(value: OMDConfig) -> dict[str, object]:
    return asdict(value)


def _resolve_spoken_layer(
    mode: str | None,
    hint: str | None,
) -> tuple[str, str | None]:
    if mode is None and hint is not None:
        mode = "explicit"
    if mode is None:
        mode = "inherit-adapter-default"
    elif isinstance(mode, str):
        mode = mode.strip().lower()
        if mode == "auto":
            mode = "auto-detect"
    return _validate_spoken_pair(mode, hint)


def resolve_capture_config(
    configured: OMDConfig | None = None,
    *,
    explicit_ocr_language: str | None = None,
    explicit_spoken_language_mode: str | None = None,
    explicit_spoken_language_hint: str | None = None,
    environ: Mapping[str, str] | None = None,
    default_ocr_language: str = DEFAULT_OCR_LANGUAGE,
) -> ResolvedCaptureConfig:
    """Resolve capture settings using explicit > environment > config > default."""
    environment = os.environ if environ is None else environ

    try:
        if explicit_ocr_language is not None:
            ocr_language = normalize_ocr_language_set(explicit_ocr_language)
            ocr_source = "explicit"
        elif OCR_LANGUAGE_ENV in environment:
            ocr_language = normalize_ocr_language_set(environment[OCR_LANGUAGE_ENV])
            ocr_source = "environment"
        elif configured is not None:
            ocr_language = configured.capture.ocr_language
            ocr_source = "config"
        else:
            ocr_language = normalize_ocr_language_set(default_ocr_language)
            ocr_source = "default"
    except OcrLanguageError as exc:
        raise ConfigError(f"invalid OCR language: {exc}") from exc

    explicit_spoken = (
        explicit_spoken_language_mode is not None
        or explicit_spoken_language_hint is not None
    )
    environment_spoken = (
        SPOKEN_LANGUAGE_MODE_ENV in environment
        or SPOKEN_LANGUAGE_HINT_ENV in environment
    )
    legacy_preferred = parse_preferred_languages(
        environment.get(PREFERRED_LANGUAGES_ENV)
    )
    if explicit_spoken:
        spoken_mode, spoken_hint = _resolve_spoken_layer(
            explicit_spoken_language_mode,
            explicit_spoken_language_hint,
        )
        spoken_source = "explicit"
    elif environment_spoken:
        spoken_mode, spoken_hint = _resolve_spoken_layer(
            environment.get(SPOKEN_LANGUAGE_MODE_ENV),
            environment.get(SPOKEN_LANGUAGE_HINT_ENV),
        )
        spoken_source = "environment"
    elif legacy_preferred:
        spoken_mode = "explicit"
        spoken_hint = legacy_preferred[0]
        spoken_source = "environment"
    elif configured is not None:
        spoken_mode = configured.capture.spoken_language_mode
        spoken_hint = configured.capture.spoken_language_hint
        spoken_source = "config"
    else:
        spoken_mode = "inherit-adapter-default"
        spoken_hint = None
        spoken_source = "default"

    return ResolvedCaptureConfig(
        ocr_language=ocr_language,
        ocr_language_source=ocr_source,
        spoken_language_mode=spoken_mode,
        spoken_language_hint=spoken_hint,
        spoken_language_source=spoken_source,
    )


def config_show_payload(
    *,
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    explicit_ocr_language: str | None = None,
    explicit_spoken_language_mode: str | None = None,
    explicit_spoken_language_hint: str | None = None,
    adapter_default_spoken_language: str | None = None,
) -> dict[str, object]:
    """Build a machine-readable, read-only explanation for ``config show``."""
    resolved_path = config_path(environ=environ) if path is None else Path(path).expanduser()
    exists = resolved_path.is_file()
    configured = load_config(resolved_path, environ=environ)
    resolved = resolve_capture_config(
        configured if exists else None,
        explicit_ocr_language=explicit_ocr_language,
        explicit_spoken_language_mode=explicit_spoken_language_mode,
        explicit_spoken_language_hint=explicit_spoken_language_hint,
        environ=environ,
    )
    if resolved.spoken_language_mode == "explicit":
        spoken_effective: str | None = resolved.spoken_language_hint
    elif resolved.spoken_language_mode == "auto-detect":
        spoken_effective = None
    else:
        spoken_effective = adapter_default_spoken_language or "adapter-dependent"
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "path": str(resolved_path),
        "exists": exists,
        "capture": {
            "ocr_language": {
                "configured": configured.capture.ocr_language,
                "effective": resolved.ocr_language,
                "source": resolved.ocr_language_source,
            },
            "spoken_language": {
                "configured_mode": configured.capture.spoken_language_mode,
                "configured_hint": configured.capture.spoken_language_hint,
                "mode": resolved.spoken_language_mode,
                "hint": resolved.spoken_language_hint,
                "effective": spoken_effective,
                "source": resolved.spoken_language_source,
            },
        },
        "generation": {
            "language_policy": configured.generation.language_policy,
            "effective": GENERATION_LANGUAGE_POLICY,
        },
    }
