# Obsidian Vault Capture

OMD can write captures into an Obsidian-compatible local vault without requiring
an Obsidian plugin.

The output is plain Markdown plus sidecar manifests. Obsidian can read the
Markdown files directly, and AI tools can use the same folder as context.

## Basic Workflow

```bash
omd capture "https://youtube.com/..." --vault ~/Obsidian/AI-Memory
omd capture report.pdf --vault ~/Obsidian/AI-Memory --tags research,pdf
omd capture screenshot.png --vault ~/Obsidian/AI-Memory --tags inbox,ocr
omd capture ~/Downloads/sources/ --vault ~/Obsidian/AI-Memory --batch
omd capture sources.txt --vault ~/Obsidian/AI-Memory --batch
```

Capture writes:

```text
AI-Memory/
  Sources/
    YouTube/
    PDFs/
    Images/
    Web/
    Xiaohongshu/
  Index/
    OMD Captures.md
  _attachments/
```

Each note includes light YAML front matter for reading and search. Full trace
metadata, including hashes, capture IDs, route diagnostics, and model errors,
lives in the adjacent `.omd.json` sidecar.

```yaml
---
title: "Example Source"
source_type: "youtube"
captured_at: "2026-07-04T00:00:00Z"
source_url: "https://youtube.com/..."
tags:
  - "youtube"
---
```

## Recommended Pattern

Use one vault or folder as the OMD memory root:

```text
AI-Memory/
  Sources/        # generated capture notes
  Index/          # generated indexes
  _attachments/   # OMD-generated attachments later; not mirrored by default in Phase 1
  Notes/          # your own notes, optional
  Attachments/    # your own attachments, optional
```

Keep generated capture notes separate from hand-written notes. This makes it
easy to search captures, delete them, or regenerate them without mixing them
with your own writing.

`_attachments/` is reserved for future OMD-generated media or asset mirroring.
Phase 1 does not mirror remote images, audio, or video into that folder by
default.

## Inspect Before Capturing

Use `inspect` when a source may need cookies or external tools:

```bash
omd inspect "https://v.douyin.com/abc/" --with-readiness --json
```

This reports the probable backend, needed tools, network/cookie requirements,
and warnings before conversion starts.

## Repeated Captures

OMD does not overwrite an existing capture by default. Repeating the same
capture creates a distinct filename with a numeric suffix, preserving the
previous note.

## Using The Vault With AI Tools

Point Claude Code, Cursor, Codex, or another local AI workflow at the vault
folder. The files are ordinary Markdown, and `Index/OMD Captures.md` gives a
simple chronological entry point.

Future MCP memory tools can build on the same vault structure.

## Optional Memory Cards

Memory cards are optional local LLM output. The local UI enables
**Polish for Obsidian** by default in capture mode; turn it off for faster raw
capture. The CLI remains explicit. Memory cards do not replace the raw capture:

```bash
omd capture "https://podcasts.apple.com/..." \
  --vault ~/Obsidian/AI-Memory \
  --memory-cards \
  --memory-model qwen3:4b-instruct
```

When enabled, OMD appends generated sections such as `## Summary`,
`## Generated Tags`, and `## Memory Cards` before `## Full Content`. The raw
converted Markdown remains available in `## Full Content`.

Generated metadata is also written to front matter:

```yaml
memory_cards: true
memory_model: "qwen3:4b-instruct"
summary_generated: true
generated_tags:
  - "local-ai"
  - "obsidian"
```

If generated output is unusually short or lacks explicit `Evidence:`
references, OMD prints a warning and records the warning in the note metadata.
Review generated cards before treating them as source truth.

If Ollama is unavailable, OMD still writes the raw capture and records the
memory-card error in front matter instead of replacing or deleting the source
content.

OMD does not download local models in the background. Before first use, install
the model you want explicitly:

```bash
ollama pull qwen3:4b-instruct  # 16 GB Chinese / mixed-language example
ollama pull gemma3:4b     # optional image/OCR-heavy enhancement
ollama pull bge-m3        # future multilingual local search
```

## Browser UI

The local Gradio UI can also write into a vault. Choose **Capture to vault note**,
select the vault folder, and start conversion. The vault folder is just a
normal local folder; OMD does not need an Obsidian plugin or Obsidian API.
