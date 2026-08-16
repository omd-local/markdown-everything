"""Consent-gated AI task orchestration with no provider fallback."""
from __future__ import annotations

import json
import hashlib
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlsplit

from ._models import LOCAL_TEXT_CONTEXT_TOKENS, estimated_text_tokens
from ._network_policy import validate_ollama_base_url
from .ai_providers import (
    AIProviderError,
    AIProviderRequest,
    AIProviderResponse,
    request_text,
)
from .credentials import CredentialError, load_api_key
from .processing_policy import (
    AICapability,
    AIMode,
    AIProcessingPolicy,
    AIProvider,
    evaluate_processing_readiness,
)
from .provider_disclosure import (
    ProviderDisclosureUnavailableError,
    build_cloud_request_preview,
)
from .provider_models import (
    ModelAvailability,
    ProviderCatalogError,
    validate_selected_model,
)
from .structured_output import AIOutputSchema


_HOSTED = frozenset({"openai", "anthropic", "deepseek"})
_DEFAULT_CONSENT_TTL_SECONDS = 10 * 60
_MAX_OUTPUT_TOKENS = 8192
_MAX_CONTEXT_TOKENS = 128 * 1024
_HOSTED_CONTEXT_TOKENS = 32 * 1024
_PROVIDER_ENUM = {
    "ollama": AIProvider.LOCAL_OLLAMA,
    "openai": AIProvider.OPENAI,
    "anthropic": AIProvider.ANTHROPIC,
    "deepseek": AIProvider.DEEPSEEK,
}


@dataclass(frozen=True)
class AITextTask:
    provider: str
    model: str
    capability: str
    operation: str
    system_prompt: str
    max_output_tokens: int
    endpoint: str | None = None
    timeout_seconds: float = 45.0
    output_schema: AIOutputSchema | None = None
    stream: bool = True
    temperature: float | None = None
    allow_remote_ollama: bool = False
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model", "capability", "operation", "system_prompt"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        provider = self.provider.strip().lower()
        if provider not in _PROVIDER_ENUM:
            raise ValueError(f"unsupported provider: {self.provider}")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        try:
            AICapability(self.capability.strip())
        except ValueError as exc:
            raise ValueError(f"unsupported capability: {self.capability}") from exc
        if not self.operation.strip():
            raise ValueError("operation must not be empty")
        if type(self.max_output_tokens) is not int or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        if self.max_output_tokens > _MAX_OUTPUT_TOKENS:
            raise ValueError(f"max_output_tokens must be at most {_MAX_OUTPUT_TOKENS}")
        if self.endpoint is not None and not isinstance(self.endpoint, str):
            raise TypeError("endpoint must be a string or None")
        if provider in _HOSTED and self.endpoint is not None:
            raise ValueError(f"{provider} uses its fixed endpoint")
        if self.output_schema is not None and not isinstance(self.output_schema, AIOutputSchema):
            raise TypeError("output_schema must be an AIOutputSchema or None")
        if type(self.stream) is not bool:
            raise TypeError("stream must be a boolean")
        if type(self.allow_remote_ollama) is not bool:
            raise TypeError("allow_remote_ollama must be a boolean")
        if self.allow_remote_ollama and provider != "ollama":
            raise ValueError("allow_remote_ollama is valid only for Ollama")
        if self.context_window_tokens is not None:
            if type(self.context_window_tokens) is not int or self.context_window_tokens <= 0:
                raise ValueError("context_window_tokens must be a positive integer or None")
            if provider != "ollama":
                raise ValueError("context_window_tokens is valid only for Ollama")
            if self.context_window_tokens > _MAX_CONTEXT_TOKENS:
                raise ValueError(
                    f"context_window_tokens must be at most {_MAX_CONTEXT_TOKENS}"
                )
            if self.context_window_tokens < self.max_output_tokens:
                raise ValueError("context_window_tokens must include the output token budget")
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature must be a finite number between 0 and 2")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")


@dataclass(frozen=True)
class AIConsentGrant:
    """Short-lived approval bound to one exact hosted text task and source."""

    provider: str
    model: str
    capability: str
    destination_domain: str
    source_sha256: str
    task_sha256: str
    issued_at: float
    expires_at: float


@dataclass(frozen=True)
class AIRequestPreview:
    provider: str
    model: str
    capability: str
    operation: str
    privacy_mode: str
    destination_domain: str
    character_count: int
    estimated_input_tokens: int
    sends_attachment: bool
    policy_url: str | None
    data_handling_summary: str


@dataclass(frozen=True, init=False)
class AITextResult:
    provider: str
    requested_model: str
    actual_model: str
    capability: str
    privacy_mode: str
    destination_domain: str
    text: str = field(repr=False)
    _usage_json: str = field(repr=False)
    _timing_json: str = field(repr=False)
    _structured_json: str | None = field(repr=False)

    def __init__(
        self,
        *,
        provider: str,
        requested_model: str,
        actual_model: str,
        capability: str,
        privacy_mode: str,
        destination_domain: str,
        text: str,
        usage: dict[str, int],
        timing: dict[str, float],
        structured: Mapping[str, object] | None = None,
    ) -> None:
        values = {
            "provider": provider,
            "requested_model": requested_model,
            "actual_model": actual_model,
            "capability": capability,
            "privacy_mode": privacy_mode,
            "destination_domain": destination_domain,
            "text": text,
            "_usage_json": json.dumps(usage, sort_keys=True, allow_nan=False),
            "_timing_json": json.dumps(timing, sort_keys=True, allow_nan=False),
            "_structured_json": (
                json.dumps(dict(structured), sort_keys=True, allow_nan=False)
                if structured is not None
                else None
            ),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def usage(self) -> dict[str, int]:
        return json.loads(self._usage_json)

    @property
    def timing(self) -> dict[str, float]:
        return json.loads(self._timing_json)

    @property
    def structured(self) -> dict[str, object] | None:
        return json.loads(self._structured_json) if self._structured_json is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "capability": self.capability,
            "privacy_mode": self.privacy_mode,
            "destination_domain": self.destination_domain,
            "text": self.text,
            "usage": self.usage,
            "timing": self.timing,
            "structured": self.structured,
        }


class AIServiceError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code


def prepare_text_task(
    task: AITextTask,
    *,
    source_text: str,
    sends_attachment: bool = False,
    as_of: date | None = None,
) -> AIRequestPreview:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if type(sends_attachment) is not bool:
        raise TypeError("sends_attachment must be a boolean")
    _validate_context_budget(task, source_text)
    provider = task.provider.strip().lower()
    if provider in _HOSTED:
        cloud = build_cloud_request_preview(
            provider=provider,
            model=task.model,
            capability=task.capability,
            source_text=source_text,
            sends_attachment=sends_attachment,
            as_of=as_of,
        )
        return AIRequestPreview(
            provider=cloud.provider,
            model=cloud.model,
            capability=cloud.capability,
            operation=task.operation.strip(),
            privacy_mode=AIMode.CLOUD_FOR_THIS_TASK.value,
            destination_domain=cloud.destination_domain,
            character_count=cloud.character_count,
            estimated_input_tokens=cloud.estimated_input_tokens,
            sends_attachment=cloud.sends_attachment,
            policy_url=cloud.policy_url,
            data_handling_summary=cloud.data_handling_summary,
        )

    endpoint = (task.endpoint or "http://localhost:11434").strip()
    policy = _policy(task)
    destination = urlsplit(policy.endpoint or endpoint).hostname or "localhost"
    remote = policy.provider is AIProvider.REMOTE_OLLAMA
    return AIRequestPreview(
        provider="ollama",
        model=task.model.strip(),
        capability=task.capability.strip(),
        operation=task.operation.strip(),
        privacy_mode=policy.mode.value,
        destination_domain=destination,
        character_count=len(source_text),
        estimated_input_tokens=estimated_text_tokens(source_text),
        sends_attachment=sends_attachment,
        policy_url=None,
        data_handling_summary=(
            "Text is sent to the explicitly approved remote HTTPS Ollama endpoint."
            if remote
            else "Text is sent only to the selected loopback Ollama endpoint on this Mac."
        ),
    )


def create_text_task_consent(
    task: AITextTask,
    *,
    source_text: str,
    as_of: date | None = None,
    now: Callable[[], float] | None = None,
    ttl_seconds: float = _DEFAULT_CONSENT_TTL_SECONDS,
) -> AIConsentGrant:
    """Create a source-bound grant after the hosted request preview is shown."""
    provider = task.provider.strip().lower()
    policy = _policy(task)
    if provider not in _HOSTED and policy.provider is not AIProvider.REMOTE_OLLAMA:
        raise ValueError("local AI does not require a cloud consent grant")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, (int, float))
        or not math.isfinite(ttl_seconds)
        or ttl_seconds <= 0
    ):
        raise ValueError("ttl_seconds must be a positive finite number")
    preview = prepare_text_task(task, source_text=source_text, as_of=as_of)
    current = _current_time(now)
    return AIConsentGrant(
        provider=provider,
        model=task.model.strip(),
        capability=task.capability.strip(),
        destination_domain=preview.destination_domain,
        source_sha256=_source_sha256(source_text),
        task_sha256=_task_sha256(task),
        issued_at=current,
        expires_at=current + float(ttl_seconds),
    )


def execute_text_task(
    task: AITextTask,
    *,
    source_text: str,
    consent_granted: bool,
    consent_grant: AIConsentGrant | None = None,
    sends_attachment: bool = False,
    as_of: date | None = None,
    credential_loader: Callable[[str], str] = load_api_key,
    model_validator: Callable[..., ModelAvailability] = validate_selected_model,
    requester: Callable[[AIProviderRequest], AIProviderResponse] = request_text,
    now: Callable[[], float] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> AITextResult:
    provider = task.provider.strip().lower()
    _validate_context_budget(task, source_text)
    if is_cancelled is not None and not callable(is_cancelled):
        raise TypeError("is_cancelled must be callable or None")
    if is_cancelled is not None and is_cancelled():
        raise AIServiceError(provider, "cancelled", f"{provider} task cancelled")
    policy = _policy(task)
    if provider in _HOSTED and not consent_granted:
        raise AIServiceError(provider, "consent_required", "cloud consent is required for this task")

    preliminary = evaluate_processing_readiness(
        policy,
        consent_granted=consent_granted,
        # This pass validates mode and capability before credentials are touched.
        credentials_present=True,
    )
    if not preliminary.allowed:
        raise AIServiceError(provider, preliminary.reason, preliminary.reason.replace("_", " "))

    try:
        preview = prepare_text_task(
            task,
            source_text=source_text,
            sends_attachment=sends_attachment,
            as_of=as_of,
        )
    except ProviderDisclosureUnavailableError as exc:
        raise AIServiceError(
            provider,
            "disclosure_unavailable",
            f"{provider} disclosure is unavailable or out of date",
        ) from exc

    if provider in _HOSTED or policy.provider is AIProvider.REMOTE_OLLAMA:
        _validate_consent_grant(
            consent_grant,
            task=task,
            source_text=source_text,
            destination_domain=preview.destination_domain,
            current_time=_current_time(now),
        )

    api_key = None
    if provider in _HOSTED:
        try:
            api_key = credential_loader(provider)
        except (CredentialError, ValueError) as exc:
            raise AIServiceError(
                provider,
                "credentials_missing",
                f"{provider} API credential is unavailable",
            ) from exc
        if not isinstance(api_key, str) or not api_key.strip():
            raise AIServiceError(
                provider,
                "credentials_missing",
                f"{provider} API credential is unavailable",
            )

    try:
        model_kwargs = {
            "api_key": api_key,
            "endpoint": task.endpoint,
            "timeout_seconds": min(task.timeout_seconds, 10.0),
        }
        if task.allow_remote_ollama:
            model_kwargs["allow_remote_ollama"] = True
        availability = model_validator(provider, task.model.strip(), **model_kwargs)
    except ProviderCatalogError as exc:
        raise AIServiceError(provider, exc.code, str(exc)) from exc
    if (
        availability.provider != provider
        or availability.selected_model != task.model.strip()
    ):
        raise AIServiceError(provider, "model_check_invalid", "model check identity mismatch")
    if not availability.available:
        raise AIServiceError(
            provider,
            "model_unavailable",
            f"selected model {task.model.strip()} is unavailable for {provider}",
        )

    readiness = evaluate_processing_readiness(
        policy,
        consent_granted=consent_granted,
        credentials_present=api_key is not None if provider in _HOSTED else False,
        service_available=True,
        model_available=True,
    )
    if not readiness.allowed:
        raise AIServiceError(provider, readiness.reason, readiness.reason.replace("_", " "))
    if is_cancelled is not None and is_cancelled():
        raise AIServiceError(provider, "cancelled", f"{provider} task cancelled")

    request = AIProviderRequest(
        provider=provider,
        model=task.model.strip(),
        task=task.operation.strip(),
        system=task.system_prompt,
        user=source_text,
        max_output_tokens=task.max_output_tokens,
        api_key=api_key,
        timeout_seconds=task.timeout_seconds,
        endpoint=task.endpoint,
        is_cancelled=is_cancelled,
        output_schema=task.output_schema,
        stream=task.stream,
        temperature=task.temperature,
        allow_remote_ollama=task.allow_remote_ollama,
        context_window_tokens=task.context_window_tokens,
    )
    try:
        response = requester(request)
    except AIProviderError as exc:
        raise AIServiceError(provider, exc.code, str(exc)) from exc
    if response.provider != provider:
        raise AIServiceError(provider, "provider_mismatch", "provider response identity mismatch")
    return AITextResult(
        provider=provider,
        requested_model=task.model.strip(),
        actual_model=response.model,
        capability=task.capability.strip(),
        privacy_mode=preview.privacy_mode,
        destination_domain=preview.destination_domain,
        text=response.text,
        usage=response.usage,
        timing=response.timing,
        structured=response.structured,
    )


def _policy(task: AITextTask) -> AIProcessingPolicy:
    provider = task.provider.strip().lower()
    if provider == "ollama":
        endpoint = task.endpoint or "http://localhost:11434"
        remote = _ollama_endpoint_is_remote(
            endpoint,
            allow_remote=task.allow_remote_ollama,
        )
        return AIProcessingPolicy(
            mode=AIMode.CLOUD_FOR_THIS_TASK if remote else AIMode.LOCAL_ONLY,
            provider=AIProvider.REMOTE_OLLAMA if remote else AIProvider.LOCAL_OLLAMA,
            model=task.model.strip(),
            endpoint=task.endpoint,
            capability=AICapability(task.capability.strip()),
        )
    return AIProcessingPolicy(
        mode=AIMode.CLOUD_FOR_THIS_TASK if provider in _HOSTED else AIMode.LOCAL_ONLY,
        provider=_PROVIDER_ENUM[provider],
        model=task.model.strip(),
        endpoint=task.endpoint,
        capability=AICapability(task.capability.strip()),
    )


def _ollama_endpoint_is_remote(endpoint: str, *, allow_remote: bool) -> bool:
    try:
        validate_ollama_base_url(endpoint)
    except ValueError:
        validate_ollama_base_url(endpoint, allow_remote=allow_remote)
        return True
    return False


def _validate_context_budget(task: AITextTask, source_text: str) -> None:
    """Reject oversized tasks rather than silently truncating private source text."""
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    provider = task.provider.strip().lower()
    schema_text = (
        json.dumps(task.output_schema.schema, ensure_ascii=True, separators=(",", ":"))
        if task.output_schema is not None
        else ""
    )
    input_tokens = estimated_text_tokens(
        "\n".join((task.system_prompt, task.operation, source_text, schema_text))
    )
    context_tokens = _HOSTED_CONTEXT_TOKENS
    if provider == "ollama":
        context_tokens = task.context_window_tokens or LOCAL_TEXT_CONTEXT_TOKENS
    if input_tokens + task.max_output_tokens > context_tokens:
        raise AIServiceError(
            provider,
            "context_limit_exceeded",
            (
                f"{provider} task exceeds OMD's {context_tokens}-token context budget; "
                "reduce the source selection or output limit"
            ),
        )


def _validate_consent_grant(
    grant: AIConsentGrant | None,
    *,
    task: AITextTask,
    source_text: str,
    destination_domain: str,
    current_time: float,
) -> None:
    provider = task.provider.strip().lower()
    if not isinstance(grant, AIConsentGrant):
        raise AIServiceError(
            provider,
            "consent_preview_required",
            "a matching cloud request preview is required before consent",
        )
    if current_time >= grant.expires_at:
        raise AIServiceError(provider, "consent_expired", "cloud request preview consent expired")
    if current_time < grant.issued_at:
        raise AIServiceError(provider, "consent_mismatch", "cloud request preview does not match")
    expected = (
        provider,
        task.model.strip(),
        task.capability.strip(),
        destination_domain,
        _source_sha256(source_text),
        _task_sha256(task),
    )
    actual = (
        grant.provider,
        grant.model,
        grant.capability,
        grant.destination_domain,
        grant.source_sha256,
        grant.task_sha256,
    )
    if actual != expected:
        raise AIServiceError(
            provider,
            "consent_mismatch",
            "cloud request preview does not match the current task",
        )


def _source_sha256(source_text: str) -> str:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _task_sha256(task: AITextTask) -> str:
    payload = {
        "provider": task.provider.strip().lower(),
        "model": task.model.strip(),
        "capability": task.capability.strip(),
        "operation": task.operation.strip(),
        "system_prompt": task.system_prompt,
        "max_output_tokens": task.max_output_tokens,
        "endpoint": task.endpoint,
        "output_schema": task.output_schema.schema if task.output_schema is not None else None,
        "stream": task.stream,
    }
    if task.temperature is not None:
        payload["temperature"] = float(task.temperature)
    if task.allow_remote_ollama:
        payload["allow_remote_ollama"] = True
    if task.context_window_tokens is not None:
        payload["context_window_tokens"] = task.context_window_tokens
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _current_time(now: Callable[[], float] | None) -> float:
    current = (now or time.time)()
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
        raise ValueError("clock must return a finite timestamp")
    return float(current)
