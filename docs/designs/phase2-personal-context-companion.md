# Phase 2: Local-First Personal Context Companion

Status: implementation active. M1-M4 are implemented behind local-first
fallbacks; M5 now includes bounded machine-aware lanes, passive observations,
shadow-sample collection, an executable calibration evaluator, and an opt-in
candidate benchmark contract, but its real corpus/device gate is not complete;
M6 implementation review is complete but its external and manual release
evidence is not. This document does not yet declare Phase 2 release-ready.

Last reviewed: 2026-07-20

## Implementation Snapshot

| Milestone | Current state | Evidence and remaining gate |
| --- | --- | --- |
| M1 Context Receipt | Implemented | Atomic local outbox, durable receipt lifecycle, secured local-file copy, Inbox UI, and synthetic receipt benchmark. A fresh 30-sample run passed all four receipt scenarios on the current arm64/16 GB machine; the full supported-device matrix remains required before a public SLO claim. |
| M2 Model/provider boundary | Implemented, integration-gated | Loopback Ollama plus direct OpenAI, Anthropic, and DeepSeek BYOK adapters; task-bound consent; native macOS Keychain/session credentials; model validation; conservative preflight context/output budgets; structured output; bounded timeout; native streaming; cancellation between chunks; TTFT/usage/total-time telemetry; no provider fallback. The CLI requires explicit consent plus HTTPS for remote Ollama-compatible endpoints. Live hosted-provider checks require the user's own API credentials and are not exercised by the offline suite. |
| M3 Voice-ready Review | Implemented | Existing audio attachment intake, preserved audio, transcript, My Notes, and AI suggestion remain separate; accepting review writes a traceable `Notes/` derivative while preserving the Inbox source and audio. Recording is intentionally Phase 3. |
| M4 Retrieval/preferences | Implemented | Allowed-root lexical search, evidence snippets, duplicate/related-note candidates, and explicit preference inspect/export/reset. Vector retrieval remains benchmark-gated. |
| M5 Performance/ETA | Implemented foundation, evidence-gated | Structured work units, timing/peak RSS, one model worker, parser-before-OCR, memory-aware Ollama keep-alive, machine-aware conversion lanes (maximum three; OCR/ASR/model remain one), per-item event identity, passive throughput/retry/queue observations, privacy-safe local history, baseline-versus-shadow collection/evaluator, a critical-path helper, and truthful fallback states are implemented. Historical ranges still require 30 comparable successful samples plus a versioned calibration gate. The candidate harness exists, but real public article/ASR/VAD runs, production aggregate critical-path ETA, and the full device/source matrix remain open; no speed or ETA-accuracy claim is authorised. |
| M6 Local review gate | Reviewed, external gates open | On 2026-07-20, 1,207 offline tests, Ruff, source compilation, diff checks, an isolated wheel build/install, CLI/MCP entry points, Phase 2 imports, Gradio app construction, dependency audit, and the synthetic receipt benchmark passed. Independent global, security, and design reviews returned `CLEAR`; product review returned `WATCH` because one end-to-end first-use smoke across all four input types, live hosted-provider cancellation with user-owned credentials, the full device/source performance matrix, and a clean release-candidate commit remain open. |

Executable receipt-gate scope and claim limits are recorded in
[`docs/phase2-benchmark.md`](../phase2-benchmark.md).

## Product Decision

Phase 2 keeps Obsidian as the primary note visualisation system and develops OMD
from a converter into a local-first context inbox:

1. Put material into an Inbox with minimal friction.
2. Secure the original input before expensive processing starts.
3. Keep source content, user-authored notes, and AI suggestions distinct.
4. Let the user review and promote useful material into their vault later.

The Mac remains the authoritative capture processor. Phase 2 prepares the local
receiver, review contract, durable job queue, and performance foundation needed
by a future mobile companion. It does not ship phone recording, a share sheet,
QR pairing, or mobile sync.

## Phase Boundary

### Phase 2 includes

- a durable local Inbox and Context Receipt;
- **Voice-ready Inbox and Review** for audio files received by the Mac;
- local transcription, preservation of the original audio attachment, and a
  place for the user to add notes to that audio;
- explicit source, user, transcript, and AI-suggestion sections in Review;
- a stable provider boundary for supported local model runtimes;
- optional task-scoped OpenAI, Anthropic, and DeepSeek access using the user's
  own developer API key, without making cloud AI part of the capture path;
- performance instrumentation, fast paths, and a measurable local save SLO;
- a unified review of the existing local UI and feature set before mobile work.

### Phase 2 does not include

- recording from a phone or desktop microphone;
- a mobile app, PWA, share sheet, offline mobile queue, or QR pairing;
- a promise that OCR, transcription, downloading, or AI enrichment finishes in
  ten seconds;
- automatic cloud fallback or a hosted OMD service;
- replacing Obsidian as the primary note visualisation system.

Actual voice capture and the end-to-end **mobile capture** experience for
"reliably saved within 10 seconds" are Phase 3 backlog items. Phase 2 still owns
and benchmarks the durable local Context Receipt SLO that the future mobile
experience will call.

## Non-Negotiable Boundaries

- Canonical content remains readable Markdown in the user's chosen vault.
- `Inbox/` holds unreviewed captures; `Notes/` holds reviewed derivatives.
- Existing `Sources/`, `_attachments/`, manifests, and indexes remain compatible.
- Source content, transcript, `My Notes`, highlights, and AI suggestions stay
  visibly and structurally distinct.
- Storage, sync transport, and AI processing are independent choices.
- Local processing is the default. OMD never silently falls back to cloud AI.
- Optional cloud processing requires an explicit per-task choice, a configured
  user-owned API credential, and a visible destination before content leaves
  the machine.
- Failed conversion or enrichment never removes the secured original input.
- The source record is immutable; organisation creates a traceable derivative.
- Other note-system integrations remain Phase 3 research.

## Public Privacy Model

The current three-mode vocabulary is too difficult to explain and makes
`cloud_allowed` look like a permanent blanket permission. When at least one
hosted adapter passes its adapter-specific M2 security and release gate, OMD
should present only two memorable choices:

| User-facing mode | Meaning | Failure behaviour |
| --- | --- | --- |
| **Local only** (default) | Content is processed on this Mac or an explicitly approved loopback endpoint. | Keep the raw capture and show which local capability was unavailable. |
| **Cloud for this task** | The selected operation may send the previewed content to the named provider and model using the user's API credential. Permission expires with the task. | Do not send without consent, switch providers, or retry through another provider; keep the local capture if the request fails. |

Privacy mode controls AI processing, not where Markdown is stored. Sync remains
a separate transport setting; for example, local-only AI may coexist with a
user-enabled iCloud folder in Phase 3.

Implementation compatibility note: the existing internal
`ask_before_cloud`/`cloud_allowed` values may require a migration window, but
`cloud_allowed` must not remain a public product mode. `remote_ollama` is an
advanced endpoint type, not a privacy mode. **Cloud for this task** appears only
after the user configures an adapter that passed its M2 gate; otherwise the UI
shows only **Local only**, not a disabled or non-functional cloud choice.

## Optional Cloud Providers (BYOK)

### Baseline before implementation

At plan approval, OMD did not have a working OpenRouter, OpenAI, Anthropic, or
DeepSeek client. That baseline justified the provider work above; the current
implementation state is recorded in the Implementation Snapshot. OpenRouter
remains intentionally absent and must not be described as configured or
available to users.

Paid ChatGPT and Claude subscriptions do not by themselves provide API access to
OMD. Consumer subscriptions and developer APIs have separate credentials and
billing. User-facing labels must therefore say **OpenAI API** and **Anthropic
API**, not "connect ChatGPT" or "connect Claude account". DeepSeek also requires
a DeepSeek Platform API key.

### Phase 2 decision

- Omit OpenRouter. It adds a routing, retention, and billing intermediary that is
  unnecessary when users can choose a direct provider.
- Keep capture, deterministic conversion, Inbox, and Review fully functional
  without a cloud credential or network connection.
- Add direct, user-supplied credential support for OpenAI, Anthropic, and
  DeepSeek behind one internal capability contract.
- Use native provider semantics: OpenAI Responses API, Anthropic Messages API,
  and a provider-specific DeepSeek adapter over its documented compatible API.
  Do not pretend one generic OpenAI-compatible client makes error, streaming,
  usage, retention, and model behaviour identical.
- Keep every request opt-in and task-scoped. Never make a provider the hidden
  fallback for another provider or for a failed local model.

### Provider contract

Each adapter must implement:

- credential validation without echoing the secret;
- model discovery or explicit model validation where the provider supports it;
- task capability and context/output-limit checks;
- streaming, cancellation, bounded timeout, and retry classification;
- structured-output capability without assuming identical schemas;
- input/output token usage, time-to-first-token, total time, and status class;
- a human-readable destination, provider policy link, and data-handling summary.

Provider and model identifiers are stored with the job receipt, but the secret
is not. Model discovery is not a complete capability source: OMD needs curated,
versioned capability metadata plus live validation. Model aliases and
availability change, so OMD must validate the selected model at run time rather
than ship a permanent "best cloud model" default.

The current adapter guard uses a 4,096-token local Ollama context, a conservative
32,768-token hosted context budget, and an 8,192-token maximum output request.
Ollama receives the same 4,096 `num_ctx` value used by preflight. These are OMD
safety budgets, not claims about every selected model's maximum context. Tasks
that do not fit are rejected before credential access or network I/O; source
text is never silently truncated. Curated model-specific capability metadata
and live provider checks remain part of the M2 integration gate.

### Credentials and request disclosure

- Store UI-entered secrets in macOS Keychain. CLI and automation may read the
  standard provider environment variables, but keys must never appear in argv,
  Markdown, manifests, sync envelopes, ETA history, analytics, or logs.
- Provide test, replace, and delete-credential actions. A missing Keychain must
  fall back to session-only entry, not plaintext application state.
- Before sending, show provider, model, destination domain, operation, estimated
  input size, and whether source text or an attachment will leave the Mac.
- Send the minimum required note chunks by default. Cookies, credentials, the
  whole vault, raw attachments, and unrelated Context Receipt fields are never
  included implicitly.
- Show estimated tokens before consent where practical and actual usage after
  the request. Price is an estimate only when OMD has versioned, current pricing
  metadata; stale pricing must not be presented as exact cost.

### Provider-specific privacy position

| Provider | Phase 2 transport | Disclosure required before consent |
| --- | --- | --- |
| OpenAI | Native Responses API with storage disabled where the endpoint permits it; no background mode or hosted vector/file state in the initial adapter. | API content is not used for training by default, but abuse-monitoring or endpoint application-state retention may still apply. Link the current endpoint policy. |
| Anthropic | Native Messages API; use token counting and streaming where available. | API inputs and outputs are normally deleted within 30 days unless another agreement, policy enforcement, or law applies. Link the current commercial retention policy. |
| DeepSeek | Explicit DeepSeek adapter using its documented OpenAI-compatible transport first; Anthropic compatibility remains optional and must not silently remap unsupported features. | The published policy describes processing/storage in the People's Republic of China and retention that varies by purpose. Do not label this option equivalent to local processing or infer an API-specific zero-retention promise. |

If a provider changes its API or policy and OMD can no longer display an accurate
destination and retention summary, disable that adapter until its disclosure and
tests are updated. Cloud failure always leaves the secured source and
deterministic Markdown available for local review.

## Local Model Strategy

Phase 2 should support multiple models without presenting every runtime as a
different product mode.

| Runtime | Phase 2 position | Notes |
| --- | --- | --- |
| Ollama | Supported default | Existing integration; users may select any installed compatible model tag. No automatic downloads. |
| LM Studio | Provider-boundary candidate | Exposes a local OpenAI-compatible server; benchmark before claiming support. |
| llama.cpp server | Provider-boundary candidate | Local OpenAI-compatible endpoint with continuous batching and optional speculative decoding; advanced users only after tests. |
| MLX / MLX-LM | Benchmark candidate | Strong Apple Silicon fit and prompt caching, but adding a second managed model lifecycle would increase packaging work. |
| vLLM | Not a default Mac path | Useful for high-throughput GPU servers, but too heavy for OMD's normal local desktop workflow. |

Provider selection and model selection are separate:

- provider: where and how inference runs;
- model: the installed model identifier used for the task;
- privacy mode: whether content may leave the approved local boundary.

The local provider contract should cover health, installed-model discovery,
capability, context limit, structured output, timeout, cancellation, streaming,
and timing telemetry. Loopback endpoints are local by default. Non-loopback
endpoints require explicit advanced configuration, HTTPS where applicable, and
a visible destination.

Model recommendation may use machine RAM and model metadata, but it must remain
advisory. OMD must check actual availability and estimated fit before starting a
large task, never download a model automatically, and never silently switch to a
different model.

## Canonical Records

### `InboxItem`

An immutable source-of-truth record with:

- stable content-derived ID;
- `capture_surface`: `my_note`, `highlight`, `voice_memo`, or `import` in the
  existing v1 schema;
- `provenance_kind`: `authored`, `excerpt`, `audio`, or `imported`;
- title and verbatim raw content;
- source locator, attachment identity, and capture timestamp.

The existing `voice_memo` value is a compatibility label for audio received by
the Mac; it does not imply that OMD records audio. A clearer
`audio_attachment` value may be introduced only in a versioned schema with an
explicit migration and round-trip tests.

### `KnowledgeNote`

A reviewed derivative with:

- stable source identity;
- `derived_from` pointing to the Inbox item;
- source content, transcript, highlights, `My Notes`, and AI suggestions in
  separate fields;
- no mutation of the canonical Inbox item.

### `InboxJob`

A versioned, idempotent processing job with:

- stable job and source identity;
- current stage and per-stage attempt count;
- secured-input locator and content hash;
- retryable versus terminal failure state;
- timestamps for queue, start, finish, and cancellation;
- no cookies, tokens, complete command lines, or unrelated machine details.

### `ContextReceipt`

The receipt is the user-visible proof that OMD has secured responsibility for an
input. It contains:

- job ID and accepted source type;
- whether the source bytes are secured or only referenced;
- destination and privacy mode;
- accepted time and current processing state;
- a clear recovery path if durable save failed.

"Saved" must never mean only that a background task was started. For local
files, OMD should not claim that the attachment is secured until an atomic copy,
same-volume clone, or equivalent durable operation is committed.

The receipt belongs in local application state keyed by job/item ID. It may be
shown or explicitly exported, but processing mechanics and private diagnostics
must not be injected into the canonical note Markdown.

## Voice-Ready Inbox and Review

Phase 2's voice scope is a receiver and review surface:

1. The user selects or drops an existing audio file.
2. OMD secures the original attachment and immediately creates a Context Receipt.
3. Local transcription runs as a background stage.
4. Review shows the original audio, transcript source/model/language, transcript,
   `My Notes`, and optional AI suggestions as separate sections.
5. The user may edit the transcript derivative, add a note, retry local
   transcription, accept the derivative, or keep only the raw attachment.

Required failure behaviour:

- missing or incompatible transcription runtime keeps the audio and user note;
- low-confidence, empty, or repetition-heavy transcripts are flagged for review;
- retry never overwrites the original audio or an earlier accepted note;
- AI polish remains optional and runs after deterministic validation.

Recording controls, microphone permission, mobile compression, and upload UX are
not Phase 2 work.

## Ten-Second Reliability Research

### Define the promise correctly

The core promise is:

> Within ten seconds, OMD either returns a durable Context Receipt or explains
> clearly why it could not secure the input.

It is not a universal processing-completion promise. A long podcast, scanned PDF,
or gated social post can legitimately take minutes or require user action.

### Target pipeline

```text
minimal validation
  -> secure raw input and content hash
  -> commit durable job
  -> return Context Receipt
  -> fetch / extract / OCR / transcribe in background
  -> deterministic clean-up
  -> optional AI enrichment last
  -> user review
```

The foreground path should contain no model load, network fetch, OCR,
transcription, or Markdown polish. Every background stage must be idempotent and
able to resume from the last committed state.

### Technology assessment

| Technique | Decision | Benefit | Constraint or fallback |
| --- | --- | --- | --- |
| Atomic file write plus directory sync | Adopt | Prevents a receipt pointing to a partially written job. | Keep the previous complete file if replacement fails. |
| SQLite outbox with WAL and one writer | Adopt after version gate | Durable jobs and responsive readers without Redis/Celery. | Require a SQLite version containing the 2026 WAL-reset fix; otherwise use rollback journal or a single conservative connection. |
| Same-volume APFS clone/copy-on-write | Prototype | Can secure large local files without copying every byte in the foreground. | Fall back to ordinary verified copy; never treat a source path alone as secured. |
| Content hash and stable source identity | Adopt | Idempotent retry and duplicate suppression. | Hash large files incrementally and report attachment state separately. |
| Separate bounded worker lanes | Adopt | URL extraction can continue while one local model is busy. | Bound OCR/ASR/LLM concurrency by RAM and model fit; one large-model worker by default. |
| Trafilatura/Readability/Defuddle fast extraction | Benchmark | Faster clean article path before a browser fallback. | Preserve current converter and raw HTML fallback until quality tests pass. |
| Browser rendering fallback | Defer to observed need | Handles JavaScript-heavy pages. | Expensive and fragile; never place in the receipt path. |
| Parser before OCR | Adopt as routing rule | Avoids OCR when a PDF already contains usable text. | OCR only pages that fail text-quality checks. |
| Silero VAD before ASR | Benchmark | Removes silence and reduces wasted audio inference. | Preserve timestamps and avoid clipping speech on noisy inputs. |
| MLX/whisper.cpp/faster-whisper ASR | Benchmark on Apple Silicon | Quantisation, Core ML/Metal, VAD, and batching can improve latency. | Keep the current working backend until word error rate and packaging gates pass. |
| Ollama `keep_alive` and timing telemetry | Adopt | Avoids repeated model load and separates load from generation time. | Release memory after idle timeout and under memory pressure. |
| Prompt/KV caching and bounded output | Benchmark | Reduces repeated system-prompt prefill and unbounded polish work. | Deterministic transforms first; preserve raw chunks on timeout. |
| Structured progress events v2 | Adopt | Replaces human-log keyword guesses with stage IDs and real work units. | Unknown totals remain indeterminate; human-readable logs continue separately. |
| Local stage-history store | Adopt with privacy limits | Learns realistic timing from this device, runtime, model, and workload. | Keep no source text, URL, path, cookie, credential, or filename; allow disable/reset. |
| Parallel LLM calls per document | Reject by default | May reduce wall time on large hardware. | On normal Macs it multiplies memory pressure, can reorder output, and worsens timeout recovery. |
| Redis/Celery or vLLM in the desktop package | Reject | Proven server throughput patterns. | Operational weight is disproportionate for a single-user local app. |

For multi-item runs, conversion workers may continue while one warm model worker
enriches an earlier item. OMD should not start multiple large-model calls merely
because multiple files are queued. Adaptive concurrency is allowed only after a
machine-fit check and benchmark evidence.

### Measurable SLOs

These are initial test targets, not release claims until benchmarked:

| Operation | Target on supported local APFS hardware |
| --- | --- |
| Authored note/highlight durable receipt | p95 <= 500 ms |
| URL job durable receipt | p95 <= 1 s |
| Local file receipt when a safe clone succeeds | p95 <= 2 s |
| Any supported local capture | durable receipt or explicit failure <= 10 s |
| Simple public article normalisation | p95 <= 10 s on a versioned benchmark corpus; excludes browser/gated fallbacks |

The UI must distinguish `queued`, `source secured`, `processing`, `needs action`,
`partial output`, and `complete`. A receipt for a referenced path is not the same
as a secured attachment.

### Benchmark matrix

Before changing a default backend or advertising performance, test:

- Apple M1, M2, and M4 classes where available;
- 8, 16, 24, and 32+ GB memory tiers;
- cold runtime versus warm runtime;
- short text, public article, text PDF, scanned PDF, and short/long audio;
- queue latency, secure-copy latency, fetch, extraction, OCR, ASR, model load,
  prompt evaluation, generation, total processing time, and peak memory;
- output quality and fallback correctness, not speed alone.

### ETA accuracy plan

The current UI heuristic is not an acceptable Phase 2 endpoint. It assigns fixed
percentages to log words such as `downloading`, `transcribing`, and `polishing`,
then smooths percent-per-second. Those percentages are not proportional to work,
so a slow model load or a short final stage can make the ETA appear late and then
jump directly to complete.

ETA becomes a separate, stage-aware prediction subsystem. It predicts:

```text
queue wait
  + remaining time in the active stage
  + predicted time for unstarted sequential stages
  + longest path through any stages that safely run in parallel
```

Every adapter emits versioned structured events with a stage ID, completed and
total work units where known, elapsed time, and non-sensitive workload features.
The UI must not derive ETA by parsing presentation logs.

#### Predictive factors

Preflight may collect only cheap information that does not delay the durable
receipt. Deeper features become available as processing proceeds:

| Stage | Useful factors and real progress units |
| --- | --- |
| Fetch/download | Adapter class, known content length, bytes transferred, passive rolling throughput, time to first byte, retries, and queue wait. Do not run a separate speed test; it wastes bandwidth and does not predict the source host reliably. |
| File/article conversion | Source type, byte size, extracted character count, image count, converter route, volume class, and passive read/copy throughput. |
| PDF/OCR | Page count, pages with usable embedded text, pages requiring OCR, image pixel count, and OCR language set; progress is pages or pixels, not a guessed percentage. |
| ASR | Media duration, detected speech duration when VAD is enabled, model/backend, language mode, and audio seconds processed. |
| Local LLM | Device tier, RAM tier, runtime/model identity and digest, context size, input/output token bounds, model cold/warm state, load time, prompt-evaluation rate, and generation rate. Ollama's running-model and usage APIs supply these observations. |
| Hosted LLM | Provider/model, estimated input tokens, output cap, time to first token, rolling output tokens per second, rate-limit/retry state, and locally observed provider history. Prompt and response content are not ETA telemetry. |
| Batch | Item source classes, queue depth, worker-lane limits, completed items, and predicted critical path; do not add parallel stage times as though they were sequential. |

Device detection is internal and coarse: Apple chip family, logical-core and
memory tier, usable accelerator/backend, worker occupancy, process/system load,
memory pressure, and power/thermal state when cheaply available. It must not be
shown as a guarantee that a model will fit. Network evidence comes passively
from the required request itself; OMD does not probe unrelated servers or
persist IP addresses. Feature-ablation results decide which signals remain in
the estimator; collecting more device data is not itself an accuracy win.

#### Local timing history

Store stage-level observations in local application state, not the vault. A
record may contain only schema/app version, stage and adapter class, coarse input
units, device tier, runtime/provider, model identifier or digest, cold/warm
state, queue depth, duration, throughput, outcome class, and timestamp.

Do not retain source text, prompts, responses, full URLs, arbitrary hostnames,
file paths, filenames, cookies, user IDs, API keys, or note titles. Cap history
by age and count, expose disable/reset/export-summary controls, and down-weight
or invalidate observations after pipeline, runtime, or model-version changes.
Shared telemetry remains opt-in and is not required for ETA.

#### Estimator and fallback

1. Cold start uses conservative p50/p90 priors from the versioned benchmark
   corpus, selected by stage, source class, device tier, runtime, and model.
2. Local history supplies robust, recency-weighted median and quantile estimates
   with hierarchical fallback: exact bucket, then stage/source/device tier, then
   global stage prior. A small or stale bucket never produces high confidence.
3. After a minimum amount of real work, blend the prior with observed throughput
   using a robust EWMA. Increase the live observation's weight as meaningful
   units complete; never infer throughput from cosmetic progress percentages.
4. Sum sequential stage distributions and use the critical path for parallel
   work. Recompute batch ETA when a stage finishes, a retry is scheduled, or the
   worker plan changes.
5. If a total is unknowable, a source needs login/cookies, a provider rate-limits
   the task, or the pipeline awaits user action, suspend the countdown and show
   `estimating`, `retrying`, or `needs action` with the reason.

The fallback is always a truthful range or indeterminate state. Missing history,
unknown content length, or unavailable device telemetry must never block capture
and must never fabricate a precise point estimate.

The first release deliberately uses robust medians, quantiles, hierarchical
fallback, and EWMA rather than adding OpenTelemetry, XGBoost, LightGBM, or a
conformal-prediction runtime to the desktop package. Per-user history will be
sparse at first, and a learned model without enough representative data adds
complexity rather than accuracy. Failed, cancelled, and needs-action runs are
recorded as separate outcomes and do not count as successful completion times.
If censoring becomes material and the structured corpus is large enough, M5 may
benchmark survival/AFT plus calibrated quantile models offline; adoption still
requires a measured win and no heavyweight default runtime dependency.

#### UI contract

- Before enough evidence: `Usually 2-4 min - low confidence`.
- After live calibration: `About 1m 40s - likely 1m 20s-2m 10s`.
- Before a measurable stage begins: `Estimating after download starts`.
- For a blocker: `Needs cookies - ETA paused` rather than a counting timer.
- Always show current stage and total elapsed separately from ETA.
- Update at a bounded cadence and damp cosmetic oscillation, but immediately
  surface retries, provider throttling, worker-plan changes, or a widened range.

Do not show second-level precision for minute-scale work. Confidence is derived
from sample count, recency, interval width, and how much real work has completed;
it is not a decorative label.

#### Accuracy and calibration gates

Compare the new estimator with the current fixed-range/log-percentage baseline on
the full benchmark matrix. Initial engineering targets, not public promises:

- after at least 30 comparable observations in a stable local-stage bucket, the
  p90 interval covers 85-95 percent of actual completion times;
- after five seconds or 10 percent of meaningful work, whichever is later,
  median relative error is <= 30 percent for stable local OCR, ASR, and LLM
  stages; use median absolute error for tasks shorter than 30 seconds;
- report error separately for cold/warm models, device tier, source class,
  provider, success, retry, and fallback paths;
- a release candidate must materially improve median error and interval
  calibration over the baseline without increasing receipt latency;
- buckets with fewer than 30 comparable successful observations inherit the
  nearest calibrated parent range and remain labelled low confidence. They do
  not block release if the indeterminate/range fallback and scenario tests pass,
  but OMD may not publish a bucket-specific accuracy claim. Rare retry and
  fallback paths use deterministic scenario tests until their sample threshold
  is reached;
- no accuracy claim is published until the benchmark corpus, sample size,
  estimator version, and confidence definition are documented.

## Current Local Product Review

### What is already strong

- broad URL/file intake and direct Obsidian vault output;
- raw-output fallback when optional AI fails;
- source-specific cookie separation and visible run warnings;
- local model readiness checks and machine-aware recommendations;
- immutable Inbox, review decisions, provenance, lexical retrieval, and local
  preference foundations;
- progress and partial-failure states for multi-item runs.

### Remaining gaps before "best possible" is credible

1. The implemented context workspace, retrieval, preference controls, Voice-ready
   Review, and provider guidance still require independent usability and
   accessibility review on the supported desktop and narrow-width matrix.
2. Direct OpenAI, Anthropic, and DeepSeek BYOK adapters are implemented, but live
   provider checks require user-owned credentials and adapter disclosures must be
   revalidated against current provider policy before release.
3. Structured work-unit telemetry and privacy-safe local history are implemented,
   but VAD/ASR/article/model benchmarks, the device/source matrix, and
   baseline-versus-shadow ETA calibration remain incomplete.
4. A clean release candidate still needs independent product, design, security,
   performance, packaging, and smoke-test evidence with no open P0/P1 defects.

### Design read

- Purpose: a local production tool for knowledge workers, not a note editor or
  another general-purpose note app.
- Tone: trustworthy, direct, and inspectable.
- Visual constraint: preserve the OMD.EXE retro desktop theme, high contrast,
  low motion, and moderate information density.
- Differentiator: every capture produces a visible receipt and every AI-derived
  claim remains reviewable against its source.

The next UI design should improve hierarchy, not replace the established theme:

- Capture remains the primary action.
- Inbox/Review becomes a real secondary workspace.
- Retrieval and preference controls become discoverable but not dominant.
- Model and privacy status use one consistent destination banner.
- Advanced runtime details remain collapsed until needed.

## Dependency-Ordered Delivery

No milestone begins implementation until this plan is approved.

### M0: Contract and Baseline

Deliverables:

- freeze the Phase 2/3 boundary in docs and user-facing terminology;
- define `ContextReceipt`, durable versus referenced input, and job states;
- record current UI flows, performance, and regression tests;
- define a versioned benchmark corpus without private user content.

Gate: product, architecture, security, and test reviewers agree on definitions
and baseline evidence.

### M1: Durable Receipt and Outbox

Deliverables:

- atomic source staging and versioned, idempotent local jobs;
- SQLite outbox or an equivalently durable single-machine queue;
- explicit `queued` versus `source secured` receipt states;
- stage retry, cancellation, crash recovery, and duplicate handling;
- no network, OCR, ASR, or model call in the foreground receipt path.

Fallback: current synchronous capture remains available until crash/restart and
data-loss tests pass.

Gate: power-interruption simulation, partial-write, duplicate, cancellation,
large-file, and restart tests; supported SQLite version documented.

### M2: Provider Boundary, Local Routing, and Optional BYOK Cloud

Deliverables:

- preserve Ollama as the supported default;
- separate privacy mode, provider, model, and task capability;
- local endpoint and installed-model discovery;
- machine-fit warning based on memory and model metadata;
- provider contract for health, timing, cancellation, context, and structured
  output;
- prototype one localhost OpenAI-compatible adapter behind a feature flag only
  if benchmarks justify it;
- direct OpenAI Responses API and Anthropic Messages API adapters;
- an explicit DeepSeek adapter using the OpenAI-format transport first, without
  assuming API-specific retention guarantees that its documentation does not
  state;
- macOS Keychain storage, environment-variable CLI support, secret redaction,
  connection testing, model validation, and credential deletion;
- per-task destination/content preview, current provider-policy links, estimated
  and actual usage, and capability-aware UI;
- remove OpenRouter from user-visible settings and implementation scope.

Fallback: deterministic organisation and raw Markdown remain fully usable with
all model providers disabled.

Delivery order inside M2:

1. Freeze the capability, credential, consent, destination, usage, and error
   contracts and their denial-path tests.
2. Stabilise Ollama and the localhost provider boundary.
3. Add Keychain and disclosure UI, then the native OpenAI adapter.
4. Add the native Anthropic adapter without routing it through OpenAI
   compatibility.
5. Add DeepSeek over its OpenAI-format transport with its distinct policy and
   error semantics.
6. Reveal each adapter independently only after its own security, fallback, and
   policy tests pass. Phase 2 scope is complete when all three direct adapters
   pass; none blocks local capture while under development.

Gate: denial-path, missing-model, retired-model, stopped-runtime, timeout,
memory-fit, non-loopback destination, consent expiry, secret-redaction,
provider-policy, unsupported-capability, and no-silent-fallback tests. Each
adapter must also prove the actual endpoint, request fields, storage flags, and
enabled features match its displayed retention disclosure; policy drift fails
the adapter closed. A release build must prove that consumer ChatGPT/Claude login
state is neither requested nor consumed.

### M3: Voice-Ready Inbox and Review

Deliverables:

- audio attachment receipt and original-file preservation;
- local transcription as a resumable background stage;
- transcript quality checks for empty, low-confidence, and repetitive output;
- separate source audio, transcript, `My Notes`, and AI suggestion sections;
- edit, retry-local, keep-raw, accept, and reject actions.

Fallback: a failed transcript leaves the audio attachment and user note intact.

Gate: audio-format matrix, corrupt-file, missing-runtime, repeated-transcript,
timeout, retry, and provenance tests plus design review.

### M4: Retrieval and Preference Loop in the UI

Deliverables:

- local lexical search with note path and bounded evidence snippet;
- duplicate detection by stable identity;
- related-note candidates that never rewrite notes automatically;
- visible preference inspect, export, and reset;
- preferences update only from explicit accept, reject, and user-edit events.

Embeddings remain optional until real-vault benchmarks show lexical retrieval is
insufficient. TurboVec or another vector backend requires a separate benchmark
and must remain replaceable.

Fallback: direct lexical matches and deterministic defaults.

Gate: deterministic retrieval, allowed-root, preference provenance, reset, and
large-vault tests.

### M5: Performance and Scheduling

Deliverables:

- per-stage timing and peak-memory instrumentation;
- one warm local-model worker plus bounded conversion/OCR/ASR lanes;
- Ollama keep-alive with idle/memory-pressure release;
- parser-before-OCR and deterministic-before-LLM routing;
- benchmark VAD/ASR and article-extraction candidates;
- structured progress events using bytes, pages/pixels, audio seconds, tokens,
  and item/stage completion rather than human log parsing;
- privacy-minimised local stage history with reset/disable controls;
- stage-aware p50/p90 ETA using device tier, workload features, model cold/warm
  state, passive network observations, local history, and critical-path
  scheduling;
- calibrated range/confidence UI plus explicit indeterminate, retrying, and
  needs-action states.

Fallback: one conservative worker and truthful indeterminate progress.

Delivery order inside M5:

1. Freeze structured stage/event and privacy-minimised history schemas.
2. Emit real work units from download, conversion, OCR, ASR, and model stages.
3. Collect the baseline corpus without changing the current user-visible ETA.
4. Run the stage-aware estimator in shadow mode and compare every prediction
   with the frozen heuristic.
5. Expose range/confidence UI only after calibration gates pass, retaining the
   indeterminate fallback for unsupported or low-evidence stages.

Gate: benchmark matrix passes without output-quality regression, memory
exhaustion, starvation, receipt-latency regression, or loss of fallback output.
Against the frozen baseline, ETA median error and p90 interval calibration must
improve materially; results are segmented by source/stage, device tier,
cold/warm state, provider, retry, and fallback. Sparse slices use the documented
parent-range fallback and are reported rather than omitted.

Current implementation note: local history is collection-only until a bucket or
parent contains at least 30 comparable successful observations **and** the
current pipeline version has an explicit calibration gate. That gate requires a
privacy-safe benchmark token, an integer sample count of at least 30, at least a
10 percent improvement over the frozen baseline median error, and p90 coverage
of at least 90 percent. Invalid individual gates are ignored without discarding
valid observations; summaries expose only calibrated pipeline versions and the
gate count. Once a shadow bucket is mature, the UI records a separate bounded
baseline-versus-shadow row only after five seconds and 10 percent of real work;
Reset removes both stores. The first stage for a runtime/model pair is recorded as cold and
later stages in the run as warm. Live throughput may still produce an ETA after
five seconds and 10 percent of real work. The receipt benchmark is executable,
and the opt-in candidate harness enforces privacy, quality, latency, missing-
runtime, and VAD-retention contracts. Neither is evidence for production OCR,
ASR, article extraction, model throughput, or ETA accuracy until the public
corpus/device matrix is run. Therefore M5 remains evidence-gated and no public
speed or ETA-accuracy claim is authorised yet.

### M6: Local UI and Feature Review Gate

This is a release gate, not a cosmetic pass. Review the complete local workflow
before any mobile implementation begins:

1. Can a first-time user capture a URL, file, text, or audio attachment without
   opening Advanced settings?
2. Does every accepted input produce a trustworthy receipt and clear next action?
3. Can the user tell what is raw source, transcript, personal input, deterministic
   metadata, and AI suggestion?
4. Can every AI and processing failure fall back to readable local material?
5. Are storage, sync, provider, model, and network destination visible at the
   point where they matter?
6. Can the user search, inspect related notes, and reset learned preferences?
7. Are ETA, confidence range, item counts, completion totals, stage, and elapsed
   time internally consistent, and does the countdown pause truthfully when the
   task needs action or has no measurable total?
8. Does the retro UI remain readable at supported desktop and narrow widths?
9. Are cookie, gated-source, legal-use, and model warnings actionable?
10. Does a clean install work without private developer paths or credentials?
11. Before cloud processing, can the user identify the exact provider, model,
    destination, content leaving the Mac, retention-policy link, and estimated
    usage, then cancel without sending anything?

Required review lanes:

- product review: core workflow and scope;
- design-taste review: hierarchy, readability, and interaction clarity;
- accessibility review: keyboard order, labels, contrast, and dynamic status;
- code review: correctness and maintainability;
- security review: paths, credentials, endpoints, file parsing, and provenance;
- test/release review: regression, packaging, clean-install, and smoke evidence.

Gate: no open P0/P1 defects; P2 defects have an explicit release decision; full
suite, clean-install smoke test, and benchmark report pass.

Current evidence (2026-07-20):

- all 1,207 offline tests pass, including 226 UI tests;
- Ruff, source compilation, diff checks, isolated wheel build/install, CLI/MCP
  entry points, Phase 2 imports, and Gradio app construction pass;
- the optional dependency audit reports no known vulnerabilities, and package
  scans find no private developer paths, credentials, or production secrets;
- independent global, security, and design reviews are `CLEAR` after the
  identified credential, path, redirect, response-size, provenance, and MCP log
  disclosure issues were fixed and regression-tested;
- desktop and narrow-width browser checks show no horizontal overflow, keyboard
  activation works for the reviewed controls, and dynamic run status is exposed;
- local Ollama synthetic processing succeeds with an installed model;
- product review is `WATCH`, not a defect finding: receipt lifecycle, content
  separation, fallback, disclosure, retrieval, preferences, progress, layout,
  and warnings are code- and test-backed, while one first-use end-to-end smoke
  spanning URL, file, text, and audio remains a manual release check;
- live OpenAI, Anthropic, and DeepSeek calls and cancel-before-send checks remain
  untested because no user-owned provider credentials were supplied;
- the complete supported-device/source performance matrix and baseline-versus-
  shadow ETA calibration remain M5 gates, so no public speed or ETA-accuracy
  claim is authorised;
- the current worktree is intentionally uncommitted while Phase 2 is under
  review and therefore is not yet a clean release candidate.

### M7: Phase 3 Readiness Handoff

Deliverables:

- stable local receipt/job protocol for a future mobile client;
- documented mobile threat model and transport options;
- explicit decision on PWA versus native capture after prototype research;
- Phase 3 backlog reviewed against the local product gate.

This milestone documents the handoff only. It does not implement mobile capture.

## Obsidian Folder Contract

```text
<vault>/
  Inbox/          # canonical unreviewed Markdown captures
  Notes/          # reviewed KnowledgeNote derivatives
  Sources/        # existing imported source output
  Index/          # existing/generated indexes
  _attachments/   # secured local media and documents
```

Complete absolute paths, processing diagnostics, private preference state, API
credentials, and provider internals belong in local application state, not
user-facing Markdown or future sync envelopes.

## Review Ownership

Each milestone has separate implementation and verification roles:

- executor owns the bounded code slice;
- test engineer owns behaviour-first coverage;
- code reviewer checks correctness and compatibility after implementation;
- security reviewer checks M1, M2, M4, and future transport boundaries;
- designer reviews M3 and M6 before and after UI changes;
- performance reviewer owns M5 benchmark validity;
- the lead integrates results, runs the full suite, and keeps the worktree clean.

No reviewer approves work they authored.

## Explicit Non-Goals

- Phone or desktop microphone recording in Phase 2.
- Mobile app, PWA, share-sheet, pairing, or sync implementation in Phase 2.
- Automatic organisation without review.
- Hidden preference learning from passive observation.
- Silent cloud fallback or automatic model downloads.
- OpenRouter integration or consumer ChatGPT/Claude session-cookie reuse.
- Automatic cross-provider failover, cloud credentials in plaintext, or cloud
  transmission of a whole vault by default.
- Redis/Celery, vLLM, or a browser engine in the default desktop package.
- Replacing Obsidian in Phase 2.
- Promising Logseq, Anytype, Joplin, or SiYuan adapters before Phase 3 research.

## Phase 2 Release Criteria

Phase 2 is releasable only when:

- capture remains useful with AI and network disabled;
- every supported local input receives a durable receipt or explicit failure
  within ten seconds on the benchmark matrix;
- every derivative is traceable to its original source or attachment;
- failed enrichment preserves secured input and readable raw output;
- users can identify storage and AI destination independently;
- optional OpenAI, Anthropic, and DeepSeek adapters use user-owned API keys,
  task-level consent, current destination/data-handling disclosure, and never
  become a dependency of capture or deterministic conversion;
- API secrets are absent from argv, logs, Markdown, manifests, sync envelopes,
  and ETA history, and can be tested, replaced, and deleted;
- ETA uses structured stage units and privacy-minimised local calibration,
  reports a range/confidence or truthful indeterminate state, and passes the
  documented baseline comparison and calibration gate;
- Voice-ready Review keeps audio, transcript, personal note, and AI content
  distinct;
- preference learning is explicit, inspectable, exportable, and resettable;
- retrieval results include paths and evidence;
- the M6 local product review passes;
- all automated tests, package smoke tests, milestone reviews, and benchmark
  gates pass with a clean git worktree.

## Research References

Cloud/API boundaries:

- [OpenAI: ChatGPT subscriptions cannot be transferred to API
  service](https://help.openai.com/en/articles/8156019-how-can-i-move-my-chatgpt-subscription-to-the-api)
- [OpenAI: ChatGPT and API billing are
  separate](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform)
- [OpenAI developer quickstart and API-key
  handling](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
- [OpenAI API data controls by
  endpoint](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint)
- [OpenAI business/API data is not used for training by
  default](https://openai.com/business-data/)
- [OpenAI Responses API and SSE streaming](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Anthropic: Claude subscriptions and API access are billed
  separately](https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-subscription-pro-max-team-or-enterprise-plans-why-do-i-have-to-pay-separately-to-use-the-claude-api-and-console)
- [Anthropic API overview](https://platform.claude.com/docs/en/api/overview)
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create)
- [Anthropic streaming and cumulative usage](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Anthropic API retention](https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
- [DeepSeek API quickstart and compatible
  transports](https://api-docs.deepseek.com/)
- [DeepSeek API reference](https://api-docs.deepseek.com/api/deepseek-api)
- [DeepSeek chat completion streaming and usage](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek privacy policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html)
- [DeepSeek Open Platform terms](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)

Local inference and scheduling:

- [Ollama generate API: keep-alive and timing
  telemetry](https://docs.ollama.com/api/generate)
- [Ollama running-model state](https://docs.ollama.com/api/ps)
- [Ollama usage metrics](https://docs.ollama.com/api/usage)
- [Ollama NDJSON streaming](https://docs.ollama.com/api/streaming)
- [Ollama FAQ: local operation and disabling cloud
  features](https://docs.ollama.com/faq)
- [LM Studio local server](https://lmstudio.ai/docs/developer/core/server)
- [llama.cpp OpenAI-compatible server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [MLX-LM](https://github.com/ml-explore/mlx-lm)

Durability, extraction, and transcription:

- [SQLite write-ahead logging](https://sqlite.org/wal.html)
- [SQLite atomic commit](https://sqlite.org/atomiccommit.html)
- [Apple File System overview](https://developer.apple.com/documentation/foundation/about-apple-file-system)
- [Paperless-ngx asynchronous task-processing
  pattern](https://docs.paperless-ngx.com/usage/)
- [Trafilatura](https://github.com/adbar/trafilatura)
- [Mozilla Readability](https://github.com/mozilla/readability)
- [Defuddle](https://github.com/kepano/defuddle)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [Silero VAD](https://github.com/snakers4/silero-vad)

Progress and ETA:

- [tqdm rate smoothing and ETA fields](https://github.com/tqdm/tqdm)
- [yt-dlp progress hooks and download ETA](https://github.com/yt-dlp/yt-dlp)
- [faster-whisper duration, VAD, and work-unit
  implementation](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- [XGBoost accelerated-failure-time survival
  research](https://xgboost.readthedocs.io/en/latest/tutorials/aft_survival_analysis.html)
- [scikit-learn quantile histogram gradient
  boosting](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)
- [MAPIE conformalised quantile research](https://mapie.readthedocs.io/en/stable/generated/mapie.regression.ConformalizedQuantileRegressor.html)
