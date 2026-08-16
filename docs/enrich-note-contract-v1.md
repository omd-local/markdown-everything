# `enrich-note` contract v1

OMD owns this contract. `omd enrich-note` is a proposal-only, read-only engine:
it validates a Markdown note and an authoritative candidate catalog, invokes one
explicit Ollama model, and returns a fully validated proposal. The caller (for
example OMD Home) remains the only component allowed to edit the vault.

## Capability discovery

```bash
omd capabilities --json
```

```json
{"enrich_note":{"schema_versions":[1],"supported":true}}
```

Capability discovery is static. It does not connect to Ollama or inspect a
vault.

## Invocation modes

Standalone mode builds a bounded catalog from the vault:

```bash
omd enrich-note Inbox/example.md \
  --vault ~/Obsidian/Knowledge \
  --model qwen3:4b-instruct \
  --host http://localhost:11434 \
  --json-events
```

Request mode reads the complete v1 envelope from stdin so note content never
appears in a process listing:

```bash
omd enrich-note --request-json - --json-events < request.json
```

The modes are mutually exclusive. Request mode rejects a positional note and
CLI `--vault`, `--model`, or `--host` overrides. Both modes accept `--timeout`,
`--json-events`, and the authorization-only `--allow-remote-ollama` flag.
Unknown and abbreviated flags are rejected.

## Request

The top-level object and every nested object use exact fields; unknown or
missing fields are errors.

```json
{
  "schema_version": 1,
  "request_id": "request-1",
  "action": "enrich_note_preview",
  "vault_path": "/absolute/vault/path",
  "note": {
    "path": "Inbox/example.md",
    "content": "本地 AI 可以辅助个人知识工作流。",
    "content_sha256": "98887e41f98d63015e070641789fe375c7db3f458c9bb5823d125eefa460894a"
  },
  "candidates": [
    {
      "id": "candidate-1",
      "path": "Notes/Local AI.md",
      "title": "Local AI",
      "aliases": ["本地 AI"],
      "tags": ["ai/local", "research"],
      "evidence": "本地 AI 与个人知识工作流。"
    }
  ],
  "vault_tags": ["ai/local", "research", "workflow"],
  "model": "qwen3:4b-instruct",
  "host": "http://localhost:11434"
}
```

`content_sha256` is the lowercase SHA-256 of the exact UTF-8 bytes in
`note.content`. `vault_path` must be absolute. Note and candidate paths must be
vault-relative `.md` paths using POSIX separators; absolute paths, `..`, hidden
or system components, control characters, missing files, and symlink traversal
are rejected. Candidate IDs are request-local, opaque, and unique.

OMD Home's candidate catalog is authoritative for request mode, but OMD still
validates every referenced path. Standalone mode deterministically scans at
most 10,000 eligible notes, excludes the target and all hidden/symlink paths,
and sends at most 80 ranked candidates to the model.

## Response

Successful stdout is exactly one compact JSON object followed by a newline:

```json
{
  "schema_version": 1,
  "request_id": "request-1",
  "action": "enrich_note_preview",
  "note": {
    "path": "Inbox/example.md",
    "content_sha256": "98887e41f98d63015e070641789fe375c7db3f458c9bb5823d125eefa460894a"
  },
  "proposal": {
    "summary": "这篇笔记讨论本地 AI 与个人知识工作流。",
    "existing_links": [
      {
        "candidate_id": "candidate-1",
        "target_path": "Notes/Local AI.md",
        "display": "Local AI",
        "reason": "主题直接相关",
        "evidence": "本地 AI",
        "recommended": true
      }
    ],
    "new_concepts": [{"label":"个人知识工作流","reason":"可发展为独立概念"}],
    "existing_tags": [{"tag":"ai/local","reason":"匹配核心主题","recommended":true}],
    "new_tags": [{"tag":"knowledge-workflow","reason":"描述工作流主题"}]
  },
  "warnings": [],
  "generation": {
    "provider": "ollama",
    "model": "qwen3:4b-instruct",
    "endpoint_class": "local_loopback"
  }
}
```

OMD resolves every `target_path` and `display` from a validated candidate ID;
the model cannot return a path. Unknown or out-of-shortlist IDs fail the whole
run. Evidence must be a source-note substring. Existing links/tags already in
the source are removed. Duplicate title/alias identities remain tied to their
selected ID but are marked `recommended: false` with an ambiguity warning.
Generated tags use OMD's shared normalization and remain separated into
existing and new lists.

For each request, OMD deterministically extracts a bounded list of short,
verbatim source-note excerpts after skipping leading frontmatter. The list is
sent as untrusted `evidence_options`, each with an opaque request-local ID. The
request-specific model schema allows only those safe IDs; OMD resolves the
selected ID to its exact excerpt before validation. Untrusted excerpts therefore
remain outside the system prompt, while the model cannot summarize or paraphrase
evidence. OMD still performs the final source-substring check before returning a
proposal; the response shape and the 400-character evidence bound are unchanged.

Existing vault tags use the same request-local indirection. Raw tag text remains
in the untrusted payload beside a safe `tag-N` ID, the runtime schema permits only
those IDs, and OMD resolves a selection back to the catalogued tag before the
unchanged existing-tag validation. Invented existing tags therefore fail closed
without placing vault-controlled text in the system prompt.

`generation.endpoint_class` is derived by OMD and is either `local_loopback` or
`remote_https`. It is never accepted from model output.

## Bounds

| Surface | v1 bound |
|---|---:|
| stdin request | 512 KiB |
| target note content | 64 KiB UTF-8 |
| supplied candidates | 200 |
| vault tags | 500 |
| standalone eligible notes | 10,000 |
| standalone candidate file inspection | 256 KiB/file |
| candidates sent to the model | 80 maximum, reduced to fit context |
| model output | 2,048 tokens |
| links / concepts / existing tags / new tags | 20 / 12 / 20 / 12 |

If the local model context cannot contain the full bounded input, OMD truncates
only the model-facing copy and returns explicit
`source_truncated_for_model_context`, `candidate_catalog_truncated_for_model_context`,
or `vault_tags_truncated_for_model_context` warnings. The request hash and the
caller-owned note remain unchanged.

## Errors, events, and exit status

On success, exit status is `0`, stdout contains the response, and JSON-event
stderr ends with one `done` event. On failure, stdout is empty, exit status is
non-zero, and JSON-event stderr ends with one `error` event. Human mode emits a
single concise stderr line instead.

Stable v1 error kinds include:

- `unsupported_schema`
- `invalid_request`
- `note_not_found`
- `path_outside_vault`
- `request_too_large`
- `ollama_unavailable`
- `remote_ollama_not_authorized`
- `model_not_installed`
- `generation_timeout`
- `invalid_model_json`
- `unknown_candidate_id`
- `cancelled`

The command-specific stage IDs are `catalog`, `retrieve`, `generate`, and
`validate`; `done` or `error` is the unique terminal event. Terminal events may
include the validated `request_id`. Events never include note/candidate bodies,
prompts, credentials, environment values, or the full vault path.

## Trust and network boundary

All Markdown, candidate metadata, and excerpts are untrusted data. The system
instruction tells the model to ignore embedded instructions, preserve source
language, return empty suggestions at low confidence, and select existing notes
only by ID. OMD validates the strict structured output and all semantic bounds
before writing any success bytes.

Loopback Ollama is the default. A non-loopback endpoint remains blocked unless
the invocation includes `--allow-remote-ollama`; the endpoint must then be a
credential-free HTTPS base URL. JSON fields can select a host but can never
authorize remote access. Remote authorization is internally bound to the exact
source, task, model, and destination before discovery or network I/O.

OMD does not create, modify, rename, or delete vault files in this command.

## Versioning

Adding optional fields, warnings, event fields, event/stage tokens, or error
kinds is compatible within v1. Consumers must ignore unknown additive fields
and tokens. Removing/renaming fields, changing field meaning or type, weakening
validation, or changing required I/O semantics is breaking and requires schema
v2 plus capability negotiation.

The checked fixtures under `tests/fixtures/enrich_note/v1/` are the executable
compatibility examples for this document.
