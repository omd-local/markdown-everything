from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omd.ai_service import AIServiceError, AITextResult
from omd.enrich_note import (
    EnrichNoteError,
    build_proposal_response,
    decode_request,
    run_enrich_note,
    validate_model_output,
)


FIXTURES = Path(__file__).parent / "fixtures" / "enrich_note" / "v1"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request_payload(vault_path: str = "/vault", request_id: str = "request-1"):
    content = "本地 AI 可以辅助个人知识工作流。"
    return {
        "schema_version": 1,
        "request_id": request_id,
        "action": "enrich_note_preview",
        "vault_path": vault_path,
        "note": {
            "path": "Inbox/example.md",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        },
        "candidates": [
            {
                "id": "candidate-1",
                "path": "Notes/Local AI.md",
                "title": "Local AI",
                "aliases": ["本地 AI"],
                "tags": ["ai/local", "research"],
                "evidence": "本地 AI 与个人知识工作流。",
            }
        ],
        "vault_tags": ["ai/local", "research", "workflow"],
        "model": "qwen3:4b-instruct",
        "host": "http://localhost:11434",
    }


def _decode(payload):
    return decode_request(json.dumps(payload, ensure_ascii=False).encode())


@pytest.mark.parametrize("legacy_multiline", [False, True])
def test_home_builder_fixture_reaches_proposal_without_changing_files(tmp_path, legacy_multiline):
    payload = _load("home-multiline-request.json")
    builder_input = json.loads((FIXTURES.parent / "home-multiline-input.json").read_text())
    expected = [
        "- Source: https://example.com/ - Author: Example - Published: 2024-08-30",
        (
            "Dense and sparse embeddings represent text across languages. "
            "- Dense: 语义 🙂 across a wrapped list item. - Sparse: lexical features."
        ),
    ]
    assert [candidate["evidence"] for candidate in payload["candidates"]] == expected
    assert payload["note"]["content"] == builder_input["target"]["content"]
    if legacy_multiline:
        for candidate, source in zip(payload["candidates"], builder_input["candidates"]):
            candidate["evidence"] = source["evidence"]
    payload["vault_path"] = str(tmp_path)
    original_files = {payload["note"]["path"]: payload["note"]["content"].encode()}
    for candidate, source in zip(payload["candidates"], builder_input["candidates"]):
        original_files[candidate["path"]] = source["evidence"].encode()
    for relative, content in original_files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    request = _decode(payload)
    generated = []

    def execute(task, *, source_text, **kwargs):
        envelope = json.loads(source_text)
        assert [item["evidence"] for item in envelope["untrusted_candidates"]] == expected
        assert envelope["untrusted_note"]["content"] == builder_input["target"]["content"]
        generated.append(True)
        proposal = {
            "summary": "Embeddings represent text.",
            "existing_links": [], "new_concepts": [], "existing_tags": [], "new_tags": [],
        }
        return AITextResult(
            provider="ollama", requested_model=task.model, actual_model=task.model,
            capability=task.capability, privacy_mode="local_only", destination_domain="localhost",
            text=json.dumps(proposal), usage={}, timing={}, structured=proposal,
        )

    response = run_enrich_note(request, executor=execute)

    assert generated == [True]
    assert response["note"]["content_sha256"] == payload["note"]["content_sha256"]
    assert response["proposal"]["summary"] == "Embeddings represent text."
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*.md")
    } == original_files


def test_valid_response_fixture_matches_runtime_validation_and_serializer():
    request = _decode(_request_payload())
    model_output = {
        "summary": "这篇笔记讨论本地 AI 与个人知识工作流。",
        "existing_links": [
            {
                "candidate_id": "candidate-1",
                "reason": "主题直接相关",
                "evidence": "本地 AI",
                "recommended": True,
            }
        ],
        "new_concepts": [{"label": "个人知识工作流", "reason": "可发展为独立概念"}],
        "existing_tags": [
            {"tag": "ai/local", "reason": "匹配核心主题", "recommended": True}
        ],
        "new_tags": [{"tag": "knowledge-workflow", "reason": "描述工作流主题"}],
    }

    response = build_proposal_response(
        request,
        validate_model_output(model_output, request),
        provider="ollama",
        actual_model=request.model,
        endpoint_class="local_loopback",
    )

    assert response == _load("valid-response.json")


def test_unsupported_schema_fixture_uses_runtime_request_validator():
    raw = (FIXTURES / "unsupported-schema.json").read_bytes()

    with pytest.raises(EnrichNoteError) as excinfo:
        decode_request(raw)

    assert excinfo.value.code == "unsupported_schema"


def test_unknown_candidate_fixture_uses_runtime_model_validator():
    request = _decode(_request_payload())

    with pytest.raises(EnrichNoteError) as excinfo:
        validate_model_output(_load("unknown-candidate-id.json"), request)

    assert excinfo.value.code == "unknown_candidate_id"


def test_request_too_large_fixture_expands_through_runtime_request_validator():
    fixture = _load("request-too-large.json")
    raw = fixture["repeat_character"].encode() * fixture["repeat_bytes"]

    with pytest.raises(EnrichNoteError) as excinfo:
        decode_request(raw)

    assert excinfo.value.code == fixture["expected_error"]


def test_generation_timeout_fixture_uses_runtime_pipeline_mapping(tmp_path):
    fixture = _load("generation-timeout.json")
    payload = _request_payload(str(tmp_path), request_id=fixture["request_id"])
    for relative, content in (
        (payload["note"]["path"], payload["note"]["content"]),
        (payload["candidates"][0]["path"], "# Local AI\n"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def execute(*args, **kwargs):
        raise AIServiceError(
            fixture["provider"], fixture["service_code"], fixture["service_message"]
        )

    with pytest.raises(EnrichNoteError) as excinfo:
        run_enrich_note(_decode(payload), executor=execute)

    assert excinfo.value.code == fixture["expected_error"]
    assert excinfo.value.request_id == fixture["request_id"]
