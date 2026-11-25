from __future__ import annotations

import json

import pytest

from omd import config


def test_config_path_precedence_and_platform_fallback(tmp_path):
    explicit = tmp_path / "portable.json"
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"

    assert config.config_path(
        environ={"OMD_CONFIG": str(explicit), "XDG_CONFIG_HOME": str(xdg)},
        platform="linux",
        home=home,
    ) == explicit
    assert config.config_path(
        environ={"XDG_CONFIG_HOME": str(xdg)}, platform="linux", home=home
    ) == xdg / "omd" / "config.json"
    assert config.config_path(environ={}, platform="darwin", home=home) == (
        home / "Library" / "Application Support" / "omd" / "config.json"
    )
    assert config.config_path(environ={}, platform="linux", home=home) == (
        home / ".config" / "omd" / "config.json"
    )


def test_load_config_returns_defaults_when_file_is_missing(tmp_path):
    path = tmp_path / "missing.json"

    loaded = config.load_config(path)

    assert loaded == config.OMDConfig()
    assert loaded.capture.ocr_language == "eng"
    assert loaded.capture.spoken_language_mode == "inherit-adapter-default"
    assert loaded.capture.spoken_language_hint is None
    assert loaded.generation.language_policy == "same-as-source"


def test_load_config_validates_schema_and_values(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "capture": {
                    "ocr_language": "chi_sim+eng+chi_sim",
                    "spoken_language_mode": "explicit",
                    "spoken_language_hint": "zh",
                },
                "generation": {"language_policy": "same-as-source"},
            }
        ),
        encoding="utf-8",
    )

    loaded = config.load_config(path)

    assert loaded.capture.ocr_language == "chi_sim+eng"
    assert loaded.capture.spoken_language_mode == "explicit"
    assert loaded.capture.spoken_language_hint == "zh"
    assert loaded.generation.language_policy == "same-as-source"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"schema_version": 1, "token": "secret"}, "unknown"),
        (
            {
                "schema_version": 1,
                "generation": {"language_policy": "translate-to-english"},
            },
            "same-as-source",
        ),
        ({"schema_version": 1, "capture": {"api_key": "secret"}}, "unknown"),
        (
            {
                "schema_version": 1,
                "capture": {
                    "spoken_language_mode": "explicit",
                    "spoken_language_hint": None,
                },
            },
            "spoken_language_hint",
        ),
        (
            {
                "schema_version": 1,
                "capture": {
                    "spoken_language_mode": "auto-detect",
                    "spoken_language_hint": "zh",
                },
            },
            "must be null",
        ),
    ],
)
def test_load_config_rejects_unknown_or_inconsistent_fields(tmp_path, payload, message):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(config.ConfigError, match=message):
        config.load_config(path)


def test_load_config_rejects_oversized_files(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b" " * (config.MAX_CONFIG_BYTES + 1))

    with pytest.raises(config.ConfigError, match="too large"):
        config.load_config(path)


def test_resolve_capture_config_precedence_and_sources():
    configured = config.OMDConfig(
        capture=config.CaptureConfig(
            ocr_language="deu",
            spoken_language_mode="explicit",
            spoken_language_hint="de",
        )
    )
    environment = {
        config.OCR_LANGUAGE_ENV: "chi_sim+eng",
        config.SPOKEN_LANGUAGE_MODE_ENV: "auto-detect",
    }

    explicit = config.resolve_capture_config(
        configured,
        explicit_ocr_language="fra",
        explicit_spoken_language_mode="explicit",
        explicit_spoken_language_hint="fr",
        environ=environment,
    )
    from_env = config.resolve_capture_config(configured, environ=environment)
    from_config = config.resolve_capture_config(configured, environ={})

    assert explicit.ocr_language == "fra"
    assert explicit.ocr_language_source == "explicit"
    assert explicit.spoken_language_mode == "explicit"
    assert explicit.spoken_language_hint == "fr"
    assert explicit.spoken_language_source == "explicit"
    assert from_env.ocr_language == "chi_sim+eng"
    assert from_env.ocr_language_source == "environment"
    assert from_env.spoken_language_mode == "auto-detect"
    assert from_env.spoken_language_hint is None
    assert from_env.spoken_language_source == "environment"
    assert from_config.ocr_language == "deu"
    assert from_config.ocr_language_source == "config"
    assert from_config.spoken_language_hint == "de"


def test_resolve_capture_config_rejects_an_empty_environment_mode():
    with pytest.raises(config.ConfigError, match="spoken_language_mode"):
        config.resolve_capture_config(
            environ={config.SPOKEN_LANGUAGE_MODE_ENV: ""},
        )


def test_resolve_capture_config_preserves_legacy_preferred_language_environment():
    resolved = config.resolve_capture_config(
        environ={"OMD_PREFERRED_LANGUAGES": "zh,en"},
    )

    assert resolved.spoken_language_mode == "explicit"
    assert resolved.spoken_language_hint == "zh"
    assert resolved.spoken_language_source == "environment"


@pytest.mark.parametrize("hint", ["english", "e", "zh+en", "../en", "en/zh"])
def test_config_explicit_spoken_hint_uses_cli_language_code_contract(hint):
    with pytest.raises(config.ConfigError, match="language"):
        config.CaptureConfig(
            spoken_language_mode="explicit",
            spoken_language_hint=hint,
        )


def test_resolve_capture_config_normalizes_environment_auto_alias():
    resolved = config.resolve_capture_config(
        environ={config.SPOKEN_LANGUAGE_MODE_ENV: "auto"},
    )

    assert resolved.spoken_language_mode == "auto-detect"
    assert resolved.spoken_language_hint is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (" AUTO-DETECT ", "auto-detect"),
        (" INHERIT-ADAPTER-DEFAULT ", "inherit-adapter-default"),
        (" EXPLICIT ", "explicit"),
    ],
)
def test_resolve_capture_config_normalizes_runtime_mode_spelling(value, expected):
    environment = {config.SPOKEN_LANGUAGE_MODE_ENV: value}
    if expected == "explicit":
        environment[config.SPOKEN_LANGUAGE_HINT_ENV] = "zh"

    resolved = config.resolve_capture_config(environ=environment)

    assert resolved.spoken_language_mode == expected


def test_config_show_payload_is_read_only_and_explains_adapter_dependent_asr(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "capture": {
                    "ocr_language": "chi_sim+eng",
                    "spoken_language_mode": "inherit-adapter-default",
                    "spoken_language_hint": None,
                },
            }
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    payload = config.config_show_payload(path=path, environ={})

    assert path.read_bytes() == before
    assert payload["schema_version"] == 1
    assert payload["path"] == str(path)
    assert payload["exists"] is True
    assert payload["capture"]["ocr_language"]["effective"] == "chi_sim+eng"
    assert payload["capture"]["spoken_language"]["effective"] == "adapter-dependent"
