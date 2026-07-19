"""Generic Markdown post-processor via Ollama.

Splits a `.md` file into sections delimited by `## ` (and `### `) headings,
sends each section through a local Ollama chat model with a "fix typos,
normalize whitespace, preserve content" prompt, and reassembles. Used by
the `--polish-md` flag on the top-level CLI.

Skipped sections:
- `## Transcript (polished)` (and `## Transcript (raw)` if a polished
  one already exists in the same document) — those came out of
  `omd.reel.polish_transcript` and a second pass would drift.
- Fenced code blocks (```...```) within any section — never polished.

Chunked the same way `omd.reel.polish_transcript` is (≤1500 chars per
LLM call) to keep models in "edit" mode rather than "summarize" mode.

Size guards: warn at >20k chars, refuse at >100k chars unless `force=True`
(driven by the `--force` flag on the CLI).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Iterable

from omd._models import (
    LOCAL_TEXT_CONTEXT_TOKENS,
    TEXT_POLISH_MODEL,
    bounded_edit_output_budget,
    local_text_model_issue,
)

POLISH_CHUNK_SIZE = 1500
WARN_THRESHOLD_CHARS = 20_000
HARD_REFUSE_CHARS = 100_000
READINESS_TIMEOUT_SECONDS = 2.0
POLISH_CHUNK_TIMEOUT_SECONDS = float(os.environ.get("OMD_POLISH_MD_TIMEOUT", "45"))

_SECTION_RE = re.compile(r"^(#{2,3}\s+.+)$", re.MULTILINE)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)

# Headings to skip (already polished by the per-backend polish path).
SKIP_HEADINGS = (
    "## Transcript (polished)",
    "### Timestamped",  # raw segment timestamps; not prose
)


SYSTEM_PROMPT = (
    "You are a Markdown post-processor. Fix OCR/parsing artifacts: stray "
    "hyphens at line breaks, broken whitespace, doubled spaces, dropped "
    "punctuation, line-break debris. Do NOT rewrite, summarize, translate, "
    "or change meaning. Preserve the source language: Chinese input stays "
    "Chinese, English input stays English, and mixed-language input keeps the "
    "same language balance. Preserve every number, name, code identifier, URL, "
    "and Markdown structure (headings, lists, links, emphasis). For web article "
    "Markdown, preserve title, author, publication time, original link, heading "
    "hierarchy, quotes, code blocks, footnotes, links, and original image URLs; "
    "only remove obvious navigation, ads, recommendations, cookie banners, "
    "unrelated comments, or page chrome when they are clearly boilerplate. For "
    "Reddit Markdown, preserve subreddit, post author, comment authors, time, "
    "comment nesting, permalinks, and deleted/edited markers. For podcast "
    "Markdown, preserve show name, episode title, guests/hosts when present, "
    "publication time, original episode link, speakers, timestamps, and "
    "transcript source. Output the "
    "corrected Markdown only - no preamble, no explanation, no code fences "
    "around the output."
)


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Split Markdown into (heading, body) pairs.

    The first chunk before any `## ` heading is returned with heading = ""
    (typically the H1 title + intro). Each subsequent chunk includes its
    heading line as part of `heading`, body is the content under it.
    """
    parts: list[tuple[str, str]] = []
    matches = list(_SECTION_RE.finditer(md))
    if not matches:
        return [("", md)]
    # Pre-section prelude.
    first = matches[0]
    prelude = md[: first.start()]
    parts.append(("", prelude))
    for i, m in enumerate(matches):
        heading = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end]
        parts.append((heading, body))
    return parts


def _split_around_code(text: str) -> list[tuple[str, bool]]:
    """Split a section body into (chunk, is_code_block) pairs so the polish
    pass can skip fenced code."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _FENCED_CODE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [(text, False)]


def _chunk_for_polish(text: str, max_chars: int) -> list[str]:
    """Group text into ≤max_chars chunks at paragraph boundaries when
    possible. Falls back to char-split if a single paragraph is too big."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        candidate = (cur + "\n\n" + para).strip() if cur else para
        if len(candidate) > max_chars and cur:
            chunks.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    # Hard fallback: any chunk still over limit gets char-split.
    out: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            out.append(c)
        else:
            for i in range(0, len(c), max_chars):
                out.append(c[i:i + max_chars])
    return out


def _polish_chunk(text: str, model: str, host: str) -> str:
    """Send one chunk to Ollama, return the model's polished output."""
    n_chars = len(text)
    output_budget = bounded_edit_output_budget(text)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Input ({n_chars} chars):\n{text}\n\nOutput:\n"
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": LOCAL_TEXT_CONTEXT_TOKENS,
            "num_predict": output_budget,
        },
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=POLISH_CHUNK_TIMEOUT_SECONDS) as r:
        payload = json.loads(r.read())
    if payload.get("done_reason") == "length":
        raise RuntimeError(
            f"model reached its output limit ({output_budget} tokens) before finishing"
        )
    out = re.sub(
        r"<think>.*?</think>",
        "",
        str(payload.get("response") or ""),
        flags=re.DOTALL,
    ).strip()
    if not out:
        raise RuntimeError("model returned empty Markdown")
    source_chars = len(text.strip())
    if source_chars >= 80:
        if len(out) < source_chars * 0.45:
            raise RuntimeError("model output is unexpectedly short; refusing a partial rewrite")
        if len(out) > source_chars * 1.8 + 200:
            raise RuntimeError("model output expanded unexpectedly; refusing a likely explanation")
    return out
def _ollama_ready(model: str, host: str, *, timeout: float = READINESS_TIMEOUT_SECONDS) -> tuple[bool, str]:
    if issue := local_text_model_issue(model):
        return False, issue
    req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"Ollama is not reachable at {host}: {exc}"
    models = payload.get("models") if isinstance(payload, dict) else None
    names = {
        str(item.get("name") or item.get("model") or "")
        for item in models or []
        if isinstance(item, dict)
    }
    if names and model not in names:
        return False, f"model {model} is not installed; run `ollama pull {model}` or choose another local model"
    if not names:
        return False, f"no local Ollama models were found; run `ollama pull {model}` or choose another local model"
    return True, "ready"


def polish_markdown(
    md: str,
    model: str = TEXT_POLISH_MODEL,
    host: str = "http://localhost:11434",
    *,
    force: bool = False,
    readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
    _polish_fn=None,
) -> str:
    """Polish a Markdown string.

    `_polish_fn` is the per-chunk LLM call. Default is `_polish_chunk`
    above. Tests inject a mock. Same chunking + skip rules apply
    regardless of the underlying call.
    """
    from omd import _progress
    n = len(md)
    if not force and n > HARD_REFUSE_CHARS:
        from omd import _events
        _events.fatal(
            "polish_too_big",
            f"file is {n} chars (>{HARD_REFUSE_CHARS}); pass --force to override",
        )
    if n > WARN_THRESHOLD_CHARS:
        _progress.warn(
            f"polishing {n} chars — this will take a while via Ollama (~"
            f"{max(1, n // 1500)} chunks)"
        )

    fn = _polish_fn or _polish_chunk
    if _polish_fn is None:
        ready, reason = _ollama_ready(model, host, timeout=readiness_timeout)
        if not ready:
            _progress.warn(
                "Ollama Markdown polish skipped: "
                f"{reason}. Keeping the original Markdown. Start Ollama, install the model, "
                "choose another local model, or turn off Polish Markdown."
            )
            return md
    sections = _split_sections(md)
    out_parts: list[str] = []

    # Pre-scan: does this doc already have a "(polished)" transcript? If yes,
    # also skip the matching "(raw)" section to avoid double-pass drift.
    has_polished_transcript = any(
        h.strip() == "## Transcript (polished)" for h, _ in sections
    )
    extra_skip: set[str] = set()
    if has_polished_transcript:
        extra_skip.add("## Transcript (raw)")

    # Build chunk list across all polishable text. ProgressBar counts chunks.
    chunk_plan: list[tuple[int, int, str]] = []
    # (section_index, part_index, chunk_text)
    section_parts: list[list[list[str]]] = []
    # section_parts[s_i][p_i] is list of chunks for that part.

    for s_i, (heading, body) in enumerate(sections):
        parts_for_section: list[list[str]] = []
        if heading in SKIP_HEADINGS or heading in extra_skip:
            # Whole section bypassed — single passthrough chunk, no LLM call.
            parts_for_section.append([body])
            section_parts.append(parts_for_section)
            continue
        for chunk_text, is_code in _split_around_code(body):
            if is_code:
                parts_for_section.append([chunk_text])
                continue
            sub_chunks = _chunk_for_polish(chunk_text, POLISH_CHUNK_SIZE)
            parts_for_section.append(sub_chunks)
            for c in sub_chunks:
                if c.strip():
                    chunk_plan.append((s_i, len(parts_for_section) - 1, c))
        section_parts.append(parts_for_section)

    total = len(chunk_plan)
    if total == 0:
        return md  # nothing polishable

    # Polish each chunk; build replacement map.
    polished_by_plan_idx: list[str] = []
    with _progress.ProgressBar("Polish (md)", total=total) as bar:
        for plan_i, (s_i, p_i, c) in enumerate(chunk_plan):
            try:
                polished_by_plan_idx.append(fn(c, model, host))
            except Exception as e:
                remaining = total - plan_i - 1
                skipped = (
                    f" Skipping the remaining {remaining} chunk(s) to avoid repeated timeouts."
                    if remaining
                    else " No further model calls will run."
                )
                _progress.warn(
                    f"polish chunk {plan_i + 1}/{total} failed: {e}. Keeping this chunk and "
                    f"all remaining Markdown unchanged.{skipped} Start Ollama and pull {model}, "
                    "choose another local model, or turn off Polish Markdown."
                )
                bar.update(total - plan_i)
                if plan_i == 0:
                    return md
                polished_by_plan_idx.append(c)
                polished_by_plan_idx.extend(entry[2] for entry in chunk_plan[plan_i + 1:])
                break
            else:
                bar.update()

    # Reassemble.
    plan_iter = iter(zip(chunk_plan, polished_by_plan_idx))
    next_plan = next(plan_iter, None)

    for s_i, (heading, _orig_body) in enumerate(sections):
        if heading:
            out_parts.append(heading + "\n")
        for p_i, sub_chunks in enumerate(section_parts[s_i]):
            for c_i, c in enumerate(sub_chunks):
                if (
                    next_plan is not None
                    and next_plan[0][:2] == (s_i, p_i)
                    and next_plan[0][2] == c
                ):
                    out_parts.append(next_plan[1])
                    next_plan = next(plan_iter, None)
                else:
                    out_parts.append(c)

    return "".join(out_parts)


def polish_file(
    path,
    model: str = TEXT_POLISH_MODEL,
    host: str = "http://localhost:11434",
    *,
    force: bool = False,
    keep_raw: bool = False,
    readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
    _polish_fn=None,
) -> None:
    """Read .md, polish, write atomically. If keep_raw, copy original to
    `<name>.raw.md` first."""
    from pathlib import Path
    from omd._io import write_atomic
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    polished = polish_markdown(
        original,
        model=model,
        host=host,
        force=force,
        readiness_timeout=readiness_timeout,
        _polish_fn=_polish_fn,
    )
    if not polished.strip():
        raise ValueError(f"polish produced empty output for {p}; keeping original")
    if keep_raw:
        raw_path = p.with_name(p.stem + ".raw" + p.suffix)
        write_atomic(raw_path, original)
    write_atomic(p, polished)
