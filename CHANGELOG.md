# Changelog

All notable changes are documented here. Version numbers follow Semantic
Versioning and Python's beta-version convention.

## Unreleased

### Added

- Added the proposal-only `omd enrich-note` engine for validated existing-note
  links, concepts, and vault tags without writing to the vault.
- Added `omd capabilities --json` and the OMD-owned `enrich-note` schema v1
  contract for OMD Home and other machine clients.
- Added Obsidian plugin integration, Phase 2 gap, and Chinese UX acceptance
  guides for capability negotiation, proposal review, and hash-checked Apply.

### Changed

- Inbox review now uses explicit create/keep actions, editable note tags, and a
  single user-selected Markdown source for AI input and Obsidian linking.
- Local Inbox drafting can request a larger bounded Ollama context window while
  the shared local-provider default remains unchanged.

### Fixed

- Prevented completed Inbox decisions from being replayed or reversed, and
  cleared unsaved item-specific review state when switching items.
- Preserved Markdown whitespace around long polish chunks and fenced code, and
  report the actual model-call count in long-document warnings.
- Detect Zhihu browser-verification pages without treating anti-bot HTML as a
  successfully captured article.
- Kept long vault paths scrollable and corrected API-key contrast and model
  check button overflow in the local UI.

### Security

- Vault catalog and target reads reject hidden paths, traversal, and symlink
  swaps; model-selected IDs are resolved only through the validated catalog.
- Remote Ollama enrichment remains default-deny and requires an explicit CLI
  flag, HTTPS, and source/task/destination-bound authorization.

### Compatibility

- Schema v1 is owned by OMD. Breaking request, response, or I/O changes require
  schema v2 and capability negotiation; additive warnings and event fields may
  extend v1.

## 0.3.0b2 - 2026-07-18

### Added

- Multi-file local UI queue with clearer per-item outcomes and downloadable
  Markdown output.
- Local-memory-aware Ollama model recommendations.
- Transcript language/quality checks and safe raw-output fallbacks.
- Public adapters and recovery paths for Reddit, X, web articles, and podcasts.
- MCP untrusted-content labels and path/network trust boundaries.

### Changed

- Moved public project links, package metadata, and the custom Homebrew tap to
  the `omd-local` GitHub organisation.
- Homebrew packaging now installs and smoke-tests `omd-ui` plus common
  PDF/Office/Web conversion dependencies, while keeping the large,
  Apple-Silicon-specific `mlx-whisper` stack optional.
- The UI is positioned as a local context inbox while retaining the OMD.EXE
  desktop theme.
- Vault notes keep reader-facing metadata concise; detailed trace data stays in
  adjacent `.omd.json` manifests.
- Remote Ollama-compatible endpoints now require explicit CLI opt-in and HTTPS.
- The hosted demo is limited to public webpages and non-sensitive document
  uploads; cookies, vault writes, media transcription, and local-model calls
  remain disabled.

### Security

- Hosted-demo and MCP URL inputs reject non-public network destinations.
- Public-only generic webpage conversion validates redirect destinations before
  handing a local HTML file to MarkItDown.
- Raised security-sensitive UI dependency floors for Gradio, Pillow,
  python-multipart, and Starlette.
- Added CI tests, wheel smoke tests, dependency auditing, and a disclosure
  policy.
