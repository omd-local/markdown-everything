# Phase 2 Benchmark Gate

OMD does not publish a ten-second or ETA-accuracy claim from developer intuition.
The first executable gate measures the foreground durable Context Receipt path
with synthetic inputs only:

```bash
PYTHONPATH=. python -m omd.phase2_benchmark \
  --iterations 30 \
  --output /tmp/omd-phase2-receipt-report.json
```

The default corpus is versioned as `phase2-synthetic-v1` and includes authored
note receipts, URL job receipts, and 1 KiB / 1 MiB local-file receipt staging.
The JSON report stores a coarse device tier, sample counts, p50/p95/max duration,
targets, the executing OMD version, and pass/fail only. It never reads a vault or
stores source text, URLs, filenames, temporary paths, prompts, responses,
cookies, credentials, or exact machine identity.

## Claim Boundary

This benchmark proves only the local foreground receipt path on the machine that
produced the report. It does not prove download, conversion, OCR, transcription,
or AI completion latency. It also does not replace the Phase 2 device/source
matrix or ETA calibration gate.

Historical ETA ranges stay collection-only until the current pipeline version
has at least 30 comparable successful observations and an explicit calibration
gate. The gate records a privacy-safe benchmark token and sample count, requires
at least a 10 percent improvement over the frozen baseline median error, and
requires p90 coverage of at least 90 percent. Passing this receipt benchmark
does not create that gate.

Before a public performance claim, archive reports for the documented Apple
Silicon and fallback matrix, use at least 30 comparable successful observations
per claimed ETA bucket, compare the stage-aware estimator with the frozen
baseline, and record output-quality and memory regressions separately. Sparse
buckets remain low confidence or indeterminate.

## Baseline-versus-shadow ETA calibration

When local ETA timing collection is enabled, OMD can keep a second bounded file
of privacy-safe comparison samples. A sample is created only after a stage has
run for at least five seconds, completed at least 10 percent of real work, and a
mature shadow-history bucket can return p50/p90 values. It contains stage/source
tokens, a coarse device tier, runtime/model identity, work unit, cold/warm state,
the frozen linear baseline, shadow p50/p90, and actual duration. It contains no
source text, URL, hostname, filename, path, prompt, response, cookie, or key.

Evaluate and optionally apply an eligible calibration gate with:

```bash
PYTHONPATH=. python -m omd.eta_calibration \
  --input /path/to/eta-calibration-samples.json \
  --output /tmp/omd-eta-calibration-report.json \
  --history /path/to/eta-history.json \
  --apply
```

The command exits non-zero when the corpus is not eligible. Eligibility requires
at least 30 comparable samples in one duration regime, at least 10 percent lower
median error than the baseline, and at least 90 percent p90 upper-bound coverage.
Applying a gate changes only whether an already-mature local history range may be
shown; it does not change a converter, model, or scheduler.

## Article, ASR, and VAD candidates

`omd.candidate_benchmark` is an opt-in maintainer harness for explicit local,
versioned fixtures and caller-supplied candidate adapters. It does not discover
a vault, make network requests, install packages, download models, or change the
production backend. Missing runtimes are reported as `skipped`; VAD candidates
are excluded unless `include_vad=True` is explicitly passed.

The report keeps only safe case/candidate tokens, coarse device tier, timing,
work units, process peak memory, speech-retention ratio where applicable, and
quality pass/fail counts. Candidate text and fixture paths are never copied into
the report. Article candidates must retain fixture-required title/body terms and
meet the 10-second p95 engineering target. ASR candidates must retain fixture
terms. VAD candidates must also stay within fixture-defined speech-retention
bounds so a faster but speech-clipping path fails the quality gate.

This harness provides the executable contract, not benchmark evidence by itself.
Default CI uses deterministic fake candidates. A production-default change still
requires archived runs on the documented public corpus/device matrix, comparison
against the current backend, no output-quality regression, and a tested fallback.
