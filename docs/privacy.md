# Privacy Model

OMD is local-first, not cloud SaaS. The default CLI workflow writes files to
paths you choose and does not require a cloud account or hosted ingestion API.

This document is deliberately specific. Local-first does not mean every source
is offline, and it does not mean every optional model endpoint is local.

## Default Behavior

- Local files are read from disk and converted into local Markdown outputs.
- Sidecar manifests are written next to generated Markdown files.
- `omd capture` writes Markdown into a user-selected vault path.
- Core conversion does not require an LLM.
- Capture front matter uses `privacy: "local_storage"` to describe where OMD
  writes the note. Check `network_fetch` and `model_endpoint` for transport and
  model-routing privacy.

## What Stays Local

These workflows run on local files and local tools:

- PDF, Office, HTML, CSV, JSON, XML, archive, and email conversion through
  MarkItDown.
- Image OCR through Tesseract.
- Audio and video transcription through local Whisper tooling where configured.
- Optional Ollama transcript polish when pointed at a local Ollama daemon.

## When Network Access Is Used

Some inputs are URLs. Those routes must fetch the source over the network:

- Generic web pages.
- Public social posts.
- YouTube, TikTok, Instagram, Bilibili, Douyin, and other video sources.
- Apple Podcasts RSS and episode audio.

Some platforms may require user-provided cookies for sources that are visible
to you in a browser but not accessible anonymously. OMD does not make that
workflow cloud-hosted; it still means the source is fetched from its platform.
Cookie-backed routes such as Douyin and Xiaohongshu/Rednote are advanced
local-only workflows. Hosted sample demos disable cookies and browser-cookie
extraction.

## Optional LLMs

LLM use is optional:

- Documents and web pages are not summarized by default.
- Images use OCR by default, not a vision model.
- Audio and video routes produce raw transcripts by default.
- `--polish` and `--polish-md` opt into local cleanup/polish.
- `omd capture --memory-cards` opts into local summary, tag, and memory-card
  generation.

The intended default model path is local Ollama. The local UI accepts loopback
Ollama only. The CLI requires both `--allow-remote-ollama` and an HTTPS endpoint
before it will send source content to a remote Ollama-compatible host.

The Inbox review surface also supports an explicit **Cloud for this task**
choice for direct OpenAI, Anthropic, or DeepSeek developer APIs. Before any note
text is sent, OMD requires a task-bound preview and consent showing the selected
provider, exact model, destination domain, estimated input size, and current
provider-policy link. A consumer ChatGPT or Claude subscription is not an API
credential. OMD does not use OpenRouter, automatically switch providers, send a
whole vault, or make hosted AI part of capture and deterministic conversion.
Before credential access or a model request, OMD also checks a conservative
input-plus-output context budget. It rejects an oversized task and preserves
the Inbox source instead of silently truncating private text.

UI-entered provider keys are stored through the native macOS Security framework
when Keychain is available. If Keychain is unavailable, the UI uses session-only
entry rather than writing the secret into application state. API keys never
enter a command argument. They must not appear in
Markdown, manifests, Context Receipts, ETA history, exports, or logs. Provider
and model identifiers plus usage/timing may be recorded with the reviewed task;
prompt and response content are not ETA telemetry.

Local ETA history stores only coarse stage, source class, device tier,
runtime/model identity, cold/warm state, work units, duration, outcome,
timestamp, and pipeline version. Path-like model identities are hashed. It does
not store source URLs, filenames, note text, prompts, responses, API keys, or
cookie paths. Historical ranges remain hidden until both the minimum sample
count and an explicit versioned calibration gate pass; the user can disable or
reset this history.
Retry state, a coarse queue-depth bucket, and throughput derived passively from
the required operation may also be stored. OMD does not run a separate network
speed test or retain an IP address for ETA.

After a history bucket is mature, OMD may additionally store bounded
baseline-versus-shadow rows containing only the same safe tokens and numeric
baseline/shadow/actual durations. These rows are local application state, not
vault notes and not shared telemetry. Disabling timing collection stops new
rows; Reset ETA history removes both timing observations and shadow rows.

Recommended local model defaults for the current beta:

- A memory-aware text recommendation for optional transcript polish, Markdown
  cleanup, summaries, and memory cards. A 16 GB machine selects
  `qwen3:4b-instruct`; smaller machines select a 1.5B or 3B instruct model.
- `gemma3:4b` for optional vision-aware cleanup when OCR alone is not enough.
- `bge-m3` for future local multilingual embedding search.

The research roadmap may evaluate newer local defaults such as `qwen3.5:4b`,
`qwen3.5:9b`, or `gemma3n:e2b` / `gemma3n:e4b` for memory-card and desktop-app
flows. Those are upgrade candidates, not the current shipped default.

OMD should not download these models in the background. If a workflow needs a
model that is not installed, the user should see an explicit command such as
`ollama pull qwen3:4b-instruct` and understand the download size before
continuing. The plain `qwen3:4b` alias is not recommended for bounded editing
because it can spend the output budget on reasoning instead of returning the
requested Markdown.

Memory cards are generated sections, not source truth. Capture notes keep raw
converted content in `## Full Content`, and OMD emits a drift warning when the
generated cards are very short or lack explicit `Evidence:` references.

## What Is Not Required

OMD does not require:

- A hosted OMD account.
- Mandatory upload to an OMD cloud service.
- A cloud LLM key for core conversion.
- An Obsidian account.
- An MCP client.

## Agent Safety

For agent-facing workflows, use `--agent-safe` where supported. It requires an
explicit output path, enables JSON events, rejects risky generated-output and
credential-related flags, labels extracted Markdown as untrusted content, and
writes a manifest sidecar.

MCP path access is restricted by default. Configure `OMD_MCP_ALLOWED_ROOTS` for
trusted vault paths instead of granting broad filesystem access.
