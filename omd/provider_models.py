"""Provider-specific model discovery and exact model availability checks."""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from ._network_policy import build_no_redirect_opener, build_ollama_opener, validate_ollama_base_url


_MAX_CATALOG_BYTES = 1024 * 1024
_HOSTED_CATALOGS = {
    "openai": ("https://api.openai.com/v1/models", "api.openai.com"),
    "anthropic": ("https://api.anthropic.com/v1/models?limit=1000", "api.anthropic.com"),
    "deepseek": ("https://api.deepseek.com/models", "api.deepseek.com"),
}


@dataclass(frozen=True)
class ProviderModelCatalog:
    provider: str
    destination_domain: str
    models: tuple[str, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class ModelAvailability:
    provider: str
    selected_model: str
    available: bool
    destination_domain: str
    alternative_models: tuple[str, ...]
    elapsed_seconds: float
    answer_compatibility: str
    answer_compatibility_reason: str
    answer_contract: str | None
    catalog_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerModelCompatibility:
    status: str
    reason: str
    contract: str | None


class ProviderCatalogError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code


def discover_provider_models(
    provider: str,
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    timeout_seconds: float = 5.0,
    opener=None,
    clock: Callable[[], float] | None = None,
    allow_remote_ollama: bool = False,
) -> ProviderModelCatalog:
    normalized_provider = _provider(provider)
    if type(allow_remote_ollama) is not bool:
        raise TypeError("allow_remote_ollama must be a boolean")
    if allow_remote_ollama and normalized_provider != "ollama":
        raise ValueError("allow_remote_ollama is valid only for Ollama")
    _timeout(timeout_seconds)
    request, destination = _catalog_request(
        normalized_provider,
        api_key=api_key,
        endpoint=endpoint,
        allow_remote_ollama=allow_remote_ollama,
    )
    open_call = opener
    if open_call is None:
        if normalized_provider == "ollama":
            open_call = build_ollama_opener(request.full_url).open
        else:
            open_call = build_no_redirect_opener().open
    now = clock or monotonic
    started_at = now()
    try:
        with open_call(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_CATALOG_BYTES + 1)
            if len(raw) > _MAX_CATALOG_BYTES:
                raise ProviderCatalogError(
                    normalized_provider,
                    "response_too_large",
                    f"{normalized_provider} model catalog response is too large",
                )
            payload = json.loads(raw.decode("utf-8"))
    except ProviderCatalogError:
        raise
    except TimeoutError as exc:
        raise ProviderCatalogError(
            normalized_provider,
            "timeout",
            f"{normalized_provider} model check timed out",
        ) from exc
    except urllib.error.HTTPError as exc:
        raise ProviderCatalogError(
            normalized_provider,
            "http_error",
            f"{normalized_provider} model check returned HTTP {exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        if "timed out" in str(exc.reason).lower():
            raise ProviderCatalogError(
                normalized_provider,
                "timeout",
                f"{normalized_provider} model check timed out",
            ) from exc
        raise ProviderCatalogError(
            normalized_provider,
            "transport_error",
            f"{normalized_provider} model check could not connect",
        ) from exc
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderCatalogError(
            normalized_provider,
            "malformed_response",
            f"{normalized_provider} model catalog response is malformed",
        ) from exc

    models = _catalog_models(normalized_provider, payload)
    return ProviderModelCatalog(
        provider=normalized_provider,
        destination_domain=destination,
        models=models,
        elapsed_seconds=max(0.0, now() - started_at),
    )


def validate_selected_model(
    provider: str,
    selected_model: str,
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    timeout_seconds: float = 5.0,
    opener=None,
    allow_remote_ollama: bool = False,
) -> ModelAvailability:
    if not isinstance(selected_model, str) or not selected_model.strip():
        raise ValueError("selected_model must not be empty")
    normalized_model = selected_model.strip()
    catalog = discover_provider_models(
        provider,
        api_key=api_key,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        opener=opener,
        allow_remote_ollama=allow_remote_ollama,
    )
    available = normalized_model in catalog.models
    compatibility = answer_model_compatibility(catalog.provider, normalized_model)
    return ModelAvailability(
        provider=catalog.provider,
        selected_model=normalized_model,
        available=available,
        destination_domain=catalog.destination_domain,
        alternative_models=() if available else catalog.models[:20],
        elapsed_seconds=catalog.elapsed_seconds,
        answer_compatibility=compatibility.status,
        answer_compatibility_reason=compatibility.reason,
        answer_contract=compatibility.contract,
        catalog_models=catalog.models,
    )


def answer_model_compatibility(provider: str, model: str) -> AnswerModelCompatibility:
    """Return OMD's exact provider/model answer contract without probing user content.

    Catalog membership proves availability only. This deliberately uses a conservative
    provider-specific allowlist so new or renamed models remain unverified until OMD's
    request shape has been checked against the provider contract.
    """
    normalized_provider = _provider(provider)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must not be empty")
    selected = model.strip()
    normalized_model = selected.lower()

    if normalized_provider == "ollama":
        return AnswerModelCompatibility(
            status="supported",
            reason=(
                f"{selected} uses Ollama's local JSON schema output with OMD validation."
            ),
            contract="local_json_schema",
        )
    if normalized_provider == "openai":
        return _openai_answer_compatibility(selected, normalized_model)
    if normalized_provider == "anthropic":
        return _anthropic_answer_compatibility(selected, normalized_model)
    return _deepseek_answer_compatibility(selected, normalized_model)


def _openai_answer_compatibility(
    selected: str, normalized_model: str
) -> AnswerModelCompatibility:
    # This is deliberately a model-family registry rather than a broad gpt-* match.
    # OpenAI's catalog proves that an ID exists, but does not publish capability
    # flags; notably, some Pro variants do not support Structured Outputs even
    # when the base model does.
    supported_families = (
        "gpt-6-astra",
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.2-chat-latest",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-pro",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o-mini",
        "o1",
        "o3",
        "o3-mini",
        "o4-mini",
    )
    no_strict_schema_families = ("gpt-5.2-pro", "gpt-5.4-pro")
    no_streaming_families = ("gpt-5.5-pro", "o1-pro", "o3-pro")
    if normalized_model == "gpt-4o":
        return _supported_strict(selected, "OpenAI")
    if normalized_model.startswith("gpt-4o-"):
        snapshot = normalized_model.removeprefix("gpt-4o-")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot):
            if snapshot >= "2024-08-06":
                return _supported_strict(selected, "OpenAI")
            return AnswerModelCompatibility(
                status="unsupported",
                reason=(
                    f"{selected} predates OpenAI strict JSON schema output support "
                    "required by OMD answers."
                ),
                contract=None,
            )
    if any(_model_family_match(normalized_model, family) for family in supported_families):
        return _supported_strict(selected, "OpenAI")
    if any(
        _model_family_match(normalized_model, family)
        for family in no_strict_schema_families
    ):
        return AnswerModelCompatibility(
            status="unsupported",
            reason=(
                f"{selected} does not support the OpenAI strict JSON schema output "
                "required by OMD answers."
            ),
            contract=None,
        )
    if any(
        _model_family_match(normalized_model, family)
        for family in no_streaming_families
    ):
        return AnswerModelCompatibility(
            status="unsupported",
            reason=(
                f"{selected} does not support streaming, which is required by "
                "OMD's hosted answer request contract."
            ),
            contract=None,
        )
    if re.match(r"^gpt-(?:3(?:\.5)?|4)(?:-|$)", normalized_model):
        return AnswerModelCompatibility(
            status="unsupported",
            reason=(
                f"{selected} does not support the OpenAI strict JSON schema output "
                "required by OMD answers."
            ),
            contract=None,
        )
    return _unverified(selected, "OpenAI")


def _anthropic_answer_compatibility(
    selected: str, normalized_model: str
) -> AnswerModelCompatibility:
    supported = re.fullmatch(
        r"claude-(?:(?:opus|sonnet)-4-5(?:-\d{8})?|haiku-4-5(?:-\d{8})?"
        r"|opus-(?:4-6|4-7|4-8|5|5-5)|sonnet-(?:4-6|5|5-5)"
        r"|fable-(?:5|5-1)|mythos-(?:preview|5|5-1))",
        normalized_model,
    )
    if supported:
        return _supported_strict(selected, "Anthropic")
    if normalized_model.startswith("claude-3-") or re.fullmatch(
        r"claude-(?:opus|sonnet)-4(?:-\d{8})?", normalized_model
    ):
        return AnswerModelCompatibility(
            status="unsupported",
            reason=(
                f"{selected} is not supported by Anthropic JSON schema outputs "
                "required by OMD answers."
            ),
            contract=None,
        )
    return _unverified(selected, "Anthropic")


def _deepseek_answer_compatibility(
    selected: str, normalized_model: str
) -> AnswerModelCompatibility:
    if normalized_model in {
        "deepseek-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "deepseek-v4-pro",
    }:
        return AnswerModelCompatibility(
            status="supported",
            reason=(
                f"{selected} uses DeepSeek JSON mode followed by OMD's local schema "
                "validation; DeepSeek does not enforce OMD's schema as a strict provider contract."
            ),
            contract="json_mode_local_validation",
        )
    if normalized_model in {"deepseek-chat", "deepseek-reasoner"}:
        return AnswerModelCompatibility(
            status="unsupported",
            reason=(
                f"{selected} is a discontinued DeepSeek model ID. Choose an exact "
                "model returned by the current provider catalog."
            ),
            contract=None,
        )
    return _unverified(selected, "DeepSeek")


def _supported_strict(selected: str, provider_label: str) -> AnswerModelCompatibility:
    return AnswerModelCompatibility(
        status="supported",
        reason=(
            f"{selected} supports the {provider_label} JSON schema output contract "
            "required by OMD answers."
        ),
        contract="strict_json_schema",
    )


def _unverified(selected: str, provider_label: str) -> AnswerModelCompatibility:
    return AnswerModelCompatibility(
        status="unverified",
        reason=(
            f"OMD has not verified {selected} against the {provider_label} structured "
            "answer contract. Catalog availability alone does not prove compatibility."
        ),
        contract=None,
    )


def _model_family_match(model: str, family: str) -> bool:
    if model == family:
        return True
    suffix = model.removeprefix(f"{family}-")
    return model.startswith(f"{family}-") and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", suffix)
    )


def _catalog_request(
    provider: str,
    *,
    api_key: str | None,
    endpoint: str | None,
    allow_remote_ollama: bool,
) -> tuple[urllib.request.Request, str]:
    if provider == "ollama":
        host = (endpoint or "http://localhost:11434").strip()
        validate_ollama_base_url(host, allow_remote=allow_remote_ollama)
        if "://" not in host:
            host = f"http://{host}"
        parsed = urlsplit(host)
        base = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        url = f"{base}/api/tags"
        return urllib.request.Request(url, method="GET"), parsed.hostname or "localhost"

    if endpoint is not None:
        raise ValueError(f"{provider} model discovery uses its fixed endpoint")
    url, destination = _HOSTED_CATALOGS[provider]
    secret = _api_key(provider, api_key)
    if provider == "anthropic":
        headers = {
            "x-api-key": secret,
            "anthropic-version": "2023-06-01",
        }
    else:
        headers = {"Authorization": f"Bearer {secret}"}
    return urllib.request.Request(url, headers=headers, method="GET"), destination


def _catalog_models(provider: str, payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise ProviderCatalogError(
            provider,
            "malformed_response",
            f"{provider} model catalog response is malformed",
        )
    key = "models" if provider == "ollama" else "data"
    items = payload.get(key)
    if not isinstance(items, list):
        raise ProviderCatalogError(
            provider,
            "malformed_response",
            f"{provider} model catalog response is malformed",
        )
    names = set()
    for item in items:
        if not isinstance(item, dict):
            raise ProviderCatalogError(
                provider,
                "malformed_response",
                f"{provider} model catalog response is malformed",
            )
        if provider == "ollama":
            value = item.get("name") or item.get("model")
        else:
            value = item.get("id")
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ProviderCatalogError(
                provider,
                "malformed_response",
                f"{provider} model catalog response is malformed",
            )
        names.add(value.strip())
    if not names:
        raise ProviderCatalogError(
            provider,
            "malformed_response",
            f"{provider} model catalog response is malformed",
        )
    return tuple(sorted(names))


def _provider(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("provider must be a string")
    provider = value.strip().lower()
    if provider not in {"ollama", *_HOSTED_CATALOGS}:
        raise ValueError(f"unsupported provider: {value}")
    return provider


def _api_key(provider: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderCatalogError(
            provider,
            "credentials_missing",
            f"{provider} API key is required for the model check",
        )
    normalized = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ProviderCatalogError(
            provider,
            "credentials_invalid",
            f"{provider} API key contains invalid control characters",
        )
    return normalized


def _timeout(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")
