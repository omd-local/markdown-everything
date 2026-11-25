from __future__ import annotations

import json

import pytest


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _headers(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_ollama_catalog_uses_loopback_tags_endpoint():
    from omd.provider_models import discover_provider_models

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = _headers(request)
        seen["timeout"] = timeout
        return _FakeResponse(
            {"models": [{"name": "qwen3:4b-instruct"}, {"model": "gemma3:4b"}]}
        )

    catalog = discover_provider_models(
        "ollama",
        endpoint="http://localhost:11434",
        timeout_seconds=1.5,
        opener=opener,
    )

    assert seen == {
        "url": "http://localhost:11434/api/tags",
        "headers": {},
        "timeout": 1.5,
    }
    assert catalog.provider == "ollama"
    assert catalog.destination_domain == "localhost"
    assert catalog.models == ("gemma3:4b", "qwen3:4b-instruct")


def test_ollama_catalog_rejects_non_loopback_endpoint_before_network():
    from omd.provider_models import discover_provider_models

    called = False

    def opener(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ValueError, match="Remote Ollama"):
        discover_provider_models(
            "ollama",
            endpoint="https://models.example.com",
            opener=opener,
        )

    assert called is False


def test_ollama_catalog_allows_only_explicit_https_remote_endpoint():
    from omd.provider_models import discover_provider_models

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        return _FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    catalog = discover_provider_models(
        "ollama",
        endpoint="https://models.example.com",
        allow_remote_ollama=True,
        opener=opener,
    )

    assert seen["url"] == "https://models.example.com/api/tags"
    assert catalog.destination_domain == "models.example.com"

    with pytest.raises(ValueError, match="must use HTTPS"):
        discover_provider_models(
            "ollama",
            endpoint="http://models.example.com",
            allow_remote_ollama=True,
            opener=opener,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/custom",
        "http://localhost:11434?scope=all",
        "http://localhost:11434#catalog",
    ],
)
def test_ollama_catalog_rejects_non_base_url_before_network(endpoint):
    from omd.provider_models import discover_provider_models

    called = False

    def opener(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ValueError, match="base URL"):
        discover_provider_models("ollama", endpoint=endpoint, opener=opener)

    assert called is False


@pytest.mark.parametrize(
    ("provider", "url", "header", "header_value"),
    [
        ("openai", "https://api.openai.com/v1/models", "authorization", "Bearer sk-test"),
        (
            "anthropic",
            "https://api.anthropic.com/v1/models?limit=1000",
            "x-api-key",
            "sk-test",
        ),
        ("deepseek", "https://api.deepseek.com/models", "authorization", "Bearer sk-test"),
    ],
)
def test_hosted_catalog_uses_provider_specific_endpoint_and_auth(
    provider, url, header, header_value
):
    from omd.provider_models import discover_provider_models

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = _headers(request)
        return _FakeResponse({"data": [{"id": "model-b"}, {"id": "model-a"}]})

    catalog = discover_provider_models(
        provider,
        api_key="sk-test",
        opener=opener,
    )

    assert seen["url"] == url
    assert seen["headers"][header] == header_value
    if provider == "anthropic":
        assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert catalog.models == ("model-a", "model-b")


def test_hosted_catalog_requires_key_without_echoing_it():
    from omd.provider_models import ProviderCatalogError, discover_provider_models

    with pytest.raises(ProviderCatalogError, match="API key is required") as excinfo:
        discover_provider_models("openai", api_key=" ", opener=lambda *_a, **_k: None)

    assert "sk-" not in str(excinfo.value)


def test_hosted_catalog_rejects_api_key_header_injection_before_network():
    from omd.provider_models import ProviderCatalogError, discover_provider_models

    called = False

    def opener(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ProviderCatalogError, match="control characters") as excinfo:
        discover_provider_models(
            "openai",
            api_key="sk-safe\r\nX-Injected: yes",
            opener=opener,
        )

    assert excinfo.value.code == "credentials_invalid"
    assert called is False


def test_model_validation_never_switches_to_another_available_model():
    from omd.provider_models import validate_selected_model

    status = validate_selected_model(
        "openai",
        "requested-model",
        api_key="sk-test",
        opener=lambda request, timeout: _FakeResponse(
            {"data": [{"id": "different-model"}]}
        ),
    )

    assert status.available is False
    assert status.selected_model == "requested-model"
    assert status.alternative_models == ("different-model",)
    assert status.catalog_models == ("different-model",)


def test_model_validation_reports_exact_selected_model_as_available():
    from omd.provider_models import validate_selected_model

    status = validate_selected_model(
        "anthropic",
        "claude-sonnet-4-5-20250929",
        api_key="sk-test",
        opener=lambda request, timeout: _FakeResponse(
            {
                "data": [
                    {"id": "claude-opus-5-5"},
                    {"id": "claude-sonnet-4-5-20250929"},
                ]
            }
        ),
    )

    assert status.available is True
    assert status.selected_model == "claude-sonnet-4-5-20250929"
    assert status.alternative_models == ()
    assert status.catalog_models == (
        "claude-opus-5-5",
        "claude-sonnet-4-5-20250929",
    )
    assert status.answer_compatibility == "supported"
    assert status.answer_contract == "strict_json_schema"
    assert "Anthropic JSON schema" in status.answer_compatibility_reason


@pytest.mark.parametrize(
    ("provider", "model", "status", "contract"),
    [
        ("openai", "gpt-4.1", "supported", "strict_json_schema"),
        ("openai", "gpt-4.1-mini-2025-04-14", "supported", "strict_json_schema"),
        ("openai", "gpt-6-astra", "supported", "strict_json_schema"),
        ("openai", "gpt-5.6-terra", "supported", "strict_json_schema"),
        ("openai", "gpt-5.5-pro-2026-04-23", "unsupported", None),
        ("openai", "gpt-5.4-mini", "supported", "strict_json_schema"),
        ("openai", "gpt-5.2-chat-latest", "supported", "strict_json_schema"),
        ("openai", "o4-mini-2025-04-16", "supported", "strict_json_schema"),
        ("openai", "o3-pro", "unsupported", None),
        ("openai", "gpt-5.4-pro", "unsupported", None),
        ("openai", "gpt-5.2-pro-2025-12-11", "unsupported", None),
        ("openai", "gpt-4", "unsupported", None),
        ("openai", "future-openai-model", "unverified", None),
        ("anthropic", "claude-haiku-4-5-20251001", "supported", "strict_json_schema"),
        ("anthropic", "claude-opus-5-5", "supported", "strict_json_schema"),
        ("anthropic", "claude-sonnet-5-5", "supported", "strict_json_schema"),
        ("anthropic", "claude-sonnet-5", "supported", "strict_json_schema"),
        ("anthropic", "claude-opus-5-5-20260922", "unverified", None),
        ("anthropic", "claude-opus-5.5", "unverified", None),
        ("anthropic", "claude-sonnet-5-6", "unverified", None),
        ("anthropic", "claude-fable-5-1", "supported", "strict_json_schema"),
        ("anthropic", "claude-mythos-preview", "supported", "strict_json_schema"),
        ("anthropic", "claude-sonnet-4-20250514", "unsupported", None),
        ("anthropic", "future-claude-model", "unverified", None),
        ("deepseek", "deepseek-v4-flash", "supported", "json_mode_local_validation"),
        ("deepseek", "deepseek-chat", "unsupported", None),
        ("deepseek", "future-deepseek-model", "unverified", None),
    ],
)
def test_answer_compatibility_is_provider_and_model_specific(
    provider, model, status, contract
):
    from omd.provider_models import answer_model_compatibility

    result = answer_model_compatibility(provider, model)

    assert result.status == status
    assert result.contract == contract
    assert model in result.reason


def test_deepseek_compatibility_does_not_claim_provider_strict_schema():
    from omd.provider_models import answer_model_compatibility

    result = answer_model_compatibility("deepseek", "deepseek-flash")

    assert result.status == "supported"
    assert result.contract == "json_mode_local_validation"
    assert "local schema validation" in result.reason
    assert "does not enforce" in result.reason


def test_openai_non_streaming_model_is_not_approved_for_streaming_answers():
    from omd.provider_models import answer_model_compatibility

    result = answer_model_compatibility("openai", "gpt-5.5-pro")

    assert result.status == "unsupported"
    assert result.contract is None
    assert "streaming" in result.reason


def test_catalog_normalises_timeout_without_leaking_credentials():
    from omd.provider_models import ProviderCatalogError, discover_provider_models

    secret = "sk-private-key"

    def opener(request, timeout):
        raise TimeoutError("socket timed out with internal detail")

    with pytest.raises(ProviderCatalogError, match="openai model check timed out") as excinfo:
        discover_provider_models("openai", api_key=secret, opener=opener)

    assert excinfo.value.code == "timeout"
    assert secret not in str(excinfo.value)


def test_catalog_rejects_malformed_or_oversized_response():
    from omd.provider_models import ProviderCatalogError, discover_provider_models

    with pytest.raises(ProviderCatalogError, match="malformed"):
        discover_provider_models(
            "deepseek",
            api_key="sk-test",
            opener=lambda request, timeout: _FakeResponse({"data": [{"no_id": "x"}]}),
        )

    huge_model = "x" * (2 * 1024 * 1024)
    with pytest.raises(ProviderCatalogError, match="too large"):
        discover_provider_models(
            "openai",
            api_key="sk-test",
            opener=lambda request, timeout: _FakeResponse(
                {"data": [{"id": huge_model}]}
            ),
        )
