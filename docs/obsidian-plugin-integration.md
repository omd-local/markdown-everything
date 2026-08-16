# Integrating OMD with an Obsidian plugin

OMD exposes a versioned, proposal-only boundary for desktop Obsidian plugins.
OMD owns model discovery, local generation, prompt isolation, and proposal
validation. The plugin owns its interface, Obsidian metadata catalog, user
confirmation, concurrency checks, and every vault write.

This boundary deliberately keeps the two applications independently releasable:
an Obsidian plugin must not import OMD's Python modules or call Ollama directly.

For the audited OMD Home consumer status and the ordered remaining work, use the
[Phase 2 gap plan](obsidian-plugin-phase2-gap-plan.md).

## Runtime gate

Resolve the user-configured `omd` executable, then run this before enabling note
enrichment:

```bash
omd capabilities --json
```

The plugin may enable the v1 flow only when the parsed response contains:

```json
{"enrich_note":{"schema_versions":[1],"supported":true}}
```

Use a short timeout and bounded stdout/stderr. Treat a missing executable,
timeout, non-zero exit, malformed JSON, `supported: false`, or a missing schema
version as an unavailable capability. Show a repair action and keep all existing
vault features usable. Capability discovery does not require Ollama or a vault.

Cache the result for the current plugin session only and provide an explicit
retry after the executable or package is changed.

## Managed request

Build the v1 envelope documented in
[`enrich-note` contract v1](enrich-note-contract-v1.md). Start OMD without a
shell, keep arguments fixed, and send the entire JSON request through stdin:

```text
executable: /absolute/path/to/omd
argv:       enrich-note --request-json - --json-events
stdin:      <one v1 request JSON object>
```

The source note must never appear in argv, logs, settings, or an exception
message. Recommended process controls are:

- `shell: false` and an explicit executable path;
- a user-visible cancel action followed by bounded graceful/forced termination;
- a request timeout appropriate to the selected local model;
- bounded stdout and stderr buffers;
- stdout reserved for the single final proposal JSON;
- stderr parsed line by line as JSON events, with unknown additive events safely
  ignored;
- no partial proposal displayed when the process exits non-zero, is cancelled,
  times out, or exceeds an output bound.

The plugin should create the candidate catalog from Obsidian's metadata cache.
Exclude the target note, `.obsidian`, hidden/system paths, symlinks, sidecars,
and non-Markdown files. Keep candidate IDs opaque and request-local. OMD validates
the catalog again, but that does not replace the plugin's own input bounds.

## Do not trust the response

Parse stdout only after exit `0`, then validate it as untrusted runtime data.
At minimum verify:

- `schema_version`, `request_id`, and `action` match the request;
- returned note path and `content_sha256` match the previewed note;
- every required object, field, scalar type, and array bound is valid;
- every existing-link candidate ID/path pair maps to the catalog supplied for
  this request;
- tags and evidence obey the documented bounds;
- unknown additive v1 fields are ignored, while missing fields or changed types
  fail closed.

Keep a copy of OMD's canonical fixtures under
`tests/fixtures/enrich_note/v1/` in the plugin's contract tests. Fixture parity
detects accidental drift before either package is deployed.

## Review and apply ownership

The proposal is a preview, not a vault edit. Present summary, verified existing
links, new concepts, existing tags, and new tags as separate groups. A safe v1
review screen follows these defaults:

- existing links and tags may be recommended but remain individually selectable;
- new concepts are not selected and are never silently created as notes or
  wikilinks;
- the user can see the target note and exact evidence before applying;
- the interface says plainly that OMD has not written to the vault.

Immediately before Apply, read the target again through the Obsidian Vault API
and compare the exact UTF-8 SHA-256 with the proposal hash. If it changed, stop
and require a fresh proposal. Apply only selected changes through the Vault API;
never ask OMD to edit the file. Set workflow metadata such as
`omd_home_status: reviewed` only after the full write succeeds.

An apply error must leave the note recoverable and must not claim success. Do not
move OMD Capture outputs, rewrite sidecars, or reuse ordinary content tags as
workflow state.

## Platform and privacy behavior

Local Python/Ollama subprocesses are desktop-only in v1. Disable the action on
Obsidian mobile with a concise explanation; do not expose a failing button.
Loopback Ollama is the default network boundary. A remote Ollama host remains an
explicit OMD CLI authorization decision and must never be inferred from plugin
settings alone.

## Release smoke test

Run these against the exact executable configured in the plugin:

```bash
OMD_PLUGIN_EXECUTABLE=/absolute/path/to/omd
"$OMD_PLUGIN_EXECUTABLE" --version
"$OMD_PLUGIN_EXECUTABLE" capabilities --json
"$OMD_PLUGIN_EXECUTABLE" enrich-note --help
```

Then complete the separate
[Chinese plugin UX acceptance guide](obsidian-plugin-ux-acceptance-guide.zh-CN.md)
in a disposable copy of a test vault. A successful CLI smoke test means the OMD
engine is available; it does not prove that the plugin's review/apply workflow is
implemented or safe.
