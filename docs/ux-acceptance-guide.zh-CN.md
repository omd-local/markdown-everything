# OMD 最新 UI 可用性与 Vault 安全验收手册

这份手册用于主持一对一的“边操作边说”测试。目标不是证明参与者最终能学会 OMD，而是观察一个第一次使用的人，能否仅凭产品界面和公开文档完成核心任务，并正确理解数据保存位置、AI 的输入范围以及哪些操作会真正写入 vault。

本手册验收 OMD 自身 UI。若要验收 Obsidian 插件消费 `enrich_note`、复核 proposal、并发 hash 检查及选择性 Apply，请另用
[OMD Home × OMD 新增集成 UX 验收手册](obsidian-plugin-ux-acceptance-guide.zh-CN.md)。两套流程不能互相代替。

适用界面基线：

- Inbox 页面包含“保存内容—复核原文—决定是否创建笔记”三个步骤。
- Inbox 队列自动刷新，不再提供 **Refresh Inbox** 按钮。
- 本地 AI 入口为 **Generate draft from selected text**。
- **Draft a takeaway with AI (optional)** 位于只读原文附近；选择 **No AI** 时整块隐藏，而不是留下一个必然失败的入口。
- **Suggest sources** 会在同一列表中合并当前 Inbox 项目的相关笔记与 vault `Sources/` 中最近的转换/Capture 结果；用户仍须明确选择，系统不会自动猜测。
- 只有云端 AI 服务才显示 **Review cloud request** 和本次请求授权。
- 最终操作为 **Create note in Notes** 或 **Keep original · mark as not needed**。

如果待测构建不符合以上任一项，先确认启动的版本，不要用主持人口头解释来弥补界面差异。

## 1. 一页式验收清单

### 测试前

- [ ] 使用准备发布的安装包或 Homebrew 构建；若测试工作树，明确把“安装体验”标为未测。
- [ ] 使用测试 vault 的副本，不直接操作原始测试 vault，更不能使用参与者的真实 vault。
- [ ] 每个会写入数据的任务使用独立副本，避免前一个任务改变后一个任务的起始状态。
- [ ] 测试材料不含私人信息、真实 API key、cookie、客户数据或未公开文档。
- [ ] 已准备稳定的 PDF、公开网页、个人笔记、精确摘录和仅含网址的 Inbox 项目。
- [ ] 已确认 Ollama 和一个指令模型可用，但不把恢复步骤提前告诉参与者。
- [ ] 已记录只读任务目标文件的 SHA-256。
- [ ] 已准备计时器、记录表，并取得录屏和录音同意。
- [ ] 主持人已完成启动前快速检查，参与者没有看到测试输出。

### 每个任务开始时

- [ ] 切换到该任务专用的 vault 副本。
- [ ] 只念“参与者任务”，不念主持步骤、预期路径和通过标准。
- [ ] 从参与者开始阅读任务时计时。
- [ ] 提醒参与者把看到的内容、预期结果和困惑说出来，但不提示控件位置。

### 每个任务结束时

- [ ] 记录是否独立完成、耗时和查阅文档次数。
- [ ] 原样记录第一次卡住时说的话。
- [ ] 追问：“数据现在在哪里？原件在哪里？下一步哪个动作会真正写入 vault？”
- [ ] 主持人核对文件、状态和哈希值，不以参与者口头判断代替证据。
- [ ] 记录信心评分 1–5。
- [ ] 出现主持人提示、Python 异常堆栈（traceback）、数据丢失或隐私误解时，按规则判定，不做“差不多通过”。

### 发布前

- [ ] 8 个核心任务达到本手册的成功门槛。
- [ ] 数据丢失、真实 vault 误操作和隐私误解均为 0。
- [ ] 普通用户界面没有暴露 Python 异常堆栈（traceback）。
- [ ] 参与者能区分 Inbox 原文、AI 草稿、命令行只读建议和 Notes 派生笔记。
- [ ] 两个 Inbox 最终动作都有立即可见、与磁盘结果一致的确认消息。
- [ ] 参与者不会把只读检索结果误认为已经选择、链接或修改了现有笔记。
- [ ] 本地 AI 不出现云端授权；云端 AI 不绕过预览和逐次授权。
- [ ] 修复问题后由另一名没看过修复的人重测。

## 2. 先理解数据流与写入边界

主持人必须先理解产品边界，但不能在任务开始前把答案讲给参与者。

| 用户意图 | 操作入口 | 数据去向 | 是否使用 AI | 是否改动原件 |
| --- | --- | --- | --- | --- |
| 把普通文件转成 Markdown | **Convert to .md file** | 用户选择的普通文件夹 | 可选；核心转换不依赖 AI | 否 |
| 把网页、PDF 等外部资料存入 Obsidian | **Capture to vault note** | `Sources/<类型>/` | 可选 | 否 |
| 保存自己的想法或精确摘录 | **Inbox / review → Save to Inbox** | `Inbox/` | 否 | 新建原文，不覆盖输入 |
| 从选中文字生成 AI 草稿 | **Generate draft from selected text** | 先停留在可编辑预览框 | 是 | 否 |
| 把复核结果形成正式笔记 | **Create note in Notes** | `Notes/` | 仅在用户选择加入草稿时包含 AI 内容；保存用户确认的 tags；若选择了来源 Markdown，则加入 vault 相对 `[[Obsidian wikilink]]` | `Inbox/` 与来源 Markdown 保留 |
| 不再处理某个 Inbox 项目 | **Keep original · mark as not needed** | 更新复核状态 | 否 | `Inbox/` 原文保留 |
| 为现有笔记生成只读增强建议 | `omd enrich-note` | 标准输出中的 `proposal` 字段 | 是 | 否 |

必须特别注意：

1. Capture 和 Inbox 仍是两种保存意图。前者把外部来源放入 `Sources/`，后者保存个人内容；但复核页现在可以由用户明确选择一篇 `Sources/**/*.md` 作为 AI 来源和新 Notes 笔记的链接目标。
2. Inbox AI 默认读取当前 Inbox 原文；选择 **Markdown source for AI and new-note link (optional)** 后，改为只读取界面显示的那篇只读 Markdown。它不会打开网址，也不会读取未选择的文件。
3. AI 草稿不是已保存笔记。只有勾选 **Add this edited AI draft to the new note**，再创建 Notes 笔记，草稿才会写入。
4. `enrich-note` 是独立的命令行只读建议工具，不是 Inbox AI 的后台实现。
5. **Suggest sources** 是一个统一的只读候选入口：它同时显示与当前 Inbox 原文相关的 vault 笔记，以及 `Sources/` 中最近的转换 Markdown。**Search notes** 则只按用户输入的词检索。两者都把候选放入同一个来源选择框，检索或选择本身不写文件。
6. 只有 **Create note in Notes** 会创建派生笔记、保存 **Tags for new note (editable)** 中的 tags，并把所选来源写成 vault 相对 `[[wikilink]]`；它不会反向修改来源。普通 **Convert to .md file** 如果输出到 vault 外部文件夹，仍不会被自动导入或链接。

因此，“检索到候选”“选择来源”“生成 AI 草稿”“创建带来源链接的新笔记”仍是四个分开的状态。测试时不得因为列表里出现了文件名，就把“已经建立关联”判为成功；也不得把只读来源选择误认为来源文件已被修改。

### `[[链接]]`、tags 与 SHA-256 的普通话解释

- `[[Sources/Web/某篇文章]]` 指向 vault 中一篇明确的笔记。Obsidian 可以点击它导航，并在 Graph view 中把两篇笔记画成一条直接连线。OMD 用它记录“这篇新 Note 明确来自哪一篇 source”。
- `#tag` 或 YAML `tags:` 是分类标签，不是某一篇来源的地址。它适合把许多笔记按 `ai`、`research` 等主题筛选或聚合；Graph view 启用 tag 节点时也可显示这种多对一分类关系。
- 同一篇 Note 可以同时有两者：用 `[[链接]]` 指向具体来源，用 tags 表示宽泛主题。不要用 tag 代替来源链接，也不要把链接当作分类系统。
- SHA-256 是文件内容的“指纹”，通常显示为 64 个十六进制字符。同一份 UTF-8 文件内容会得到同一指纹，哪怕只改一个字符，指纹通常也会完全不同。它不是加密，也不能用来还原原文。
- 本手册比较修改前后的 SHA-256，是为了证明预览、检索和 AI 草稿没有改动原文件。插件 Phase 2 还会在 Apply 前重新计算它；如果用户在预览后编辑过目标笔记，指纹不一致就整次停止，避免覆盖较新的编辑。

## 3. 主持规则

### 可以做

- 请参与者继续说出自己看到什么、以为会发生什么。
- 允许参与者自行查看产品提供的帮助、README 或命令帮助。
- 在参与者宣称完成后，追问数据去向、原件位置和下一步动作。
- 在参与者即将操作真实敏感数据、删除重要内容或暴露凭据时中止任务。

### 不可以做

- 指出应该点击哪个菜单、按钮或字段。
- 替参与者输入命令、选择模型、解释错误或指出文件位置。
- 用鼠标指向正确区域，或通过语气暗示正确答案。
- 因为参与者“其实懂了”而忽略超时、误解或主持人介入。
- 为了让任务可测而手工伪造 Inbox 配套元数据文件。

一旦主持人提供了能直接推动任务完成的提示，该任务就不能再记为“独立完成”。可以继续观察，但必须如实记录介入。

## 4. 主持人准备

### 4.1 启动前快速检查

不要把下面输出展示给参与者：

```bash
cd /path/to/omd
.venv/bin/omd --help
.venv/bin/omd-ui --help
.venv/bin/omd enrich-note
.venv/bin/python -m pytest tests/test_ui.py -q
```

检查：

- [ ] 根帮助列出 `doctor`、`capabilities` 和 `enrich-note`。
- [ ] `omd-ui --help` 只显示帮助，不启动服务器。
- [ ] 缺少参数的 `enrich-note` 给出可执行的下一步，并说明 vault 未改变。
- [ ] UI 测试通过。
- [ ] 启动 UI 后能看到本手册开头列出的界面基线。

### 4.2 创建独立 vault 副本

下面的命令只创建副本，不删除原目录：

```bash
OMD_UX_SOURCE="/path/to/test-vault"
OMD_UX_ROOT="/tmp/omd-ux-acceptance-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OMD_UX_ROOT"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/03-inbox-capture"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/04-review-decisions"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/05-local-ai-draft"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/06-ollama-errors"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/07-enrich-note-boundaries"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/08-中文 English mixed path with spaces and a very long vault name"
ditto "$OMD_UX_SOURCE" "$OMD_UX_ROOT/optional-cloud-preview"
echo "$OMD_UX_ROOT"
```

检查：

- [ ] `03-inbox-capture` 允许参与者自行新建 Inbox 项目。
- [ ] `04-review-decisions` 有两个状态为 `needs review` 的项目。
- [ ] `05-local-ai-draft` 有一个包含完整原句的 Highlight 和一个正文只有 URL 的项目。
- [ ] `06-ollama-errors` 有一个适合生成 AI 草稿的非敏感 Inbox 项目。
- [ ] `07-enrich-note-boundaries` 包含约 46 KB 的长笔记。
- [ ] 所有预置 Inbox 项目都由当前 OMD 正常创建，并能在 **Choose an Inbox item** 中看到。
- [ ] 没有把以 `._` 开头的 AppleDouble 文件误认为用户笔记。

### 4.3 用 UI 准备任务 4 和任务 5 的项目

复制 vault 不会自动生成待复核项目。下面的准备动作由主持人在参与者到场前完成，并且只能对相应的任务副本执行。不要直接创建或复制 `.omd.json`。

准备 `04-review-decisions`：

1. 在 UI 中把 **Vault folder** 指向 `$OMD_UX_ROOT/04-review-decisions`。
2. 打开 **Inbox / review**，通过 **Save to Inbox** 保存两条非敏感内容：一条 **My note**，一条 **Highlight**。
3. 不要生成 AI 草稿，也不要点击两个最终动作。
4. 在 **Choose an Inbox item** 中确认两条都显示为 `needs review`。
5. 在 Finder 或终端中确认每条内容都有一个 `.md` 和同名 `.omd.json`；仅有 Markdown 不算可复核项目。

准备 `05-local-ai-draft`：

1. 把 **Vault folder** 改为 `$OMD_UX_ROOT/05-local-ai-draft`。
2. 保存一条 **Highlight**，正文使用三到五句完整、可公开的原文，其中至少有一句适合作为逐字 evidence。
3. 再保存一条 **Highlight**，正文只能包含这一类单独网址，不要加标题说明或评论：

   ```text
   https://example.com/omd-url-only-test
   ```

4. 确认两条项目都显示为 `needs review`，然后停止准备；不要提前点击生成。

如果还要验证“刚转换的 Markdown 能否直接进入 AI”，请在这个任务副本的 `Sources/` 下保留至少一篇小于 64 KiB 的真实转换结果；更大的文件属于单独的输入边界测试。最自然的准备方法是用 **Capture to vault** 把一篇非敏感网页保存到 `05-local-ai-draft`；也可以把任务 2 的转换结果复制到这个副本的 `Sources/`。当前版本会在参与者点击 **Suggest sources** 后把它与相关笔记用同一种样式列出，但不会自动替用户选择或发送任何文件。

### 4.4 记录文件与哈希基线

先记录任务 4 的 Inbox 原文和 Notes 初始列表：

```bash
TASK4_VAULT="$OMD_UX_ROOT/04-review-decisions"
mkdir -p "$TASK4_VAULT/Notes"
find "$TASK4_VAULT/Inbox" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/04-inbox-before.sha256"
find "$TASK4_VAULT/Notes" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/04-notes-before.sha256"
```

再记录任务 5 的 Markdown 和配套元数据。基线文件放在 `$OMD_UX_ROOT`，不要放进待测 vault：

```bash
TASK5_VAULT="$OMD_UX_ROOT/05-local-ai-draft"
find "$TASK5_VAULT/Inbox" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-inbox-md-before.sha256"
find "$TASK5_VAULT/Inbox" -type f -name '*.omd.json' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-inbox-meta-before.sha256"
find "$TASK5_VAULT/Sources" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-sources-before.sha256"
```

其余只读任务可记录目标文件：

```bash
find "$OMD_UX_ROOT/06-ollama-errors/Inbox" -type f -name '*.md' -print0 | xargs -0 shasum -a 256
shasum -a 256 "$OMD_UX_ROOT/07-enrich-note-boundaries/Sources/Web/Transfer Learning for Computer Vision Tutorial.md"
```

任务结束后的精确比较命令写在相应任务中。AI 草稿和 `enrich-note` 不得改变目标 Markdown。创建笔记或标记不需要会更新配套的复核元数据，因此不要要求任务 4 的整个 Inbox 目录完全不变；应核对原始 `.md` 正文仍可读取。

## 5. 核心任务

每张任务卡都分为四部分：

- “参与者任务”是唯一可以念给参与者的内容。
- “主持步骤”用于控制测试过程。
- “通过清单”必须逐项核对。
- “直接判失败”用于避免模糊放行。

### 任务 1：安装并确认环境

测试目的：判断第一次使用的人能否完成安装、理解 `doctor` 的结果，并知道下一步做什么。

参与者任务：

> 这是 OMD 的发布构建和 README。请安装它，确认这台 Mac 是否已经可以使用 OMD，并告诉我你接下来会做什么。

主持步骤：

1. 提供发布构建和 README，开始计时。
2. 让参与者独立安装并诊断环境。
3. 参与者认为完成后，问：“现在有哪些功能可以用？如果有缺失项，会影响什么？”
4. 主持人核对实际安装结果和 `omd doctor` 输出。

通过清单：

- [ ] 5 分钟内完成安装和 `omd doctor`。
- [ ] 能区分核心转换依赖和可选能力。
- [ ] 知道如何启动 UI 或进行第一次转换。
- [ ] 没有暴露 Python 异常堆栈。
- [ ] 全程没有主持人操作提示。

直接判失败：

- 安装未完成却认为已经完成。
- 把可选 Ollama、OCR 或转录工具误认为核心转换的硬性条件。
- 错误信息只剩异常堆栈，参与者不知道下一步做什么。

### 任务 2：第一次转换与保存外部来源

测试目的：验证普通导出与 vault Capture 的区别是否清楚，结果是否容易找到。

参与者任务：

> 请把桌面上的测试 PDF 转成普通 Markdown，再把这个公开网页保存到测试 vault。完成每一项后，告诉我结果在哪里，以及哪一项属于外部资料收藏。

主持步骤：

1. 提供 PDF、公开网页和任务专用 vault，开始计时。
2. 观察参与者如何选择输出方式，不解释 Convert 与 Capture。
3. 每完成一项，要求参与者重新找到生成文件。
4. 主持人核对普通输出目录和 vault 的 `Sources/<类型>/`。
5. Capture 完成后观察参与者能否从精简结果区找到 **Most recent capture files**。结果区最多显示 3 个最近文件，并提供完整历史索引 `Index/OMD Captures.md`，不应展开整个索引正文。

通过清单：

- [ ] PDF 使用普通 Markdown 输出，首次结果不超过 2 分钟。
- [ ] 网页使用 vault Capture，首次结果不超过 2 分钟。
- [ ] 能重新找到两个结果的完整路径。
- [ ] 能用自己的话说明：**Capture to vault note** 把网页、PDF、播客等外部资料保存成 `Sources/<类型>/<标题>.md`，并更新 `Index/OMD Captures.md`；它不会创建待复核的 `Inbox/` 项目。只有 **Save to Inbox** 才把自己的想法或精确摘录放入 `Inbox/`。
- [ ] 能从完成页指出本次最近的 `Sources/.../*.md` 路径；需要更多历史记录时，知道打开 `Index/OMD Captures.md`，而不是在完成页阅读全部旧记录。
- [ ] 原始 PDF 和网页内容没有被覆盖。
- [ ] 网络失败时能读懂原因和下一步，没有暴露 Python 异常堆栈。

直接判失败：

- 把 Capture 当成 Inbox，或只背出文件夹名却无法指出本次实际生成的 source 文件。
- 找不到输出位置。
- 在主持人解释后才知道两个结果的区别。

### 任务 3：把个人内容保存到 Inbox

测试目的：验证参与者能否理解“我的想法”和“精确摘录”的区别，并确认 Inbox 保留原文。

参与者任务：

> 请把这段个人笔记保存进测试 vault。完成后找到系统保留的原始版本，并告诉我刚才输入的文字有没有被覆盖。

主持步骤：

1. 切换到 `03-inbox-capture`，提供一段个人笔记，开始计时。
2. 观察参与者是否自行找到 Inbox，并选择适合个人笔记的类型。
3. 保存后不要提示刷新；观察列表是否自动更新、参与者是否能继续。
4. 参与者认为完成后，要求他在 vault 中找到原始文件。

预期操作路径（只供主持人核对）：

1. 打开 **Inbox / review**。
2. 在第一步选择个人笔记类型 **My note**。
3. 输入标题和正文，选择 **Save to Inbox**。
4. 在自动更新的列表中看到新项目，并在 `Inbox/` 找到原文。

通过清单：

- [ ] 独立找到 Inbox 工作流。
- [ ] 能解释 My note 与 Highlight 的区别。
- [ ] 不寻找已经不存在的 Refresh 按钮。
- [ ] 队列自动出现可读标题，而不是内部 ID。
- [ ] 能找到 `Inbox/` 下的原始 Markdown。
- [ ] 明白保存不会调用 AI，也不会覆盖输入。

直接判失败：

- 使用外部 Capture 完成任务，并认为它等同于 Inbox。
- 找不到原文或认为保存后原文会被自动整理覆盖。

### 任务 4：复核并决定是否创建笔记

测试目的：验证两个最终动作是否可以在执行前被正确预测、执行后是否有可见反馈，且参与者知道原文始终可恢复。

这里的 **Keep original · mark as not needed** 不是删除。它表示“保留这条 Inbox 原文，但本轮不创建 Notes 笔记”，只更新配套元数据中的复核状态。按钮把 **Keep original** 直接写进名称，是为了避免用户把它误认为删除。如果产品以后需要删除，应提供名称明确的独立删除动作、二次确认和可恢复位置，不能悄悄改变这个安全契约。

参与者任务：

> 这个 Inbox 中有两条待处理内容。请找到它们，先分别说出“创建 Notes 笔记”和“标记为不再需要”会发生什么，再各执行一次。最后证明两条原始内容仍然可以恢复。

主持步骤：

1. 切换到 `04-review-decisions`，开始计时。
2. 在参与者点击最终按钮前，要求他说出预测结果。
3. 观察他是否阅读保存信息、只读原文和可选个人补充；不要替他解释 **Keep original · mark as not needed**。
4. 让参与者先对第一条执行 **Create note in Notes**。按钮下方必须立即显示创建结果、生成的文件名，以及原文仍在 Inbox。
5. 记录此时 Notes 文件列表，再让参与者对第二条执行 **Keep original · mark as not needed**。按钮下方必须立即显示没有创建 Note、原文仍在 Inbox。
6. 两个动作完成后，让参与者自己查找 `Notes/` 和 `Inbox/`。
7. 主持人核对派生笔记、复核状态、确认消息和两份原始正文。
8. 技术扩展检查：对第一条再次点击同一个最终动作，界面应显示“已经完成”，且不得重复创建 Note；再尝试相反动作，界面应拒绝变更，状态与文件均保持不变。该步骤用于验证防重复写入，不纳入首次用户操作计时。

预期界面反馈（只供主持人核对）：

- 创建成功：`Created in Notes: <文件名>. The original remains in Inbox.`
- 标记成功：`Marked as not needed. The original remains in Inbox and no Note was created.`
- 下拉框和队列中的状态分别变为 `note created` 与 `not needed`，选中的项目不应无故跳走。

这两个按钮点击后会立即执行，目前没有二次确认弹窗。按钮下方如果仍停留在 **No review action yet**，不能判为成功；出现 Python traceback 同样直接判失败。

主持人文件核对：

```bash
# 第一项创建完成后
find "$TASK4_VAULT/Notes" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/04-notes-after-create.sha256"

# 第二项标记完成后：这份列表应与上一份完全相同
find "$TASK4_VAULT/Notes" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/04-notes-after-not-needed.sha256"
diff -u "$OMD_UX_ROOT/04-notes-after-create.sha256" "$OMD_UX_ROOT/04-notes-after-not-needed.sha256"

# 两个动作都不得改写 Inbox 的 Markdown 原文；diff 成功时没有输出
find "$TASK4_VAULT/Inbox" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/04-inbox-after.sha256"
diff -u "$OMD_UX_ROOT/04-inbox-before.sha256" "$OMD_UX_ROOT/04-inbox-after.sha256"
```

`04-notes-after-create.sha256` 相比初始列表应新增一篇派生笔记；第二次 `diff` 应没有输出。Inbox 的 `.omd.json` 状态会改变，所以这里故意只比较 `.md`。

如果点击后看起来“没有反应”，按下面证据分类，不要反复点击：

| 确认消息 | 文件和队列状态 | 判定 |
| --- | --- | --- |
| 正确 | 正确 | 动作通过 |
| 缺失 | 已改变 | 后台成功，但属于关键反馈缺失；任务失败 |
| 显示成功 | 未改变或文件缺失 | 成功信息不真实；阻塞发布 |
| 缺失 | 未改变 | 动作未执行或事件绑定失败；阻塞核心流程 |

通过清单：

- [ ] 通过可读标题选择项目，不需要识别内部 ID。
- [ ] 理解 **Saved details** 是保存信息，不是需要编辑的来源证明。
- [ ] 理解 **Original text (kept unchanged)** 是不可编辑原文。
- [ ] **Create note in Notes** 在 `Notes/` 生成可追溯派生笔记。
- [ ] **Keep original · mark as not needed** 不创建 Notes 笔记，也不删除 Inbox 原文。
- [ ] 每次点击后都立即出现准确、可读且包含数据去向的确认消息。
- [ ] 两个动作后，`Inbox/` 原文都仍然存在。
- [ ] 队列状态分别变为 `note created` 和 `not needed`。
- [ ] 参与者没有把“按钮没反馈”当作完成，也没有因为不确定而重复创建。
- [ ] 重复同一最终动作不会重放写入；相反最终动作不会翻转已经完成的决定。

直接判失败：

- 把“标记为不再需要”理解成永久删除。
- 认为创建 Notes 笔记会移动或覆盖 Inbox 原文。
- 执行前无法预测，执行后仍说不清数据去了哪里。
- 点击后没有确认消息，即使后台文件碰巧已经生成。
- 同一点击生成多篇 Notes、显示成功但没有对应文件，或标记不需要后仍新增 Note。

#### 扩展观察：检索、选择、生成和写入是四个独立动作

**Suggest sources** 会把“相关笔记”和“最近转换的 Markdown”按同一种结果卡片样式放入 **Markdown source for AI and new-note link (optional)** 下拉框；**Search notes** 仍供用户输入明确关键词。用户必须显式选择一篇；选择后只会在 **Selected Markdown source (kept unchanged)** 中只读预览，不会自动修改候选文件，也不会自动创建链接。

可向参与者追加一个不计入核心成功率的问题：

> 请找出与当前 Inbox 内容相关的一篇 Markdown，明确选择并预览它，然后创建一篇新 Notes 笔记。告诉我链接写到了哪里、哪个原文件没有被修改。

主持人按以下步骤核对：

1. 对候选文件先运行 `shasum -a 256 <文件>` 并保存结果。
2. 点击 **Suggest sources**，确认结果区能区分 `Related note` 与 `Converted source`，并统一显示标题、vault 相对路径和只读说明或片段。
3. 在下拉框中明确选择一个候选；确认完整相对路径和预览内容正确。
4. 此时再次计算哈希；选择和预览阶段不应有任何文件变化。
5. 在 **Tags for new note (editable)** 输入一个测试 tag，再点击 **Create note in Notes** 一次；确认新 `Notes/*.md` 的 YAML frontmatter 包含该 tag，且 **Linked source** 使用完整 vault 相对路径的 `[[Obsidian wikilink]]`。
6. 再次计算候选文件哈希；它必须保持一致。链接只写进新建的派生 Note，不会反向改写所选文件。

记录以下观察：

- [ ] 能否理解“出现在结果中”不等于已经选择或建立链接。
- [ ] 能否通过完整相对路径确认所选文件。
- [ ] 能否理解 AI 只会读取下拉框中明确选择并预览的文件。
- [ ] 能否预测 **Create note in Notes** 会创建新 Note，而不是追加到或改写现有笔记。
- [ ] 选择、预览和检索阶段没有修改 vault；创建后只有新 Note 和 Inbox 复核元数据发生预期变化。

### 任务 5：生成有原文依据的本地 AI 草稿

测试目的：判断 AI 草稿是否真的有用，参与者是否理解模型只看到选中的文字，以及草稿尚未写入 vault。

当前输入边界必须先说清楚给主持人，但不要提前教参与者：默认情况下，**Generate draft from selected text** 读取 **Choose an Inbox item** 当前项目的原始正文；如果用户在 **Markdown source for AI and new-note link (optional)** 中明确选择了一个 vault Markdown，它就改为读取右侧只读预览中的文字。它不会读取 Finder 中随意选中的文件，也不会自动发送“上一份转换结果”。

Inbox 的本地 AI 单独使用最多 32768 token 的 Ollama context window；其他已有 AI 工作流仍保留各自预算。超过该预算时必须明确提示缩短所选文字，并保留 Inbox 原文、当前草稿、tags 和来源文件。不要把提高预算误判为无限输入：本地模型仍有上下文与内存边界。

参与者任务：

> 请选择包含完整摘录的 Inbox 项目，让本地 AI 生成一份草稿，但不要把它写进 Notes。完成后告诉我：AI 实际读了什么、建议是什么、依据对应哪段原文，以及现在有没有写入 vault。然后再对正文只有网址的项目试一次，并解释结果。

主持步骤：

1. 切换到 `05-local-ai-draft`，确认 AI 服务为本地 Ollama，开始计时。
2. 观察参与者是否先阅读 AI 功能范围，再生成草稿。
3. 草稿出现后，要求参与者逐字核对原文依据。
4. 确认参与者没有勾选“把草稿加入新笔记”，也没有创建 Notes 笔记。
5. 让参与者切换到仅含网址的项目，再次尝试生成。
6. 检查 URL-only 的警告和草稿框状态，再执行下面的哈希比较。

预期操作路径（只供主持人核对）：

1. 在第二步选择完整 Highlight。
2. 确认 **Draft a takeaway with AI (optional)** 位于只读原文附近并可见；如果设置为 **No AI**，这整块应隐藏。选择本地 Ollama 后再展开。
3. 选择 **Generate draft from selected text**。
4. 阅读可编辑草稿和原文依据。成功后，状态区必须明确列出新增的建议 tags，并告诉用户在 AI 面板正下方的 **Tags for new note (editable)** 审核或删除；不能只显示一句容易错过的泛化提示。
5. 核对建议 tags 已进入紧邻 AI 面板的可编辑字段，但尚未写入任何文件。
6. 切换到仅含网址的项目并再次生成。

仅含网址项目的精确测试步骤（只供主持人核对）：

1. 确认选中的项目正文除了 `https://...` 没有任何单词、标题说明或评论。
2. 切换项目后确认 **AI draft (edit before including)** 已清空，并确认 **Markdown source for AI and new-note link (optional)** 已回到空值；否则测试的会是外部 Markdown，而不是 URL-only Inbox 原文。
3. 点击 **Generate draft from selected text** 一次，不要先停止 Ollama，也不要改成云端服务。
4. 界面必须说明：需要具体文字；OMD 没有打开网址或推断网页内容；可改为明确选择已转换 Markdown 或保存具体 Highlight；**Nothing was sent**；没有修改 vault 文件。
5. 草稿框必须继续为空，不能出现通用建议、`missing-evidence`、旧项目的草稿或成功颜色状态。
6. 如果出现 Ollama 连接错误、模型错误或 token 用量，说明请求已经越过了 URL-only 本地校验，判定失败。

普通用户只能验证界面承诺。主持人还应在测试前运行这条确定性回归测试；它会把模型调用替换成“一旦被调用就立即失败”的 transport，因此能证明 URL-only 分支没有调用模型：

```bash
cd /path/to/omd
.venv/bin/python -m pytest tests/test_ui.py::test_inbox_ai_does_not_call_model_for_url_only_item -q
```

任务 5 的哈希比较：

```bash
find "$TASK5_VAULT/Inbox" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-inbox-md-after.sha256"
find "$TASK5_VAULT/Inbox" -type f -name '*.omd.json' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-inbox-meta-after.sha256"
find "$TASK5_VAULT/Sources" -type f -name '*.md' -exec shasum -a 256 {} + | LC_ALL=C sort > "$OMD_UX_ROOT/05-sources-after.sha256"
diff -u "$OMD_UX_ROOT/05-inbox-md-before.sha256" "$OMD_UX_ROOT/05-inbox-md-after.sha256"
diff -u "$OMD_UX_ROOT/05-inbox-meta-before.sha256" "$OMD_UX_ROOT/05-inbox-meta-after.sha256"
diff -u "$OMD_UX_ROOT/05-sources-before.sha256" "$OMD_UX_ROOT/05-sources-after.sha256"
```

三个 `diff` 都应以退出码 0 结束并且没有输出。这里比较 Inbox `.md` 和 `Sources/*.md` 是为了证明原文未改，额外比较 `.omd.json` 是为了证明“只生成或拒绝生成草稿”连复核状态也没有偷偷改变。必须在后面的“把 AI 草稿写入新笔记”扩展验证之前执行。

通过清单：

- [ ] 本地 Ollama 下不显示 **Review cloud request** 或云端授权。
- [ ] 参与者知道 AI 只读取当前 Inbox 原文，或下拉框中明确选择且已经预览的 vault Markdown；不会读取任何未选择文件。
- [ ] 建议内容与摘录相关，而不是“缺少证据”模板。
- [ ] 至少一条原文依据能在只读原文中逐字找到。
- [ ] 参与者知道草稿和建议 tags 都可以编辑，但尚未写入 `Notes/`；只有 **Create note in Notes** 才会保存用户确认的内容。
- [ ] AI 成功反馈列出本次加入的 tags，tag 输入框紧邻 AI 面板，参与者无需向上翻一整页寻找。
- [ ] 仅含网址的项目明确提示需要具体文字。
- [ ] 仅含网址的项目显示没有发送内容，且不把失败包装成成功建议。
- [ ] 前后 Markdown 哈希值一致。
- [ ] 在尚未创建 Notes 笔记时，Inbox 配套元数据哈希也一致。

直接判失败：

- 接受空的原文依据、概括性伪引文或 `missing-evidence` 标签作为成功。
- 参与者认为 AI 自动打开了网址、自动选择了最近转换结果，或读取了任何未选择的 `Sources/` 文章。
- 参与者认为生成草稿就等于已经保存笔记。
- URL-only 点击后出现模型连接、模型缺失或 token 信息，而不是本地输入校验提示。

#### 扩展验证 A：刚转换的 Markdown 到 AI 草稿

用任务 2 刚刚生成的 `Sources/.../*.md` 做一次单独观察：

> 请根据刚才转换好的文章生成一份有原文依据的草稿，并告诉我它与源文件之间的关系。

当前版本支持从 vault 内的 `Sources/` 显式选择转换结果，不需要先复制成 Highlight。按下面步骤测试：

1. 确认测试副本的 `Sources/` 中有一篇非敏感 Markdown，并记录它的 SHA-256。
2. 在 **Choose a vault source (optional)** 中点击 **Suggest sources**，从统一列表中找到标为 `Converted source` 的目标文件。
3. 在 **Markdown source for AI and new-note link (optional)** 中选择目标文件。
4. 核对状态区显示完整 vault 相对路径，且 **Selected Markdown source (kept unchanged)** 只读预览与文件内容一致。
5. 点击 **Generate draft from selected text**；逐字验证每条 evidence 都能在这份只读预览中找到，而不是只在 Inbox 原文中寻找。
6. 生成后重新计算 `Sources` 文件哈希；必须与步骤 1 一致。
7. 检查成功反馈明确列出建议 tags，且这些 tags 已进入 AI 面板正下方的 **Tags for new note (editable)**。编辑其中一个 tag；可选勾选 **Add this edited AI draft to the new note**，再点击 **Create note in Notes**。新 Note 应同时包含 Inbox 原文、AI 草稿、已确认 tags 和指向所选 `Sources/.../*.md` 的 vault 相对 `[[wikilink]]`；被选中的源文件仍不得改变。

通过标准：

- [ ] 参与者能主动加载、明确选择并预览刚转换的 Markdown。
- [ ] 发送前能说清模型实际会收到哪份文字。
- [ ] AI evidence 是所选 Markdown 的逐字子串。
- [ ] 系统不会自动修改“看起来最相关”的笔记，也不会自动选择最近的文件。
- [ ] 只有点击 **Create note in Notes** 才写入新 Note；生成草稿、检索和预览都不写 vault。
- [ ] 新 Note 的 YAML frontmatter 包含用户确认的 tags，并以完整 vault 相对路径的 `[[wikilink]]` 指向所选 Markdown；确认消息显示新 Note 文件名、linked source 和 tags。
- [ ] 原 `Sources` Markdown 始终不被 AI 草稿或关联动作覆盖。

当前仍有一个明确边界：普通 **Convert** 如果把结果写到 vault 之外，不会自动导入这个下拉框。要测试连续 vault 工作流，应使用 **Capture to vault**，或由用户明确把结果保存到当前 vault 的 `Sources/`；OMD 不会在未授权的情况下扫描任意外部目录。

#### 扩展验证 B：把已生成的 AI 草稿加入新笔记

核心任务完成并保存哈希证据后，可继续验证用户报告的“有 AI 内容时点击创建没有反应”：

1. 重新选择完整 Highlight 并成功生成草稿。
2. 在草稿末尾加入容易搜索的非敏感标记，例如 `UX-AI-DRAFT-CHECK-2026`。
3. 勾选 **Add this edited AI draft to the new note**。
4. 点击 **Create note in Notes** 一次。
5. 按钮下方必须出现 `Created in Notes: <文件名>. The original remains in Inbox.`，队列状态变为 `note created`。
6. 打开新生成的 `Notes/*.md`，确认它同时包含 Inbox 原文、可选个人补充，以及带标记的 AI 草稿；Inbox 的 `.md` 哈希仍应与基线一致。
7. 如果没有反馈，使用任务 4 的证据矩阵区分“已经写入但反馈缺失”和“没有执行”，不要连续点击造成重复判断。

若不勾选加入草稿，创建出来的 Note 不应包含草稿。**Keep original · mark as not needed** 不会保存草稿或 `Add your note (optional)` 的内容；如果参与者以为这些内容已经保存，记录为重要的数据去向误解和界面说明缺口。

主持人还应运行两条确定性回归测试，分别证明“选中的转换结果可作为 AI 输入且不会被改写”和“创建的新 Note 只链接、不改写源文件”：

```bash
cd /path/to/omd
.venv/bin/python -m pytest tests/test_ui.py::test_inbox_ai_can_use_selected_converted_markdown_without_rewriting_it -q
.venv/bin/python -m pytest tests/test_inbox_workflow.py::test_promote_inbox_item_links_an_explicit_validated_vault_markdown_source -q
```

### 任务 6：从 Ollama 停止和模型缺失中恢复

测试目的：验证错误信息能否帮助普通用户在一分钟内恢复，同时保证原文不受影响且不会偷偷切换 AI 服务。

参与者任务：

> 本地 AI 现在不能工作。请根据 OMD 给出的信息判断问题、恢复它并重试。告诉我失败期间哪些数据被保留了。

主持步骤：

1. 在参与者进入任务前切换到 `06-ollama-errors`，并退出 Ollama；保留已安装模型。
2. 参与者触发错误时开始计算恢复时间，不解释原因。
3. 服务恢复并成功重试后，暂停计时，把模型改成一个确定未安装的假名称，再开始第二轮计时。
4. 再次让参与者独立判断、检查可用模型并恢复。
5. 重新计算 Inbox 原文哈希值。

通过清单：

- [ ] 能区分“服务未运行”和“模型未安装”。
- [ ] 能自行找到 **Advanced settings → AI provider for Inbox review**。
- [ ] 会使用 **Check models** 或错误中给出的命令。
- [ ] 每轮从错误出现到恢复不超过 1 分钟。
- [ ] OMD 没有自动下载、替换模型或切换到云端。
- [ ] 错误期间 Inbox 原文和已有 AI 草稿都被保留。
- [ ] 没有暴露 Python 异常堆栈，前后 Markdown 哈希值一致。

直接判失败：

- 错误只告诉参与者“失败”，没有可理解的原因或下一步。
- 需要主持人指出 Ollama、模型菜单或修复命令。
- 在未授权的情况下切换到云端 AI 服务。

### 任务 7：超长输入与取消（命令行技术边界）

测试目的：验证 `enrich-note` 的截断与取消契约。这个任务只评价命令行体验，不评价 Inbox UI。

参与者任务：

> 请使用 OMD 的命令行只读建议功能，为指定的长笔记生成建议。解释警告的含义。再运行一次并在完成前取消，然后确认是否留下半成品或修改了 vault。

主持步骤：

1. 切换到 `07-enrich-note-boundaries`，提供目标文件路径，开始计时。
2. 允许参与者自行使用 `omd --help` 或 README 找到命令。
3. 第一次运行完成后，要求参与者解释截断警告。
4. 第二次运行时让参与者自行取消。
5. 检查标准输出是否出现半成品，并重新计算目标文件哈希值。

通过清单：

- [ ] 明白只有“发送给模型的副本”被截断，磁盘原文没有截断。
- [ ] 能区分目标笔记、只读建议和原文依据。
- [ ] 取消后没有不完整建议被当作成功输出。
- [ ] 取消和完成两种情况下目标文件哈希值都不变。
- [ ] 警告对非开发者可读，没有只显示机器代码或 Python 异常堆栈。

直接判失败：

- 参与者认为 OMD 截短了磁盘上的原笔记。
- 取消后留下看似可用的半成品。
- 主持人必须翻译警告才让参与者理解。

### 任务 8：中英文、空格与长路径

测试目的：确认关键信息不会因语言混排或路径过长而不可恢复。

参与者任务：

> 请在这个包含中文、English、空格和长名称的测试 vault 中保存一条内容，再处理一个故意失败的输入。告诉我成功结果在哪里，以及错误要求你做什么。

主持步骤：

1. 切换到最长名称的 `08-...` vault，提供一段中英文混合内容。
2. 提供一个不存在的本地文件路径作为确定性失败输入。
3. 观察参与者如何查找完整 vault 路径、结果路径和错误修复信息。
4. 主持人核对保存结果和原文。

通过清单：

- [ ] 能完成一次 Inbox 保存或 vault Capture。
- [ ] 长 vault 路径可以横向滚动或复制，关键目录没有不可恢复地省略。
- [ ] 中文、English、空格和文件名均可读。
- [ ] 错误说明失败对象、原因、下一步和被保留的数据。
- [ ] 修复命令没有被截断或遮挡。
- [ ] 没有暴露 Python 异常堆栈。

直接判失败：

- 参与者无法确认实际保存位置。
- 关键路径只显示省略号且无法复制完整值。
- 中英文换行遮住错误原因或下一步。

## 6. 可选任务：云端请求预览，但不发送

本任务不计入 8 个核心任务成功率。仅在发布范围包含 OpenAI、Anthropic 或 DeepSeek 云端服务时执行。

参与者任务：

> 请把 Inbox 的 AI 服务改成一个云端 API，但不要真正发送内容。告诉我系统准备把什么发给谁、需要什么授权，以及退出预览后 vault 是否改变。

主持步骤：

1. 切换到 `optional-cloud-preview`，开始计时。
2. 让参与者独立选择云端服务和明确的模型。
3. 观察他能否找到并理解 **Review cloud request**。
4. 不提供真实 API key，不勾选授权，不点击生成。
5. 主持人核对 vault 没有变化。

通过清单：

- [ ] 预览说明服务提供方、模型、API 目标域名、字符数和附件状态。
- [ ] 预览提供数据政策链接，但不展示完整原文。
- [ ] 参与者知道使用的是开发者 API key，不是 ChatGPT 或 Claude 消费者登录。
- [ ] 本次请求授权默认未勾选，并且每次请求都需要重新授权。
- [ ] 没有生成请求、没有网络内容发送、没有 vault 修改。

## 7. 记录表

每完成一个任务立即填写：

| 字段 | 记录 |
| --- | --- |
| 任务编号 / 名称 | |
| 是否独立完成 | 是 / 否 |
| 开始时间 / 完成时间 / 总耗时 | |
| 查阅文档次数和页面 | |
| 主持人介入次数和内容 | |
| 第一次卡住时说出的原话 | |
| 参与者认为数据去了哪里 | |
| 实际数据位置 | |
| 是否理解原文、AI 草稿、命令行只读建议与 Notes 派生笔记 | |
| 是否出现 Python 异常堆栈、数据丢失或隐私误解 | |
| 完成后信心评分 1–5 及原因 | |
| 结论 | 通过 / 失败 / 外部阻塞 |
| 建议修复 | |

汇总表：

| 任务 | 独立完成 | 耗时 | 文档次数 | 数据去向正确 | 信心 1–5 | 结论 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| 1. 安装与 doctor | | | | | | |
| 2. 转换与外部 Capture | | | | | | |
| 3. Inbox 保存 | | | | | | |
| 4. 创建笔记 / 标记不需要 | | | | | | |
| 5. 本地 AI 草稿与原文依据 | | | | | | |
| 6. Ollama 错误恢复 | | | | | | |
| 7. `enrich-note` 长输入 / 取消 | | | | | | |
| 8. 中英文与长路径 | | | | | | |

## 8. 放行标准

- 核心任务独立成功率至少 90%。只有 8 个任务时，单名参与者必须 8/8 才能达到本轮门槛；7/8 仅为 87.5%。
- 首次有效结果不超过 5 分钟；PDF 和网页首次结果分别不超过 2 分钟。
- Ollama 错误恢复不超过 1 分钟。
- 普通用户界面和命令行的人类可读模式不得出现 Python 异常堆栈（traceback）。
- 数据丢失、真实 vault 误操作和远程/本地隐私误解必须为 0。
- 参与者必须能区分 Inbox 原文、AI 草稿、命令行只读建议和 `Notes/` 派生笔记。
- Inbox 最终动作必须同时具备正确的磁盘结果、队列状态和可见确认消息；三者缺一不能放行。
- 只读检索不得暗示已经建立链接；如果发布承诺关联到现有笔记，必须提供显式选择、变更预览、确认和结果路径。
- 本地 Ollama 不得出现云端授权；云端 AI 服务不得在缺少预览和逐次授权时发送内容。
- 仅含网址的 Inbox 项目不得被包装成有用 AI 建议。
- 外部网站临时不可用应记为“外部阻塞”，更换稳定来源重测；不能直接算产品成功，也不能在没有重测时归因于产品失败。

一名参与者只能发现形成性问题，不能证明总体成功率。发布前建议至少邀请 5 名彼此独立的新手，共完成 40 次核心任务尝试，其中至少 36 次独立成功，并继续满足全部零容忍项。

## 9. 问题分级与复测

按以下顺序处理问题：

1. **立即阻塞发布**：数据丢失、真实 vault 误操作、隐私误解、未经授权的数据发送、Python 异常堆栈暴露。
2. **核心流程阻塞**：无法恢复的错误、关键路径不可见、无法区分预览与写入、核心任务超时。
3. **重要可用性问题**：控件含义错误、状态不可预测、参与者无法找到数据。
4. **一般体验问题**：文案不顺、布局密度、低信心但仍能独立完成。

修复后必须换一名没有看过修复的人重测同一任务。原参与者已经形成学习记忆，不能作为修复通过的唯一证据。
