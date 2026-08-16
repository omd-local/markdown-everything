from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from omd._models import recommended_local_text_model


def _processing_policy_module():
    import omd.processing_policy as processing_policy

    return processing_policy


def _allowed_and_reason(result):
    if isinstance(result, dict):
        return result["allowed"], result["reason"]
    return result.allowed, result.reason


def test_ai_mode_enum_exposes_expected_values():
    processing_policy = _processing_policy_module()

    assert processing_policy.AIMode.LOCAL_ONLY.value == "local_only"
    assert processing_policy.AIMode.ASK_BEFORE_CLOUD.value == "ask_before_cloud"
    assert processing_policy.AIMode.CLOUD_ALLOWED.value == "cloud_allowed"


def test_ai_provider_enum_exposes_expected_values():
    processing_policy = _processing_policy_module()

    assert processing_policy.AIProvider.NONE.value == "none"
    assert processing_policy.AIProvider.LOCAL_OLLAMA.value == "local_ollama"
    assert processing_policy.AIProvider.REMOTE_OLLAMA.value == "remote_ollama"
    assert processing_policy.AIProvider.OPENROUTER.value == "openrouter"


def test_storage_policy_is_a_frozen_dataclass():
    processing_policy = _processing_policy_module()

    assert is_dataclass(processing_policy.StoragePolicy)
    assert processing_policy.StoragePolicy.__dataclass_params__.frozen is True


def test_sync_policy_is_a_frozen_dataclass():
    processing_policy = _processing_policy_module()

    assert is_dataclass(processing_policy.SyncPolicy)
    assert processing_policy.SyncPolicy.__dataclass_params__.frozen is True


def test_ai_processing_policy_is_a_frozen_dataclass():
    processing_policy = _processing_policy_module()

    assert is_dataclass(processing_policy.AIProcessingPolicy)
    assert processing_policy.AIProcessingPolicy.__dataclass_params__.frozen is True


def test_ai_processing_policy_rejects_mutation():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.LOCAL_ONLY,
        provider=processing_policy.AIProvider.NONE,
        model=None,
    )

    with pytest.raises(FrozenInstanceError):
        policy.model = "qwen3:4b-instruct"


def test_evaluate_processing_readiness_allows_no_ai_provider():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.LOCAL_ONLY,
        provider=processing_policy.AIProvider.NONE,
        model=None,
    )

    allowed, reason = _allowed_and_reason(processing_policy.evaluate_processing_readiness(policy))

    assert allowed is True
    assert reason == "no_ai"


def test_evaluate_processing_readiness_denies_local_ollama_when_service_is_unavailable():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.LOCAL_ONLY,
        provider=processing_policy.AIProvider.LOCAL_OLLAMA,
        model="qwen3:4b-instruct",
    )

    allowed, reason = _allowed_and_reason(
        processing_policy.evaluate_processing_readiness(policy, service_available=False)
    )

    assert allowed is False
    assert reason == "service_unavailable"


def test_evaluate_processing_readiness_denies_local_ollama_when_model_is_unavailable():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.LOCAL_ONLY,
        provider=processing_policy.AIProvider.LOCAL_OLLAMA,
        model="qwen3:4b-instruct",
    )

    allowed, reason = _allowed_and_reason(
        processing_policy.evaluate_processing_readiness(policy, model_available=False)
    )

    assert allowed is False
    assert reason == "model_unavailable"


def test_evaluate_processing_readiness_denies_remote_ollama_in_local_only_mode():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.LOCAL_ONLY,
        provider=processing_policy.AIProvider.REMOTE_OLLAMA,
        model="qwen3:4b-instruct",
        endpoint="https://models.example.com",
    )

    allowed, reason = _allowed_and_reason(processing_policy.evaluate_processing_readiness(policy))

    assert allowed is False
    assert reason == "provider_requires_explicit_non_local_mode"


def test_legacy_cloud_allowed_does_not_bypass_per_task_consent():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.CLOUD_ALLOWED,
        provider=processing_policy.AIProvider.REMOTE_OLLAMA,
        model="qwen3:4b-instruct",
        endpoint="https://models.example.com",
    )

    allowed, reason = _allowed_and_reason(processing_policy.evaluate_processing_readiness(policy))

    assert allowed is False
    assert reason == "consent_required"


def test_legacy_openrouter_policy_is_always_removed():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.ASK_BEFORE_CLOUD,
        provider=processing_policy.AIProvider.OPENROUTER,
        model="openrouter/auto",
    )

    allowed, reason = _allowed_and_reason(
        processing_policy.evaluate_processing_readiness(
            policy,
            consent_granted=True,
            credentials_present=True,
        )
    )

    assert allowed is False
    assert reason == "provider_removed"


def test_recommended_ai_processing_policy_uses_recommended_local_text_model(monkeypatch):
    processing_policy = _processing_policy_module()
    monkeypatch.setattr(processing_policy, "recommended_local_text_model", lambda: "demo:model")

    policy = processing_policy.recommended_ai_processing_policy()

    assert policy.provider == processing_policy.AIProvider.LOCAL_OLLAMA
    assert policy.mode == processing_policy.AIMode.LOCAL_ONLY
    assert policy.model == "demo:model"


def test_recommended_ai_processing_policy_matches_current_model_helper_by_default():
    processing_policy = _processing_policy_module()

    policy = processing_policy.recommended_ai_processing_policy()

    assert policy.model == recommended_local_text_model()


def test_ai_processing_policy_rejects_unknown_string_provider():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="provider"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.LOCAL_ONLY,
            provider="custom_cloud",
            model="model",
        )


def test_ai_processing_policy_rejects_unknown_string_mode():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="mode"):
        processing_policy.AIProcessingPolicy(
            mode="automatic",
            provider=processing_policy.AIProvider.LOCAL_OLLAMA,
            model="model",
        )


def test_remote_ollama_in_ask_mode_requires_request_consent():
    processing_policy = _processing_policy_module()
    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.ASK_BEFORE_CLOUD,
        provider=processing_policy.AIProvider.REMOTE_OLLAMA,
        model="qwen3:4b-instruct",
        endpoint="https://models.example.com",
    )

    readiness = processing_policy.evaluate_processing_readiness(policy)

    assert readiness.allowed is False
    assert readiness.reason == "consent_required"


def test_local_ollama_policy_rejects_remote_endpoint_misclassification():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="Remote Ollama"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.LOCAL_ONLY,
            provider=processing_policy.AIProvider.LOCAL_OLLAMA,
            model="qwen3:4b-instruct",
            endpoint="https://models.example.com",
        )


def test_remote_ollama_policy_requires_https_remote_endpoint():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="HTTPS"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.CLOUD_ALLOWED,
            provider=processing_policy.AIProvider.REMOTE_OLLAMA,
            model="qwen3:4b-instruct",
            endpoint="http://models.example.com",
        )


def test_remote_ollama_policy_accepts_explicit_https_endpoint():
    processing_policy = _processing_policy_module()

    policy = processing_policy.AIProcessingPolicy(
        mode=processing_policy.AIMode.CLOUD_ALLOWED,
        provider=processing_policy.AIProvider.REMOTE_OLLAMA,
        model="qwen3:4b-instruct",
        endpoint="https://models.example.com",
    )

    assert policy.endpoint == "https://models.example.com"


def test_none_provider_rejects_model_or_endpoint():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="none provider"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.LOCAL_ONLY,
            provider=processing_policy.AIProvider.NONE,
            model="unexpected",
        )


def test_ai_provider_rejects_blank_model():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="non-empty model"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.LOCAL_ONLY,
            provider=processing_policy.AIProvider.LOCAL_OLLAMA,
            model="   ",
        )


def test_remote_ollama_requires_explicit_endpoint():
    processing_policy = _processing_policy_module()

    with pytest.raises(ValueError, match="explicit HTTPS endpoint"):
        processing_policy.AIProcessingPolicy(
            mode=processing_policy.AIMode.CLOUD_ALLOWED,
            provider=processing_policy.AIProvider.REMOTE_OLLAMA,
            model="qwen3:4b-instruct",
        )
