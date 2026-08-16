"""Transport-only provider adapters for Phase 2 model requests."""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

from ._models import LOCAL_TEXT_CONTEXT_TOKENS
from ._network_policy import build_no_redirect_opener, validate_ollama_base_url
from .ollama_runtime import ollama_keep_alive
from .structured_output import AIOutputSchema, StructuredOutputError, parse_structured_output


_OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_ANTHROPIC_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
_DEEPSEEK_CHAT_ENDPOINT = "https://api.deepseek.com/chat/completions"
_OLLAMA_CHAT_ENDPOINT = "http://localhost:11434/api/chat"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_CONTEXT_TOKENS = 128 * 1024


@dataclass(frozen=True)
class AIProviderRequest:
    provider: str
    model: str
    task: str
    system: str
    user: str = field(repr=False)
    max_output_tokens: int
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    endpoint: str | None = None
    is_cancelled: Callable[[], bool] | None = None
    output_schema: AIOutputSchema | None = None
    stream: bool = False
    temperature: float | None = None
    allow_remote_ollama: bool = False
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider", "model", "task", "system", "user"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if not self.task.strip():
            raise ValueError("task must not be empty")
        if type(self.max_output_tokens) is not int or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise TypeError("api_key must be a string or None")
        if self.api_key is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in self.api_key
        ):
            raise ValueError("api_key must not contain control characters")
        if self.endpoint is not None and not isinstance(self.endpoint, str):
            raise TypeError("endpoint must be a string or None")
        if self.is_cancelled is not None and not callable(self.is_cancelled):
            raise TypeError("is_cancelled must be callable or None")
        if self.output_schema is not None and not isinstance(self.output_schema, AIOutputSchema):
            raise TypeError("output_schema must be an AIOutputSchema or None")
        if type(self.stream) is not bool:
            raise TypeError("stream must be a boolean")
        if type(self.allow_remote_ollama) is not bool:
            raise TypeError("allow_remote_ollama must be a boolean")
        if self.allow_remote_ollama and self.provider.strip().lower() != "ollama":
            raise ValueError("allow_remote_ollama is valid only for Ollama")
        if self.context_window_tokens is not None:
            if type(self.context_window_tokens) is not int or self.context_window_tokens <= 0:
                raise ValueError("context_window_tokens must be a positive integer or None")
            if self.provider.strip().lower() != "ollama":
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
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class AIProviderResponse:
    provider: str
    model: str
    text: str = field(repr=False)
    usage: dict[str, int]
    timing: dict[str, float]
    structured: dict[str, object] | None = field(default=None, repr=False)


class AIProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class AIProviderCancelledError(AIProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(provider, "cancelled", f"{provider} cancelled")


def request_text(
    request: AIProviderRequest,
    *,
    opener=None,
    clock: Callable[[], float] | None = None,
) -> AIProviderResponse:
    if request.is_cancelled is not None and request.is_cancelled():
        raise AIProviderCancelledError(request.provider)

    transport = _transport_for(request)
    body = json.dumps(transport.body).encode("utf-8")
    http_request = urllib.request.Request(
        transport.endpoint,
        data=body,
        headers=transport.headers,
        method="POST",
    )
    open_call = opener or build_no_redirect_opener().open
    now = clock or monotonic
    started_at = now()
    stream_deadline = monotonic() + float(request.timeout_seconds)
    try:
        with open_call(http_request, timeout=request.timeout_seconds) as response:
            if request.stream:
                model, text, usage, provider_timing = _parse_streaming_response(
                    response,
                    request=request,
                    clock=now,
                    started_at=started_at,
                    deadline_at=stream_deadline,
                )
            else:
                raw_response = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw_response) > _MAX_RESPONSE_BYTES:
                    raise AIProviderError(
                        request.provider,
                        "response_too_large",
                        f"{request.provider} response too large",
                    )
                payload = json.loads(raw_response.decode("utf-8"))
                model, text, usage, provider_timing = transport.parser(payload)
    except TimeoutError as exc:
        raise AIProviderError(
            request.provider,
            "timeout",
            f"{request.provider} timeout",
            retryable=True,
        ) from exc
    except urllib.error.HTTPError as exc:
        raise AIProviderError(
            request.provider,
            "http_error",
            f"{request.provider} http error {exc.code}",
            retryable=_retryable_http_status(exc.code),
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        if _timeout_reason(exc.reason):
            raise AIProviderError(
                request.provider,
                "timeout",
                f"{request.provider} timeout",
                retryable=True,
            ) from exc
        raise AIProviderError(
            request.provider,
            "transport_error",
            f"{request.provider} transport error",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise AIProviderError(
            request.provider,
            "transport_error",
            f"{request.provider} transport error",
            retryable=True,
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIProviderError(
            request.provider,
            "malformed_response",
            f"{request.provider} malformed response",
        ) from exc

    if request.is_cancelled is not None and request.is_cancelled():
        raise AIProviderCancelledError(request.provider)

    elapsed_seconds = now() - started_at
    structured = None
    if request.output_schema is not None:
        try:
            structured = parse_structured_output(text, request.output_schema)
        except StructuredOutputError as exc:
            raise AIProviderError(
                request.provider,
                "malformed_structured_output",
                f"{request.provider} returned invalid structured output",
            ) from exc
    timing = {"elapsed_seconds": elapsed_seconds}
    timing.update(provider_timing)
    return AIProviderResponse(
        provider=request.provider,
        model=model,
        text=text,
        usage=usage,
        timing=timing,
        structured=structured,
    )


@dataclass(frozen=True)
class _Transport:
    endpoint: str
    headers: dict[str, str]
    body: dict[str, object]
    parser: Callable[[object], tuple[str, str, dict[str, int], dict[str, float]]]


def _transport_for(request: AIProviderRequest) -> _Transport:
    provider = request.provider.strip().lower()
    if provider == "openai":
        _reject_custom_endpoint(request)
        body: dict[str, object] = {
            "model": request.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": request.system}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": _user_prompt(request)}],
                },
            ],
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }
        if request.output_schema is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema.name,
                    "schema": request.output_schema.schema,
                    "strict": True,
                }
            }
        if request.stream:
            body["stream"] = True
        if request.temperature is not None:
            body["temperature"] = float(request.temperature)
        return _Transport(
            endpoint=_OPENAI_RESPONSES_ENDPOINT,
            headers=_bearer_headers(request),
            body=body,
            parser=_parse_openai_response,
        )
    if provider == "anthropic":
        _reject_custom_endpoint(request)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": _require_api_key(request),
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": request.model,
            "system": request.system,
            "messages": [{"role": "user", "content": _user_prompt(request)}],
            "max_tokens": request.max_output_tokens,
        }
        if request.output_schema is not None:
            body["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.output_schema.schema,
                }
            }
        if request.stream:
            body["stream"] = True
        if request.temperature is not None:
            body["temperature"] = float(request.temperature)
        return _Transport(
            endpoint=_ANTHROPIC_MESSAGES_ENDPOINT,
            headers=headers,
            body=body,
            parser=_parse_anthropic_response,
        )
    if provider == "deepseek":
        _reject_custom_endpoint(request)
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": _structured_system_prompt(request)},
                {"role": "user", "content": _user_prompt(request)},
            ],
            "max_tokens": request.max_output_tokens,
            "stream": request.stream,
        }
        if request.stream:
            body["stream_options"] = {"include_usage": True}
        if request.output_schema is not None:
            body["response_format"] = {"type": "json_object"}
        if request.temperature is not None:
            body["temperature"] = float(request.temperature)
        return _Transport(
            endpoint=_DEEPSEEK_CHAT_ENDPOINT,
            headers=_bearer_headers(request),
            body=body,
            parser=_parse_deepseek_response,
        )
    if provider == "ollama":
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": _structured_system_prompt(request)},
                {"role": "user", "content": _user_prompt(request)},
            ],
            "stream": request.stream,
            "keep_alive": ollama_keep_alive(),
            "options": {
                "num_ctx": request.context_window_tokens or LOCAL_TEXT_CONTEXT_TOKENS,
                "num_predict": request.max_output_tokens,
            },
        }
        if request.output_schema is not None:
            body["format"] = request.output_schema.schema
        if request.temperature is not None:
            body["options"]["temperature"] = float(request.temperature)
        return _Transport(
            endpoint=_ollama_chat_endpoint(
                request.endpoint,
                allow_remote=request.allow_remote_ollama,
            ),
            headers={"Content-Type": "application/json"},
            body=body,
            parser=_parse_ollama_response,
        )
    raise ValueError(f"unsupported provider: {request.provider}")


def _reject_custom_endpoint(request: AIProviderRequest) -> None:
    if request.endpoint is not None:
        raise ValueError("custom endpoints are not supported by this provider")


def _ollama_chat_endpoint(endpoint: str | None, *, allow_remote: bool = False) -> str:
    raw = (endpoint or _OLLAMA_CHAT_ENDPOINT.removesuffix("/api/chat")).strip()
    validate_ollama_base_url(raw, allow_remote=allow_remote)
    absolute = raw if "://" in raw else f"http://{raw}"
    parsed = urlsplit(absolute)
    base = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    return f"{base}/api/chat"


def _user_prompt(request: AIProviderRequest) -> str:
    return f"Task:\n{request.task}\n\nUser:\n{request.user}"


def _structured_system_prompt(request: AIProviderRequest) -> str:
    if request.output_schema is None:
        return request.system
    schema = json.dumps(request.output_schema.schema, ensure_ascii=True, separators=(",", ":"))
    return f"{request.system}\n\nReturn JSON matching this schema exactly:\n{schema}"


def _require_api_key(request: AIProviderRequest) -> str:
    if request.api_key is None or not request.api_key.strip():
        raise ValueError(f"{request.provider} api_key is required")
    return request.api_key


def _bearer_headers(request: AIProviderRequest) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_require_api_key(request)}",
    }


def _timeout_reason(reason: object) -> bool:
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason).lower()


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def _response_object(payload: object, *, provider: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise AIProviderError(provider, "malformed_response", f"{provider} malformed response")
    return payload


def _require_text(value: object, *, provider: str) -> str:
    if not isinstance(value, str) or not value:
        raise AIProviderError(provider, "malformed_response", f"{provider} malformed response")
    return value


def _parse_usage(payload: object, *, provider: str, mapping: dict[str, str]) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise AIProviderError(provider, "malformed_response", f"{provider} malformed response")
    usage: dict[str, int] = {}
    for source_key, target_key in mapping.items():
        value = payload.get(source_key)
        if type(value) is not int or value < 0:
            raise AIProviderError(
                provider,
                "malformed_response",
                f"{provider} malformed response",
            )
        usage[target_key] = value
    return usage


def _parse_openai_response(payload: object) -> tuple[str, str, dict[str, int], dict[str, float]]:
    body = _response_object(payload, provider="openai")
    status = body.get("status")
    if status in {"incomplete", "in_progress", "queued"}:
        _raise_incomplete_response("openai")
    if status in {"failed", "cancelled"}:
        _raise_provider_failure("openai")
    if status != "completed":
        _raise_incomplete_response("openai")
    text_parts: list[str] = []
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "refusal":
                    _raise_provider_refusal("openai")
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        text_parts.append(text)
    text = _require_text("".join(text_parts), provider="openai")
    usage = _parse_usage(
        body.get("usage"),
        provider="openai",
        mapping={
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "total_tokens": "total_tokens",
        },
    )
    return _require_text(body.get("model"), provider="openai"), text, usage, {}


def _parse_anthropic_response(payload: object) -> tuple[str, str, dict[str, int], dict[str, float]]:
    body = _response_object(payload, provider="anthropic")
    stop_reason = body.get("stop_reason")
    if stop_reason is None:
        _raise_incomplete_response("anthropic")
    if stop_reason in {"max_tokens", "model_context_window_exceeded", "pause_turn"}:
        _raise_incomplete_response("anthropic")
    if stop_reason == "refusal":
        _raise_provider_refusal("anthropic")
    if stop_reason not in {"end_turn", "stop_sequence"}:
        _raise_provider_failure("anthropic")
    content = body.get("content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
    text = _require_text("".join(text_parts), provider="anthropic")
    usage = _parse_usage(
        body.get("usage"),
        provider="anthropic",
        mapping={"input_tokens": "input_tokens", "output_tokens": "output_tokens"},
    )
    return _require_text(body.get("model"), provider="anthropic"), text, usage, {}


def _parse_deepseek_response(payload: object) -> tuple[str, str, dict[str, int], dict[str, float]]:
    body = _response_object(payload, provider="deepseek")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIProviderError("deepseek", "malformed_response", "deepseek malformed response")
    finish_reason = choices[0].get("finish_reason")
    if finish_reason is None:
        _raise_incomplete_response("deepseek")
    if finish_reason == "length":
        _raise_incomplete_response("deepseek")
    if finish_reason == "content_filter":
        _raise_provider_refusal("deepseek")
    if finish_reason != "stop":
        _raise_provider_failure("deepseek")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AIProviderError("deepseek", "malformed_response", "deepseek malformed response")
    text = _require_text(message.get("content"), provider="deepseek")
    usage = _parse_usage(
        body.get("usage"),
        provider="deepseek",
        mapping={
            "prompt_tokens": "input_tokens",
            "completion_tokens": "output_tokens",
            "total_tokens": "total_tokens",
        },
    )
    return _require_text(body.get("model"), provider="deepseek"), text, usage, {}


def _parse_ollama_response(payload: object) -> tuple[str, str, dict[str, int], dict[str, float]]:
    body = _response_object(payload, provider="ollama")
    if body.get("done") is not True or body.get("done_reason") == "length":
        _raise_incomplete_response("ollama")
    message = body.get("message")
    if not isinstance(message, dict):
        raise AIProviderError("ollama", "malformed_response", "ollama malformed response")
    text = _require_text(message.get("content"), provider="ollama")
    input_tokens = body.get("prompt_eval_count")
    output_tokens = body.get("eval_count")
    if (
        type(input_tokens) is not int
        or input_tokens < 0
        or type(output_tokens) is not int
        or output_tokens < 0
    ):
        raise AIProviderError("ollama", "malformed_response", "ollama malformed response")
    timing: dict[str, float] = {}
    for source_key, target_key in (
        ("total_duration", "total_seconds"),
        ("load_duration", "load_seconds"),
        ("prompt_eval_duration", "input_seconds"),
        ("eval_duration", "output_seconds"),
    ):
        value = body.get(source_key)
        if type(value) is int and value >= 0:
            timing[target_key] = value / 1_000_000_000
    return (
        _require_text(body.get("model"), provider="ollama"),
        text,
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        timing,
    )


def _parse_streaming_response(
    response,
    *,
    request: AIProviderRequest,
    clock: Callable[[], float],
    started_at: float,
    deadline_at: float,
) -> tuple[str, str, dict[str, int], dict[str, float]]:
    provider = request.provider.strip().lower()
    if provider == "openai":
        return _parse_openai_stream(
            response,
            request=request,
            clock=clock,
            started_at=started_at,
            deadline_at=deadline_at,
        )
    if provider == "anthropic":
        return _parse_anthropic_stream(
            response,
            request=request,
            clock=clock,
            started_at=started_at,
            deadline_at=deadline_at,
        )
    if provider == "deepseek":
        return _parse_deepseek_stream(
            response,
            request=request,
            clock=clock,
            started_at=started_at,
            deadline_at=deadline_at,
        )
    if provider == "ollama":
        return _parse_ollama_stream(
            response,
            request=request,
            clock=clock,
            started_at=started_at,
            deadline_at=deadline_at,
        )
    raise ValueError(f"unsupported provider: {request.provider}")


def _parse_openai_stream(
    response,
    *,
    request: AIProviderRequest,
    clock: Callable[[], float],
    started_at: float,
    deadline_at: float,
) -> tuple[str, str, dict[str, int], dict[str, float]]:
    text_parts: list[str] = []
    first_token: float | None = None
    completed: dict[str, object] | None = None
    for payload, _done in _sse_payloads(
        response,
        request=request,
        deadline_at=deadline_at,
    ):
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str):
                _raise_malformed_stream("openai")
            if delta:
                first_token = _first_token_time(first_token, clock, started_at)
                text_parts.append(delta)
        elif event_type == "response.completed":
            value = payload.get("response")
            if not isinstance(value, dict):
                _raise_malformed_stream("openai")
            completed = value
        elif event_type in {"response.incomplete", "response.cancelled"}:
            _raise_incomplete_response("openai")
        elif event_type in {"response.failed", "error"}:
            _raise_provider_failure("openai")
    if completed is None:
        _raise_incomplete_response("openai")
    model, text, usage, timing = _parse_openai_response(completed)
    if text_parts and "".join(text_parts) != text:
        _raise_malformed_stream("openai")
    return model, text, usage, _stream_timing(timing, first_token, provider="openai")


def _parse_anthropic_stream(
    response,
    *,
    request: AIProviderRequest,
    clock: Callable[[], float],
    started_at: float,
    deadline_at: float,
) -> tuple[str, str, dict[str, int], dict[str, float]]:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: object = None
    text_parts: list[str] = []
    first_token: float | None = None
    stopped = False
    for payload, _done in _sse_payloads(
        response,
        request=request,
        deadline_at=deadline_at,
    ):
        event_type = payload.get("type")
        if event_type == "message_start":
            message = payload.get("message")
            if not isinstance(message, dict):
                _raise_malformed_stream("anthropic")
            model = _require_text(message.get("model"), provider="anthropic")
            usage = message.get("usage")
            if not isinstance(usage, dict):
                _raise_malformed_stream("anthropic")
            input_tokens = _nonnegative_int(usage.get("input_tokens"), provider="anthropic")
        elif event_type == "content_block_delta":
            delta = payload.get("delta")
            if not isinstance(delta, dict):
                _raise_malformed_stream("anthropic")
            if delta.get("type") == "text_delta":
                text = delta.get("text")
                if not isinstance(text, str):
                    _raise_malformed_stream("anthropic")
                if text:
                    first_token = _first_token_time(first_token, clock, started_at)
                    text_parts.append(text)
        elif event_type == "message_delta":
            delta = payload.get("delta")
            usage = payload.get("usage")
            if not isinstance(delta, dict) or not isinstance(usage, dict):
                _raise_malformed_stream("anthropic")
            stop_reason = delta.get("stop_reason")
            output_tokens = _nonnegative_int(usage.get("output_tokens"), provider="anthropic")
        elif event_type == "message_stop":
            stopped = True
        elif event_type == "error":
            error = payload.get("error")
            error_type = error.get("type") if isinstance(error, dict) else None
            raise AIProviderError(
                "anthropic",
                "stream_error",
                "anthropic stream failed",
                retryable=error_type in {"api_error", "overloaded_error", "rate_limit_error"},
            )
    if not stopped:
        _raise_incomplete_response("anthropic")
    if stop_reason in {"max_tokens", "model_context_window_exceeded", "pause_turn"}:
        _raise_incomplete_response("anthropic")
    if stop_reason == "refusal":
        _raise_provider_refusal("anthropic")
    if stop_reason not in {"end_turn", "stop_sequence"}:
        _raise_provider_failure("anthropic")
    text = _require_text("".join(text_parts), provider="anthropic")
    if model is None or input_tokens is None or output_tokens is None:
        _raise_malformed_stream("anthropic")
    return (
        model,
        text,
        {"input_tokens": input_tokens, "output_tokens": output_tokens},
        _stream_timing({}, first_token, provider="anthropic"),
    )


def _parse_deepseek_stream(
    response,
    *,
    request: AIProviderRequest,
    clock: Callable[[], float],
    started_at: float,
    deadline_at: float,
) -> tuple[str, str, dict[str, int], dict[str, float]]:
    model: str | None = None
    usage: dict[str, int] | None = None
    finish_reason: object = None
    text_parts: list[str] = []
    first_token: float | None = None
    done = False
    for payload, done_marker in _sse_payloads(
        response,
        request=request,
        deadline_at=deadline_at,
    ):
        if done_marker:
            done = True
            continue
        if isinstance(payload.get("error"), dict):
            _raise_provider_failure("deepseek")
        if isinstance(payload.get("model"), str):
            model = _require_text(payload.get("model"), provider="deepseek")
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if not isinstance(choice, dict):
                _raise_malformed_stream("deepseek")
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                _raise_malformed_stream("deepseek")
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                _raise_malformed_stream("deepseek")
            if content:
                first_token = _first_token_time(first_token, clock, started_at)
                text_parts.append(content)
        elif choices is not None and choices != []:
            _raise_malformed_stream("deepseek")
        if payload.get("usage") is not None:
            usage = _parse_usage(
                payload.get("usage"),
                provider="deepseek",
                mapping={
                    "prompt_tokens": "input_tokens",
                    "completion_tokens": "output_tokens",
                    "total_tokens": "total_tokens",
                },
            )
    if not done:
        _raise_incomplete_response("deepseek")
    if finish_reason == "length":
        _raise_incomplete_response("deepseek")
    if finish_reason == "content_filter":
        _raise_provider_refusal("deepseek")
    if finish_reason != "stop":
        _raise_provider_failure("deepseek")
    if model is None or usage is None:
        _raise_malformed_stream("deepseek")
    return (
        model,
        _require_text("".join(text_parts), provider="deepseek"),
        usage,
        _stream_timing({}, first_token, provider="deepseek"),
    )


def _parse_ollama_stream(
    response,
    *,
    request: AIProviderRequest,
    clock: Callable[[], float],
    started_at: float,
    deadline_at: float,
) -> tuple[str, str, dict[str, int], dict[str, float]]:
    text_parts: list[str] = []
    first_token: float | None = None
    completed: dict[str, object] | None = None
    for payload in _ndjson_payloads(
        response,
        request=request,
        deadline_at=deadline_at,
    ):
        if payload.get("error") is not None:
            _raise_provider_failure("ollama")
        message = payload.get("message")
        if not isinstance(message, dict):
            _raise_malformed_stream("ollama")
        content = message.get("content")
        if not isinstance(content, str):
            _raise_malformed_stream("ollama")
        if content:
            first_token = _first_token_time(first_token, clock, started_at)
            text_parts.append(content)
        if payload.get("done") is True:
            completed = payload
    if completed is None:
        _raise_incomplete_response("ollama")
    completed = dict(completed)
    completed["message"] = {"content": "".join(text_parts)}
    model, text, usage, timing = _parse_ollama_response(completed)
    return model, text, usage, _stream_timing(timing, first_token, provider="ollama")


def _stream_lines(
    response,
    *,
    request: AIProviderRequest,
    deadline_at: float,
) -> Iterator[str]:
    total_bytes = 0
    for raw_line in response:
        if monotonic() > deadline_at:
            raise AIProviderError(
                request.provider,
                "timeout",
                f"{request.provider} timeout",
                retryable=True,
            )
        if request.is_cancelled is not None and request.is_cancelled():
            raise AIProviderCancelledError(request.provider)
        if not isinstance(raw_line, bytes):
            _raise_malformed_stream(request.provider)
        total_bytes += len(raw_line)
        if total_bytes > _MAX_RESPONSE_BYTES:
            raise AIProviderError(
                request.provider,
                "response_too_large",
                f"{request.provider} response too large",
            )
        yield raw_line.decode("utf-8").strip()


def _sse_payloads(
    response,
    *,
    request: AIProviderRequest,
    deadline_at: float,
) -> Iterator[tuple[dict[str, object], bool]]:
    for line in _stream_lines(response, request=request, deadline_at=deadline_at):
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            _raise_malformed_stream(request.provider)
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            yield {}, True
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                request.provider,
                "malformed_response",
                f"{request.provider} malformed response",
            ) from exc
        if not isinstance(payload, dict):
            _raise_malformed_stream(request.provider)
        yield payload, False


def _ndjson_payloads(
    response,
    *,
    request: AIProviderRequest,
    deadline_at: float,
) -> Iterator[dict[str, object]]:
    for line in _stream_lines(response, request=request, deadline_at=deadline_at):
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                request.provider,
                "malformed_response",
                f"{request.provider} malformed response",
            ) from exc
        if not isinstance(payload, dict):
            _raise_malformed_stream(request.provider)
        yield payload


def _first_token_time(
    current: float | None,
    clock: Callable[[], float],
    started_at: float,
) -> float:
    return current if current is not None else max(0.0, clock() - started_at)


def _stream_timing(
    timing: dict[str, float],
    first_token: float | None,
    *,
    provider: str,
) -> dict[str, float]:
    if first_token is None:
        _raise_malformed_stream(provider)
    return {**timing, "time_to_first_token_seconds": first_token}


def _nonnegative_int(value: object, *, provider: str) -> int:
    if type(value) is not int or value < 0:
        _raise_malformed_stream(provider)
    return value


def _raise_malformed_stream(provider: str) -> None:
    raise AIProviderError(provider, "malformed_response", f"{provider} malformed response")


def _raise_incomplete_response(provider: str) -> None:
    raise AIProviderError(
        provider,
        "incomplete_response",
        f"{provider} returned an incomplete response",
    )


def _raise_provider_failure(provider: str) -> None:
    raise AIProviderError(
        provider,
        "provider_failure",
        f"{provider} did not complete the response",
    )


def _raise_provider_refusal(provider: str) -> None:
    raise AIProviderError(
        provider,
        "refused",
        f"{provider} refused the request",
    )
