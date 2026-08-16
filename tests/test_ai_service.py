from __future__ import annotations

from datetime import date

import pytest


def _availability(provider: str, model: str, *, available: bool = True):
    from omd.provider_models import ModelAvailability

    return ModelAvailability(
        provider=provider,
        selected_model=model,
        available=available,
        destination_domain=("localhost" if provider == "ollama" else f"api.{provider}.com"),
        alternative_models=() if available else ("other-model",),
        elapsed_seconds=0.01,
    )


def test_cloud_execution_requires_fresh_per_task_consent_before_key_or_network_access():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="consent") as excinfo:
        execute_text_task(
            task,
            source_text="Private note",
            consent_granted=False,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2026, 7, 19),
        )

    assert excinfo.value.code == "consent_required"
    assert calls == []


def test_cloud_checkbox_without_matching_preview_grant_does_not_send():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls: list[str] = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="preview") as excinfo:
        execute_text_task(
            task,
            source_text="Private note",
            consent_granted=True,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2026, 7, 19),
            now=lambda: 100.0,
        )

    assert excinfo.value.code == "consent_preview_required"
    assert calls == []


def test_cloud_preview_grant_expires_before_credentials_are_loaded():
    from omd.ai_service import (
        AIServiceError,
        AITextTask,
        create_text_task_consent,
        execute_text_task,
    )

    calls: list[str] = []
    source = "Private note"
    task = AITextTask(
        provider="anthropic",
        model="claude-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )
    grant = create_text_task_consent(
        task,
        source_text=source,
        as_of=date(2026, 7, 19),
        now=lambda: 100.0,
        ttl_seconds=30,
    )

    with pytest.raises(AIServiceError, match="expired") as excinfo:
        execute_text_task(
            task,
            source_text=source,
            consent_granted=True,
            consent_grant=grant,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2026, 7, 19),
            now=lambda: 131.0,
        )

    assert excinfo.value.code == "consent_expired"
    assert calls == []


def test_cloud_preview_grant_is_bound_to_exact_source_and_model():
    from omd.ai_service import (
        AIServiceError,
        AITextTask,
        create_text_task_consent,
        execute_text_task,
    )

    task = AITextTask(
        provider="deepseek",
        model="deepseek-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )
    grant = create_text_task_consent(
        task,
        source_text="Original source",
        as_of=date(2026, 7, 19),
        now=lambda: 100.0,
    )

    with pytest.raises(AIServiceError, match="does not match") as excinfo:
        execute_text_task(
            task,
            source_text="Changed source",
            consent_granted=True,
            consent_grant=grant,
            credential_loader=lambda provider: "sk-secret",
            model_validator=lambda provider, model, **kwargs: _availability(provider, model),
            requester=lambda request: None,
            as_of=date(2026, 7, 19),
            now=lambda: 101.0,
        )

    assert excinfo.value.code == "consent_mismatch"


def test_cancelled_task_stops_before_credentials_or_network_access():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls: list[str] = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="cancelled") as excinfo:
        execute_text_task(
            task,
            source_text="Private note",
            consent_granted=True,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            is_cancelled=lambda: True,
            as_of=date(2026, 7, 19),
        )

    assert excinfo.value.code == "cancelled"
    assert calls == []


def test_oversized_context_is_rejected_before_credentials_or_network_access():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls: list[str] = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="context budget") as excinfo:
        execute_text_task(
            task,
            source_text="x" * 140_000,
            consent_granted=True,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2026, 7, 19),
        )

    assert excinfo.value.code == "context_limit_exceeded"
    assert calls == []


def test_output_budget_above_provider_safety_limit_is_rejected():
    from omd.ai_service import AITextTask

    with pytest.raises(ValueError, match="at most 8192"):
        AITextTask(
            provider="anthropic",
            model="claude-selected",
            capability="note_organisation",
            operation="Organise this note",
            system_prompt="Keep source claims unchanged.",
            max_output_tokens=8193,
        )


def test_local_preview_rejects_source_that_will_not_fit_selected_runtime_budget():
    from omd.ai_service import AIServiceError, AITextTask, prepare_text_task

    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="context budget") as excinfo:
        prepare_text_task(task, source_text="x" * 20_000)

    assert excinfo.value.code == "context_limit_exceeded"


def test_local_task_can_opt_into_a_larger_context_and_passes_it_to_transport():
    from omd.ai_providers import AIProviderResponse
    from omd.ai_service import AITextTask, execute_text_task

    seen = {}
    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep source claims unchanged.",
        max_output_tokens=128,
        context_window_tokens=32 * 1024,
    )

    def requester(request):
        seen["request"] = request
        return AIProviderResponse(
            provider="ollama",
            model="qwen3:4b-instruct",
            text="Draft",
            usage={},
            timing={},
        )

    result = execute_text_task(
        task,
        source_text="Long local note. " * 1_500,
        consent_granted=False,
        model_validator=lambda provider, model, **kwargs: _availability(provider, model),
        requester=requester,
    )

    assert result.text == "Draft"
    assert seen["request"].context_window_tokens == 32 * 1024


def test_stale_cloud_disclosure_blocks_request_before_credential_access():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls = []
    task = AITextTask(
        provider="anthropic",
        model="claude-selected",
        capability="markdown_polish",
        operation="Polish Markdown",
        system_prompt="Preserve meaning.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError, match="disclosure") as excinfo:
        execute_text_task(
            task,
            source_text="Private note",
            consent_granted=True,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2027, 1, 19),
        )

    assert excinfo.value.code == "disclosure_unavailable"
    assert calls == []


def test_unavailable_selected_model_is_not_replaced_or_sent():
    from omd.ai_service import (
        AIServiceError,
        AITextTask,
        create_text_task_consent,
        execute_text_task,
    )

    requested = []
    task = AITextTask(
        provider="deepseek",
        model="requested-model",
        capability="memory_cards",
        operation="Create memory cards",
        system_prompt="Use only the source.",
        max_output_tokens=128,
    )

    source = "Private note"
    grant = create_text_task_consent(
        task,
        source_text=source,
        as_of=date(2026, 7, 19),
        now=lambda: 100.0,
    )

    with pytest.raises(AIServiceError, match="requested-model") as excinfo:
        execute_text_task(
            task,
            source_text=source,
            consent_granted=True,
            consent_grant=grant,
            credential_loader=lambda provider: "sk-secret",
            model_validator=lambda provider, model, **kwargs: _availability(
                provider, model, available=False
            ),
            requester=lambda request: requested.append(request),
            as_of=date(2026, 7, 19),
            now=lambda: 101.0,
        )

    assert excinfo.value.code == "model_unavailable"
    assert requested == []


def test_cloud_result_exposes_usage_and_destination_but_not_secret():
    from omd.ai_providers import AIProviderResponse
    from omd.ai_service import AITextTask, create_text_task_consent, execute_text_task

    secret = "sk-never-return-this"
    captured = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="note_organisation",
        operation="Organise this note",
        system_prompt="Keep evidence separate.",
        max_output_tokens=128,
    )

    def requester(request):
        captured.append(request)
        return AIProviderResponse(
            provider="openai",
            model="gpt-selected-versioned",
            text="AI suggestion",
            usage={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
            timing={"elapsed_seconds": 0.5},
        )

    source = "Private note"
    grant = create_text_task_consent(
        task,
        source_text=source,
        as_of=date(2026, 7, 19),
        now=lambda: 100.0,
    )
    result = execute_text_task(
        task,
        source_text=source,
        consent_granted=True,
        consent_grant=grant,
        credential_loader=lambda provider: secret,
        model_validator=lambda provider, model, **kwargs: _availability(provider, model),
        requester=requester,
        as_of=date(2026, 7, 19),
        now=lambda: 101.0,
    )

    assert captured[0].provider == "openai"
    assert captured[0].model == "gpt-selected"
    assert captured[0].api_key == secret
    assert captured[0].stream is True
    assert result.destination_domain == "api.openai.com"
    assert result.text == "AI suggestion"
    assert result.usage["total_tokens"] == 12
    assert secret not in repr(result)
    assert "AI suggestion" not in repr(result)
    assert secret not in str(result.to_dict())


def test_local_ollama_execution_uses_no_cloud_credential_or_consent():
    from omd.ai_providers import AIProviderResponse
    from omd.ai_service import AITextTask, execute_text_task

    credential_calls = []
    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="markdown_polish",
        operation="Polish Markdown",
        system_prompt="Preserve language.",
        max_output_tokens=128,
        endpoint="http://localhost:11434",
    )

    result = execute_text_task(
        task,
        source_text="Local note",
        consent_granted=False,
        credential_loader=lambda provider: credential_calls.append(provider),
        model_validator=lambda provider, model, **kwargs: _availability(provider, model),
        requester=lambda request: AIProviderResponse(
            provider="ollama",
            model=request.model,
            text="Local result",
            usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            timing={"elapsed_seconds": 0.2},
        ),
    )

    assert credential_calls == []
    assert result.privacy_mode == "local_only"
    assert result.destination_domain == "localhost"
    assert result.text == "Local result"


def test_prepare_preview_never_retains_source_text():
    from omd.ai_service import AITextTask, prepare_text_task

    source = "This private text must not be retained."
    task = AITextTask(
        provider="deepseek",
        model="deepseek-selected",
        capability="note_organisation",
        operation="Organise note",
        system_prompt="Keep claims exact.",
        max_output_tokens=128,
    )

    preview = prepare_text_task(task, source_text=source, as_of=date(2026, 7, 19))

    assert preview.character_count == len(source)
    assert source not in repr(preview)
    assert "source_text" not in preview.__dict__
    assert preview.destination_domain == "api.deepseek.com"


def test_unsupported_capability_fails_before_credential_or_network_access():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    calls: list[str] = []
    task = AITextTask(
        provider="openai",
        model="gpt-selected",
        capability="transcription",
        operation="Transcribe audio",
        system_prompt="Return a transcript.",
        max_output_tokens=128,
    )

    with pytest.raises(AIServiceError) as excinfo:
        execute_text_task(
            task,
            source_text="Local attachment metadata",
            consent_granted=True,
            credential_loader=lambda provider: calls.append("credential"),
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
            as_of=date(2026, 7, 19),
        )

    assert excinfo.value.code == "unsupported_capability"
    assert calls == []


def test_structured_contract_is_forwarded_and_returned_defensively():
    from omd.ai_providers import AIProviderResponse
    from omd.ai_service import AITextTask, execute_text_task
    from omd.structured_output import AIOutputSchema

    contract = AIOutputSchema(
        name="note_result",
        schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    )
    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Organise note",
        system_prompt="Use the source only.",
        max_output_tokens=128,
        output_schema=contract,
    )
    captured = []

    result = execute_text_task(
        task,
        source_text="Local note",
        consent_granted=False,
        model_validator=lambda provider, model, **kwargs: _availability(provider, model),
        requester=lambda request: (
            captured.append(request)
            or AIProviderResponse(
                provider="ollama",
                model=request.model,
                text='{"summary":"Result"}',
                usage={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                timing={"elapsed_seconds": 0.1},
                structured={"summary": "Result"},
            )
        ),
    )

    assert captured[0].output_schema is contract
    assert result.structured == {"summary": "Result"}
    exported = result.structured
    exported["summary"] = "mutated"
    assert result.structured == {"summary": "Result"}


def test_local_task_forwards_temperature_and_remote_authorization_explicitly():
    from omd.ai_providers import AIProviderResponse
    from omd.ai_service import AITextTask, create_text_task_consent, execute_text_task

    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Enrich note",
        system_prompt="Use only the source.",
        max_output_tokens=128,
        endpoint="https://models.example.com",
        temperature=0.1,
        allow_remote_ollama=True,
    )
    model_checks = []
    requests = []
    grant = create_text_task_consent(task, source_text="Private note", now=lambda: 100.0)

    result = execute_text_task(
        task,
        source_text="Private note",
        consent_granted=True,
        consent_grant=grant,
        model_validator=lambda provider, model, **kwargs: (
            model_checks.append(kwargs) or _availability(provider, model)
        ),
        requester=lambda request: (
            requests.append(request)
            or AIProviderResponse(
                provider="ollama",
                model=request.model,
                text="{}",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                timing={"elapsed_seconds": 0.1},
            )
        ),
        now=lambda: 101.0,
    )

    assert model_checks[0]["allow_remote_ollama"] is True
    assert requests[0].allow_remote_ollama is True
    assert requests[0].temperature == 0.1
    assert result.privacy_mode == "cloud_for_this_task"
    assert result.destination_domain == "models.example.com"


def test_remote_ollama_requires_source_bound_consent_before_model_or_network():
    from omd.ai_service import AIServiceError, AITextTask, execute_text_task

    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Enrich note",
        system_prompt="Use only the source.",
        max_output_tokens=128,
        endpoint="https://models.example.com",
        allow_remote_ollama=True,
    )
    calls = []

    with pytest.raises(AIServiceError) as excinfo:
        execute_text_task(
            task,
            source_text="Private note",
            consent_granted=True,
            model_validator=lambda *args, **kwargs: calls.append("model"),
            requester=lambda request: calls.append("request"),
        )

    assert excinfo.value.code == "consent_preview_required"
    assert calls == []


def test_remote_ollama_preview_rejects_non_base_url():
    from omd.ai_service import AITextTask, prepare_text_task

    task = AITextTask(
        provider="ollama",
        model="qwen3:4b-instruct",
        capability="note_organisation",
        operation="Enrich note",
        system_prompt="Use only the source.",
        max_output_tokens=128,
        endpoint="https://models.example.com/custom?x=1",
        allow_remote_ollama=True,
    )

    with pytest.raises(ValueError, match="base URL"):
        prepare_text_task(task, source_text="Private note")
