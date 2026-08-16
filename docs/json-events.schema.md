# omd `--json-events` schema

Transport version: **v1** (locked)
Structured work extension: **work v2** (additive)

When `--json-events` is set on `omd` (or `omd.reel` / `omd.podcast` / `omd.xhs`),
the CLI emits one JSON object per line on **stderr** in place of the human-readable
"pretty" stage labels and progress bar. The schema is stable across v1.x — consumers
(notably the future Stage 2 paid Mac GUI app) can pin to v1 and rely on the shape
below.

## Transport

- One JSON object per line on **stderr**.
- UTF-8.
- Mutex with `--verbose`: passing both is an error.
- Stdout is unchanged. If `--output` is omitted, the rendered Markdown still goes to
  stdout (just like in pretty mode), and events go to stderr alongside it.

## Sidecar manifest v2

Successful file outputs should have a sibling `.omd.json` sidecar when the
calling command writes to disk. The manifest is additive to this event stream;
`--json-events` remains v1 and `done.output` keeps pointing at the Markdown
output path.

Important v2 fields:

| field | type | meaning |
|-------|------|---------|
| `manifest_version` | integer | Current sidecar manifest version, `2`. |
| `source_id` | string | Stable source identifier derived from the input source. |
| `source_hash` | string | SHA-256 hash of the input source string/path. |
| `capture_id` | string | Stable capture identifier for this source/output pair. |
| `source_url` | string | Present for URL inputs. |
| `local_source_path` | string | Present for local file inputs. |
| `content_checksum` | string | SHA-256 hash of the final Markdown bytes. `checksum` remains as a compatibility alias. |
| `elements` | array | Conservative line-level Markdown element skeleton for downstream RAG/context indexing. |

`elements[]` currently indexes final Markdown content after YAML front matter and
uses nullable source-location fields when a backend cannot provide page or time
coordinates:

```json
{
  "id": "el_0001",
  "type": "title",
  "markdown_start_line": 12,
  "markdown_end_line": 12,
  "page_number": null,
  "timestamp_start": null,
  "timestamp_end": null,
  "source_ref": "/path/to/source.pdf"
}
```

## Common fields

Every event has these:

| field | type | meaning |
|-------|------|---------|
| `v` | integer | Schema version. v1 = always `1`. Consumers MUST check this and refuse to parse unknown versions. |
| `ts` | float | Unix epoch seconds, rounded to 3 decimal places. |
| `event` | string | One of the core event types below, or a documented command-specific event such as the batch events listed in this file. |

## Structured work extension (`work_v: 2`)

Stage, progress, and stage-state events may include an additive `work_v: 2`
snapshot. The top-level transport remains `v: 1`; existing consumers must ignore
the new fields and continue to work. New UI consumers should use these fields
instead of parsing human log text or treating cosmetic percentages as work.

| field | type | meaning |
|-------|------|---------|
| `work_v` | integer | Structured work schema version. Current value is `2`. |
| `stage_id` | string | Stable lowercase stage token such as `download`, `convert`, `ocr`, `transcribe`, or `polish`. |
| `state` | string | `determinate`, `indeterminate`, `retrying`, `needs_action`, `completed`, `failed`, or `cancelled`. |
| `unit` | string | Optional real work unit: `bytes`, `pages`, `pixels`, `audio_seconds`, `tokens`, or `items`. |
| `completed` | number | Completed real work. Omitted when it cannot be measured. |
| `total` | number | Known positive work total. Omitted when unknowable. |
| `elapsed_s` | number | Elapsed time for the current stage observation. |
| `attempt` | integer | One-based stage attempt. |
| `item_index` | integer | Optional one-based active batch item. It does not mean the item has completed. |
| `item_total` | integer | Optional total batch item count. |
| `peak_memory_bytes` | integer | Optional process high-water RSS snapshot, normalised to bytes. It contains no source or machine identity. |

Unknown totals remain explicitly indeterminate. A consumer must not invent a
percentage when `state` is not `determinate` or when `completed`/`total` are
absent.

## Event types

### `stage`

Marks the start of a named pipeline stage.

```json
{"v":1,"ts":1715342400.123,"event":"stage","name":"transcribe","work_v":2,"stage_id":"transcribe","state":"indeterminate","unit":"audio_seconds","elapsed_s":0.0,"attempt":1}
```

| field | type | meaning |
|-------|------|---------|
| `name` | string | Stage identifier. Stable values: `"download"`, `"transcribe"`, `"polish"`, `"compose"`, `"ocr"`, `"convert"`. New names may be added in v1.x; consumers should fall back to displaying the value as-is. |

### `stage_state`

Reports an explicit non-determinate or terminal state without fabricating a
legacy progress percentage.

```json
{"v":1,"ts":1715342450.456,"event":"stage_state","work_v":2,"stage_id":"transcribe","state":"completed","unit":"audio_seconds","completed":373.0,"total":373.0,"elapsed_s":61.2,"attempt":1}
```

Use `needs_action` for blockers such as missing cookies, `retrying` while a
retry is scheduled, and `completed`/`failed`/`cancelled` for terminal stage
states. ETA countdowns must pause for `needs_action` and `retrying`.

### `progress`

Per-tick progress update for a determinate operation. Emitted from inside
`ProgressBar` (e.g. polish chunks, batch folder iteration).

```json
{"v":1,"ts":1715342410.456,"event":"progress","label":"Polish","cur":420,"total":1680,"percent":25.0,"elapsed_s":4.27,"eta_s":12.81,"work_v":2,"stage_id":"polish","state":"determinate","unit":"tokens","completed":420.0,"attempt":1}
```

| field | type | meaning |
|-------|------|---------|
| `label` | string | Bar label. Examples: `"Polish"`, `"Batch"`, `"Download"`. |
| `cur` | integer | Legacy current count. Prefer `completed` when `work_v: 2` is present. |
| `total` | integer | Legacy total count. Always positive. With `work_v: 2`, it is the real-unit total declared by `unit`. |
| `percent` | float | Completion percentage, rounded to 1 decimal place. |
| `elapsed_s` | float | Seconds since the bar started. Rounded to 2 decimals. |
| `eta_s` | float | Legacy instantaneous estimate, rounded to 2 decimals. `0` when complete. It is retained for compatibility and is not the calibrated Stage ETA contract. |

### `done`

Pipeline finished successfully. Emitted exactly once per CLI invocation that
completes normally.

```json
{"v":1,"ts":1715342500.789,"event":"done","output":"/path/to/output.md"}
```

| field | type | meaning |
|-------|------|---------|
| `output` | string \| null | Absolute or working-directory-relative path written. `null` if the result went to stdout (no `--output`). |
| `request_id` | string | Optional additive field for request-scoped commands such as `enrich-note`. |

### `warn`

Non-fatal warning. Pipeline continues. Consumer may surface as a toast or
ignore (most UIs ignore unless debugging).

```json
{"v":1,"ts":1715342440.123,"event":"warn","message":"polished output much shorter than raw"}
```

| field | type | meaning |
|-------|------|---------|
| `message` | string | Human-readable warning line. |

### `error`

Fatal error. The process will exit non-zero immediately after emitting this
event. Consumer should treat the next non-zero exit code as the failure
signal and use the most recent `error` event for the user-facing message.

```json
{"v":1,"ts":1715342460.456,"event":"error","kind":"tool_missing","message":"`tesseract` not on PATH"}
```

| field | type | meaning |
|-------|------|---------|
| `kind` | string | Stable machine token. Known examples include `"tool_missing"`, `"unsupported_extension"`, `"file_not_found"`, `"flag_conflict"`, `"format_invalid"`, `"agent_safe_blocked_flag"`, `"url_not_found"`, `"cookies_missing"`, `"cookies_invalid"`, `"network"`, `"parse_failed"`, `"fetch_failed"`, `"transcribe_failed"`, and `"f2_no_audio"`. New `kind` values may be added in v1.x; consumers should fall back to displaying `message`. |
| `message` | string | Human-readable error line. May contain paths or URLs. |
| `request_id` | string | Optional additive field once a request-scoped command has validated the ID. |

### `enrich-note` stages and terminal events

`omd enrich-note --json-events` uses the additive stage IDs `catalog`,
`retrieve`, `generate`, and `validate` in that order. A successful run ends in
one `done` event with `output: null`; a failed run ends in one `error` event.
Terminal events include `request_id` when it is already known. Enrichment
events never contain note/candidate bodies, prompts, credentials, environment
values, or the full vault path. See
[`enrich-note` contract v1](enrich-note-contract-v1.md).

## Batch Events

`omd batch ... --json-events` emits command-level batch events. These are part
of the v1 contract and use the same common fields (`v`, `ts`, `event`) as core
events.

### `batch_started`

Emitted once after the batch list has been read.

```json
{"v":1,"ts":1715342400.123,"event":"batch_started","out_dir":"/path/to/out","total":2,"retries":1,"worker_plan":{"global":2,"convert":2,"network":2,"ocr":1,"asr":1,"model":1}}
```

| field | type | meaning |
|-------|------|---------|
| `out_dir` | string | Output directory for generated Markdown/RMarkdown files. |
| `total` | integer | Number of batch items to process. |
| `retries` | integer | Configured retry count for failed items. |
| `worker_plan` | object | Effective bounded limits for this run. `global` is the process-wide conversion cap; `convert`, `network`, `ocr`, `asr`, and `model` are lane caps. OCR, ASR, and model lanes remain one worker. |

### `batch_item_started`

Emitted before each item attempt.

```json
{"v":1,"ts":1715342401.123,"event":"batch_item_started","item":"https://example.com/a","output":"/path/to/out/example-com-a.Rmd","index":1,"total":2,"attempt":1}
```

| field | type | meaning |
|-------|------|---------|
| `item` | string | Source line being converted. |
| `output` | string | Planned output path for this item. |
| `index` | integer | 1-based item index. |
| `total` | integer | Total batch item count. |
| `attempt` | integer | 1-based attempt number for this item. |

### `batch_item_retry`

Emitted when an attempt fails and another attempt will run.

```json
{"v":1,"ts":1715342402.123,"event":"batch_item_retry","item":"https://example.com/a","output":"/path/to/out/example-com-a.Rmd","index":1,"total":2,"attempt":1,"return_code":1,"error":"converter returned 1"}
```

| field | type | meaning |
|-------|------|---------|
| `item` | string | Source line being retried. |
| `output` | string | Planned output path for this item. |
| `index` | integer | 1-based item index. |
| `total` | integer | Total batch item count. |
| `attempt` | integer | Failed attempt number. |
| `return_code` | integer | Converter return code for the failed attempt. |
| `error` | string | Human-readable failure reason. |

### `batch_item_succeeded`

Emitted once for each successfully converted item.

```json
{"v":1,"ts":1715342403.123,"event":"batch_item_succeeded","item":"https://example.com/a","output":"/path/to/out/example-com-a.Rmd","index":1,"total":2,"attempts":1,"return_code":0}
```

| field | type | meaning |
|-------|------|---------|
| `item` | string | Source line that converted successfully. |
| `output` | string | Output path written by this item. |
| `index` | integer | 1-based item index. |
| `total` | integer | Total batch item count. |
| `attempts` | integer | Attempts used before success. |
| `return_code` | integer | Converter return code, normally `0`. |

### `batch_item_failed`

Emitted once for each item that exhausts its attempts.

```json
{"v":1,"ts":1715342404.123,"event":"batch_item_failed","item":"https://example.com/b","output":"/path/to/out/example-com-b.Rmd","index":2,"total":2,"attempts":2,"return_code":1,"error":"converter returned 1"}
```

| field | type | meaning |
|-------|------|---------|
| `item` | string | Source line that failed. |
| `output` | string | Planned output path for this item. |
| `index` | integer | 1-based item index. |
| `total` | integer | Total batch item count. |
| `attempts` | integer | Attempts used before final failure. |
| `return_code` | integer | Converter return code from the final attempt. |
| `error` | string | Human-readable failure reason. |

### `batch_completed`

Emitted once when the batch loop finishes, even if some items failed.

```json
{"v":1,"ts":1715342405.123,"event":"batch_completed","out_dir":"/path/to/out","total":2,"succeeded":1,"failed":1,"exit_code":1}
```

| field | type | meaning |
|-------|------|---------|
| `out_dir` | string | Output directory for generated Markdown/RMarkdown files. |
| `total` | integer | Total batch item count. |
| `succeeded` | integer | Number of successful items. |
| `failed` | integer | Number of failed items. |
| `exit_code` | integer | Process exit code implied by the batch result. |

## Mutual exclusion

| flag combination | behavior |
|---|---|
| `--json-events` alone | events on stderr; pretty mode suppressed |
| `--verbose` alone | pretty mode + chatty subprocess output |
| `--quiet` alone | nothing on stderr except `error` (still emitted for debug logs) |
| `--json-events --verbose` | rejected at argparse: events are for machines, verbose is for humans |
| `--json-events --quiet` | rejected at argparse: events ARE the machine surface; quiet means none of it |

## Forward compatibility (v1.x)

Consumers MAY encounter:
- New event types not listed above. Consumers MUST ignore unknown event types
  (parse the JSON, see unrecognized `"event"` value, log + skip).
- New fields on existing events. Consumers MUST ignore unknown fields on
  known events.
- New `name` values on `stage`, new `kind` values on `error`. Consumers MUST
  fall back to displaying the value as-is.

Consumers MUST NOT:
- Pin to specific `name` or `kind` values for control flow (use `event` type).
- Assume event ordering beyond: `stage` events appear in pipeline order,
  `progress` events are monotonically increasing per `label`, `done` or
  `error` is the last event of a run.

## Versioning

- `v: 1` is locked. Breaking changes go in `v: 2` (separate flag or behind
  schema-version negotiation; not yet defined).
- Adding fields, event types, or stable `name`/`kind` tokens is non-breaking
  and lands in v1.x.

## Example session — successful Apple Podcasts conversion

```
{"v":1,"ts":1715342400.123,"event":"stage","name":"feed_lookup"}
{"v":1,"ts":1715342402.456,"event":"stage","name":"download"}
{"v":1,"ts":1715342410.789,"event":"stage","name":"transcribe"}
{"v":1,"ts":1715342900.012,"event":"stage","name":"polish"}
{"v":1,"ts":1715342901.234,"event":"progress","label":"Polish","cur":1,"total":12,"elapsed_s":1.22}
{"v":1,"ts":1715342902.456,"event":"progress","label":"Polish","cur":2,"total":12,"elapsed_s":2.44}
... (10 more progress events) ...
{"v":1,"ts":1715342930.678,"event":"progress","label":"Polish","cur":12,"total":12,"elapsed_s":30.66}
{"v":1,"ts":1715342931.789,"event":"done","output":"/Users/me/notes/episode.md"}
```

## Example session — error path

```
{"v":1,"ts":1715342400.123,"event":"stage","name":"feed_lookup"}
{"v":1,"ts":1715342402.456,"event":"error","kind":"network","message":"iTunes lookup failed: connection timed out"}
```

(process then exits with code 1)
