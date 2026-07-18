# ADR: Phase 2 Local Model Defaults

Status: superseded for `0.3.0b2` by the memory-aware instruct-model policy.

The original Phase 2 decision is retained below as historical context. The
beta now avoids the plain `qwen3:4b` thinking alias and derives a conservative
1.5B, 3B, 4B, 7B, or 14B instruct recommendation from total system memory.
Explicit user model settings still take priority, and OMD still never downloads
a model automatically.

## Decision

Keep `qwen3:4b` as the shipped default text model for `--memory-cards`,
`--polish`, and `--polish-md`.

Keep `gemma3:4b` positioned for optional vision/OCR-heavy cleanup and keep
`bge-m3` reserved for future local embedding search.

## Context

The GTM plan asks Phase 2 to add local LLM memory cards while preserving the
no-LLM default. The deep research report recommends evaluating newer local
model candidates such as `qwen3.5:4b`, `qwen3.5:9b`, `gemma3n:e2b`, and
`gemma3n:e4b` for desktop and memory-card workflows.

Those candidates are promising, but switching defaults touches CLI help,
README, privacy docs, UI labels, tests, model install instructions, and user
expectations. Phase 2 should ship a coherent opt-in memory-card workflow before
changing model defaults.

## Consequences

- Core conversion and capture still do not require an LLM.
- `omd capture --memory-cards` is the explicit opt-in path.
- No model is downloaded silently.
- Remote Ollama-compatible hosts remain explicit user configuration.
- A later model refresh must update code, docs, UI, tests, and privacy wording
  in one reviewed change.

## Rejected

- Change the default to `qwen3.5:4b` immediately: rejected because it would
  create a docs/tests/defaults migration before the memory-card feature has
  user feedback.
- Use `gemma3:4b` as the default text memory model: rejected because it is
  better positioned for image/OCR-aware cleanup than ordinary text summaries.
- Auto-download a recommended model: rejected because it violates the no-silent
  download and explicit local model install boundary.
