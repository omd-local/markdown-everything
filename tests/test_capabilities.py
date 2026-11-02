import json
from datetime import date

from omd import __version__

from omd.capabilities import capabilities_json, capabilities_payload

REVIEW_DATE = date(2026, 11, 2)


def test_capabilities_reports_enrich_note_v1_without_runtime_probe(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network probe")),
    )

    payload = capabilities_payload(as_of=REVIEW_DATE)
    assert payload["enrich_note"] == {"supported": True, "schema_versions": [1]}
    assert payload["package_version"] == __version__
    assert payload["protocol_version"] == 1
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


def test_hosted_disclosure_capabilities_exact_contract_is_offline(monkeypatch):

    def forbidden(*args, **kwargs):
        raise AssertionError("capabilities must not load credentials or contact providers")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("omd.credentials.load_api_key", forbidden)
    monkeypatch.setattr("omd.provider_models.validate_selected_model", forbidden)
    monkeypatch.setattr("omd.ai_providers.request_text", forbidden)
    as_of = date(2026, 11, 2)
    payload = capabilities_payload(as_of=as_of)
    assert set(payload) == {
        "package_version", "protocol_version", "build_revision", "enrich_note", "hosted_ai_disclosure",
    }
    assert payload["protocol_version"] == 1
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
    assert capabilities_payload(as_of=date(2026, 11, 2))["hosted_ai_disclosure"]["providers"]["openai"] == {
        "status": "unavailable", "code": "disclosure_unavailable", "reason": "missing",
        "reviewed_on": None, "expires_on": None, "policy_url": None,
    }
