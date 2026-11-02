from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from omd import provider_disclosure as disclosures
from omd.provider_disclosure import ProviderDisclosureUnavailableError

REVIEW_DATE = date(2026, 11, 2)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("age", [0, 90])
def test_verified_disclosure_is_available_through_day_90(provider, age):
    disclosure = disclosures.get_provider_disclosure(
        provider, as_of=REVIEW_DATE + timedelta(days=age)
    )
    assert disclosure.provider == provider
    assert disclosure.reviewed_on == REVIEW_DATE
    assert disclosure.destination_domain == f"api.{provider}.com"
    assert disclosure.verified is True


def test_openai_disclosure_does_not_equate_store_false_with_zero_retention():
    disclosure = disclosures.get_provider_disclosure("openai", as_of=REVIEW_DATE)
    assert disclosure.storage_disabled is True
    assert disclosure.policy_url == "https://developers.openai.com/api/docs/guides/your-data"
    assert "store=false, but this is not zero data retention" in disclosure.data_handling_summary
    assert "prompt caching" in disclosure.data_handling_summary


def test_anthropic_disclosure_preserves_retention_exceptions():
    disclosure = disclosures.get_provider_disclosure("anthropic", as_of=REVIEW_DATE)
    assert "within 30 days" in disclosure.data_handling_summary
    assert "policy-enforcement, legal, model, and feature exceptions" in disclosure.data_handling_summary
    assert "does not configure zero data retention" in disclosure.data_handling_summary


@pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek"])
@pytest.mark.parametrize("age", [-1, 91])
def test_out_of_date_disclosure_fails_closed_with_safe_metadata(provider, age):
    with pytest.raises(ProviderDisclosureUnavailableError, match="out of date") as error:
        disclosures.get_provider_disclosure(provider, as_of=REVIEW_DATE + timedelta(days=age))
    assert error.value.to_dict() == {"provider": provider, "code": "disclosure_unavailable"}


@pytest.mark.parametrize("age", [0, 90, 91])
def test_deepseek_audit_date_does_not_imply_verified_api_handling(age):
    record = disclosures._DISCLOSURES["deepseek"]
    assert record.reviewed_on == REVIEW_DATE
    assert record.verified is False
    assert "People's Republic of China" in record.data_handling_summary
    with pytest.raises(ProviderDisclosureUnavailableError) as error:
        disclosures.get_provider_disclosure("deepseek", as_of=REVIEW_DATE + timedelta(days=age))
    assert error.value.to_dict() == {"provider": "deepseek", "code": "disclosure_unavailable"}


@pytest.mark.parametrize("provider", ["ollama", "unknown", "sk-secret Vault evidence HTTP body", "", None])
def test_unknown_provider_is_not_echoed_in_error(provider):
    with pytest.raises(ProviderDisclosureUnavailableError) as error:
        disclosures.get_provider_disclosure(provider, as_of=REVIEW_DATE)
    assert str(error.value) == "provider disclosure is unavailable"
    assert error.value.to_dict() == {"provider": None, "code": "disclosure_unavailable"}


def test_missing_known_provider_keeps_safe_provider_identity(monkeypatch):
    monkeypatch.delitem(disclosures._DISCLOSURES, "openai")
    with pytest.raises(ProviderDisclosureUnavailableError) as error:
        disclosures.get_provider_disclosure("openai", as_of=REVIEW_DATE)
    assert error.value.to_dict() == {"provider": "openai", "code": "disclosure_unavailable"}
    assert disclosures.check_disclosures(as_of=REVIEW_DATE)["review_due"] == ["openai"]


@pytest.mark.parametrize("field", ["policy_url", "data_handling_summary"])
def test_incomplete_disclosure_fails_closed(monkeypatch, field):
    monkeypatch.setitem(disclosures._DISCLOSURES, "openai", replace(
        disclosures._DISCLOSURES["openai"], **{field: ""}
    ))
    with pytest.raises(ProviderDisclosureUnavailableError):
        disclosures.get_provider_disclosure("openai", as_of=REVIEW_DATE)


def test_one_provider_review_cannot_renew_another(monkeypatch):
    later = REVIEW_DATE + timedelta(days=91)
    monkeypatch.setitem(disclosures._DISCLOSURES, "openai", replace(
        disclosures._DISCLOSURES["openai"], reviewed_on=later
    ))
    assert disclosures.get_provider_disclosure("openai", as_of=later).reviewed_on == later
    with pytest.raises(ProviderDisclosureUnavailableError):
        disclosures.get_provider_disclosure("anthropic", as_of=later)


def test_exception_preserves_legacy_message_constructor_but_exports_no_message():
    error = ProviderDisclosureUnavailableError("sk-secret Vault evidence HTTP body")
    error.__cause__ = RuntimeError("private HTTP response body")
    assert isinstance(error, RuntimeError)
    assert error.args == ("sk-secret Vault evidence HTTP body",)
    assert error.to_dict() == {"provider": None, "code": "disclosure_unavailable"}


def test_cloud_request_preview_contains_size_not_source_content():
    source = "Private source text that must not be retained by the preview."
    preview = disclosures.build_cloud_request_preview(
        provider="anthropic", model="claude-model", capability="markdown_polish",
        source_text=source, sends_attachment=False, as_of=REVIEW_DATE,
    )
    assert preview.destination_domain == "api.anthropic.com"
    assert preview.character_count == len(source)
    assert preview.estimated_input_tokens > 0
    assert preview.sends_attachment is False
    assert source not in repr(preview)
    assert "source_text" not in preview.__dict__


def test_cloud_request_preview_accounts_for_non_ascii_text_conservatively():
    preview = disclosures.build_cloud_request_preview(
        provider="openai", model="gpt-model", capability="note_organisation",
        source_text="\u4e2d\u6587\u7b14\u8bb0", sends_attachment=False, as_of=REVIEW_DATE,
    )
    assert preview.estimated_input_tokens >= 4


@pytest.mark.parametrize("field", ["model", "capability"])
def test_cloud_request_preview_rejects_blank_model_and_capability(field):
    values = {"model": "gpt-model", "capability": "markdown_polish", field: " "}
    with pytest.raises(ValueError, match=field):
        disclosures.build_cloud_request_preview(
            provider="openai", **values, source_text="text",
            sends_attachment=False, as_of=REVIEW_DATE,
        )


@pytest.mark.parametrize("age, due", [(0, False), (75, False), (76, True), (90, True), (91, True)])
def test_maintenance_gate_fails_fourteen_days_before_expiry(age, due):
    report = disclosures.check_disclosures(as_of=REVIEW_DATE + timedelta(days=age))
    assert report["review_due"] == (["openai", "anthropic", "deepseek"] if due else [])


def test_maintenance_gate_rejects_future_review_and_invalid_threshold():
    assert disclosures.check_disclosures(as_of=REVIEW_DATE - timedelta(days=1))["review_due"]
    with pytest.raises(ValueError, match="non-negative"):
        disclosures.check_disclosures(as_of=REVIEW_DATE, warn_within_days=-1)


@pytest.mark.parametrize("as_of, exit_code", [("2026-11-02", 0), ("2027-01-17", 1), ("2027-02-01", 1)])
def test_maintenance_cli_is_offline_and_never_renews_reviews(monkeypatch, capsys, as_of, exit_code):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network access"))
    before = dict(disclosures._DISCLOSURES)
    assert disclosures.main(["--as-of", as_of]) == exit_code
    output = capsys.readouterr()
    assert json.loads(output.out)["checked_on"] == as_of
    assert "deepseek: disclosure_unavailable" in output.err
    assert ("ERROR: disclosure review due" in output.err) == bool(exit_code)
    assert disclosures._DISCLOSURES == before
