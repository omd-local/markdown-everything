# Changelog

All notable changes are documented here. Version numbers follow Semantic
Versioning and Python's beta-version convention.

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
- Homebrew packaging now installs and smoke-tests `omd-ui` when the source
  release includes the browser interface.
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
