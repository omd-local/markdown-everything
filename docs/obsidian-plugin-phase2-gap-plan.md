# OMD Home Phase 2 差距执行计划

状态：OMD 引擎已就绪；Obsidian 插件消费流程尚未实现。

本计划用于对齐 OMD 只返回 proposal 的 `enrich_note` schema v1 边界与独立的
OMD Home Obsidian 插件。完成 OMD 包并不等于完成插件工作流：插件仍须协商能力、
校验不可信 proposal、提供复核控件，并独占所有 vault 写入。

## 仓库职责面图

| 功能面 | 当前负责人 | 当前状态 |
| --- | --- | --- |
| `capabilities --json` 与 schema 协商 | OMD | 已实现并通过包测试 |
| Ollama 发现、生成与 proposal 校验 | OMD | 已实现；只返回 proposal |
| v1 stdin 请求、stdout 响应、stderr JSONL 事件 | OMD | 已实现并通过契约测试 |
| 受管子进程、超时、取消与输出上限 | OMD Home | 已有 bridge 基础能力可复用 |
| `enrich_note` capability 消费者 | OMD Home | 缺失 |
| v1 TypeScript 请求/响应校验器 | OMD Home | 缺失 |
| Proposal 复核与选择性 Apply UI | OMD Home | 缺失 |
| Apply 前目标 hash 检查与 Vault API 写入 | OMD Home | 缺失 |
| 插件源码基线与可复现部署 | OMD Home | 缺失；阻塞发布 |

## 按顺序执行的交付门禁

### G2：Capability 门禁

通过配置的 OMD executable 增加 session 范围的 `capabilities()` 调用。使用 5 秒
超时和有界输出。只有 `enrich_note.supported` 为 `true` 且
`schema_versions` 包含 `1` 时才启用增强功能。executable 缺失、schema 过旧、
超时或 JSON 损坏时，必须显示带 Retry 的可操作禁用状态，且不能禁用插件的无关功能。

### G3：请求与响应契约

新建独立 TypeScript 契约模块：

- 从 Obsidian metadata 构建有上限的候选目录；
- 笔记正文和候选目录只通过 stdin 发送，绝不放入 argv；
- 重新计算请求使用的精确 UTF-8 SHA-256；
- 校验所有必填响应字段、边界、candidate ID/path 映射、request ID、目标路径和返回 hash；
- 允许 v1 新增可选字段，但字段缺失或类型错误时必须 fail closed；
- 在插件契约测试中保留 OMD canonical v1 fixtures 的副本。

### G4：复核与 Apply 状态机

实现：

```text
idle → capability-check → catalog → generating → review
     → applying → applied | error | cancelled | conflict
```

复核页必须分开展示已验证的现有链接、新概念、现有 tags、新 tags、summary 和精确
evidence。新概念默认不选，绝不能自动变成笔记或 wikilink。Apply 前通过 Obsidian
Vault API 重新读取目标，hash 已变化时拒绝写入。只通过该 API 应用用户选中的项目；
只有完整写入成功后才设置已复核工作流状态。

### G5：基线、构建与部署

Phase 2 实现前先为插件仓库创建首个经过复核的基线 commit，使后续改动可审计、可回滚。
为 `main.js`、`styles.css`、`manifest.json` 和所需 bridge/helper 文件增加可复现的
test-vault 安装步骤。绝不能覆盖用户的插件 `data.json`。只有 test-vault 验收通过后，
才能把同一个已复核构建部署到真实 vault。

## 必测项目与当前状态

以下是完整 OMD Home 集成的 Phase 2 发布门禁。只通过 OMD 引擎测试，不能把对应行
标记为完成。

| # | 必须提供的证据 | OMD 引擎 | OMD Home 插件 | 整体状态 |
| --- | --- | --- | --- | --- |
| 1 | Capability 成功、executable 缺失、旧 schema、损坏 JSON、超时、cache 与手动刷新 | 已覆盖 capability 成功和 schema 输出 | 消费者、cache、Retry 与失败分支未实现 | 未完成 |
| 2 | 候选排除、Unicode、aliases、tags、请求边界与 SHA-256 | 已覆盖 catalog 校验与请求边界 | metadata-cache catalog builder 未实现 | 未完成 |
| 3 | 正文只走 stdin、固定 argv、stdout/stderr 有界 | 已覆盖 CLI 契约 | 已有通用 bridge 上限，但固定 `enrich-note` 调用尚未接入或端到端测试 | 未完成 |
| 4 | Canonical fixture 一致性与严格的不可信响应校验 | 已覆盖 canonical v1 fixtures 与 fail-closed 校验 | TypeScript 校验器及 fixture 一致性未实现 | 未完成 |
| 5 | 取消、超时、非零退出、无效 JSON、输出溢出时没有半成品 proposal 或 vault 写入 | 已覆盖 proposal-only 失败分支 | 增强状态机和 no-write 集成测试未实现 | 未完成 |
| 6 | 精确选择性 Apply；未选择的 links、tags、concepts 保持不存在 | OMD 从不 Apply，因此不适用 | 选择性 Apply 未实现 | 未完成 |
| 7 | Apply 前 hash 冲突阻止所有 proposal 写入并保留外部编辑 | 响应携带原始 hash | Apply 前重新读取与冲突处理未实现 | 未完成 |
| 8 | Capture 输出、候选笔记与 sidecars 保持逐字节不变 | Proposal 生成和真实模型检查已证明源文件不变 | 完整 generate-review-Apply 稳定性测试未实现 | 未完成 |
| 9 | 桌面限定行为明确；移动端不显示必然失败的操作 | 边界已写入文档 | 桌面门禁与移动端 UI 行为未实现 | 未完成 |
| 10 | 在重置后的 test-vault 副本中，完整真实模型流程连续通过 3 次 | OMD proposal-only Ollama 流程已连续通过 3 次 | 完整插件 generate-review-Apply 流程尚未运行 | 未完成 |

当前结论：OMD proposal 引擎在自身边界内可以发布，但 10 项完整插件门禁目前没有一项
可以标记为完成。第 1-5、8、10 项已有可复用的 OMD 或 bridge 证据；第 6、7 项及
所有行的插件部分仍属于 Phase 2 工作。

## 发布边界

OMD 通过自身包门禁后可以发布。OMD Home 增强功能必须等 G2-G5 和
[插件 UX 验收手册](obsidian-plugin-ux-acceptance-guide.zh-CN.md)全部通过后，才能宣布
发布。插件不能绕过 OMD 直接调用 Ollama；OMD 也不能新增 Apply 命令。
