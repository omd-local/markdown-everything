from __future__ import annotations

import json


def test_generate_memory_cards_chunks_long_markdown_before_final_pass():
    from omd import memory_cards

    prompts: list[str] = []

    def fake_chat(prompt: str, model: str, host: str) -> str:
        prompts.append(prompt)
        if "Chunk " in prompt and "Create memory output" not in prompt:
            return "- Chunk note. Evidence: source section above."
        return (
            '{"summary":"Grounded summary with enough source-specific detail to avoid a short-output drift warning.",'
            '"tags":["Local AI","RAG preprocessing"],'
            '"memory_cards_markdown":"### Concepts\\n- [[RAG preprocessing]]: Document conversion quality affects retrieval quality for downstream AI memory workflows. Evidence: source section above."}'
        )

    result = memory_cards.generate_memory_cards(
        "source detail " * 1200,
        model="qwen3:4b",
        host="http://localhost:11434",
        title="Long Source",
        source_type="podcast",
        _chat_fn=fake_chat,
    )

    chunk_prompts = [prompt for prompt in prompts if "Chunk " in prompt]
    assert len(chunk_prompts) > 1
    assert prompts[-1].startswith("Source title: Long Source")
    assert "chunk notes" in prompts[-1]
    assert result.summary.startswith("Grounded summary")
    assert result.tags == ["local-ai", "rag-preprocessing"]
    assert "[[RAG preprocessing]]" in result.cards_markdown
    assert result.warnings == []


def test_generate_memory_cards_parses_json_embedded_in_model_text():
    from omd import memory_cards

    def fake_chat(_prompt: str, _model: str, _host: str) -> str:
        return (
            "Here is the JSON:\n"
            '{"summary":"Summary","tags":"Research, Local AI",'
            '"memory_cards_markdown":"### Claims\\n- Claim: X. Evidence: source section above."}'
        )

    result = memory_cards.generate_memory_cards(
        "# Source\n\nBody",
        _chat_fn=fake_chat,
    )

    assert result.summary == "Summary"
    assert result.tags == ["research", "local-ai"]
    assert "Claim: X" in result.cards_markdown


def test_generate_memory_cards_preserves_language_prompt_and_chinese_tags():
    from omd import memory_cards

    prompts: list[str] = []

    def fake_chat(prompt: str, _model: str, _host: str) -> str:
        prompts.append(prompt)
        return (
            '{"summary":"这是一段中文总结，保留原文语言。",'
            '"tags":["女性智慧","自我关怀","Local AI"],'
            '"memory_cards_markdown":"### Concepts\\n- [[自我关怀]]: 记录中文原文中的观点。 Evidence: source section above."}'
        )

    result = memory_cards.generate_memory_cards(
        "# 中文来源\n\n这是关于自我关怀和女性智慧的中文内容。",
        _chat_fn=fake_chat,
    )

    assert "preserve the source language" in prompts[-1]
    assert "do not translate Chinese" in prompts[-1]
    assert result.tags == ["女性智慧", "自我关怀", "local-ai"]
    assert "[[自我关怀]]" in result.cards_markdown


def test_generate_memory_cards_warns_on_empty_generated_content():
    from omd import memory_cards

    def fake_chat(_prompt: str, _model: str, _host: str) -> str:
        return '{"summary":"","tags":[],"memory_cards_markdown":""}'

    result = memory_cards.generate_memory_cards(
        "# Source\n\nShort source",
        _chat_fn=fake_chat,
    )

    assert "memory cards returned no generated content" in result.warnings
    assert "no generated tags were returned" in result.warnings


def test_generate_memory_cards_warns_when_source_exceeds_chunk_limit():
    from omd import memory_cards

    def fake_chat(prompt: str, _model: str, _host: str) -> str:
        if "Chunk " in prompt and "Create memory output" not in prompt:
            return "- Chunk note. Evidence: source section above."
        return (
            '{"summary":"Grounded summary with enough source-specific detail to avoid a short-output drift warning.",'
            '"tags":["Local AI"],'
            '"memory_cards_markdown":"### Claims\\n- Claim: X. Evidence: source section above."}'
        )

    result = memory_cards.generate_memory_cards(
        "source detail " * 5000,
        _chat_fn=fake_chat,
    )

    assert any("first 8 chunks" in warning for warning in result.warnings)


def test_chat_ollama_disables_thinking(monkeypatch):
    from omd import memory_cards

    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"message":{"content":"{\\"summary\\":\\"ok\\",\\"tags\\":[],\\"memory_cards_markdown\\":\\"\\"}"}}'

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(memory_cards.urllib.request, "urlopen", fake_urlopen)

    response = memory_cards._chat_ollama("prompt", "qwen3:4b", "http://localhost:11434")

    assert response.startswith('{"summary"')
    assert captured["timeout"] == 180
    assert captured["body"]["think"] is False
    assert captured["body"]["options"]["num_predict"] == 900
