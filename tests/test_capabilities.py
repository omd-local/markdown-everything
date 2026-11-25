import json
from datetime import date

from omd import __version__
from omd.capabilities import capabilities_json, capabilities_payload

REVIEW_DATE = date(2026, 11, 2)


def _tesseract_ready(**overrides):
    status = {
        "available": True,
        "ready": True,
        "languages": ["chi_sim", "eng", "osd"],
        "effective_language": "chi_sim+eng",
        "missing_languages": [],
        "error": None,
    }
    status.update(overrides)
    return status


def test_capabilities_reports_enrich_note_v1_without_network_probe(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network probe")),
    )
    monkeypatch.setattr("omd.doctor.tesseract_readiness", lambda: _tesseract_ready())

    payload = capabilities_payload(as_of=REVIEW_DATE)

    assert payload["enrich_note"] == {"supported": True, "schema_versions": [1]}
    assert payload["protocol_version"] == 1
    assert payload["browser_ui"] == {
        "supported": False, "reason": "dependency_security_update_pending",
    }
    assert payload["config_schema"] == {
        "current_version": 1,
        "supported_versions": [1],
    }
    assert payload["capture_language_options"]["ocr"] == {
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
        "readiness": {
            "available": True,
            "ready": True,
            "installed_packs": ["chi_sim", "eng", "osd"],
            "effective_language": "chi_sim+eng",
            "missing_packs": [],
            "error": None,
        },
    }
    assert payload["capture_language_options"]["asr"] == {
        "argument": "--whisper-lang",
        "modes": ["inherit-adapter-default", "auto-detect", "explicit"],
        "auto_detection_matrix": [
            {"adapter": "local-audio", "backend": "mlx", "supported": True},
            {
                "adapter": "local-audio",
                "backend": "faster-whisper",
                "supported": True,
            },
            {"adapter": "reel", "backend": "mlx", "supported": True},
            {"adapter": "reel", "backend": "faster-whisper", "supported": True},
            {"adapter": "xhs", "backend": "mlx", "supported": True},
            {"adapter": "xhs", "backend": "faster-whisper", "supported": True},
            {"adapter": "podcast", "backend": "mlx", "supported": True},
            {
                "adapter": "podcast",
                "backend": "faster-whisper",
                "supported": True,
            },
        ],
    }
    assert payload["capture_auth_options"] == {
        "share_text": {
            "supported": True,
            "requires_single_http_url": True,
        },
        "platform_cookie_flags": {
            "douyin": "--douyin-cookies",
            "xhs": "--xhs-cookies",
        },
    }
    assert payload["local_ollama_transport"] == {"version": 1}
    assert json.loads(capabilities_json(as_of=REVIEW_DATE)) == capabilities_payload(as_of=REVIEW_DATE)
    assert capabilities_json(as_of=REVIEW_DATE).endswith("\n")


def test_capabilities_build_identity_comes_from_package_and_bounded_environment(monkeypatch):
    from omd import __version__

    monkeypatch.setenv("OMD_BUILD_REVISION", "  abc123  ")
    monkeypatch.setattr("omd.doctor.tesseract_readiness", lambda: _tesseract_ready())
    payload = capabilities_payload(as_of=REVIEW_DATE)

    assert payload["package_version"] == __version__
    assert payload["build_revision"] == "abc123"

    monkeypatch.setenv("OMD_BUILD_REVISION", "x" * 500)
    assert len(capabilities_payload(as_of=REVIEW_DATE)["build_revision"]) == 128


def test_capabilities_reports_missing_ocr_packs_without_changing_static_support(monkeypatch):
    monkeypatch.setattr(
        "omd.doctor.tesseract_readiness",
        lambda: _tesseract_ready(
            ready=False,
            languages=["eng"],
            effective_language="chi_sim+eng",
            missing_languages=["chi_sim"],
            error="Missing Tesseract language pack(s): chi_sim.",
        ),
    )

    payload = capabilities_payload(as_of=REVIEW_DATE)

    assert payload["capture_language_options"]["ocr"]["readiness"] == {
        "available": True,
        "ready": False,
        "installed_packs": ["eng"],
        "effective_language": "chi_sim+eng",
        "missing_packs": ["chi_sim"],
        "error": "Missing Tesseract language pack(s): chi_sim.",
    }
    assert all(
        item["supported"]
        for item in payload["capture_language_options"]["asr"]["auto_detection_matrix"]
    )


def test_hosted_disclosure_capabilities_exact_contract_is_offline(monkeypatch):

    def forbidden(*args, **kwargs):
        raise AssertionError("capabilities must not load credentials or contact providers")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("omd.credentials.load_api_key", forbidden)
    monkeypatch.setattr("omd.provider_models.validate_selected_model", forbidden)
    monkeypatch.setattr("omd.ai_providers.request_text", forbidden)
    monkeypatch.setattr("omd.doctor.tesseract_readiness", lambda: _tesseract_ready())
    as_of = date(2026, 11, 2)
    payload = capabilities_payload(as_of=as_of)
    assert set(payload) == {
        "package_version", "protocol_version", "build_revision", "config_schema",
        "capture_language_options", "capture_auth_options", "local_ollama_transport",
        "enrich_note", "hosted_ai_disclosure", "browser_ui",
    }
    assert payload["protocol_version"] == 1
    assert payload["browser_ui"] == {
        "supported": False, "reason": "dependency_security_update_pending",
    }
    assert payload["hosted_ai_disclosure"] == {
        "schema_version": 1,
        "checked_on": "2026-11-02",
        "max_age_days": 90,
        "providers": {
            "openai": {
                "status": "ready", "code": None, "reason": None,
                "reviewed_on": "2026-11-02", "expires_on": "2027-01-31",
                "policy_url": "https://developers.openai.com/api/docs/guides/your-data",
            },
            "anthropic": {
                "status": "ready", "code": None, "reason": None,
                "reviewed_on": "2026-11-02", "expires_on": "2027-01-31",
                "policy_url": "https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data",
            },
            "deepseek": {
                "status": "unavailable", "code": "disclosure_unavailable", "reason": "unverified",
                "reviewed_on": "2026-11-02", "expires_on": "2027-01-31",
                "policy_url": "https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html",
            },
        },
    }
    assert json.loads(capabilities_json(as_of=as_of)) == payload
    for boundary, status, reason in [
        (date(2027, 1, 31), "ready", None),
        (date(2027, 2, 1), "unavailable", "expired"),
    ]:
        providers = capabilities_payload(as_of=boundary)["hosted_ai_disclosure"]["providers"]
        for provider in ("openai", "anthropic"):
            assert providers[provider]["status"] == status
            assert providers[provider]["reason"] == reason
        assert providers["deepseek"]["status"] == "unavailable"


def test_missing_disclosure_capabilities_exact_record(monkeypatch):
    from omd import provider_disclosure

    monkeypatch.delitem(provider_disclosure._DISCLOSURES, "openai")
    monkeypatch.setattr("omd.doctor.tesseract_readiness", lambda: _tesseract_ready())
    assert capabilities_payload(as_of=date(2026, 11, 2))["hosted_ai_disclosure"]["providers"]["openai"] == {
        "status": "unavailable", "code": "disclosure_unavailable", "reason": "missing",
        "reviewed_on": None, "expires_on": None, "policy_url": None,
    }


def test_capabilities_reports_enrich_note_v1_without_runtime_probe(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network probe")),
    )

    payload = capabilities_payload(as_of=REVIEW_DATE)
    assert payload["enrich_note"] == {"supported": True, "schema_versions": [1]}
    assert payload["package_version"] == __version__
    assert payload["protocol_version"] == 1
    assert payload["browser_ui"] == {
        "supported": False, "reason": "dependency_security_update_pending",
    }
    assert json.loads(capabilities_json(as_of=REVIEW_DATE)) == capabilities_payload(as_of=REVIEW_DATE)
    assert capabilities_json(as_of=REVIEW_DATE).endswith("\n")



def test_capabilities_build_identity_persists_without_environment(monkeypatch):
    import omd.capabilities as capabilities

    revision = "a" * 40
    monkeypatch.delenv("OMD_BUILD_REVISION", raising=False)
    monkeypatch.setattr(capabilities, "BUILD_REVISION", revision)
    assert capabilities_payload(as_of=REVIEW_DATE)["build_revision"] == revision
    monkeypatch.setenv("OMD_BUILD_REVISION", "  abc123  ")
    assert capabilities_payload(as_of=REVIEW_DATE)["build_revision"] == "abc123"
    monkeypatch.setenv("OMD_BUILD_REVISION", "x" * 500)
    assert len(capabilities_payload(as_of=REVIEW_DATE)["build_revision"]) == 128
