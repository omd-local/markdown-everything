# Phase 1 Execution Plan: Obsidian Vault-Compatible Local AI Memory

> Source briefs: `OMD_monetisation_GTM_implementation_CN.md`, `deep-research-report.md`
> Scope: Engineering Phase 1 executable plan only. This is not the full commercial MVP plan.
> Positioning constraint: OMD is **Anything -> Markdown -> Local AI Memory**, not a generic document converter.
> Execution rule: this document defines work. Do not treat later phases as in-scope unless explicitly promoted.

---

## 0. 原文 Phase 1（逐字保留）

### Phase 1：Obsidian vault-compatible export，中等复杂度

目标：不用写 Obsidian 插件，先让 OMD 输出直接适配 Obsidian vault。

新增命令建议：

```bash
omd capture "https://youtube.com/..." --vault ~/Obsidian/AI-Memory
omd capture report.pdf --vault ~/Obsidian/AI-Memory --tags research,pdf
omd capture ~/Downloads/sources/ --vault ~/Obsidian/AI-Memory --batch
```

输出路径建议：

```text
AI-Memory/
  Sources/
    YouTube/
    Podcasts/
    PDFs/
    Images/
    Web/
    Xiaohongshu/
  Index/
    OMD Captures.md
  _attachments/
```

任务：

- [ ] 增加 `capture` 子命令，或给现有 CLI 增加 `--vault`
- [ ] 自动生成安全文件名：日期 + source type + title slug
- [ ] 添加 YAML frontmatter
- [ ] 添加 `source_url`、`captured_at`、`source_type`、`tags`
- [ ] 生成 `Index/OMD Captures.md`
- [ ] 新增 Obsidian recipe 文档

验收标准：

- 用户可以把任意支持输入转入 Obsidian vault。
- Obsidian 可以按 tag/source/date 检索。
- Claude/Cursor 也可以直接读取这个 vault 文件夹。

预计时间：3–7 天。

复杂度：中等。

---

## 0.5 全局产品对齐契约

Phase 1 的任何实现和文档都必须同时满足下面这些约束。这些约束来自中文 GTM 计划和 deep research report 的共同结论。

### 0.5.1 Positioning

OMD 的定位是：

> **Anything -> Markdown -> Local AI Memory**

或更完整地说：

> **the local-first, multilingual, workflow-aware Markdown intake layer for AI tools and knowledge systems**

实现含义：

- 不把 OMD 写成 generic converter。
- 不把 OMD 写成 hosted parser API。
- 不把 OMD 写成默认 AI summarizer。
- Capture / vault / MCP / local AI memory 是产品主线。
- MarkItDown、Docling、LlamaParse 是底层/竞品参照，不是 OMD 要正面复制的定位。

### 0.5.2 No-LLM Default

核心转换默认不需要 LLM。

实现含义：

- `omd <input> -o out.md` 不默认调用 LLM。
- `omd capture <input> --vault <path>` 不默认调用 LLM。
- 文档、网页、图片 OCR、音频/视频 transcript 的 raw output 必须保留。
- LLM 生成内容不得替代 raw content。
- 没有 Ollama 或本地模型时，核心转换和 Phase 1 capture 仍必须可用。

### 0.5.3 Optional Local Model Strategy

本地模型是增强层，不是默认依赖。

两份源文档的模型建议需要按层级执行：

- **Phase 1 shipped-compatible defaults**：保持当前 repo / GTM 文档已经采用的 `qwen3:4b`、`gemma3:4b`、`bge-m3`，避免 writer、executor、tests 在同一阶段互相改默认值。
- **Phase 2 model refresh candidates**：根据 deep research 的 2026 桌面本地模型建议，把 `qwen3.5:4b` / `qwen3.5:9b`、`gemma3n:e2b` / `gemma3n:e4b`、`gemma3:4b` 纳入 memory cards / desktop app ADR。
- **No-LLM invariant**：无论模型推荐如何变化，核心 conversion / capture 不能依赖模型，也不能静默下载模型。

Phase 1 推荐模型：

```text
Text polish / summary / memory cards: qwen3:4b
Vision-aware cleanup / OCR enhancement: gemma3:4b
Local multilingual embedding search: bge-m3
```

实现含义：

- 当用户显式启用 `--polish`、`--polish-md`、未来 `--memory-cards` 且未指定模型时，默认文本模型应为 `qwen3:4b`。
- `gemma3:4b` 用于 vision/OCR enhancement，不作为普通文本 polish 的默认值。
- `bge-m3` 只用于后续 embedding/search，不进入 Phase 1 必需实现。
- 不允许后台静默下载模型；模型安装必须是用户显式动作，例如 `ollama pull qwen3:4b`。
- 远程 Ollama host 是显式 opt-in；默认文案不能暗示会上传到云端。
- 不允许 executor 在 Phase 1 直接把默认模型改成 `qwen3.5:*` 或 `gemma3n:*`；如果要升级默认值，先写 ADR，更新 README / privacy docs / tests / CLI help，再改实现。

### 0.5.4 Privacy Wording

允许说：

- local-first
- no cloud upload required for local CLI workflows
- core conversion does not require an LLM
- optional local Ollama polish

不允许说：

- absolutely secure
- always offline
- never uses network
- private for public demo uploads

原因：URL capture 需要访问源网站；HF/public demo 不是私密工作流；远程 Ollama endpoint 是用户显式配置后的外部发送。

### 0.5.5 ICP / GTM Fit

Phase 1 先服务：

- Obsidian / Markdown vault 用户
- local AI / privacy-first 用户
- developer / AI builder
- researchers / analysts / creators

Phase 1 不优先服务：

- 企业权限/审计/SSO
- hosted SaaS parser API
- Windows-first office deployment
- broad Microsoft 365 / Google Workspace / Slack / Notion connector suite

### 0.5.6 Implementation Gate

任何 agent 完成工作前必须检查：

- README、docs、CLI help、frontmatter、tests 的模型默认值是否一致。
- No-LLM 默认路径是否被测试覆盖。
- Optional LLM 路径是否清楚记录 `llm_used`。
- Capture 输出是否保持 Obsidian-compatible plain Markdown。
- 文案是否仍然强调 local AI memory，而不是 generic conversion。

### 0.5.7 Phase Boundary

本文件定义的是 **Engineering Phase 1: vault-compatible capture export**。

不要把它和完整商业化 MVP 混成一个范围：

- Phase 0 包装工作：README、demo GIF、sample workflows、public demo copy。
- Engineering Phase 1：`capture`、vault layout、frontmatter、index、batch capture、Obsidian/AI-tool readability。
- Phase 2：memory cards、summary/tags generation、drift warning、model refresh ADR（评估 `qwen3.5:*` / `gemma3n:*` / `gemma3:4b`）。
- Phase 3：MCP memory tools、search/read/list recent、vault-root security ADR。

执行含义：

- Demo / HF copy 是 GTM support task，不阻塞 Engineering Phase 1 完成。
- `.omd.json` manifest 是工程实现 invariant，不是用户侧主卖点。
- 轻量反馈采集不等于产品内 telemetry；Phase 1 不加 in-product telemetry，先用 GitHub Discussions、demo page feedback、manual user interview 替代。

### 0.5.8 Current Repository Reality Check

本计划不是从零开始。执行前必须先做 gap audit，而不是重复设计已有内容。

当前已存在或部分存在：

- `capture` 命令。
- capture frontmatter / manifest / index 基础实现。
- README repositioning。
- Obsidian / privacy / positioning docs。
- capture tests。

执行前必须确认：

- 哪些 Phase 1 条目已经完成。
- 哪些条目只完成了 Phase 1A，仍缺 Phase 1B/1C。
- 当前实现是否与本文件最新契约冲突。
- 冲突项先进入 ADR，不直接让 executor 猜。

---

## 1. Phase 1 修订目标

Phase 1 的目标不是“又加一个输出目录参数”。Phase 1 要把 OMD 的产品对象从“conversion output”推进到“capture record”。

Phase 1 完成后，用户应该可以执行：

```bash
omd capture <url-or-file> --vault ~/Obsidian/AI-Memory
```

并得到一个可长期保存、可被 Obsidian 和 AI 工具读取的本地 Markdown capture record。

Phase 1 的核心承诺：

- Capture 是一等对象。
- 输出是本地 Markdown，不强制云端。
- 核心转换不依赖 LLM。
- Obsidian 是第一目标 vault，但输出必须保持普通文件夹兼容。
- Claude Code、Cursor、Codex、ChatGPT 粘贴上下文、RAG pipeline 都应该能消费这些 Markdown。

---

## 2. Phase 1 分层

### Phase 1A：Single Capture MVP

目标：单个 URL 或文件可以写入 vault。

范围：

- `omd capture <input> --vault <path>`
- 自动生成 vault 输出路径。
- 添加 YAML frontmatter。
- 写入 sidecar manifest。
- 更新 `Index/OMD Captures.md`。
- 重复 capture 不覆盖已有文件。

不做：

- 不做目录 capture。
- 不做 memory cards。
- 不做 MCP memory search。
- 不做 embedding。
- 不做 Obsidian plugin。

### Phase 1B：Batch / Folder Capture

目标：覆盖原文中的：

```bash
omd capture ~/Downloads/sources/ --vault ~/Obsidian/AI-Memory --batch
```

Canonical command decision:

- Preferred canonical UX: `omd capture <folder-or-list> --vault <path> --batch`.
- ADR result: Phase 1 keeps `omd capture <folder-or-list> --vault <path> --batch` as the canonical public vault-capture UX.
- `omd batch` remains the plain conversion batch path, not the first-class vault capture path.
- Docs and examples must promote `capture --batch` for capture records.
- If a future ADR chooses `omd batch --vault`, this document must be revised because it would intentionally diverge from the source brief example.
- Do not implement both as first-class independent workflows in the same pass.

范围：

- `omd capture <folder> --vault <path> --batch`
- `omd capture <batch-list.txt> --vault <path> --batch`
- 复用现有 batch/watch 转换能力。
- 每个 item 仍生成独立 capture record。
- index 记录每条 capture。
- partial failure 不阻塞其他 item。

不做：

- 不引入并发下载。
- 不做长任务队列 UI。
- 不做 cloud batch API。

### Phase 1C：Attachments / Media Policy

目标：明确 `_attachments/` 的边界。

范围：

- 创建 `_attachments/` 目录作为 vault 标准结构的一部分。
- 文档说明第一版不保证镜像所有远端图片/音频/视频资源。
- 后续如需保存附件，必须有清晰命名、manifest 关联和大小限制。

不做：

- 不默认下载所有远端图片。
- 不做公共批量抓取。
- 不承诺离线完整网页归档。

---

## 3. Capture Record v1 Schema

### 3.1 Phase 1 必填 frontmatter

Phase 1 必须写入这些字段：

```yaml
---
omd_version: "0.2.0"
source_type: "youtube | podcast | pdf | image | xiaohongshu | douyin | webpage | office_doc | audio | other"
source_url: "https://..."
local_source_path: "/absolute/or/user/path"
captured_at: "2026-07-04T12:00:00Z"
title: "..."
privacy: "local"
storage: "local"
network_fetch: true
model_endpoint: "none | local_ollama | remote_ollama"
llm_used: "none"
tags:
  - "omd"
  - "ai-memory"
  - "source/youtube"
---
```

Rules:

- `source_url` is present for URL-backed captures.
- `local_source_path` is present for file-backed captures.
- `privacy: local` is a legacy/user-facing shorthand for local storage, not a claim that the entire workflow was offline.
- `storage` is always `local` for local CLI capture.
- `network_fetch` is `true` for URL-backed captures and `false` for local file captures.
- `model_endpoint` is `none` unless the user explicitly enables local or remote model post-processing.
- `llm_used` is `none` unless the user explicitly enables local polish.
- `tags` always include `omd`, `ai-memory`, and `source/<source_type>`.
- timestamps are ISO-8601 with timezone.

### 3.2 Phase 1 should add if available

These fields should be included when the backend can provide them without LLM generation:

```yaml
author: "..."
language: "zh | en | mixed | unknown"
transcript: "raw | polished | none"
detected_type: "..."
```

Rules:

- Do not fake `author`.
- Do not infer `language` with an LLM in Phase 1.
- `transcript` is metadata about existing transcript sections, not a request to generate one.

### 3.3 Phase 2 reserved fields

These are explicitly reserved for memory cards and should not be required in Phase 1:

```yaml
memory_cards: true
memory_model: "qwen3:4b"
summary_generated: true
vision_model: "gemma3:4b"
embedding_model: "bge-m3"
```

---

## 4. Capture Note Body Template

Phase 1 should standardize body sections even when some sections are empty.

Recommended template:

```markdown
# Title

## Source
- URL:
- Local file:
- Captured at:
- Type:
- Tags:

## Summary
Not generated in Phase 1 unless explicitly enabled.

## Key Points
Not generated in Phase 1 unless explicitly enabled.

## Actionable Notes
Not generated in Phase 1 unless explicitly enabled.

## Open Questions
Not generated in Phase 1 unless explicitly enabled.

## Full Content
<raw converted Markdown content>

## Transcript Raw
<raw transcript if present>

## Transcript Polished
<polished transcript if present and explicitly requested>
```

Rules:

- Phase 1 must preserve raw/full content.
- Generated sections must never replace raw content.
- Empty generated sections should be honest, not pretend model output exists.
- Future memory cards append after raw content or in a clearly marked generated section.

---

## 5. Vault Layout

Required structure:

```text
AI-Memory/
  Sources/
    YouTube/
    Podcasts/
    PDFs/
    Images/
    Web/
    Xiaohongshu/
    Douyin/
    Audio/
    Documents/
  Index/
    OMD Captures.md
  _attachments/
```

Rules:

- `Sources/` contains generated capture notes.
- `Index/OMD Captures.md` is a human-readable index, not the only source of truth.
- `_attachments/` is the canonical future media/asset mirroring directory.
- Phase 1 creates or documents `_attachments/`, but does not mirror remote assets into it by default.
- User-owned attachment folders such as `Attachments/` may coexist, but OMD-generated attachments must use `_attachments/` unless a future ADR changes it.
- Sidecar `.omd.json` manifests remain next to generated Markdown files.
- Avoid hiding important metadata only in JSON; frontmatter is the user-facing metadata layer.

---

## 6. Filename Rules

Filename format:

```text
YYYY-MM-DD-source_type-title-slug-short-hash.md
```

Examples:

```text
2026-07-04-youtube-local-ai-memory-demo-a1b2c3d4.md
2026-07-04-pdf-quarterly-report-9f8e7d6c.md
2026-07-04-xiaohongshu-note-title-12ab34cd.md
```

Rules:

- Use a safe slug.
- Include date.
- Include source type.
- Include a short stable hash from source URL/path.
- If a file already exists, create `-2`, `-3`, etc. rather than overwriting.
- Do not silently overwrite user-visible notes.

---

## 7. Agent Work Scope

### `planner`

Owns:

- Convert this document into ordered tasks.
- Maintain `Now / Next / Later / Not Now`.
- Keep Phase 1 from expanding into Phase 2/3/4.

Outputs:

- Updated execution checklist.
- Dependency graph.
- Open questions that block implementation.

Does not own:

- Code implementation.
- GTM copy.
- Security approval.

### `schema-owner`

Owns:

- Capture Record v1 schema.
- Frontmatter field definitions.
- Body template.
- Phase 1 vs Phase 2 field boundary.

Outputs:

- Schema section in docs.
- Testable examples for URL, PDF, image, audio/video.

Does not own:

- CLI parser.
- MCP tools.

### `executor-capture`

Owns:

- `omd capture`.
- Vault output path.
- File naming.
- Frontmatter writing.
- Manifest refresh.
- Index update.
- Batch capture in Phase 1B.

Rules:

- Reuse existing `route_one`, preflight, manifest, batch helpers.
- Do not reimplement converters.
- Do not touch unrelated dirty files.

Does not own:

- Memory cards.
- MCP search.
- Desktop UI.

### `test-engineer`

Owns tests for:

- CLI dispatch.
- Single capture.
- Batch/folder capture.
- Vault layout.
- Filename safety.
- Frontmatter fields.
- Body template.
- Index generation.
- Repeated capture no-overwrite behavior.
- Manifest preservation.
- Source type classification.

Must run:

```bash
.venv/bin/python -m pytest tests/ -q
```

### `compatibility-reviewer`

Owns:

- Obsidian readability.
- Claude Code/Cursor/Codex project-context readability.
- ChatGPT copy/paste usability.
- RAG pipeline friendliness.

Checks:

- Markdown has clear headings.
- Metadata is in YAML frontmatter.
- Raw content remains available.
- Generated sections are clearly marked.
- No Obsidian-only syntax is required for basic readability.

### `security-reviewer`

Owns:

- Local-first claims.
- Path write boundaries.
- Agent-safe compatibility.
- Manifest handling.
- No silent overwrite.
- No default cloud upload.

Checks:

- Privacy wording matches actual behavior.
- URL routes correctly disclose network fetches.
- Remote Ollama is explicit opt-in.
- Future MCP tools reuse allowed-root boundaries.

### `risk-reviewer`

Owns product and market risks:

- Positioning too broad.
- Accidental comparison as “better parser than MarkItDown”.
- Local install friction.
- Video/social platform policy wording.
- Privacy overclaiming.
- Public demo sensitive-file warning.

Outputs:

- Risk checklist with required wording fixes.

### `verifier`

Owns:

- Checking the previous agent’s output before the next phase starts.

Verifier gates:

1. Planner output matches source brief.
2. Schema is complete enough for Phase 1 and honest about Phase 2.
3. Executor implemented only Phase 1 scope.
4. Tests cover the acceptance criteria.
5. Docs match actual CLI behavior.
6. Security/risk reviewers found no blocking mismatch.

Verifier does not own:

- Direct implementation, unless explicitly reassigned to fix lane.

---

## 7.5 Native Subagent Execution Topology

Do not map every role in Section 7 to a separate Codex native subagent. Those roles describe responsibilities; native subagents should be fewer, larger, and bounded by write-set.

Recommended execution shape: **4 lanes, max 5 active agents**.

| Lane | Native role | Purpose | Write scope | Parallelism |
|---|---|---|---|---|
| Gap audit / contract lock | `explore` or `architect` | Compare repo reality against this plan; lock command contract ADR before implementation. | Read-only, or this plan document only. | Runs first. Blocks implementation. |
| Capture implementation | `executor` | Implement or complete `capture`, vault paths, schema/frontmatter, index, manifest, batch capture. | `omd/cli.py`, `omd/capture.py`, possibly `omd/batch.py`, `omd/_manifest.py`. | Single owner. Do not split Step 3/4 across multiple executors. |
| Tests | `test-engineer` | Add focused regression tests and run focused suites. | `tests/test_capture.py`, batch/routing/security tests as needed. | Can start after command contract is locked; final assertions depend on executor output. |
| Docs / recipes | `writer` | Align README, Obsidian, privacy, examples, demo copy. | `README.md`, `docs/obsidian.md`, `docs/privacy.md`, `docs/positioning.md`, `examples/`. | Starts after CLI contract and schema are stable. |
| Final gate | `verifier` plus optional `security-reviewer` | Read-only proof that implementation, docs, tests, and privacy claims match. | Read-only unless explicitly reassigned to fix a narrow issue. | Runs last. |

Rules:

- Step 3 single capture and Step 4 batch capture are not independent implementation lanes; they touch the same command parser, capture helper, schema, and tests.
- Review-only roles (`compatibility-reviewer`, `risk-reviewer`, `brand-consistency-verifier`) should normally be folded into final `verifier` prompts unless a specific risk is blocking.
- `writer` must not document unfinished behavior as shipped.
- If a code lane and docs lane conflict, code/CLI help/test output is the source of truth until the plan is revised.
- If current worktree has unrelated dirty files, agents must not revert them.

### 7.5.1 Handoff Prompts

Use these bounded prompts when launching native subagents.

Gap audit prompt:

```text
Read the supplied `deep-research-report.md`, `OMD_monetisation_GTM_implementation_CN.md`, and `docs/phase1-local-ai-memory-execution-plan.md`. Inspect current repo implementation for capture/vault docs/tests. Return: completed items, missing items, command-contract conflicts, and files likely touched. Do not edit.
```

Executor prompt:

```text
Implement only the locked Engineering Phase 1 capture contract. Own omd/cli.py, omd/capture.py, and any explicitly needed helper files. Preserve No-LLM default, raw content, local-first privacy metadata, safe filenames, no overwrite, index updates, manifest refresh, and the chosen batch command. Do not touch docs or unrelated dirty files.
```

Test engineer prompt:

```text
After the command contract is locked, add focused tests for single capture, batch capture, frontmatter privacy fields, model endpoint recording, no-overwrite behavior, index updates, manifest consistency, and agent-safe rejection. Run focused tests first, then full tests.
```

Writer prompt:

```text
After implementation and tests stabilize, update README/docs/examples only. Keep positioning as Anything -> Markdown -> Local AI Memory. Explain No-LLM default, optional qwen3:4b/gemma3:4b/bge-m3 model strategy, public URL network fetches, public demo sensitivity warning, and the exact shipped capture/batch command.
```

Verifier prompt:

```text
Read-only final gate. Verify source briefs, plan, implementation, tests, CLI help, generated sample vault, docs, and privacy claims. Return blocking mismatches first. Confirm whether Engineering Phase 1 can be marked complete, or list exact fix lanes.
```

---

## 8. Execution Order

### Step 0：Gap Audit And ADR Lock

Agent: `explore` or `architect`

Tasks:

- Compare this plan against current repo reality.
- Mark existing Phase 0 / Phase 1A work as already done, partial, or missing.
- Lock the command-contract ADR for batch capture:
  - canonical target `omd capture <folder-or-list> --vault <path> --batch`, or
  - explicit alternate `omd batch --vault <path>` with plan revision.
- Lock privacy metadata semantics:
  - `privacy`
  - `storage`
  - `network_fetch`
  - `model_endpoint`
  - `llm_used`
- Lock `_attachments/` as canonical OMD-generated attachment namespace.

Verifier gate:

- No code implementation starts until command contract, privacy metadata, and attachment namespace are locked.

### Step 1：Plan Lock

Agent: `planner`

Tasks:

- Confirm Phase 1A/1B/1C boundaries.
- Confirm `capture` is a subcommand, not a global `--vault` on every command.
- Confirm batch capture is Phase 1B, not silently omitted.
- Confirm `_attachments/` policy.

Verifier gate:

- `verifier` checks this document against original Phase 1 and confirms no original item was dropped.

### Step 2：Schema Lock

Agent: `schema-owner`

Tasks:

- Finalize frontmatter required fields.
- Finalize optional backend-derived fields.
- Finalize body template.
- Define examples for URL, PDF, image, podcast/video.

Verifier gate:

- `compatibility-reviewer` checks schema against Obsidian, ChatGPT, Codex/Cursor, and RAG pipeline use.

### Step 3：Single Capture Implementation

Agent: `executor-capture`

Tasks:

- Implement `omd capture <input> --vault <path>`.
- Write single-source output under `Sources/<source type>/`.
- Write frontmatter.
- Preserve raw converted content.
- Refresh manifest.
- Update index.
- Avoid overwrite on repeated capture.

Verifier gate:

- `test-engineer` runs focused tests.
- `verifier` confirms no Phase 2/3 behavior slipped in.

### Step 4：Batch Capture Implementation

Agent: `executor-capture`

Tasks:

- Implement `--batch` for folder and list inputs.
- Reuse existing batch behavior.
- Continue after partial failures.
- Write one capture note per successful item.
- Record failures in CLI output and/or events.

Verifier gate:

- `test-engineer` covers folder/list batch.
- `verifier` confirms this satisfies original Phase 1 command example.

### Step 5：Docs and Recipes

Agent: `writer`

Tasks:

- Update README with Phase 1 capture workflow.
- Add Obsidian recipe.
- Add PDF-to-vault recipe.
- Add video/podcast-to-vault recipe.
- Add developer/RAG workflow recipe.
- Explain directory capture via `--batch`.
- Explain what is local and what uses network.

Verifier gate:

- `risk-reviewer` checks for overclaiming.
- `security-reviewer` checks privacy claims.

### Step 6：Demo / Assets Handoff

Agent: `demo-assets-agent`

Status: GTM support task. This does not block Engineering Phase 1 completion.

Tasks:

- Define demo GIF script: URL/PDF/video -> Markdown -> Obsidian/Codex.
- Define before/after screenshot.
- Update Hugging Face demo copy to say public demo is not for sensitive files.

Verifier gate:

- `brand-consistency-verifier` checks demo wording against README positioning.
- Demo copy must include public-demo sensitive-file warning before any public launch.

---

## 9. Phase 1 Test Matrix

Required tests:

- `capture` CLI help parses.
- `capture` rejects unsupported input clearly.
- chosen batch command help parses.
- if `capture --batch` is canonical, directory/list input is accepted only with `--batch`.
- Single PDF capture writes to `Sources/PDFs/`.
- Single image capture writes to `Sources/Images/`.
- YouTube URL maps to `Sources/YouTube/`.
- Xiaohongshu URL maps to `Sources/Xiaohongshu/`.
- Safe filename includes date, source type, slug, short hash.
- Repeated same input creates a distinct note.
- YAML frontmatter includes required fields.
- YAML frontmatter separates local storage from network/model behavior:
  - `storage`
  - `network_fetch`
  - `model_endpoint`
  - `llm_used`
- Optional fields are present only when available.
- Body template preserves raw content.
- Index file is created and updated.
- Manifest sidecar is refreshed and checksum matches final Markdown.
- Batch folder capture writes multiple notes.
- Batch list capture continues on partial failure.
- Agent-safe mode does not allow unsafe flags.
- Sample captured note can be pasted into ChatGPT/Claude/Codex and still preserve title, source metadata, tags, and raw content headings.
- Sample captured note can be ingested by a simple RAG loader without Obsidian-specific syntax.
- Full test suite passes.

Required command:

```bash
.venv/bin/python -m pytest tests/ -q
```

---

## 10. Phase 1 Documentation Checklist

README must show:

- `Anything -> Markdown -> Local AI Memory`
- `omd capture <url-or-file> --vault ~/Obsidian/AI-Memory`
- direct converter command still works
- MCP server exists
- local-first privacy model
- LLM optional
- supported inputs
- recipes

Obsidian docs must show:

- vault layout
- frontmatter example
- repeated capture behavior
- batch capture behavior
- how Claude/Cursor/Codex can read vault files

Privacy docs must say:

- local files stay local by default
- public URL inputs use network fetches
- optional Ollama is local if local host is used
- remote Ollama is explicit opt-in
- public demo is not for sensitive files

Examples must include:

- Obsidian vault capture
- PDF capture
- video/podcast capture
- folder/list batch capture
- direct conversion
- optional local polish

---

## 11. Explicit Non-Goals For Phase 1

Do not implement:

- `--memory-cards`
- memory model prompts
- embedding search
- MCP `search_memory`
- MCP `read_capture`
- MCP `list_recent_captures`
- desktop paid app
- hosted API
- cloud upload
- Obsidian plugin
- enterprise connectors
- SSO
- audit logs
- telemetry

Telemetry clarification:

- Do not add product-internal telemetry in Engineering Phase 1.
- Do collect feedback through GitHub Discussions, demo page CTA, manual user interviews, release comments, and issue templates.
- Do not claim telemetry exists unless a later phase explicitly implements it with a privacy review.

Do not claim:

- “fully offline” for URL workflows.
- “secure” without qualifiers.
- “search_memory exists” before MCP Phase 3.
- “memory cards generated” before Phase 2.
- “enterprise-ready” in Phase 1.

---

## 12. Phase 1 Acceptance Criteria

Phase 1 is complete only when all are true:

- A user can run a first capture in under 10 minutes after installation.
- A supported URL can be captured into an Obsidian-compatible vault.
- A supported local file can be captured into the vault.
- Batch/folder capture is supported via `--batch`.
- Captures have required frontmatter.
- Captures distinguish local storage, URL network fetches, and optional model endpoint use.
- Captures preserve raw converted content.
- `Index/OMD Captures.md` is generated.
- `.omd.json` manifest is written next to the capture note.
- Repeated capture does not overwrite previous notes.
- Obsidian can search by tag/source/date.
- Claude Code/Cursor/Codex can read the vault folder as plain Markdown.
- ChatGPT users can copy/paste the note without losing source context.
- RAG users can ingest the Markdown without needing Obsidian.
- AI-tool smoke sample preserves title, source metadata, tags, raw content headings, and generated/empty section labels.
- Privacy docs accurately distinguish local file conversion from URL network fetches.
- Full test suite passes.
- `verifier` approves the phase.

---

## 13. Phase 1 Deliverables

Code:

- `omd capture <input> --vault <path>`
- `omd capture <folder-or-list> --vault <path> --batch`
- capture schema/frontmatter helper
- vault path helper
- index helper
- manifest refresh integration

Tests:

- capture unit tests
- CLI dispatch tests
- batch capture tests
- manifest preservation tests
- full regression run

Docs:

- README Phase 1 section
- Obsidian recipe
- privacy doc update
- examples
- demo script

Review artifacts:

- verifier report
- security review
- risk review
- compatibility review

---

## 14. Hand-Off To Phase 2

Phase 2 can start only after:

- Capture schema is stable enough.
- Raw content is preserved.
- Generated sections have a clear place in the note body.
- Batch capture is complete or explicitly deferred with rationale.
- Phase 1 docs do not promise memory cards.

Phase 2 starts with:

- `--memory-cards`
- `--memory-model qwen3:4b`
- chunking strategy
- drift warning
- generated sections with evidence references

---

## 15. Hand-Off To Phase 3

Phase 3 can start only after:

- Vault path convention is stable.
- Frontmatter schema is stable.
- Index behavior is stable.
- Security reviewer approves path boundaries.

Phase 3 starts with an ADR for:

- JSON index vs SQLite vs ripgrep-only.
- configured vault root vs per-call vault path.
- MCP allowed roots.
- search result shape.
- read_capture path restrictions.

---

## 16. Pre-Mortem And Contingency Plan

### 16.1 Top Risks

| Risk | Trigger | Impact | Pre-Decision | Contingency |
|---|---|---|---|---|
| Batch command contract splits | `capture --batch` plan and existing `omd batch` behavior both evolve as public UX | duplicate implementation, inconsistent docs, fragile tests | Step 0 ADR chooses exactly one canonical UX | If conflict appears during implementation, stop executor lane, revise ADR, then update docs/tests before code resumes |
| Privacy metadata overclaims | `privacy: local` is interpreted as fully offline for URL or remote-model workflows | public trust damage, inaccurate GTM copy | schema separates `storage`, `network_fetch`, `model_endpoint`, `llm_used` | If existing captures only have `privacy`, keep backward compatibility but add new fields going forward |
| Public demo data lifecycle unclear | HF/Gradio demo accepts uploads without TTL/deletion/log warning | users may upload sensitive files under wrong assumptions | Demo is GTM support, not Phase 1 blocker | Before public launch, add banner, synthetic examples, TTL/delete/log statement, and failure cleanup note |
| MCP memory boundary weakens later | Phase 3 adds `read_capture/search_memory` without vault-root ADR | local filesystem exposure risk | Phase 1 handoff requires Phase 3 ADR | If MCP work starts early, block until allowed roots, symlink policy, and read failure behavior are documented and tested |
| Attachment namespace drifts | `_attachments/` and user `Attachments/` both become generated-output targets | broken relative links, cleanup ambiguity | `_attachments/` is canonical for OMD-generated assets | If an existing user folder conflicts, do not migrate automatically; document coexistence and require explicit opt-in |
| Platform/model promise exceeds reality | Linux / non-Apple Silicon / no Ollama users expect all workflows to work locally | onboarding failures and support load | docs must show capability matrix and fallback paths | If model/transcription dependency is missing, degrade to raw conversion where possible and print exact install/readiness guidance |
| Local model defaults drift | one agent follows current GTM defaults while another swaps in deep-research candidates | inconsistent README, CLI help, tests, and privacy docs | Phase 1 keeps current `qwen3:4b` / `gemma3:4b` / `bge-m3`; Phase 2 evaluates `qwen3.5:*` and `gemma3n:*` by ADR | If a model upgrade is required, pause implementation, update ADR/docs/tests together, then change code |
| GTM/docs drift | README, roadmap, Phase 1 plan, demo copy evolve separately | agents execute conflicting objectives | this plan is engineering source of truth for Phase 1; README/docs reflect shipped behavior | If a doc promises unshipped behavior, writer lane fixes docs before verifier approves |
| Review lanes overload execution | too many review-only subagents are launched | slow approval chain, no added quality | use 4-lane topology, fold reviews into verifier unless blocking | If review backlog forms, leader collapses reviewer prompts into one verifier/security pass |

### 16.2 Platform And Model Fallback Matrix

| Environment | Core file capture | URL/web capture | Audio/video transcript | Optional polish/cards | Expected behavior |
|---|---|---|---|---|---|
| macOS Apple Silicon + full tools | yes | yes, network required for URLs | yes via local Whisper tooling | yes via local Ollama if installed | best-supported local workflow |
| macOS without Ollama | yes | yes | raw transcript if Whisper tools exist | no generated polish/cards | core capture still works; tell user optional model install command |
| macOS without Whisper tools | docs/images/web yes | yes | no transcript for audio/video routes | no transcript polish | readiness warning, do not fail unrelated file capture |
| Linux with adapted Whisper backend | yes | yes | possible via faster-whisper/openai-whisper path | yes if Ollama/compatible local endpoint exists | document as supported only where tools are installed |
| Public demo | synthetic/public samples only | public URLs/uploads only within demo limits | limited by hosted policy | disabled unless explicitly configured | always show sensitive-file warning |
| Remote Ollama host | yes | yes | raw or polished if user opted in | sends content to configured endpoint | mark `model_endpoint: remote_ollama` and avoid local-only claim |

### 16.3 Source Of Truth Table

| Surface | Source of truth | Must not promise |
|---|---|---|
| Engineering Phase 1 | this document plus passing tests | memory cards, MCP memory search, desktop app, hosted API |
| README | shipped CLI/docs behavior | unimplemented `search_memory`, automatic model download, enterprise readiness |
| Privacy docs | actual network/model/storage behavior | always offline, absolute security, private public demo uploads |
| Obsidian docs | actual vault layout and note examples | plugin behavior, full remote asset mirroring |
| Public demo copy | demo policy and sample workflows | sensitive-file safety, full local privacy |
| Phase 2 docs | memory-card ADR and model behavior | replacing raw content with generated summaries |
| Phase 3 docs | MCP memory ADR and path-boundary tests | arbitrary filesystem search/read |

### 16.4 Stop Conditions For Subagents

Subagents must stop and report upward instead of freelancing when:

- command contract is ambiguous or conflicts with tests/docs.
- implementation requires changing files outside the assigned write scope.
- a privacy statement would become broader than actual behavior.
- a model install/download would be required to pass core Phase 1 tests.
- a public demo change would accept sensitive content without explicit warning.
- Phase 2/3 behavior is needed to satisfy a Phase 1 requirement.

### 16.5 Recovery Order

If execution starts failing, recover in this order:

1. Re-run gap audit against current `git status`, CLI help, and tests.
2. Re-lock command contract ADR.
3. Narrow executor lane to the smallest failing behavior.
4. Add or fix focused regression tests.
5. Update docs only after behavior is stable.
6. Run final verifier/security pass.
