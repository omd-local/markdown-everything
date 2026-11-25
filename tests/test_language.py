from __future__ import annotations

import subprocess

import pytest

from omd import _language
from omd.config import CaptureConfig, OMDConfig


def test_normalize_ocr_language_set_deduplicates_and_preserves_order():
    assert _language.normalize_ocr_language_set(" chi_sim + eng +chi_sim ") == "chi_sim+eng"
    assert _language.parse_ocr_language_set("script/HanS+eng") == ("script/HanS", "eng")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "+",
        "eng++chi_sim",
        "../../eng",
        "eng;touch",
        "eng chi_sim",
        "/absolute",
        "+".join(f"lang{i}" for i in range(_language.MAX_OCR_LANGUAGE_COUNT + 1)),
    ],
)
def test_normalize_ocr_language_set_rejects_unsafe_or_unbounded_values(value):
    with pytest.raises(_language.OcrLanguageError):
        _language.normalize_ocr_language_set(value)


def test_probe_tesseract_languages_parses_machine_output(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="List of available languages in /tmp/tessdata/ (3):\neng\nchi_sim\nosd\n",
            stderr="",
        )

    monkeypatch.setattr(_language.subprocess, "run", fake_run)

    languages = _language.probe_tesseract_languages("/bin/tesseract", timeout=1.25)

    assert languages == frozenset({"eng", "chi_sim", "osd"})
    assert calls[0][0] == ["/bin/tesseract", "--list-langs"]
    assert calls[0][1]["timeout"] == 1.25
    assert calls[0][1]["shell"] is False


def test_probe_tesseract_languages_wraps_timeout(monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["tesseract", "--list-langs"], 1)

    monkeypatch.setattr(_language.subprocess, "run", timed_out)

    with pytest.raises(_language.OcrLanguageProbeError, match="timed out"):
        _language.probe_tesseract_languages(timeout=1)


def test_validate_ocr_language_packs_reports_every_missing_pack():
    with pytest.raises(_language.MissingOcrLanguagePacks) as exc_info:
        _language.validate_ocr_language_packs(
            "chi_sim+eng+deu",
            available_languages={"eng", "osd"},
        )

    error = exc_info.value
    assert error.missing == ("chi_sim", "deu")
    assert error.requested == "chi_sim+eng+deu"
    assert "brew install tesseract-lang" in str(error)
    assert "Available language packs: eng, osd" in str(error)


def test_resolve_ocr_language_precedence(monkeypatch):
    config = OMDConfig(capture=CaptureConfig(ocr_language="deu"))
    environment = {_language.OCR_LANGUAGE_ENV: "chi_sim+eng"}

    assert _language.resolve_ocr_language("fra", environ=environment, config=config) == "fra"
    assert _language.resolve_ocr_language(None, environ=environment, config=config) == "chi_sim+eng"
    assert _language.resolve_ocr_language(None, environ={}, config=config) == "deu"
    assert _language.resolve_ocr_language(None, environ={}, config=None) == "eng"


def test_choose_whisper_language_supports_explicit_auto_without_falling_back():
    config = OMDConfig(
        capture=CaptureConfig(
            spoken_language_mode="explicit",
            spoken_language_hint="de",
        )
    )

    assert _language.choose_whisper_language("auto", config=config, default="en") is None
    assert _language.choose_whisper_language("AUTO-DETECT", config=config, default="en") is None


def test_choose_whisper_language_uses_explicit_then_environment_then_config_then_default():
    config = OMDConfig(
        capture=CaptureConfig(
            spoken_language_mode="explicit",
            spoken_language_hint="de",
        )
    )

    assert _language.choose_whisper_language("fr", environ={}, config=config, default="en") == "fr"
    assert _language.choose_whisper_language(
        None,
        environ={
            _language.SPOKEN_LANGUAGE_MODE_ENV: "explicit",
            _language.SPOKEN_LANGUAGE_HINT_ENV: "zh",
        },
        config=config,
        default="en",
    ) == "zh"
    assert _language.choose_whisper_language(None, environ={}, config=config, default="en") == "de"
    assert _language.choose_whisper_language(None, environ={}, config=None, default="en") == "en"


def test_choose_whisper_language_preserves_legacy_preferred_languages(monkeypatch):
    monkeypatch.setenv(_language.PREFERRED_LANGUAGES_ENV, "zh,en")

    assert _language.choose_whisper_language(None, default="en") == "zh"


def test_explicit_empty_preferred_value_still_suppresses_legacy_environment(monkeypatch):
    monkeypatch.setenv(_language.PREFERRED_LANGUAGES_ENV, "zh,en")

    assert _language.choose_whisper_language(
        None,
        preferred="",
        environ={
            _language.PREFERRED_LANGUAGES_ENV: "zh,en",
            _language.SPOKEN_LANGUAGE_MODE_ENV: "auto-detect",
        },
        default="en",
    ) == "en"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" EN ", "en"),
        ("zh_CN", "zh-cn"),
        ("pt-BR", "pt-br"),
        ("YUE", "yue"),
        ("zh-Hans", "zh-hans"),
    ],
)
def test_normalize_spoken_language_hint_returns_canonical_language_code(raw, expected):
    assert _language.normalize_spoken_language_hint(raw) == expected


@pytest.mark.parametrize("raw", ["auto", "AUTO-DETECT"])
def test_normalize_spoken_language_hint_allows_auto_only_when_requested(raw):
    with pytest.raises(ValueError, match="language code"):
        _language.normalize_spoken_language_hint(raw)

    assert _language.normalize_spoken_language_hint(raw, allow_auto=True) == "auto"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "e",
        "engl",
        "english",
        "zh+en",
        "zh,en",
        "zh en",
        "zh-hans-cn",
        "en-!",
    ],
)
def test_normalize_spoken_language_hint_rejects_names_lists_and_invalid_codes(raw):
    with pytest.raises(ValueError, match="language code"):
        _language.normalize_spoken_language_hint(raw, allow_auto=True)


def test_legacy_preferred_languages_use_the_same_spoken_code_contract():
    assert _language.parse_preferred_languages(" ZH_cn, english, PT-BR, e ") == [
        "zh-cn",
        "pt-br",
    ]


def test_choose_whisper_language_validates_and_normalizes_explicit_hints():
    assert _language.choose_whisper_language(" ZH_cn ") == "zh-cn"
    with pytest.raises(ValueError, match="language code"):
        _language.choose_whisper_language("english")
