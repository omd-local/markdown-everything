"""Optional local memory-card generation for capture notes."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from omd._models import TEXT_POLISH_MODEL
from omd._network_policy import validate_ollama_host
from omd.ollama_runtime import ollama_keep_alive, request_ollama_json
from omd.tag_normalization import normalize_generated_tags

CHUNK_CHARS = 6000
MAX_CHUNKS = 8
MIN_GENERATED_CHARS = 120


@dataclass(frozen=True)
class MemoryCardsResult:
    summary: str
    tags: list[str]
    cards_markdown: str
    model: str
    host: str
    warnings: list[str]

    @property
    def generated_text(self) -> str:
        return "\n\n".join(part for part in [self.summary, self.cards_markdown] if part.strip())


def generate_memory_cards(
    markdown: str,
    *,
    model: str = TEXT_POLISH_MODEL,
    host: str = "http://localhost:11434",
    timeout: float = 180,
    title: str = "",
    source_type: str = "",
    allow_remote: bool = False,
    _chat_fn=None,
) -> MemoryCardsResult:
    """Generate summary, tags, and memory cards through an Ollama-compatible API.

    The local UI enables this for vault capture by default; CLI capture remains
    explicit and calls it only when the user passes --memory-cards.
    """
    validate_ollama_host(host, allow_remote=allow_remote)
    chunks, truncated = _chunk_markdown(markdown)
    if _chat_fn is None:
        def chat(prompt, model, host):
            return _chat_ollama(prompt, model, host, timeout=timeout)
    else:
        chat = _chat_fn
    if len(chunks) == 1:
        payload = _parse_model_json(
            chat(_final_prompt(chunks[0], title=title, source_type=source_type), model, host)
        )
    else:
        chunk_notes = []
        for index, chunk in enumerate(chunks, 1):
            chunk_notes.append(
                chat(_chunk_prompt(chunk, index=index, total=len(chunks), title=title), model, host)
            )
        payload = _parse_model_json(
            chat(
                _final_prompt(
                    "\n\n".join(chunk_notes),
                    title=title,
                    source_type=source_type,
                    from_chunk_notes=True,
                ),
                model,
                host,
            )
        )

    summary = str(payload.get("summary") or "").strip()
    tags = _normalize_generated_tags(payload.get("tags"))
    cards = str(payload.get("memory_cards_markdown") or payload.get("memory_cards") or "").strip()
    warnings = _drift_warnings(markdown, summary=summary, cards=cards, tags=tags, truncated=truncated)
    return MemoryCardsResult(
        summary=summary,
        tags=tags,
        cards_markdown=cards,
        model=model,
        host=host,
        warnings=warnings,
    )


def format_memory_sections(result: MemoryCardsResult) -> str:
    tags = "\n".join(f"- `{tag}`" for tag in result.tags) or "_No generated tags._"
    summary = result.summary or "_No summary generated._"
    cards = result.cards_markdown or "_No memory cards generated._"
    warnings = ""
    if result.warnings:
        warnings = "\n\n## Memory Warnings\n\n" + "\n".join(f"- {warning}" for warning in result.warnings)
    return (
        "## Summary\n\n"
        f"{summary}\n\n"
        "## Generated Tags\n\n"
        f"{tags}\n\n"
        "## Memory Cards\n\n"
        f"{cards}"
        f"{warnings}\n\n"
    )


def _chat_ollama(prompt: str, model: str, host: str, *, timeout: float = 180) -> str:
    endpoint = host.rstrip("/") + "/api/chat"
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": ollama_keep_alive(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You create concise AI memory cards from source Markdown. "
                        "Preserve uncertainty. Do not invent facts."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.2, "num_predict": 900},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        payload = request_ollama_json(request, timeout=timeout)
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        raise RuntimeError(f"Ollama memory-card generation failed: {exc}") from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(payload.get("response") or "") if isinstance(payload, dict) else ""


def _chunk_markdown(markdown: str) -> tuple[list[str], bool]:
    text = markdown.strip()
    if not text:
        return [""], False
    chunks = [text[index:index + CHUNK_CHARS] for index in range(0, len(text), CHUNK_CHARS)]
    return chunks[:MAX_CHUNKS], len(chunks) > MAX_CHUNKS


def _chunk_prompt(chunk: str, *, index: int, total: int, title: str) -> str:
    return (
        f"Source title: {title or 'unknown'}\n"
        f"Chunk {index} of {total}.\n\n"
        "Extract only evidence-backed memory notes from this chunk. "
        "Preserve the source language and do not translate unless the source itself provides a translation. "
        "Use short bullets and include 'Evidence:' when a card relies on a source detail.\n\n"
        f"{chunk}"
    )


def _final_prompt(
    content: str,
    *,
    title: str,
    source_type: str,
    from_chunk_notes: bool = False,
) -> str:
    input_label = "chunk notes" if from_chunk_notes else "source Markdown"
    return (
        f"Source title: {title or 'unknown'}\n"
        f"Source type: {source_type or 'unknown'}\n\n"
        f"Create memory output from this {input_label}.\n"
        "Return strict JSON only, with this shape:\n"
        "{\n"
        '  "summary": "3-6 sentence source-grounded summary",\n'
        '  "tags": ["short-tag", "another-tag"],\n'
        '  "memory_cards_markdown": "### Concepts\\n- [[Concept]]: note. Evidence: source section above.\\n\\n### People / Orgs\\n- ...\\n\\n### Claims\\n- Claim: ...\\n  Evidence: source section above.\\n\\n### Questions\\n- ..."\n'
        "}\n\n"
        "Rules: keep raw-source facts separate from inference, avoid unsupported claims, "
        "preserve the source language for summaries, tags, and cards, do not translate Chinese "
        "or mixed-language source material into English unless explicitly present in the source, "
        "and include at least one Evidence line for claims or concepts when possible.\n\n"
        f"{content}"
    )


def _parse_model_json(text: str) -> dict[str, object]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else {"memory_cards_markdown": stripped}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {"memory_cards_markdown": stripped}
        except json.JSONDecodeError:
            pass
    return {"summary": "", "tags": [], "memory_cards_markdown": stripped}


def _normalize_generated_tags(value: object) -> list[str]:
    return normalize_generated_tags(value)


def _drift_warnings(
    markdown: str,
    *,
    summary: str,
    cards: str,
    tags: list[str],
    truncated: bool = False,
) -> list[str]:
    generated = (summary + "\n" + cards).strip()
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"memory cards were generated from the first {MAX_CHUNKS} chunks; raw content is preserved in Full Content"
        )
    if not generated:
        warnings.append("memory cards returned no generated content")
    if len(markdown.strip()) >= 500 and len(generated) < MIN_GENERATED_CHARS:
        warnings.append("memory cards are very short relative to the source; review before relying on them")
    if cards and "evidence:" not in cards.lower():
        warnings.append("memory cards do not include explicit Evidence references")
    if not tags:
        warnings.append("no generated tags were returned")
    return warnings
