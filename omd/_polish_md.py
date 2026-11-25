"""Generic Markdown post-processor via Ollama.

Splits a `.md` file into sections delimited by `## ` (and `### `) headings,
sends each section through a local Ollama chat model with a "fix parsing artifacts,
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
import unicodedata
import urllib.error
import urllib.request
from omd._models import (
    LOCAL_TEXT_CONTEXT_TOKENS,
    TEXT_POLISH_MODEL,
    bounded_edit_output_budget,
    estimated_text_tokens,
    local_text_model_issue,
)
from omd._network_policy import validate_ollama_host
from omd.ollama_runtime import ollama_keep_alive, request_ollama_json

POLISH_CHUNK_SIZE = 1500
WARN_THRESHOLD_CHARS = 20_000
HARD_REFUSE_CHARS = 100_000
DENSE_PROSE_MAX_LINE_CHARS = 240
DENSE_PROSE_MIN_TOKENS = 24
DENSE_PROSE_MIN_SEGMENT_CHARS = 80
READINESS_TIMEOUT_SECONDS = 2.0
POLISH_CHUNK_TIMEOUT_SECONDS = float(os.environ.get("OMD_POLISH_MD_TIMEOUT", "45"))

_SECTION_RE = re.compile(r"^(#{2,3}\s+.+)$", re.MULTILINE)

# Headings to skip (already polished by the per-backend polish path).
SKIP_HEADINGS = (
    "## Transcript (polished)",
    "### Timestamped",  # raw segment timestamps; not prose
)


class PolishTooLargeError(ValueError):
    """Raised when strict size policy refuses Markdown before model work."""


class PolishPunctuationChangeError(RuntimeError):
    """Raised when a candidate changes ordinary punctuation but not wording."""


SYSTEM_PROMPT = (
    "You are a Markdown post-processor. Fix OCR/parsing artifacts: stray "
    "hyphens at line breaks, broken whitespace, doubled spaces, and "
    "line-break debris. Restore paragraph breaks, unordered-list "
    "layout, and heading markers only when the source wording, labels, "
    "enumeration, or surrounding Markdown clearly indicates that structure. "
    f"A prose line longer than {DENSE_PROSE_MAX_LINE_CHARS} characters is crowded: split it into readable "
    "paragraphs by replacing existing spaces with blank lines, even when its "
    "punctuation is weak. "
    "For those repairs, you may change whitespace and Markdown heading/list "
    "markers only. Do not invent headings, list items, labels, or facts; do not "
    "add, remove, reorder, or rewrite any words. Do NOT summarize, translate, "
    "or change meaning. Preserve the source language: Chinese input stays "
    "Chinese, English input stays English, and mixed-language input keeps the "
    "same language balance. Preserve every punctuation mark except a hyphen "
    "used only to wrap one word across a line break. Preserve every number, "
    "name, code identifier, URL, "
    "and comparison, equality, logical, assignment, or arithmetic operator "
    "exactly (for example <, >=, !=, ==, &&, =, +, and ->). "
    "Preserve Markdown structure (headings, lists, links, emphasis). For web article "
    "Markdown, preserve title, author, publication time, original link, heading "
    "hierarchy, quotes, code blocks, footnotes, links, and original image URLs; "
    "retain all source text, including obvious navigation, ads, recommendations, cookie banners, "
    "unrelated comments, and page chrome. For "
    "Reddit Markdown, preserve subreddit, post author, comment authors, time, "
    "comment nesting, permalinks, and deleted/edited markers. For podcast "
    "Markdown, preserve show name, episode title, guests/hosts when present, "
    "publication time, original episode link, speakers, timestamps, and "
    "transcript source. Output the "
    "corrected Markdown only - no preamble, no explanation, no code fences "
    "around the output."
)

TRANSCRIPT_CLEANUP_SYSTEM_PROMPT = (
    "You clean an automatic speech-recognition transcript. Add punctuation, sentence "
    "boundaries, paragraph breaks, and whitespace so the transcript is readable. You may "
    "only add, remove, or replace punctuation and whitespace. Preserve every letter, Han "
    "character, word, number, name, URL, timestamp, and code token in exactly the same "
    "order. Never correct, paraphrase, summarize, translate, or add facts. Preserve the "
    "source language and script. Output the cleaned transcript only, with no preamble or "
    "surrounding code fence."
)


def _fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Locate backtick/tilde fences, including an unclosed final block."""
    spans: list[tuple[int, int]] = []
    opening = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
    closing = None
    start = 0
    position = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if closing is not None:
            if closing.fullmatch(stripped):
                spans.append((start, position + len(line)))
                closing = None
        elif match := opening.fullmatch(stripped):
            fence, info = match.groups()
            if fence[0] != "`" or "`" not in info:
                start = position
                closing = re.compile(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*")
        position += len(line)
    if closing is not None:
        spans.append((start, len(text)))
    return spans


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Split Markdown into (heading, body) pairs.

    The first chunk before any `## ` heading is returned with heading = ""
    (typically the H1 title + intro). Each subsequent chunk includes its
    heading line as part of `heading`, body is the content under it.
    """
    parts: list[tuple[str, str]] = []
    code_spans = _fenced_code_spans(md)
    matches = [
        match for match in _SECTION_RE.finditer(md)
        if not any(start <= match.start() < end for start, end in code_spans)
    ]
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
    for start, end in _fenced_code_spans(text):
        if start > pos:
            out.append((text[pos:start], False))
        out.append((text[start:end], True))
        pos = end
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [(text, False)]


def _chunk_for_polish(text: str, max_chars: int) -> list[str]:
    """Split text without discarding Markdown-significant whitespace.

    Prefer paragraph boundaries, then fall back to a hard character boundary.
    Joining the returned chunks must always reproduce ``text`` exactly so the
    reassembly pass can restore whitespace stripped by a local model.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while len(text) - start > max_chars:
        limit = start + max_chars
        boundary = text.rfind("\n\n", start, limit)
        if boundary > start:
            end = boundary + 2
        else:
            end = limit
        chunks.append(text[start:end])
        start = end
    if start < len(text):
        chunks.append(text[start:])
    return chunks


def _preserve_edge_whitespace(source: str, replacement: str) -> str:
    """Keep Markdown block boundaries even when a model strips whitespace."""
    leading = source[: len(source) - len(source.lstrip())]
    trailing = source[len(source.rstrip()):]
    return leading + replacement.strip() + trailing


def _script_family(character: str) -> str:
    """Return a coarse script family for choosing readable paragraph breaks."""
    if not character.isalnum():
        return ""
    name = unicodedata.name(character, "")
    for family in ("LATIN", "ARABIC", "HEBREW", "CYRILLIC", "GREEK", "HANGUL"):
        if family in name:
            return family
    if "CJK" in name or "IDEOGRAPH" in name or "HIRAGANA" in name or "KATAKANA" in name:
        return "CJK"
    return "OTHER"


def _ordered_letter_number_content(text: str) -> str:
    """Return source characters whose identity transcript cleanup must retain."""
    return "".join(
        character for character in unicodedata.normalize("NFC", text)
        if unicodedata.category(character)[0] in "LMN"
    )


def _numeric_punctuation_anchors(text: str) -> list[tuple[str, int]]:
    """Anchor punctuation found between two numeric characters.

    The anchor is the count of preceding letters, marks, and numbers. This
    keeps decimal points, date/time separators, and grouping marks fixed while
    still allowing trusted transcript segment boundaries to add sentence
    punctuation between otherwise adjacent numeric runs.
    """
    anchors: list[tuple[str, int]] = []
    ordered_position = 0
    for index, character in enumerate(text):
        if unicodedata.category(character)[0] in "LMN":
            ordered_position += 1
            continue
        if character.isspace():
            continue

        left = index - 1
        while left >= 0 and unicodedata.category(text[left])[0] not in "LMN":
            left -= 1
        right = index + 1
        while right < len(text) and unicodedata.category(text[right])[0] not in "LMN":
            right += 1
        if (
            left >= 0
            and right < len(text)
            and text[left].isnumeric()
            and text[right].isnumeric()
        ):
            anchors.append((character, ordered_position))
    return anchors


def _timestamped_transcript_segments(body: str) -> list[str]:
    entries: list[str] = []
    pattern = re.compile(r"^\s*-\s+\[[^\]\n]+\]\s+(.*?)\s*$")
    for line in body.splitlines():
        if match := pattern.match(line):
            if segment := match.group(1).strip():
                entries.append(segment)
    return entries


def _question_terminal(text: str) -> bool:
    if text.endswith(("像什么", "比如什么", "例如什么", "包括什么")):
        return False
    return bool(re.search(
        r"(?:什么|怎么|为何|为什么|对吧|是不是|可不可以|行不行|啥呢|什么意思呢|"
        r"(?:方法|原因|结果|问题|情况)呢|(?:是什么|怎么做|怎么办|为啥)呢|"
        r"(?:是想.*还是.*)|谁|多少|哪里|哪儿)$",
        text,
    ))


def _transcript_boundary_continues(current: str, next_segment: str) -> bool:
    trailing_connectors = (
        "的", "地", "得", "这", "这个", "那个", "一种", "一个", "一套", "一份", "某个",
        "在", "把", "被", "给", "向", "从", "对", "跟", "和", "或", "与", "以及",
        "因为", "所以", "如果", "虽然", "但是", "而且", "然后", "就是", "像", "比如",
        "相当于", "一旦", "不仅", "不管", "只要", "也就是", "包括", "呢", "啊",
    )
    leading_connectors = (
        "还是", "或者", "以及", "而且", "但是", "所以", "然后", "就是", "也就是", "并且",
        "同时", "其中", "由于", "因为", "如果", "虽然", "甚至", "比如", "像", "而", "但",
        "却", "还", "再", "才", "就", "又", "也", "都", "一会", "它", "这个", "这种", "的", "地", "得",
    )
    recent = current[-36:]
    continuing_list = any(marker in recent for marker in ("像什么", "比如", "例如", "包括")) and len(next_segment) <= 14
    return (
        current.endswith(trailing_connectors)
        or next_segment.startswith(leading_connectors)
        or continuing_list
    )


def _cleanup_cjk_transcript_from_timestamps(
    transcript_body: str,
    timestamped_body: str,
) -> str | None:
    """Build readable CJK prose from trusted ASR segment boundaries.

    Captures already contain the same transcript twice: one dense full-text
    block and one timestamped list. When their ordered letters and numbers
    match, the timestamp boundaries are safer than asking a small model to
    decide where to split a multi-thousand-character line.
    """
    source = transcript_body.strip()
    segments = _timestamped_transcript_segments(timestamped_body)
    if not source or len(segments) < 2:
        return None
    joined = "".join(segments)
    source_content = _ordered_letter_number_content(source)
    if source_content != _ordered_letter_number_content(joined):
        return None
    script_families = [_script_family(character) for character in source_content]
    script_families = [family for family in script_families if family]
    if not script_families or script_families.count("CJK") / len(script_families) < 0.35:
        return None

    terminal = "。！？!?；;…"
    separator_punctuation = terminal + "，、：:,"
    sentences: list[str] = []
    current = ""
    current_length = 0
    for index, raw_segment in enumerate(segments):
        segment = raw_segment.strip()
        if not segment:
            continue
        if current and current[-1] not in separator_punctuation:
            current += "，"
        current += segment
        current_length += len(_ordered_letter_number_content(segment))
        next_segment = segments[index + 1].strip() if index + 1 < len(segments) else ""
        explicit_end = segment.rstrip().endswith(tuple(terminal))
        question_end = _question_terminal(current)
        readable_boundary = (
            current_length >= 68
            and not _transcript_boundary_continues(current, next_segment)
        )
        if explicit_end or question_end or readable_boundary or current_length >= 130:
            if current[-1] not in terminal:
                current += "？" if question_end else "。"
            sentences.append(current)
            current = ""
            current_length = 0
    if current:
        if current[-1] not in terminal:
            current += "？" if _question_terminal(current) else "。"
        sentences.append(current)

    paragraphs: list[str] = []
    paragraph: list[str] = []
    paragraph_length = 0
    for sentence in sentences:
        paragraph.append(sentence)
        paragraph_length += len(_ordered_letter_number_content(sentence))
        if paragraph_length >= 160 or (len(paragraph) >= 2 and paragraph_length >= 90):
            paragraphs.append("".join(paragraph))
            paragraph = []
            paragraph_length = 0
    if paragraph:
        paragraphs.append("".join(paragraph))

    candidate = _preserve_edge_whitespace(transcript_body, "\n\n".join(paragraphs))
    numeric_boundary_anchors: set[int] = set()
    ordered_position = 0
    for segment in segments[:-1]:
        ordered_position += len(_ordered_letter_number_content(segment))
        numeric_boundary_anchors.add(ordered_position)
    try:
        _assert_content_preserved(
            transcript_body,
            candidate,
            allow_punctuation_changes=True,
            allowed_numeric_separator_anchors=numeric_boundary_anchors,
        )
    except RuntimeError:
        return None
    return candidate


def _nearest_script(text: str, position: int, *, left: bool) -> str:
    step = -1 if left else 1
    index = position - 1 if left else position
    while 0 <= index < len(text):
        if family := _script_family(text[index]):
            return family
        index += step
    return ""


def _is_dense_prose_line(line: str) -> bool:
    """Identify prose that needs real paragraph structure, not visual wrapping."""
    stripped = line.strip()
    if len(stripped) <= DENSE_PROSE_MAX_LINE_CHARS:
        return False
    if len(re.findall(r"\S+", stripped)) < DENSE_PROSE_MIN_TOKENS:
        return False
    if re.match(r"^(?:#{1,6}|[-+*>]|\d+[.)])\s", stripped):
        return False
    if stripped.startswith(("|", "```", "~~~", "<")):
        return False
    if re.match(r"^\[[^\]\n]+\]:\s*\S+", stripped):
        return False
    return True


def _dense_break_match(line: str, start: int) -> re.Match[str] | None:
    """Choose a balanced whitespace boundary, preferring semantic-looking edges."""
    remaining = len(line) - start
    pieces = max(2, (remaining + DENSE_PROSE_MAX_LINE_CHARS - 1) // DENSE_PROSE_MAX_LINE_CHARS)
    ideal = start + round(remaining / pieces)
    lower = start + DENSE_PROSE_MIN_SEGMENT_CHARS
    upper = min(
        start + DENSE_PROSE_MAX_LINE_CHARS,
        len(line) - DENSE_PROSE_MIN_SEGMENT_CHARS,
    )
    candidates = [
        match
        for match in re.finditer(r"[ \t]+", line)
        if lower <= match.start() <= upper
    ]
    if not candidates:
        candidates = [
            match
            for match in re.finditer(r"[ \t]+", line)
            if start < match.start() <= start + DENSE_PROSE_MAX_LINE_CHARS
        ]
    if not candidates:
        return None

    sentence_end = frozenset(".!?。！？؟")
    clause_end = frozenset(",;:，；：、")

    def score(match) -> tuple[int, int]:
        boundary = match.start()
        value = abs(boundary - ideal)
        previous = line[boundary - 1] if boundary else ""
        if previous in sentence_end:
            value -= 90
        elif previous in clause_end:
            value -= 45
        left_script = _nearest_script(line, match.start(), left=True)
        right_script = _nearest_script(line, match.end(), left=False)
        if left_script and right_script and left_script != right_script:
            value -= 60
        return value, boundary

    return min(candidates, key=score)


def _split_dense_prose_line(line: str) -> str:
    if not _is_dense_prose_line(line):
        return line
    segments: list[str] = []
    start = 0
    while len(line) - start > DENSE_PROSE_MAX_LINE_CHARS:
        match = _dense_break_match(line, start)
        if match is None:
            return line
        segments.append(line[start:match.start()])
        start = match.end()
    segments.append(line[start:])
    return "\n\n".join(segments)


def _paragraphize_dense_prose(text: str) -> str:
    """Add lossless paragraph breaks when a local model leaves dense prose flat."""
    lines = text.splitlines(keepends=True)
    in_frontmatter = False
    output: list[str] = []
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        if index == 0 and content == "---":
            in_frontmatter = True
            output.append(line)
            continue
        if in_frontmatter:
            output.append(line)
            if content == "---":
                in_frontmatter = False
            continue
        output.append(_split_dense_prose_line(content) + ending)
    return "".join(output)


def _block_structure(text: str) -> tuple[int, int]:
    """Count paragraph separators and explicit Markdown block markers."""
    paragraphs = len(re.findall(r"\n[ \t]*\n", text))
    markers = len(
        re.findall(
            r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|[-+*][ \t]+|\d+[.)][ \t]+)",
            text,
        )
    )
    return paragraphs, markers


def _has_dense_prose(text: str) -> bool:
    return any(_is_dense_prose_line(line) for line in text.splitlines())


def _layout_content(text: str) -> str:
    """Normalize only the layout edits allowed by the formatting contract."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = re.sub(
        r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|[-+*][ \t]+)",
        "",
        normalized,
    )
    normalized = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "", normalized)
    return "".join(character for character in normalized if not character.isspace())


def _link_destinations(text: str) -> list[str]:
    """Keep inline-link destinations whole, including nested/escaped parens."""
    destinations: list[str] = []
    for match in re.finditer(r"\]\(", text):
        start = position = match.end()
        depth = 1
        while position < len(text) and depth:
            character = text[position]
            if character == "\\":
                position += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            position += 1
        destinations.append(text[start:position - 1] if depth == 0 else text[start:])
    return destinations


_SEMANTIC_OPERATOR_RE = re.compile(
    r"!==|===|<<=|>>=|\*\*=|//="
    r"|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|%=|\^=|&=|\|="
    r"|<<|>>|=>|->|<-|:=|\*\*|//|<>|::"
    r"|[<>=+\-*/%^&|!~]"
    r"|[≤≥≠≈±÷×∧∨¬∈∉⊂⊆⊃⊇∑∏√∞→←↔⇒⇔]"
)
_NUMERIC_CONTENT_RE = re.compile(
    r"[+\-−]?\d+(?:[.,，．:：/／\-–—−٫٬']+\d+)*"
)
_CONTEXTUAL_OPERATOR_TOKENS = frozenset("+-*/%^&|!~")
_UNARY_OPERATOR_TOKENS = frozenset("+-*!~")


def _is_operand_character(character: str, *, left: bool) -> bool:
    """Return whether a same-line character can border an operator."""
    if character == "_" or unicodedata.category(character)[0] in "LMN":
        return True
    return character in (")]}'\"" if left else "([{\'\"")


def _operator_operand(line: str, position: int, *, left: bool) -> str:
    """Return the nearest word/number operand on one side of an operator."""
    side = line[:position] if left else line[position:]
    words = list(re.finditer(r"\w+", side, re.UNICODE))
    if not words:
        return ""
    return words[-1].group(0) if left else words[0].group(0)


def _semantic_operator_signatures(text: str) -> list[tuple[str, str, str]]:
    """Describe meaning-bearing operators without treating all punctuation as fixed.

    Explicit comparison/equality/assignment tokens are always protected. Single
    arithmetic and logical symbols are protected when they are used as infix or
    unary operators on one line. This deliberately leaves a hyphen before a line
    break unprotected so the polish pass can still join OCR/parser word wraps.
    """
    signatures: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        for match in _SEMANTIC_OPERATOR_RE.finditer(line):
            operator = match.group(0)
            is_markdown_list_marker = (
                operator in {"-", "+", "*"}
                and not line[:match.start()].strip()
                and match.end() < len(line)
                and line[match.end()] in " \t"
            )
            if is_markdown_list_marker:
                # Formatting repair may restore an unordered-list marker. A
                # signed value such as ``-3`` has no following space and is
                # still protected as a semantic operator below.
                continue
            left_index = match.start() - 1
            while left_index >= 0 and line[left_index] in " \t":
                left_index -= 1
            right_index = match.end()
            while right_index < len(line) and line[right_index] in " \t":
                right_index += 1

            has_left_operand = (
                left_index >= 0
                and _is_operand_character(line[left_index], left=True)
            )
            has_right_operand = (
                right_index < len(line)
                and _is_operand_character(line[right_index], left=False)
            )
            if operator in _CONTEXTUAL_OPERATOR_TOKENS:
                is_infix = has_left_operand and has_right_operand
                is_unary = (
                    operator in _UNARY_OPERATOR_TOKENS
                    and has_right_operand
                    and not has_left_operand
                )
                if not (is_infix or is_unary):
                    continue

            signatures.append(
                (
                    operator,
                    _operator_operand(line, match.start(), left=True),
                    _operator_operand(line, match.end(), left=False),
                )
            )
    return signatures


def _semantic_operator_anchors(text: str) -> list[tuple[str, int]]:
    """Pin operator-like tokens to their position in ordered source content.

    Transcript cleanup may insert punctuation beside percentages or expressions,
    which makes same-line operand heuristics unstable. Anchoring every operator
    token to the number of preceding letters/numbers keeps the token and its
    semantic position fixed while allowing punctuation and paragraph breaks.
    """
    return [
        (match.group(0), len(_ordered_letter_number_content(text[:match.start()])))
        for match in _SEMANTIC_OPERATOR_RE.finditer(text)
    ]


def _restore_invented_numeric_signs(source: str, output: str) -> str:
    """Remove only a model-invented leading sign from an unchanged number.

    Small edit models occasionally turn source text such as ``2 years`` into
    ``-2 years`` while otherwise making a valid whitespace-only repair. The
    source remains authoritative: restore that exact numeric token, then let
    the complete fidelity validator below re-check words, numbers, links,
    code, punctuation, and operators. Missing source signs and every other
    numeric mutation remain hard failures.
    """
    if _ordered_letter_number_content(source) != _ordered_letter_number_content(output):
        return output
    source_matches = list(_NUMERIC_CONTENT_RE.finditer(source))
    output_matches = list(_NUMERIC_CONTENT_RE.finditer(output))
    if len(source_matches) != len(output_matches):
        return output

    replacements: dict[int, str] = {}
    for index, (source_match, output_match) in enumerate(zip(source_matches, output_matches)):
        source_token = source_match.group(0)
        output_token = output_match.group(0)
        if source_token == output_token:
            continue
        source_prefix = source[:source_match.start()].rstrip()
        source_has_separate_sign = bool(source_prefix) and source_prefix[-1] in {"+", "-", "−"}
        if (
            source_token[:1] not in {"+", "-", "−"}
            and not source_has_separate_sign
            and output_token[:1] in {"+", "-", "−"}
            and output_token[1:] == source_token
            and len(_ordered_letter_number_content(source[:source_match.start()]))
            == len(_ordered_letter_number_content(output[:output_match.start()]))
        ):
            replacements[index] = source_token

    if not replacements:
        return output
    parts: list[str] = []
    position = 0
    for index, match in enumerate(output_matches):
        parts.append(output[position:match.start()])
        parts.append(replacements.get(index, match.group(0)))
        position = match.end()
    parts.append(output[position:])
    return "".join(parts)


def _assert_content_preserved(
    source: str,
    output: str,
    *,
    allow_punctuation_changes: bool = False,
    allowed_numeric_separator_anchors: set[int] | None = None,
) -> None:
    """Conservatively allow formatting repairs, not prose rewrites.

    This is a content-preservation check, not a semantic verifier. Comparing
    ordered Unicode content retains all scripts while allowing whitespace,
    punctuation cleanup and joining words hyphenated across line breaks.
    Numbers and common Markdown destinations/code need exact punctuation too.
    """
    if _ordered_letter_number_content(source) != _ordered_letter_number_content(output):
        raise RuntimeError("model changed source content; refusing a rewrite")

    source_numeric = _NUMERIC_CONTENT_RE.findall(source)
    output_numeric = _NUMERIC_CONTENT_RE.findall(output)
    if source_numeric != output_numeric:
        allowed_anchors = allowed_numeric_separator_anchors or set()
        source_punctuation = _numeric_punctuation_anchors(source)
        remaining_output_punctuation = _numeric_punctuation_anchors(output)
        for protected in source_punctuation:
            try:
                remaining_output_punctuation.remove(protected)
            except ValueError as exc:
                raise RuntimeError(
                    "model changed numeric content; refusing a rewrite"
                ) from exc
        allowed_added_punctuation = frozenset("，,。！？!?；;、：:")
        if not allowed_anchors or any(
            anchor not in allowed_anchors or punctuation not in allowed_added_punctuation
            for punctuation, anchor in remaining_output_punctuation
        ):
            raise RuntimeError("model changed numeric content; refusing a rewrite")

    if _link_destinations(source) != _link_destinations(output):
        raise RuntimeError("model changed protected link destinations; refusing a rewrite")

    protected = (
        r"(`+)(.*?)\1",
        r"^\s{0,3}\[[^\]\n]+\]:[ \t]*(\S+)",
        r"https?://[^\s<>\"'`]+",
        r"(?<!\\)(\${1,2})(.+?)\1",
        r"\\\(.*?\\\)",
        r"\\\[.*?\\\]",
    )
    for pattern in protected:
        if re.findall(pattern, source, re.MULTILINE | re.DOTALL) != re.findall(
            pattern, output, re.MULTILINE | re.DOTALL
        ):
            raise RuntimeError("model changed protected links, code or math; refusing a rewrite")

    operators_preserved = (
        _semantic_operator_anchors(source) == _semantic_operator_anchors(output)
        if allow_punctuation_changes
        else _semantic_operator_signatures(source) == _semantic_operator_signatures(output)
    )
    if not operators_preserved:
        raise RuntimeError("model changed semantic operators; refusing a rewrite")

    if not allow_punctuation_changes and _layout_content(source) != _layout_content(output):
        raise PolishPunctuationChangeError(
            "model changed punctuation or source text; refusing a rewrite"
        )


def _polish_chunk(
    text: str,
    model: str,
    host: str,
    *,
    transcript_cleanup: bool = False,
) -> str:
    """Send one chunk to Ollama, return the model's polished output."""
    n_chars = len(text)
    output_budget = bounded_edit_output_budget(text)
    system_prompt = TRANSCRIPT_CLEANUP_SYSTEM_PROMPT if transcript_cleanup else SYSTEM_PROMPT
    prompt = (
        f"{system_prompt}\n\n"
        f"Input ({n_chars} chars):\n{text}\n\nOutput:\n"
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": ollama_keep_alive(),
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
    payload = request_ollama_json(req, timeout=POLISH_CHUNK_TIMEOUT_SECONDS)
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
    out = _restore_invented_numeric_signs(text, out)
    lossless_fallback = _paragraphize_dense_prose(text.strip())
    try:
        _assert_content_preserved(
            text,
            out,
            allow_punctuation_changes=transcript_cleanup,
        )
    except PolishPunctuationChangeError:
        # Punctuation-only drift is common with small edit models. Discard the
        # candidate instead of failing the whole document: the source-derived
        # fallback is lossless and can still repair an objectively dense line.
        return lossless_fallback
    except RuntimeError:
        # A dense source line has a useful, deterministic formatting path even
        # when the model attempts a rewrite. Never use the unsafe candidate;
        # derive paragraph-only formatting from the original chunk instead.
        if lossless_fallback != text.strip():
            return lossless_fallback
        if transcript_cleanup:
            return text.strip()
        raise

    # Small local models sometimes return a dense paragraph unchanged even
    # after being asked for layout improvements. In that narrow case, add
    # balanced paragraph breaks locally. The fallback is constructed from the
    # already-validated candidate, changes whitespace only, and never touches
    # frontmatter, code (split before this function), URLs, or wording.
    if _has_dense_prose(text):
        source_structure = _block_structure(text)
        output_structure = _block_structure(out)
        structure_improved = any(
            candidate > baseline
            for baseline, candidate in zip(source_structure, output_structure)
        )
        if not structure_improved:
            return lossless_fallback
    return out


def _ollama_ready(model: str, host: str, *, timeout: float = READINESS_TIMEOUT_SECONDS) -> tuple[bool, str]:
    if issue := local_text_model_issue(model):
        return False, issue
    req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
    try:
        payload = request_ollama_json(req, timeout=timeout)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError) as exc:
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
    allow_remote: bool = False,
    readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
    transcript_cleanup: bool = False,
    _polish_fn=None,
) -> str:
    """Polish a Markdown string.

    `_polish_fn` is the per-chunk LLM call. Default is `_polish_chunk`
    above. Tests inject a mock. Same chunking + skip rules apply
    regardless of the underlying call.
    """
    from omd import _progress
    validate_ollama_host(host, allow_remote=allow_remote)
    n = len(md)
    if not force and n > HARD_REFUSE_CHARS:
        raise PolishTooLargeError(
            f"file is {n} chars (>{HARD_REFUSE_CHARS}); pass --force to override"
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
    timestamped_body = next(
        (body for heading, body in sections if heading.strip() == "### Timestamped"),
        "",
    )
    transcript_only = transcript_cleanup and any(
        heading.strip() == "## Transcript" for heading, _body in sections
    )

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
        if transcript_only and heading.strip() != "## Transcript":
            # Clean transcript is a deliberately narrow operation. Captured
            # titles, summaries, metadata, and source context are not part of
            # the request and must never be sent to the writing model.
            parts_for_section.append([body])
            section_parts.append(parts_for_section)
            continue
        if transcript_cleanup and heading.strip() == "## Transcript":
            timestamp_cleanup = _cleanup_cjk_transcript_from_timestamps(body, timestamped_body)
            if timestamp_cleanup is not None:
                parts_for_section.append([timestamp_cleanup])
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
        # A timestamp-derived transcript cleanup needs no model calls, but it
        # still changed one section. Reassemble the planned passthrough parts
        # instead of discarding that deterministic result as a no-op.
        out_parts: list[str] = []
        for s_i, (heading, _orig_body) in enumerate(sections):
            if heading:
                out_parts.append(heading)
            for sub_chunks in section_parts[s_i]:
                out_parts.extend(sub_chunks)
        return "".join(out_parts)
    if n > WARN_THRESHOLD_CHARS:
        _progress.warn(
            f"polishing {n} chars — this will take a while via Ollama "
            f"({total} chunks)"
        )
    chunk_units = [estimated_text_tokens(entry[2]) for entry in chunk_plan]
    total_units = sum(chunk_units)

    # Polish each chunk; build replacement map.
    polished_by_plan_idx: list[str] = []
    with _progress.ProgressBar(
        "Polish (md)",
        total=total_units,
        stage_id="polish",
        unit="tokens",
    ) as bar:
        for plan_i, (s_i, p_i, c) in enumerate(chunk_plan):
            try:
                if _polish_fn is None:
                    heading = sections[s_i][0].strip()
                    polished_by_plan_idx.append(fn(
                        c,
                        model,
                        host,
                        transcript_cleanup=(transcript_cleanup and heading == "## Transcript"),
                    ))
                else:
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
                bar.update(sum(chunk_units[plan_i:]))
                if plan_i == 0:
                    return md
                polished_by_plan_idx.append(c)
                polished_by_plan_idx.extend(entry[2] for entry in chunk_plan[plan_i + 1:])
                break
            else:
                bar.update(chunk_units[plan_i])

    # Reassemble.
    plan_iter = iter(zip(chunk_plan, polished_by_plan_idx))
    next_plan = next(plan_iter, None)

    for s_i, (heading, _orig_body) in enumerate(sections):
        if heading:
            out_parts.append(heading)
        for p_i, sub_chunks in enumerate(section_parts[s_i]):
            for c_i, c in enumerate(sub_chunks):
                if (
                    next_plan is not None
                    and next_plan[0][:2] == (s_i, p_i)
                    and next_plan[0][2] == c
                ):
                    out_parts.append(_preserve_edge_whitespace(c, next_plan[1]))
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
    allow_remote: bool = False,
    readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
    transcript_cleanup: bool = False,
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
        allow_remote=allow_remote,
        readiness_timeout=readiness_timeout,
        transcript_cleanup=transcript_cleanup,
        _polish_fn=_polish_fn,
    )
    if not polished.strip():
        raise ValueError(f"polish produced empty output for {p}; keeping original")
    if keep_raw:
        raw_path = p.with_name(p.stem + ".raw.md")
        write_atomic(raw_path, original)
    write_atomic(p, polished)
