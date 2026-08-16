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


def test_model_validation_reports_exact_selected_model_as_available():
    from omd.provider_models import validate_selected_model

    status = validate_selected_model(
        "anthropic",
        "claude-selected",
        api_key="sk-test",
        opener=lambda request, timeout: _FakeResponse(
            {"data": [{"id": "claude-selected"}]}
        ),
    )

    assert status.available is True
    assert status.selected_model == "claude-selected"
    assert status.alternative_models == ()


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
