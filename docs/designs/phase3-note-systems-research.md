# Phase 3 Research and Backlog

## Purpose

Phase 3 contains two research tracks that must remain separate until each has an
approved implementation plan:

1. a lightweight mobile capture companion;
2. interoperability research for adjacent note systems.

This document is a backlog and research brief. It does not promise a mobile app,
integration, migration, or implementation path.

## Entry Gate

Phase 3 mobile implementation must not begin until the Phase 2 local product
review confirms that the Mac receiver, durable Context Receipt, Voice-ready
Review, fallbacks, privacy disclosure, packaging, and performance baseline are
stable.

## Mobile Capture Backlog

### Product promise

The future mobile companion should be a fast capture endpoint, not a smaller
copy of the desktop converter:

> From a supported mobile capture action, OMD either confirms a durable handoff
> within ten seconds or clearly explains what remains only on the phone.

The ten-second target covers durable handoff, not remote downloading,
transcription, OCR, or AI enrichment. Heavy work remains on the paired Mac in the
first mobile version.

### Candidate capture surfaces

- URL and share-sheet text;
- user-authored note and highlight;
- voice recording and existing audio attachment;
- lightweight file or image attachment where platform permissions allow it;
- offline queue with visible local-only, waiting, transferred, failed, and
  acknowledged states.

### Required research before choosing PWA or native

- share-sheet and microphone support on iOS and Android;
- reliable background transfer and offline queue behaviour;
- local file durability before transfer;
- notification and retry behaviour when the Mac is unavailable;
- iCloud folder, local-network, and other pairing/transport options;
- QR or device-code pairing, key rotation, and device removal;
- attachment size limits, compression, battery use, and metered networks;
- accessibility and a first-success path under ten seconds;
- app-distribution, signing, privacy disclosure, and maintenance cost.

### Required protocol properties

- immutable, versioned, idempotent jobs;
- authenticated pairing and per-device revocation;
- no cookies, model credentials, desktop absolute paths, or command lines in
  mobile envelopes;
- atomic writes and an explicit durable-handoff acknowledgement;
- content hash, duplicate handling, bounded retry, and replay protection;
- separate source, user note, transcript, and AI-derived content;
- lossless fallback that leaves the phone's local capture readable when transfer
  fails.

### Explicit mobile non-goals for the first slice

- running full Whisper, video download, embeddings, or an LLM on the phone;
- duplicating the desktop Advanced settings UI;
- automatic capture without a user action;
- claiming processing completion inside the ten-second handoff target;
- cloud processing by default.

### Mobile implementation sequence to revisit in Phase 3

1. Finalise the mobile threat model, receipt protocol, authenticated pairing,
   key rotation, revocation, and conflict/replay behaviour.
2. Test a throwaway PWA and native share-sheet spike against that same protocol;
   choose based on evidence, not assumed portability.
3. Ship text/URL/highlight capture with pairing, an offline queue, and durable
   acknowledgements as the first limited beta.
4. Add voice recording as the second mobile slice after the core handoff is
   reliable, then expand attachment handling incrementally.
5. Consider on-device preprocessing only when measurements show a clear user
   benefit without weakening reliability or privacy.

## Note Systems Research

Obsidian remains the primary note visualisation system in Phase 2. Phase 3 may
evaluate adjacent systems to inform adapter criteria and future interoperability
decisions.

The systems currently in scope are:

- Logseq
- Anytype
- Joplin
- SiYuan

### Research goal

The research should establish which external note systems are worth adapting to,
and under what conditions an adapter would make sense. It should answer:

- What each system does well.
- What each system makes difficult.
- How content is stored and represented.
- Whether exported or imported content stays portable.
- Whether the system supports an inbox-first personal context workflow.

### Comparison lens

Evaluate each system using the same lens:

- local-first behaviour;
- Markdown compatibility;
- capture speed;
- organisation model;
- retrieval usefulness;
- sync expectations;
- extensibility;
- data portability;
- fit for review-gated personal context workflows.

### Logseq

Research focus:

- outline-first and block-based capture;
- graph-driven context retrieval;
- Markdown/org-mode compatibility and the DB-version transition.

Questions:

- Does the block model improve or hinder fast Inbox capture?
- How stable is content portability across file and DB versions?
- What adapter shape would preserve provenance and review decisions?

### Anytype

Research focus:

- object-oriented knowledge organisation;
- local-first and sync-centred workflow;
- relation-heavy content model.

Questions:

- Is the object model a good fit for Inbox-first capture?
- How portable is the underlying content?
- Would an adapter need to map notes, objects, or both?

### Joplin

Research focus:

- Markdown note storage;
- sync flexibility;
- mature capture and organisation patterns.

Questions:

- Is Joplin close enough to the Phase 2 storage model to reduce adapter
  complexity?
- What export or import surfaces matter most?
- Does the note model support preference-aware retrieval cleanly?

### SiYuan

Research focus:

- structured note management;
- block and document interactions;
- sync and local-first behaviour.

Questions:

- How does SiYuan balance structure and capture speed?
- How much transformation would an adapter require?
- Does the model support review-gated enrichment without fighting the UI?

## Adapter Criteria

An adapter is worth considering only if most of the following are true:

- content can be moved without losing user meaning;
- the target system has a stable export or import path;
- the note model preserves plain-text readability or a durable equivalent;
- capture can remain fast enough for Inbox-first use;
- retrieval benefits from the system's structure;
- sync behaviour does not erase the canonical content model;
- the adapter does not force OMD to become a second note editor;
- implementation demand is supported by user evidence.

## Research Outputs

- a short strengths and limits summary for each system;
- an adapter-fit assessment and portability risks;
- a recommendation for which systems deserve deeper compatibility work;
- a PWA-versus-native mobile capture decision backed by prototypes;
- a mobile transport threat model and receipt-protocol test report;
- a list of incompatibilities and mobile features that should stay outside scope.

## Decision Boundaries

- Research may compare systems, transports, and mobile shells.
- Research may recommend criteria and prototypes.
- Research may not promise a future integration or migration path.
- Research may not begin mobile implementation before the Phase 2 entry gate.
- Research may not imply that any note system or mobile technology is already the
  chosen target.

## Open Questions

- Which mobile shell can acknowledge a durable capture fastest and most reliably
  under real iOS/Android background restrictions?
- Which transport works when the Mac is asleep or unavailable without turning
  OMD into a cloud service?
- Which note model best preserves fast capture without losing retrieval power?
- Which system offers the cleanest adapter boundary for future work?
- Which portability constraints matter most for long-term personal knowledge
  storage?
