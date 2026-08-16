"""Explicit storage, sync, and AI processing policies for Phase 2."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ._models import recommended_local_text_model
from ._network_policy import validate_ollama_host


class AIMode(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_FOR_THIS_TASK = "cloud_for_this_task"
    # Read-only migration values. They are deliberately absent from public_ai_modes().
    ASK_BEFORE_CLOUD = "ask_before_cloud"
    CLOUD_ALLOWED = "cloud_allowed"


class AIProvider(str, Enum):
    NONE = "none"
    LOCAL_OLLAMA = "local_ollama"
    REMOTE_OLLAMA = "remote_ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    # Kept only so older serialized policy values fail closed with a clear reason.
    OPENROUTER = "openrouter"


class AICapability(str, Enum):
    MARKDOWN_POLISH = "markdown_polish"
    MEMORY_CARDS = "memory_cards"
    NOTE_ORGANISATION = "note_organisation"
    TRANSCRIPTION = "transcription"


_HOSTED_PROVIDERS = frozenset(
    {AIProvider.OPENAI, AIProvider.ANTHROPIC, AIProvider.DEEPSEEK}
)
_TEXT_CAPABILITIES = frozenset(
    {
        AICapability.MARKDOWN_POLISH,
        AICapability.MEMORY_CARDS,
        AICapability.NOTE_ORGANISATION,
    }
)
_PROVIDER_CAPABILITIES = {
    AIProvider.NONE: frozenset(),
    AIProvider.LOCAL_OLLAMA: _TEXT_CAPABILITIES,
    AIProvider.REMOTE_OLLAMA: _TEXT_CAPABILITIES,
    AIProvider.OPENAI: _TEXT_CAPABILITIES,
    AIProvider.ANTHROPIC: _TEXT_CAPABILITIES,
    AIProvider.DEEPSEEK: _TEXT_CAPABILITIES,
    AIProvider.OPENROUTER: frozenset(),
}


@dataclass(frozen=True)
class StoragePolicy:
    """Where canonical user-readable notes are written."""

    vault_path: str | None = None
    inbox_folder: str = "Inbox"
    notes_folder: str = "Notes"


@dataclass(frozen=True)
class SyncPolicy:
    """How capture envelopes move between devices, independent of storage."""

    enabled: bool = False
    transport: str = "none"
    transport_root: str | None = None


@dataclass(frozen=True)
class AIProcessingPolicy:
    """The selected AI destination. Evaluation never substitutes another provider."""

    mode: AIMode
    provider: AIProvider
    model: str | None
    endpoint: str | None = None
    capability: AICapability = AICapability.MARKDOWN_POLISH

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AIMode):
            raise ValueError("mode must be an AIMode")
        if not isinstance(self.provider, AIProvider):
            raise ValueError("provider must be an AIProvider")
        if not isinstance(self.capability, AICapability):
            raise ValueError("capability must be an AICapability")
        if self.provider is AIProvider.NONE:
            if self.model is not None or self.endpoint is not None:
                raise ValueError("the none provider must not specify a model or endpoint")
            return
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("an AI provider requires a non-empty model")
        if self.provider is AIProvider.LOCAL_OLLAMA:
            validate_ollama_host(self.endpoint or "http://localhost:11434")
        elif self.provider is AIProvider.REMOTE_OLLAMA:
            if not self.endpoint:
                raise ValueError("remote Ollama requires an explicit HTTPS endpoint")
            validate_ollama_host(self.endpoint, allow_remote=True)
        elif self.provider in _HOSTED_PROVIDERS and self.endpoint is not None:
            raise ValueError(f"{self.provider.value} uses its fixed endpoint")
        elif self.provider is AIProvider.OPENROUTER and self.endpoint is not None:
            raise ValueError("OpenRouter uses its fixed provider endpoint")


@dataclass(frozen=True)
class ProcessingReadiness:
    allowed: bool
    reason: str
    provider: AIProvider


def recommended_ai_processing_policy() -> AIProcessingPolicy:
    return AIProcessingPolicy(
        mode=AIMode.LOCAL_ONLY,
        provider=AIProvider.LOCAL_OLLAMA,
        model=recommended_local_text_model(),
    )


def public_ai_modes() -> tuple[AIMode, ...]:
    """Return only user-facing Phase 2 privacy choices."""
    return (AIMode.LOCAL_ONLY, AIMode.CLOUD_FOR_THIS_TASK)


def public_ai_providers() -> tuple[AIProvider, ...]:
    """Return supported user-facing providers; advanced endpoints stay separate."""
    return (
        AIProvider.NONE,
        AIProvider.LOCAL_OLLAMA,
        AIProvider.OPENAI,
        AIProvider.ANTHROPIC,
        AIProvider.DEEPSEEK,
    )


def evaluate_processing_readiness(
    policy: AIProcessingPolicy,
    *,
    consent_granted: bool = False,
    credentials_present: bool = False,
    service_available: bool = True,
    model_available: bool = True,
) -> ProcessingReadiness:
    """Evaluate only the selected provider; never choose a fallback provider."""
    provider = policy.provider
    if provider is AIProvider.NONE:
        return ProcessingReadiness(True, "no_ai", provider)

    if provider is AIProvider.OPENROUTER:
        return ProcessingReadiness(False, "provider_removed", provider)

    remote = provider is AIProvider.REMOTE_OLLAMA or provider in _HOSTED_PROVIDERS
    if remote and policy.mode is AIMode.LOCAL_ONLY:
        reason = (
            "provider_requires_cloud_for_this_task"
            if provider in _HOSTED_PROVIDERS
            else "provider_requires_explicit_non_local_mode"
        )
        return ProcessingReadiness(False, reason, provider)

    if remote and not consent_granted:
        return ProcessingReadiness(False, "consent_required", provider)

    if provider in _HOSTED_PROVIDERS and not credentials_present:
        return ProcessingReadiness(False, "credentials_missing", provider)

    if policy.capability not in _PROVIDER_CAPABILITIES[provider]:
        return ProcessingReadiness(False, "unsupported_capability", provider)

    if not service_available:
        return ProcessingReadiness(False, "service_unavailable", provider)
    if not model_available:
        return ProcessingReadiness(False, "model_unavailable", provider)
    return ProcessingReadiness(True, "ready", provider)
