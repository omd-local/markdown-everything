import json
import urllib.request

import pytest


def test_ollama_keep_alive_is_shorter_on_memory_constrained_machines(monkeypatch):
    from omd import ollama_runtime
    from omd._models import GIB

    monkeypatch.delenv("OMD_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.delenv("OMD_MEMORY_PRESSURE", raising=False)
    monkeypatch.setattr(ollama_runtime, "detect_total_memory_bytes", lambda: 16 * GIB)

    assert ollama_runtime.ollama_keep_alive() == "60s"


def test_ollama_keep_alive_reuses_warm_model_on_roomier_machines(monkeypatch):
    from omd import ollama_runtime
    from omd._models import GIB

    monkeypatch.delenv("OMD_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.delenv("OMD_MEMORY_PRESSURE", raising=False)
    monkeypatch.setattr(ollama_runtime, "detect_total_memory_bytes", lambda: 32 * GIB)

    assert ollama_runtime.ollama_keep_alive() == "5m"


def test_ollama_keep_alive_unloads_after_request_under_memory_pressure(monkeypatch):
    from omd import ollama_runtime

    monkeypatch.setenv("OMD_MEMORY_PRESSURE", "high")
    monkeypatch.setenv("OMD_OLLAMA_KEEP_ALIVE", "24h")

    assert ollama_runtime.ollama_keep_alive() == 0


def test_ollama_keep_alive_accepts_explicit_duration(monkeypatch):
    from omd import ollama_runtime

    monkeypatch.setenv("OMD_MEMORY_PRESSURE", "normal")
    monkeypatch.setenv("OMD_OLLAMA_KEEP_ALIVE", "10m")

    assert ollama_runtime.ollama_keep_alive() == "10m"


def test_ollama_keep_alive_ignores_invalid_override(monkeypatch):
    from omd import ollama_runtime

    monkeypatch.setenv("OMD_MEMORY_PRESSURE", "normal")
    monkeypatch.setenv("OMD_OLLAMA_KEEP_ALIVE", "forever please")
    monkeypatch.setattr(ollama_runtime, "detect_total_memory_bytes", lambda: None)

    assert ollama_runtime.ollama_keep_alive() == "2m"


def test_memory_pressure_is_detected_from_available_memory(monkeypatch):
    from omd import ollama_runtime
    from omd._models import GIB

    monkeypatch.delenv("OMD_MEMORY_PRESSURE", raising=False)
    monkeypatch.setattr(ollama_runtime, "detect_total_memory_bytes", lambda: 16 * GIB)
    monkeypatch.setattr(ollama_runtime, "detect_available_memory_bytes", lambda: 1 * GIB)

    assert ollama_runtime.memory_pressure_level() == "high"
    assert ollama_runtime.ollama_keep_alive() == 0


def test_memory_pressure_stays_normal_when_available_memory_is_unknown(monkeypatch):
    from omd import ollama_runtime

    monkeypatch.delenv("OMD_MEMORY_PRESSURE", raising=False)
    monkeypatch.setattr(ollama_runtime, "detect_total_memory_bytes", lambda: None)
    monkeypatch.setattr(ollama_runtime, "detect_available_memory_bytes", lambda: None)

    assert ollama_runtime.memory_pressure_level() == "normal"


def test_request_ollama_json_uses_no_redirect_opener_by_default(monkeypatch):
    from omd import ollama_runtime

    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit: int) -> bytes:
            captured["limit"] = limit
            return b'{"response":"ok"}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(
        ollama_runtime,
        "build_no_redirect_opener",
        lambda: FakeOpener(),
    )

    result = ollama_runtime.request_ollama_json(
        urllib.request.Request("http://localhost:11434/api/chat", data=b"{}"),
        timeout=3,
    )

    assert result == {"response": "ok"}
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == 3
    assert captured["limit"] == ollama_runtime.MAX_OLLAMA_RESPONSE_BYTES + 1


def test_request_ollama_json_rejects_oversized_response():
    from omd import ollama_runtime

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit: int) -> bytes:
            return b"{" + b"x" * limit

    with pytest.raises(RuntimeError, match="response too large"):
        ollama_runtime.request_ollama_json(
            urllib.request.Request("http://localhost:11434/api/chat", data=b"{}"),
            timeout=3,
            open_call=lambda _request, timeout: FakeResponse(),
        )


def test_request_ollama_json_rejects_non_object_payload():
    from omd import ollama_runtime

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps(["unexpected"]).encode("utf-8")

    with pytest.raises(RuntimeError, match="JSON object"):
        ollama_runtime.request_ollama_json(
            urllib.request.Request("http://localhost:11434/api/chat", data=b"{}"),
            timeout=3,
            open_call=lambda _request, timeout: FakeResponse(),
        )
