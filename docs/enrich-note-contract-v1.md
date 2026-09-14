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
    "content": "Local AI can support personal knowledge workflows.",
    "content_sha256": "794d84d3ead3ddea098401834de3cbe1097fa711f287233205ae75ecc031dc79"
  },
  "candidates": [
    {
      "id": "candidate-1",
      "path": "Notes/Local AI.md",
      "title": "Local AI",
      "aliases": ["On-device AI"],
      "tags": ["ai/local", "research"],
      "evidence": "Local AI and personal knowledge workflows."
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

`candidate.evidence` is an optional-in-content snippet (the field is required,
but an empty string is allowed), limited to 400 Unicode code points in the raw
input. OMD accepts ordinary LF, CR/CRLF, and TAB in this field and folds runs of
Unicode whitespace to one ASCII space, removing leading/trailing whitespace,
before using the snippet downstream. Other C0 controls (including vertical tab
and form feed) and DEL remain prohibited. Raw field and request limits are
checked before folding, so whitespace cannot bypass a size limit. This
compatibility clarification applies only to candidate evidence; IDs, titles,
paths, aliases, and tags retain their existing single-line validation.
`note.content`, its SHA-256, and the caller-owned files are never normalized.

Clients should canonicalize candidate snippets at their final request boundary:
reject prohibited controls, fold ordinary whitespace, trim, then truncate to
400 Unicode code points. Preserve separators between words and leave the target
note and hash untouched. OMD also accepts bounded multiline snippets from older
clients defensively.

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
    "content_sha256": "794d84d3ead3ddea098401834de3cbe1097fa711f287233205ae75ecc031dc79"
  },
  "proposal": {
    "summary": "This note discusses local AI and personal knowledge workflows.",
    "existing_links": [
      {
        "candidate_id": "candidate-1",
        "target_path": "Notes/Local AI.md",
        "display": "Local AI",
        "reason": "Directly related topic",
        "evidence": "Local AI",
        "recommended": true
      }
    ],
    "new_concepts": [{"label":"Personal knowledge workflows","reason":"Could become a separate concept"}],
    "existing_tags": [{"tag":"ai/local","reason":"Matches the main topic","recommended":true}],
    "new_tags": [{"tag":"knowledge-workflow","reason":"Describes the workflow topic"}]
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
| raw candidate evidence | 400 Unicode code points before whitespace folding |
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

Invalid candidate evidence (wrong type, raw size over 400 code points, or
prohibited controls) retains `kind: "invalid_request"` and exit status `2`.
Its error event adds a safe, stable validation category:

```json
{"v":1,"ts":1715342460.456,"event":"error","kind":"invalid_request","message":"candidate.evidence is incompatible; link/tag suggestions were not generated. Supply a string of at most 400 Unicode code points without prohibited control characters. No vault files were changed.","request_id":"request-1","validation":{"field":"candidate.evidence","reason":"incompatible_text"}}
```

`validation` is optional; existing consumers can continue using `kind`.
Clients should recognize the field/reason pair and display a fixed message,
rather than raw terminal text: "Link/tag suggestions could not be generated
because candidate text was incompatible. Update the candidate snippets before
generating again. Your note is unchanged." If the client has already confirmed
capture success, it should also say that capture succeeded. Changing models or
retrying the same payload does not fix this validation failure. The category
never includes evidence, candidate IDs, private paths, or provider details.
Consumers must ignore unknown validation fields/reasons and retain their safe
fallback for other errors. Proposal application still requires the caller's
existing review action.

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

The synthetic `home-multiline-request.json` fixture was generated by OMD Home's
real `buildEnrichmentRequest` at client commit `8e387a9`. Its input includes a
Source / Author / Published list and a wrapped paragraph/list, both in English.
Dedicated parser tests use named Unicode escapes to retain Unicode whitespace
and code-point boundary coverage while keeping test source text in English.
Regenerate it without a vault or model using a Node version with TypeScript
stripping and an OMD Home checkout:

```bash
node --experimental-strip-types tests/fixtures/enrich_note/generate_home_request.mjs \
  /path/to/omd-home > /tmp/home-multiline-request.json
diff tests/fixtures/enrich_note/v1/home-multiline-request.json /tmp/home-multiline-request.json
```

The Python fixture test exercises both that canonical builder output and the
legacy multiline snippets through decoding, prompt construction, and proposal
validation with a fake model executor. It verifies unchanged note bytes/hash
and candidate files. This is offline protocol coverage; manual Obsidian capture,
review/apply behavior, and real model quality remain separate integration checks.
