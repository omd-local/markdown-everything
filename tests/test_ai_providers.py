from __future__ import annotations

import io
import json
import urllib.error

import pytest


def _header_map(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def _request_json(request) -> dict[str, object]:
    return json.loads(request.data.decode("utf-8"))


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        return self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode("utf-8") for line in lines]

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_openai_responses_transport_uses_official_shape_and_parses_response():
    from omd.ai_providers import AIProviderRequest, request_text

    seen: dict[str, object] = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = _header_map(request)
        seen["body"] = _request_json(request)
        return _FakeResponse(
            {
                "status": "completed",
                "model": "gpt-5-mini-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "OpenAI answer."},
                        ],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            }
        )

    clock = iter([10.0, 10.5]).__next__
    response = request_text(
        AIProviderRequest(
            provider="openai",
            model="gpt-5-mini",
            task="Summarize the user input.",
            system="You are concise.",
            user="Source text",
            max_output_tokens=321,
            api_key="sk-openai-secret",
            timeout_seconds=12.5,
        ),
        opener=fake_open,
        clock=clock,
    )

    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["timeout"] == 12.5
    assert seen["headers"]["authorization"] == "Bearer sk-openai-secret"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["body"] == {
        "model": "gpt-5-mini",
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "You are concise."}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Task:\nSummarize the user input.\n\nUser:\nSource text",
                    }
                ],
            },
        ],
        "max_output_tokens": 321,
        "store": False,
    }
    assert response.provider == "openai"
    assert response.model == "gpt-5-mini-2026-01-01"
    assert response.text == "OpenAI answer."
    assert response.usage == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    assert response.timing == {"elapsed_seconds": pytest.approx(0.5)}


def test_anthropic_messages_transport_uses_native_shape_and_headers():
    from omd.ai_providers import AIProviderRequest, request_text

    seen: dict[str, object] = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = _header_map(request)
        seen["body"] = _request_json(request)
        return _FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Anthropic answer."}],
                "usage": {"input_tokens": 21, "output_tokens": 9},
            }
        )

    response = request_text(
        AIProviderRequest(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            task="Answer the question.",
            system="Be accurate.",
            user="What is the result?",
            max_output_tokens=256,
            api_key="sk-ant-secret",
            timeout_seconds=9.0,
        ),
        opener=fake_open,
    )

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["timeout"] == 9.0
    assert seen["headers"]["x-api-key"] == "sk-ant-secret"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["content-type"] == "application/json"
    assert "authorization" not in seen["headers"]
    assert seen["body"] == {
        "model": "claude-sonnet-4-20250514",
        "system": "Be accurate.",
        "messages": [
            {
                "role": "user",
                "content": "Task:\nAnswer the question.\n\nUser:\nWhat is the result?",
            }
        ],
        "max_tokens": 256,
    }
    assert response.provider == "anthropic"
    assert response.model == "claude-sonnet-4-20250514"
    assert response.text == "Anthropic answer."
    assert response.usage == {"input_tokens": 21, "output_tokens": 9}
    assert response.timing["elapsed_seconds"] >= 0.0


def test_deepseek_transport_uses_distinct_chat_completions_endpoint():
    from omd.ai_providers import AIProviderRequest, request_text

    seen: dict[str, object] = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = _header_map(request)
        seen["body"] = _request_json(request)
        return _FakeResponse(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "DeepSeek answer."},
                    }
                ],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
            }
        )

    response = request_text(
        AIProviderRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            task="Extract the answer.",
            system="Be direct.",
            user="Question text",
            max_output_tokens=111,
            api_key="sk-deepseek-secret",
        ),
        opener=fake_open,
    )

    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["url"] != "https://api.openai.com/v1/responses"
    assert seen["headers"]["authorization"] == "Bearer sk-deepseek-secret"
    assert seen["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "Be direct."},
            {
                "role": "user",
                "content": "Task:\nExtract the answer.\n\nUser:\nQuestion text",
            },
        ],
        "max_tokens": 111,
        "stream": False,
    }
    assert response.provider == "deepseek"
    assert response.model == "deepseek-v4-flash"
    assert response.text == "DeepSeek answer."
    assert response.usage == {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}


def test_ollama_transport_uses_requested_local_context_window(monkeypatch):
    from omd.ai_providers import AIProviderRequest, request_text

    monkeypatch.setattr("omd.ai_providers.ollama_keep_alive", lambda: "2m")

    seen: dict[str, object] = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = _header_map(request)
        seen["body"] = _request_json(request)
        return _FakeResponse(
            {
                "done": True,
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "Ollama answer."},
                "prompt_eval_count": 14,
                "eval_count": 9,
                "total_duration": 2500000000,
                "load_duration": 500000000,
                "prompt_eval_duration": 700000000,
                "eval_duration": 1300000000,
            }
        )

    clock = iter([20.0, 20.2]).__next__
    response = request_text(
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Summarize.",
            system="Stay local.",
            user="Input note",
            max_output_tokens=75,
            context_window_tokens=32 * 1024,
            timeout_seconds=3.0,
        ),
        opener=fake_open,
        clock=clock,
    )

    assert seen["url"] == "http://localhost:11434/api/chat"
    assert "authorization" not in seen["headers"]
    assert seen["body"] == {
        "model": "qwen3:4b-instruct",
        "messages": [
            {"role": "system", "content": "Stay local."},
            {"role": "user", "content": "Task:\nSummarize.\n\nUser:\nInput note"},
        ],
        "stream": False,
        "keep_alive": "2m",
        "options": {"num_ctx": 32768, "num_predict": 75},
    }
    assert response.provider == "ollama"
    assert response.model == "qwen3:4b-instruct"
    assert response.text == "Ollama answer."
    assert response.usage == {"input_tokens": 14, "output_tokens": 9, "total_tokens": 23}
    assert response.timing == {
        "elapsed_seconds": pytest.approx(0.2),
        "total_seconds": pytest.approx(2.5),
        "load_seconds": pytest.approx(0.5),
        "input_seconds": pytest.approx(0.7),
        "output_seconds": pytest.approx(1.3),
    }


def test_ollama_transport_uses_validated_loopback_endpoint():
    from omd.ai_providers import AIProviderRequest, request_text

    seen: dict[str, str] = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        return _FakeResponse(
            {
                "done": True,
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "Local answer."},
                "prompt_eval_count": 4,
                "eval_count": 3,
            }
        )

    request_text(
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Organise.",
            system="Stay local.",
            user="Private note",
            max_output_tokens=32,
            endpoint="http://127.0.0.1:22434",
        ),
        opener=fake_open,
    )

    assert seen["url"] == "http://127.0.0.1:22434/api/chat"


def test_ollama_transport_rejects_remote_endpoint_before_sending():
    from omd.ai_providers import AIProviderRequest, request_text

    called = False

    def fake_open(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("remote endpoint must be rejected before opening")

    with pytest.raises(ValueError, match="Remote Ollama"):
        request_text(
            AIProviderRequest(
                provider="ollama",
                model="qwen3:4b-instruct",
                task="Organise.",
                system="Stay local.",
                user="Private note",
                max_output_tokens=32,
                endpoint="https://models.example.com",
            ),
            opener=fake_open,
        )

    assert called is False


def test_ollama_transport_forwards_low_temperature_without_changing_defaults():
    from omd.ai_providers import AIProviderRequest, request_text

    seen = {}

    def fake_open(request, timeout):
        seen.update(_request_json(request))
        return _FakeResponse(
            {
                "done": True,
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "{}"},
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        )

    request_text(
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Organise.",
            system="Stay local.",
            user="Private note",
            max_output_tokens=32,
            temperature=0.1,
        ),
        opener=fake_open,
    )

    assert seen["options"]["temperature"] == 0.1


def test_ollama_transport_allows_only_explicit_https_remote_endpoint():
    from omd.ai_providers import AIProviderRequest, request_text

    seen = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        return _FakeResponse(
            {
                "done": True,
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "{}"},
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        )

    request_text(
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Organise.",
            system="Use the source.",
            user="Private note",
            max_output_tokens=32,
            endpoint="https://models.example.com",
            allow_remote_ollama=True,
        ),
        opener=fake_open,
    )

    assert seen["url"] == "https://models.example.com/api/chat"

    with pytest.raises(ValueError, match="must use HTTPS"):
        request_text(
            AIProviderRequest(
                provider="ollama",
                model="qwen3:4b-instruct",
                task="Organise.",
                system="Use the source.",
                user="Private note",
                max_output_tokens=32,
                endpoint="http://models.example.com",
                allow_remote_ollama=True,
            ),
            opener=fake_open,
        )


@pytest.mark.parametrize("temperature", [True, -0.1, 2.1, float("inf")])
def test_provider_request_rejects_invalid_temperature(temperature):
    from omd.ai_providers import AIProviderRequest

    with pytest.raises(ValueError, match="temperature"):
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Organise.",
            system="Use the source.",
            user="Private note",
            max_output_tokens=32,
            temperature=temperature,
        )


def test_transport_does_not_fallback_across_providers():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    calls: list[str] = []

    def fake_open(request, timeout):
        calls.append(request.full_url)
        raise OSError("connection reset")

    with pytest.raises(AIProviderError, match="deepseek transport error") as excinfo:
        request_text(
            AIProviderRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                task="Do the work.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-deepseek-secret",
            ),
            opener=fake_open,
        )

    assert calls == ["https://api.deepseek.com/chat/completions"]
    assert excinfo.value.provider == "deepseek"
    assert excinfo.value.code == "transport_error"


def test_transport_cancellation_prevents_network_call():
    from omd.ai_providers import AIProviderCancelledError, AIProviderRequest, request_text

    called = False

    def fake_open(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("network call should not happen")

    with pytest.raises(AIProviderCancelledError):
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Do nothing.",
                system="Stop.",
                user="Cancelled",
                max_output_tokens=32,
                api_key="sk-openai-secret",
                is_cancelled=lambda: True,
            ),
            opener=fake_open,
        )

    assert called is False


def test_transport_cancellation_after_response_discards_result():
    from omd.ai_providers import AIProviderCancelledError, AIProviderRequest, request_text

    cancelled = False

    def fake_open(request, timeout):
        nonlocal cancelled
        cancelled = True
        return _FakeResponse(
            {
                "status": "completed",
                "model": "gpt-5-mini-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Discard me."}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            }
        )

    with pytest.raises(AIProviderCancelledError):
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Do nothing.",
                system="Stop.",
                user="Cancelled",
                max_output_tokens=32,
                api_key="sk-openai-secret",
                is_cancelled=lambda: cancelled,
            ),
            opener=fake_open,
        )


def test_transport_rejects_malformed_provider_response():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        return _FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
                "content": [{"type": "image"}],
            }
        )

    with pytest.raises(AIProviderError, match="anthropic malformed response") as excinfo:
        request_text(
            AIProviderRequest(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-ant-secret",
            ),
            opener=fake_open,
        )

    assert excinfo.value.provider == "anthropic"
    assert excinfo.value.code == "malformed_response"


@pytest.mark.parametrize(
    ("provider", "payload", "expected_code"),
    [
        (
            "openai",
            {
                "status": "incomplete",
                "model": "gpt-5-mini-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Truncated."}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
            "incomplete_response",
        ),
        (
            "anthropic",
            {
                "stop_reason": "max_tokens",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "Truncated."}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
            "incomplete_response",
        ),
        (
            "deepseek",
            {
                "model": "deepseek-chat",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": "Truncated."},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
            "incomplete_response",
        ),
        (
            "ollama",
            {
                "done": False,
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "Truncated."},
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
            "incomplete_response",
        ),
    ],
)
def test_transport_rejects_incomplete_provider_output(provider, payload, expected_code):
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        return _FakeResponse(payload)

    with pytest.raises(AIProviderError) as excinfo:
        request_text(
            AIProviderRequest(
                provider=provider,
                model=str(payload["model"]),
                task="Return a complete answer.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key=None if provider == "ollama" else "sk-test-secret",
            ),
            opener=fake_open,
        )

    assert excinfo.value.provider == provider
    assert excinfo.value.code == expected_code


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        (
            "openai",
            {
                "model": "gpt-5-mini-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Unconfirmed."}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        ),
        (
            "anthropic",
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "Unconfirmed."}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        ),
        (
            "deepseek",
            {
                "model": "deepseek-chat",
                "choices": [
                    {"message": {"role": "assistant", "content": "Unconfirmed."}}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        ),
        (
            "ollama",
            {
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "Unconfirmed."},
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
        ),
    ],
)
def test_nonstream_transport_rejects_text_without_a_completion_marker(provider, payload):
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    with pytest.raises(AIProviderError, match="incomplete response") as excinfo:
        request_text(
            AIProviderRequest(
                provider=provider,
                model="selected-model",
                task="Return a complete answer.",
                system="Be direct.",
                user="Source",
                max_output_tokens=64,
                api_key=None if provider == "ollama" else "sk-secret",
            ),
            opener=lambda request, timeout: _FakeResponse(payload),
        )

    assert excinfo.value.code == "incomplete_response"
    assert excinfo.value.retryable is False


def test_transport_normalizes_timeout_errors():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(AIProviderError, match="openai timeout") as excinfo:
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-openai-secret",
            ),
            opener=fake_open,
        )

    assert excinfo.value.provider == "openai"
    assert excinfo.value.code == "timeout"
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code is None


def test_provider_request_repr_never_exposes_api_key_or_source_text():
    from omd.ai_providers import AIProviderRequest

    request = AIProviderRequest(
        provider="openai",
        model="gpt-5-mini",
        task="Return text.",
        system="Be direct.",
        user="Private source text",
        max_output_tokens=64,
        api_key="sk-never-show-this",
    )

    assert "sk-never-show-this" not in repr(request)
    assert "Private source text" not in repr(request)


def test_provider_request_rejects_api_key_header_injection():
    from omd.ai_providers import AIProviderRequest

    with pytest.raises(ValueError, match="control characters"):
        AIProviderRequest(
            provider="openai",
            model="gpt-5-mini",
            task="Return text.",
            system="Be direct.",
            user="Private source text",
            max_output_tokens=64,
            api_key="sk-safe\r\nX-Injected: yes",
        )


def test_provider_response_repr_does_not_expose_generated_private_text():
    from omd.ai_providers import AIProviderResponse

    response = AIProviderResponse(
        provider="openai",
        model="gpt-5-mini",
        text="Private generated note",
        usage={"total_tokens": 4},
        timing={"elapsed_seconds": 0.1},
    )

    assert response.text == "Private generated note"
    assert "Private generated note" not in repr(response)


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(400, False), (401, False), (403, False), (404, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_transport_classifies_http_errors_for_safe_retry(status_code, retryable):
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            status_code,
            "request failed",
            {},
            io.BytesIO(b"provider detail must not be exposed"),
        )

    with pytest.raises(AIProviderError) as excinfo:
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-secret-never-log",
            ),
            opener=fake_open,
        )

    assert excinfo.value.code == "http_error"
    assert excinfo.value.status_code == status_code
    assert excinfo.value.retryable is retryable
    assert "provider detail" not in str(excinfo.value)
    assert "sk-secret" not in str(excinfo.value)


def test_transport_marks_connection_errors_as_retryable():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        raise OSError("connection reset")

    with pytest.raises(AIProviderError) as excinfo:
        request_text(
            AIProviderRequest(
                provider="ollama",
                model="qwen3:4b-instruct",
                task="Return text.",
                system="Stay local.",
                user="Hello",
                max_output_tokens=64,
            ),
            opener=fake_open,
        )

    assert excinfo.value.code == "transport_error"
    assert excinfo.value.retryable is True


def test_malformed_response_is_not_marked_retryable():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        return _FakeResponse({"unexpected": "shape"})

    with pytest.raises(AIProviderError) as excinfo:
        request_text(
            AIProviderRequest(
                provider="deepseek",
                model="deepseek-chat",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-secret",
            ),
            opener=fake_open,
        )

    assert excinfo.value.code == "malformed_response"
    assert excinfo.value.retryable is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", 3),
        ("model", []),
        ("task", None),
        ("system", object()),
        ("user", 9),
        ("api_key", 42),
        ("endpoint", 42),
    ],
)
def test_request_rejects_non_string_text_fields(field, value):
    from omd.ai_providers import AIProviderRequest

    values = {
        "provider": "openai",
        "model": "gpt-5-mini",
        "task": "Summarize.",
        "system": "Be concise.",
        "user": "Source text",
        "max_output_tokens": 32,
        "api_key": "sk-secret",
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError)):
        AIProviderRequest(**values)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_request_rejects_invalid_output_token_limit(value):
    from omd.ai_providers import AIProviderRequest

    with pytest.raises((TypeError, ValueError)):
        AIProviderRequest(
            provider="openai",
            model="gpt-5-mini",
            task="Summarize.",
            system="Be concise.",
            user="Source text",
            max_output_tokens=value,
            api_key="sk-secret",
        )


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_request_rejects_invalid_timeout(value):
    from omd.ai_providers import AIProviderRequest

    with pytest.raises((TypeError, ValueError)):
        AIProviderRequest(
            provider="openai",
            model="gpt-5-mini",
            task="Summarize.",
            system="Be concise.",
            user="Source text",
            max_output_tokens=32,
            api_key="sk-secret",
            timeout_seconds=value,
        )


def test_transport_rejects_boolean_usage_counts():
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    def fake_open(request, timeout):
        return _FakeResponse(
            {
                "status": "completed",
                "model": "gpt-5-mini-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Answer."}],
                    }
                ],
                "usage": {"input_tokens": True, "output_tokens": 2},
            }
        )

    with pytest.raises(AIProviderError, match="openai malformed response"):
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                api_key="sk-secret",
            ),
            opener=fake_open,
        )


def test_experimental_openai_compatible_provider_is_not_publicly_available():
    from omd.ai_providers import AIProviderRequest, request_text

    with pytest.raises(ValueError, match="unsupported provider"):
        request_text(
            AIProviderRequest(
                provider="openai_local",
                model="local-model",
                task="Return text.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=64,
                endpoint="http://localhost:1234/v1/chat/completions",
            )
        )


@pytest.mark.parametrize(
    ("provider", "expected_fragment"),
    [
        ("openai", {"text": {"format": {"type": "json_schema"}}}),
        ("anthropic", {"output_config": {"format": {"type": "json_schema"}}}),
        ("deepseek", {"response_format": {"type": "json_object"}}),
        ("ollama", {"format": {"type": "object"}}),
    ],
)
def test_structured_output_maps_to_each_provider_native_contract(provider, expected_fragment):
    from omd.ai_providers import AIProviderRequest, request_text
    from omd.structured_output import AIOutputSchema

    seen = {}
    schema = AIOutputSchema(
        name="note_result",
        schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    )

    def fake_open(request, timeout):
        seen.update(_request_json(request))
        if provider == "openai":
            return _FakeResponse(
                {
                    "status": "completed",
                    "model": "selected",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"summary":"ok"}'}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            )
        if provider == "anthropic":
            return _FakeResponse(
                {
                    "model": "selected",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"summary":"ok"}'}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )
        if provider == "deepseek":
            return _FakeResponse(
                {
                    "model": "selected",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"summary":"ok"}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
        return _FakeResponse(
            {
                "done": True,
                "model": "selected",
                "message": {"content": '{"summary":"ok"}'},
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        )

    response = request_text(
        AIProviderRequest(
            provider=provider,
            model="selected",
            task="Organise note",
            system="Return the requested structure.",
            user="Source",
            max_output_tokens=64,
            api_key=None if provider == "ollama" else "sk-secret",
            output_schema=schema,
        ),
        opener=fake_open,
    )

    def contains(container, fragment):
        for key, value in fragment.items():
            assert key in container
            if isinstance(value, dict):
                contains(container[key], value)
            else:
                assert container[key] == value

    contains(seen, expected_fragment)
    assert response.structured == {"summary": "ok"}


def test_openai_streaming_aggregates_sse_and_records_time_to_first_token():
    from omd.ai_providers import AIProviderRequest, request_text

    seen = {}
    completed = {
        "status": "completed",
        "model": "gpt-5-mini-2026-01-01",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello world"}],
            }
        ],
        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
    }

    def fake_open(request, timeout):
        seen.update(_request_json(request))
        return _FakeStreamResponse(
            [
                'event: response.output_text.delta\n',
                'data: {"type":"response.output_text.delta","delta":"Hello"}\n',
                '\n',
                'event: response.output_text.delta\n',
                'data: {"type":"response.output_text.delta","delta":" world"}\n',
                '\n',
                f'data: {json.dumps({"type": "response.completed", "response": completed})}\n',
                '\n',
            ]
        )

    clock = iter([10.0, 10.2, 11.0]).__next__
    response = request_text(
        AIProviderRequest(
            provider="openai",
            model="gpt-5-mini",
            task="Answer.",
            system="Be direct.",
            user="Hello",
            max_output_tokens=32,
            api_key="sk-secret",
            stream=True,
        ),
        opener=fake_open,
        clock=clock,
    )

    assert seen["stream"] is True
    assert response.text == "Hello world"
    assert response.usage["total_tokens"] == 6
    assert response.timing == {
        "elapsed_seconds": pytest.approx(1.0),
        "time_to_first_token_seconds": pytest.approx(0.2),
    }


def test_streaming_transport_enforces_total_request_deadline(monkeypatch):
    from omd.ai_providers import AIProviderError, AIProviderRequest, request_text

    completed = {
        "status": "completed",
        "model": "gpt-5-mini-2026-01-01",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Too late"}],
            }
        ],
        "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
    }
    deadline_clock = iter([0.0, 0.5, 1.1]).__next__
    monkeypatch.setattr("omd.ai_providers.monotonic", deadline_clock)

    def fake_open(request, timeout):
        return _FakeStreamResponse(
            [
                "event: response.output_text.delta\n",
                'data: {"type":"response.output_text.delta","delta":"Too late"}\n',
                f'data: {json.dumps({"type": "response.completed", "response": completed})}\n',
            ]
        )

    with pytest.raises(AIProviderError, match="openai timeout") as excinfo:
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Answer.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=32,
                api_key="sk-secret",
                timeout_seconds=1.0,
                stream=True,
            ),
            opener=fake_open,
            clock=lambda: 10.0,
        )

    assert excinfo.value.code == "timeout"
    assert excinfo.value.retryable is True


def test_anthropic_streaming_aggregates_native_message_events():
    from omd.ai_providers import AIProviderRequest, request_text

    def fake_open(request, timeout):
        assert _request_json(request)["stream"] is True
        return _FakeStreamResponse(
            [
                'data: {"type":"message_start","message":{"model":"claude-selected","usage":{"input_tokens":7,"output_tokens":1}}}\n',
                '\n',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n',
                '\n',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" Claude"}}\n',
                '\n',
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n',
                '\n',
                'data: {"type":"message_stop"}\n',
                '\n',
            ]
        )

    response = request_text(
        AIProviderRequest(
            provider="anthropic",
            model="claude-selected",
            task="Answer.",
            system="Be direct.",
            user="Hello",
            max_output_tokens=32,
            api_key="sk-secret",
            stream=True,
        ),
        opener=fake_open,
    )

    assert response.text == "Hello Claude"
    assert response.usage == {"input_tokens": 7, "output_tokens": 3}
    assert response.timing["time_to_first_token_seconds"] >= 0


def test_deepseek_streaming_uses_final_usage_chunk():
    from omd.ai_providers import AIProviderRequest, request_text

    def fake_open(request, timeout):
        body = _request_json(request)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        return _FakeStreamResponse(
            [
                'data: {"model":"deepseek-chat","choices":[{"delta":{"content":"Deep"},"finish_reason":null}],"usage":null}\n',
                '\n',
                'data: {"model":"deepseek-chat","choices":[{"delta":{"content":"Seek"},"finish_reason":"stop"}],"usage":null}\n',
                '\n',
                'data: {"model":"deepseek-chat","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n',
                '\n',
                'data: [DONE]\n',
                '\n',
            ]
        )

    response = request_text(
        AIProviderRequest(
            provider="deepseek",
            model="deepseek-chat",
            task="Answer.",
            system="Be direct.",
            user="Hello",
            max_output_tokens=32,
            api_key="sk-secret",
            stream=True,
        ),
        opener=fake_open,
    )

    assert response.text == "DeepSeek"
    assert response.usage == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}


def test_ollama_streaming_aggregates_ndjson_and_final_timing():
    from omd.ai_providers import AIProviderRequest, request_text

    def fake_open(request, timeout):
        assert _request_json(request)["stream"] is True
        return _FakeStreamResponse(
            [
                '{"model":"qwen3:4b-instruct","message":{"content":"Local"},"done":false}\n',
                '{"model":"qwen3:4b-instruct","message":{"content":" answer"},"done":false}\n',
                '{"model":"qwen3:4b-instruct","message":{"content":""},"done":true,"done_reason":"stop","prompt_eval_count":6,"eval_count":2,"total_duration":2000000000}\n',
            ]
        )

    response = request_text(
        AIProviderRequest(
            provider="ollama",
            model="qwen3:4b-instruct",
            task="Answer.",
            system="Stay local.",
            user="Hello",
            max_output_tokens=32,
            stream=True,
        ),
        opener=fake_open,
    )

    assert response.text == "Local answer"
    assert response.usage == {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8}
    assert response.timing["total_seconds"] == pytest.approx(2.0)


def test_streaming_cancellation_discards_partial_provider_output():
    from omd.ai_providers import AIProviderCancelledError, AIProviderRequest, request_text

    checks = iter([False, False, True])

    def fake_open(request, timeout):
        return _FakeStreamResponse(
            [
                'data: {"type":"response.output_text.delta","delta":"Partial"}\n',
                '\n',
                'data: {"type":"response.output_text.delta","delta":" output"}\n',
            ]
        )

    with pytest.raises(AIProviderCancelledError):
        request_text(
            AIProviderRequest(
                provider="openai",
                model="gpt-5-mini",
                task="Answer.",
                system="Be direct.",
                user="Hello",
                max_output_tokens=32,
                api_key="sk-secret",
                stream=True,
                is_cancelled=lambda: next(checks),
            ),
            opener=fake_open,
        )
