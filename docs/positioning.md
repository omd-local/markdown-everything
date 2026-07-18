# OMD Positioning

OMD is a local-first AI context inbox.

It turns source material into Markdown that you keep in your own folders:
web pages, PDFs, screenshots, audio, podcasts, short videos, and supported
social posts. The goal is not to be a better note editor or a hosted document
parser. The goal is to make useful material available as local Markdown context
for Obsidian, Claude Code, Cursor, Codex, Gemini CLI, and RAG pipelines.

## What OMD Is

- A CLI dispatcher that routes each source to the right converter.
- A local capture workflow for writing structured Markdown into a vault.
- An MCP server so AI tools can call conversion tools directly.
- A practical bridge from scattered sources to plain-text AI context.

## What OMD Is Not

- Not a cloud SaaS ingestion platform.
- Not a model host.
- Not an Obsidian plugin yet.
- Not a replacement for MarkItDown, Docling, or OCR/transcription engines.
- Not a promise that every workflow is offline; public URLs still require
  network fetches.

## Why Local-First

Most AI workflows fail because context is scattered. Users collect sources in
browsers, downloads folders, screenshots, podcasts, short videos, and social
feeds, then manually paste fragments into chat tools. OMD makes that material
plain Markdown first, so the user can keep it, search it, diff it, sync it, or
delete it using ordinary file tools.

The local-first line is narrow on purpose:

- Files are written to user-selected local paths.
- Core conversion does not require an LLM.
- Transcript polish and cleanup are optional.
- Local Ollama is supported for optional LLM steps. The current beta recommends
  a conservative instruct model from detected system memory (for example,
  `qwen3:4b-instruct` on a 16 GB machine), with `gemma3:4b` reserved for
  vision/OCR enhancement and `bge-m3` reserved for future local search.
- Newer local model candidates such as `qwen3.5:4b`, `qwen3.5:9b`, and
  `gemma3n:e2b` / `gemma3n:e4b` should be evaluated through a later ADR, not
  silently swapped into Phase 1.
- Remote model hosts are an explicit user choice, not the default workflow.
- The local macOS/localhost Ollama path is the preferred path for real work;
  hosted sample demos are for non-sensitive trials.
- OMD should never silently download models; model installs are explicit user
  actions.

## Current State

Today OMD has three main surfaces:

- `omd <input> -o out.md` for direct conversion.
- `omd capture <input> --vault <path>` for local vault capture.
- `omd-mcp` for MCP-compatible AI clients.

The capture/vault workflow is the first product layer above the converter. It
writes source metadata into YAML front matter, stores captures under
`Sources/<source type>/`, writes sidecar manifests, and updates an index note.

## Where It Fits

Use OMD when you want source material to become durable local context:

- Obsidian or Logseq users building a Markdown vault.
- Local AI users who do not want to upload personal documents by default.
- Developers using Claude Code, Cursor, Codex, or Gemini CLI.
- Researchers, product managers, analysts, and creators collecting source
  material from many formats.

The product direction is:

```text
Sources -> Traceable Markdown -> Local AI Context
```

The important object is not a file conversion. It is a capture: one source,
normalized into a local Markdown record with metadata that future tools can
read.
