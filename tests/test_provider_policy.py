from __future__ import annotations

import pytest

from omd.processing_policy import (
    AICapability,
    AIMode,
    AIProcessingPolicy,
    AIProvider,
    evaluate_processing_readiness,
    public_ai_modes,
    public_ai_providers,
)


def _cloud_policy(provider: AIProvider) -> AIProcessingPolicy:
    return AIProcessingPolicy(
        mode=AIMode.CLOUD_FOR_THIS_TASK,
        provider=provider,
        model="provider-model",
        capability=AICapability.MARKDOWN_POLISH,
    )


def test_public_modes_expose_only_local_only_and_cloud_for_this_task():
    assert public_ai_modes() == (
        AIMode.LOCAL_ONLY,
        AIMode.CLOUD_FOR_THIS_TASK,
    )


def test_public_providers_omit_openrouter_and_advanced_remote_ollama():
    assert public_ai_providers() == (
        AIProvider.NONE,
        AIProvider.LOCAL_OLLAMA,
        AIProvider.OPENAI,
        AIProvider.ANTHROPIC,
        AIProvider.DEEPSEEK,
    )


@pytest.mark.parametrize(
    "provider",
    [AIProvider.OPENAI, AIProvider.ANTHROPIC, AIProvider.DEEPSEEK],
)
def test_hosted_provider_requires_cloud_for_this_task(provider):
    policy = AIProcessingPolicy(
        mode=AIMode.LOCAL_ONLY,
        provider=provider,
        model="provider-model",
        capability=AICapability.MARKDOWN_POLISH,
    )

    readiness = evaluate_processing_readiness(
        policy,
        consent_granted=True,
        credentials_present=True,
    )

    assert readiness.allowed is False
    assert readiness.reason == "provider_requires_cloud_for_this_task"


def test_cloud_consent_is_required_for_each_readiness_evaluation():
    policy = _cloud_policy(AIProvider.OPENAI)

    first = evaluate_processing_readiness(
        policy,
        consent_granted=True,
        credentials_present=True,
    )
    second = evaluate_processing_readiness(
        policy,
        credentials_present=True,
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "consent_required"


@pytest.mark.parametrize(
    "provider",
    [AIProvider.OPENAI, AIProvider.ANTHROPIC, AIProvider.DEEPSEEK],
)
def test_hosted_provider_requires_its_own_credential(provider):
    readiness = evaluate_processing_readiness(
        _cloud_policy(provider),
        consent_granted=True,
        credentials_present=False,
    )

    assert readiness.allowed is False
    assert readiness.reason == "credentials_missing"
    assert readiness.provider is provider


def test_openrouter_legacy_value_is_fail_closed():
    policy = AIProcessingPolicy(
        mode=AIMode.CLOUD_FOR_THIS_TASK,
        provider=AIProvider.OPENROUTER,
        model="openrouter/auto",
        capability=AICapability.MARKDOWN_POLISH,
    )

    readiness = evaluate_processing_readiness(
        policy,
        consent_granted=True,
        credentials_present=True,
    )

    assert readiness.allowed is False
    assert readiness.reason == "provider_removed"


def test_unsupported_capability_is_denied_without_switching_provider():
    policy = AIProcessingPolicy(
        mode=AIMode.LOCAL_ONLY,
        provider=AIProvider.LOCAL_OLLAMA,
        model="qwen3:4b-instruct",
        capability=AICapability.TRANSCRIPTION,
    )

    readiness = evaluate_processing_readiness(policy)

    assert readiness.allowed is False
    assert readiness.reason == "unsupported_capability"
    assert readiness.provider is AIProvider.LOCAL_OLLAMA


@pytest.mark.parametrize(
    "provider",
    [AIProvider.OPENAI, AIProvider.ANTHROPIC, AIProvider.DEEPSEEK],
)
def test_hosted_provider_rejects_custom_destination(provider):
    with pytest.raises(ValueError, match="fixed endpoint"):
        AIProcessingPolicy(
            mode=AIMode.CLOUD_FOR_THIS_TASK,
            provider=provider,
            model="provider-model",
            capability=AICapability.MARKDOWN_POLISH,
            endpoint="https://proxy.example.com",
        )
