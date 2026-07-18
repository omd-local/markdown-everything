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
