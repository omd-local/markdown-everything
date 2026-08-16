from __future__ import annotations

from datetime import date

import pytest


def test_openai_disclosure_matches_transport_storage_controls():
    from omd.provider_disclosure import get_provider_disclosure

    disclosure = get_provider_disclosure("openai", as_of=date(2026, 7, 19))

    assert disclosure.destination_domain == "api.openai.com"
    assert disclosure.storage_disabled is True
    assert "store=false" in disclosure.data_handling_summary
    assert disclosure.policy_url.startswith("https://platform.openai.com/")


@pytest.mark.parametrize(
    ("provider", "domain"),
    [
        ("openai", "api.openai.com"),
        ("anthropic", "api.anthropic.com"),
        ("deepseek", "api.deepseek.com"),
    ],
)
def test_each_hosted_provider_has_a_distinct_disclosure(provider, domain):
    from omd.provider_disclosure import get_provider_disclosure

    disclosure = get_provider_disclosure(provider, as_of=date(2026, 7, 19))

    assert disclosure.provider == provider
    assert disclosure.destination_domain == domain
    assert disclosure.policy_url.startswith("https://")
    assert disclosure.data_handling_summary


def test_deepseek_disclosure_mentions_published_processing_location():
    from omd.provider_disclosure import get_provider_disclosure

    disclosure = get_provider_disclosure("deepseek", as_of=date(2026, 7, 19))

    assert "People's Republic of China" in disclosure.data_handling_summary


def test_stale_provider_disclosure_fails_closed():
    from omd.provider_disclosure import (
        ProviderDisclosureUnavailableError,
        get_provider_disclosure,
    )

    with pytest.raises(ProviderDisclosureUnavailableError, match="out of date"):
        get_provider_disclosure(
            "openai",
            as_of=date(2027, 1, 19),
            max_age_days=90,
        )


def test_unknown_or_local_provider_has_no_cloud_disclosure():
    from omd.provider_disclosure import (
        ProviderDisclosureUnavailableError,
        get_provider_disclosure,
    )

    with pytest.raises(ProviderDisclosureUnavailableError, match="unavailable"):
        get_provider_disclosure("ollama", as_of=date(2026, 7, 19))


def test_cloud_request_preview_contains_size_not_source_content():
    from omd.provider_disclosure import build_cloud_request_preview

    source = "Private source text that must not be retained by the preview."
    preview = build_cloud_request_preview(
        provider="anthropic",
        model="claude-model",
        capability="markdown_polish",
        source_text=source,
        sends_attachment=False,
        as_of=date(2026, 7, 19),
    )

    assert preview.provider == "anthropic"
    assert preview.model == "claude-model"
    assert preview.destination_domain == "api.anthropic.com"
    assert preview.character_count == len(source)
    assert preview.estimated_input_tokens > 0
    assert preview.sends_attachment is False
    assert source not in repr(preview)
    assert "source_text" not in preview.__dict__


def test_cloud_request_preview_accounts_for_non_ascii_text_conservatively():
    from omd.provider_disclosure import build_cloud_request_preview

    preview = build_cloud_request_preview(
        provider="deepseek",
        model="deepseek-model",
        capability="note_organisation",
        source_text="中文笔记",
        sends_attachment=False,
        as_of=date(2026, 7, 19),
    )

    assert preview.estimated_input_tokens >= 4


def test_cloud_request_preview_rejects_blank_model_and_capability():
    from omd.provider_disclosure import build_cloud_request_preview

    with pytest.raises(ValueError, match="model"):
        build_cloud_request_preview(
            provider="openai",
            model=" ",
            capability="markdown_polish",
            source_text="text",
            sends_attachment=False,
            as_of=date(2026, 7, 19),
        )

    with pytest.raises(ValueError, match="capability"):
        build_cloud_request_preview(
            provider="openai",
            model="gpt-model",
            capability="",
            source_text="text",
            sends_attachment=False,
            as_of=date(2026, 7, 19),
        )
