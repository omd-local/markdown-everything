# OMD Home × OMD 新增集成 UX 验收手册

这份手册只验收 Obsidian 插件对 OMD `enrich_note` schema v1 的新增消费流程。OMD 自身的安装、转换、Capture 与独立 Inbox UI 仍按
[OMD 最新 UI 可用性与 Vault 安全验收手册](ux-acceptance-guide.zh-CN.md)
执行。

如果插件尚未出现“生成建议—复核—应用”流程，不要用命令行演示替代；应记录为“插件 Phase 2 未实现，阻塞发布”。OMD 能生成 proposal
只证明引擎已经准备好，不代表插件已经交付最终用户流程。

## 1. 验收边界

必须观察到以下所有权：

| 阶段 | 负责组件 | 是否写入 vault |
| --- | --- | --- |
| capability 探测 | OMD 返回静态 schema 支持；插件判断是否启用 | 否 |
| catalog 与请求 | 插件从 Obsidian metadata cache 形成有界候选，经 stdin 调用 OMD | 否 |
| proposal 生成与验证 | OMD 调用明确的 Ollama 模型并返回已验证建议 | 否 |
| 用户复核 | 插件分别展示链接、tags、概念、依据，用户逐项选择 | 否 |
| Apply 前并发检查 | 插件重新读取目标并比较 SHA-256 | 否 |
| Apply | 插件只通过 Obsidian Vault API 写入已选择项目 | 是 |

零容忍误解：参与者不能把“生成成功”“出现建议”或“勾选项目”理解成已经写入；只有明确点击 Apply 并看到与磁盘一致的成功确认才算写入。

## 2. 测试准备

### 人员与环境

- [ ] 使用一名没有看过实现和本手册通过标准的参与者，全程边操作边说。
- [ ] 使用目标 Obsidian vault 的可丢弃副本，不直接修改正式 vault。
- [ ] 测试副本安装的是本轮待发布插件构建，不从源码目录临时加载另一份版本。
- [ ] 插件设置指向本轮待发布 OMD 的绝对 executable。
- [ ] 桌面 Obsidian、Ollama 和 `qwen3:4b-instruct` 可用；主持人另备“停止 Ollama”的故障步骤。
- [ ] 准备一篇中英文混合、路径较长的目标 Markdown，以及 3–5 篇有明确/无明确关联的候选笔记。
- [ ] 所有材料均为非敏感测试内容；不使用真实 cookie、API key 或私人 vault。

### 快速门禁

主持人在参与者进入房间前运行；失败则停止 UX 测试：

```bash
OMD_PLUGIN_EXECUTABLE=/absolute/path/to/omd
"$OMD_PLUGIN_EXECUTABLE" --version
"$OMD_PLUGIN_EXECUTABLE" capabilities --json
"$OMD_PLUGIN_EXECUTABLE" enrich-note --help
```

通过要求：三个命令均正常；capability 明确包含 `enrich_note`、`supported: true` 和 schema `1`；输出无 traceback。

### 建立基线

假设测试目标是 `Inbox/Plugin Enrichment.md`：

```bash
OMD_UX_VAULT="/absolute/path/to/disposable-vault"
OMD_UX_NOTE="$OMD_UX_VAULT/Inbox/Plugin Enrichment.md"
shasum -a 256 "$OMD_UX_NOTE"
find "$OMD_UX_VAULT" -type f -name '*.md' -print0 | sort -z | xargs -0 shasum -a 256
```

保存输出。任务 1–2、4–7 在点击 Apply 前目标哈希必须保持不变；任务 3 只允许目标文件出现参与者明确选择的变更。

## 3. 主持规则与记录项

主持人只念每项的“参与者任务”。不告诉按钮位置、正确路径、建议勾选哪些项目，也不解释 OMD 与插件的边界。

每项记录：

- 是否独立完成；
- 从开始阅读到有效结果的时间；
- 查阅文档次数和页面；
- 第一次卡住时说出的原话；
- 参与者认为数据现在在哪里；
- 是否能说清 proposal 与 Apply 的区别；
- 完成后信心评分 1–5；
- 实际文件/hash 证据；
- 结论：通过、失败或外部阻塞。

## 4. 核心任务

### 任务 P1：发现并理解 OMD 能力

参与者任务：

> 请在插件里确认本地 OMD 是否能够为笔记生成增强建议，并告诉我如果它不可用，你会怎么修复。

通过清单：

- [ ] 5 分钟内找到入口并得到可用状态。
- [ ] 界面不要求先启动 Ollama 才能完成 capability 探测。
- [ ] executable 缺失或 schema 过旧时，动作被禁用，提示包含问题、原因和具体修复/重试动作。
- [ ] 修复设置后可以在当前 session 明确重试，不必重启 Obsidian。
- [ ] 没有 Python traceback、原始 JSON dump 或无限 loading。

### 任务 P2：生成只读 proposal

参与者任务：

> 请为指定 Inbox 笔记生成建议。看到结果后先不要应用，告诉我目标是哪篇笔记、系统建议了什么、依据来自哪里，以及 vault 是否已经改变。

主持人核对目标哈希和全 vault Markdown 清单。

通过清单：

- [ ] 生成过程显示有意义的阶段和取消入口，不显示模型 prompt 或全文日志。
- [ ] 复核页分开显示 summary、已验证的现有链接、现有 tags、新 tags 和新概念。
- [ ] 每条现有链接显示目标笔记与目标路径；关键长路径可以查看或复制完整值。
- [ ] 参与者能找到原文 evidence，并理解它来自目标笔记的精确片段。
- [ ] 新概念默认不选择，也不冒充已经存在的 `[[wikilink]]`。
- [ ] 页面明确写明尚未写入 vault；目标和候选 Markdown 哈希均不变。

### 任务 P3：只应用明确选择的项目

参与者任务：

> 请只选择一个你确认有用的现有笔记链接和一个 tag，取消其他项目，再应用到目标笔记；最后证明写入结果与选择一致。

通过清单：

- [ ] Apply 前能再次看到目标、选择项和将发生的写入。
- [ ] 只写入被选择的一个 Obsidian `[[wikilink]]` 和一个 tag。
- [ ] 未选择的链接、tags、新概念和 summary 不会偷偷写入。
- [ ] 成功确认显示目标相对路径，并与 Obsidian 中实际内容一致。
- [ ] 成功后才设置 reviewed workflow 状态；普通内容 tags 不被当成状态。
- [ ] 候选笔记、Capture 输出和 sidecar 的哈希不变。

### 任务 P4：并发编辑冲突

参与者任务：

> 请先生成建议。停在复核页时，在 Obsidian 的另一个窗格给目标笔记添加一句话，再回来尝试应用。

通过清单：

- [ ] Apply 被阻止，提示笔记在生成建议后已经改变。
- [ ] 提示要求保留当前编辑并重新生成，而不是覆盖或自动合并。
- [ ] 外部新增句子仍在；旧 proposal 的任何项目均未写入。
- [ ] reviewed 状态没有提前设置。
- [ ] 重新生成后可以基于新内容正常复核。

### 任务 P5：Ollama 停止与模型缺失

参与者任务：

> 请在当前故障状态下生成建议，并在一分钟内判断问题、原因、修复动作和哪些数据被保留。

分别执行一次停止 Ollama、一次选择/配置不存在的模型。

通过清单：

- [ ] 错误明确区分“服务不可达”和“模型未安装”。
- [ ] 给出可复制的启动或 `ollama pull <model>` 修复命令以及 Retry。
- [ ] capability、Capture 等不依赖该模型的功能没有被错误包装成全部不可用。
- [ ] 没有半个 proposal、没有成功 toast、没有 traceback、没有 vault 修改。
- [ ] 恢复错误到可重新开始不超过 1 分钟。

### 任务 P6：取消、timeout 与异常输出

参与者任务：

> 请开始一次耗时较长的生成并取消。然后告诉我任务处于什么状态、是否有半成品、目标笔记是否改变，以及如何重试。

通过清单：

- [ ] Cancel 立即可见；进度在合理时间内进入 cancelled，而不是继续转圈。
- [ ] 取消后不显示可 Apply 的旧/半 proposal。
- [ ] timeout、非零退出、stdout 超限或畸形 JSON 使用同一 fail-closed 原则。
- [ ] 重新运行不会混入上一请求的事件或结果。
- [ ] 目标、候选、Capture 输出和 sidecar 哈希不变。

### 任务 P7：中英文、长路径与移动端边界

参与者任务：

> 请用这篇中英文混合且路径很长的笔记完成一次预览，并告诉我完整目标路径和错误/警告含义；然后说明移动端是否能运行同一功能。

通过清单：

- [ ] 中英文标题、evidence、tags 和错误信息可读，不出现乱码或关键内容被控件遮住。
- [ ] 长路径可横向查看或复制；不能只有不可恢复的省略号。
- [ ] 警告与错误没有暴露 vault 全路径之外的不必要环境信息。
- [ ] 移动端入口友好禁用并说明 v1 仅支持桌面本地 runtime，不提供一个必然失败的按钮。

### 任务 P8：插件部署与重启烟测

参与者任务：

> 请重启 Obsidian，确认你使用的是刚才验收的插件与 OMD 版本，并再次完成 capability 检查和一次只读预览。

通过清单：

- [ ] 设置仍指向预期 OMD executable，版本与发布记录一致。
- [ ] 安装目录包含构建后的 `main.js`、`styles.css`、`manifest.json` 及运行所需 bridge/helper。
- [ ] 构建/部署没有覆盖正式 vault 的用户 `data.json`。
- [ ] 重启后 capability 和只读 proposal 均正常，没有依赖源码目录中的临时文件。

## 5. 汇总与放行标准

| 任务 | 独立完成 | 耗时 | 文档次数 | 数据去向正确 | 信心 1–5 | 结论 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| P1 capability | | | | | | |
| P2 只读 proposal | | | | | | |
| P3 选择性 Apply | | | | | | |
| P4 并发冲突 | | | | | | |
| P5 Ollama / 模型恢复 | | | | | | |
| P6 取消 / timeout | | | | | | |
| P7 中英文 / 长路径 / mobile | | | | | | |
| P8 部署 / 重启 | | | | | | |

放行要求：

- 8 个核心任务全部独立完成；单人 7/8 只有 87.5%，未达到 90% 门槛。
- 首次有效结果不超过 5 分钟；错误恢复不超过 1 分钟。
- traceback、数据丢失、未授权网络发送、隐私误解和“预览等于写入”的误解均为 0。
- 所有失败路径都无半成品、无错误成功确认、无 vault 修改。
- P3 的实际 diff 只包含明确选择项目；P4 的 hash 冲突必须稳定阻止写入。
- 至少 5 名互不提示的新参与者完成形成性测试；修复后换未看过修复的人重测。
- 使用真实 `qwen3:4b-instruct` 的完整“生成—复核—Apply”在重置后的测试 vault 副本中连续 3 次通过。

任何功能仍缺失时，记录为 gap 并回到
[OMD Home Phase 2 gap plan](obsidian-plugin-phase2-gap-plan.md) 的 G2–G5；不得用主持人口头解释或手工修改文件把它判为通过。
