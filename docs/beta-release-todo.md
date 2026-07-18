# OMD `0.3.0b2` 发布清单

这份清单用于判断 OMD 是否可以发布给外部 beta 用户。当前定位是
**local-first AI context inbox**，不是稳定版、云端 SaaS、法律取证或长期归档系统。

建议 Git tag：`v0.3.0-beta.1`。

## 当前发布范围

- 本地文件、URL、share text 和最多五个本地文件的 UI 队列。
- 转换为普通 Markdown，或直接写入用户选择的 Obsidian vault 文件夹。
- PDF、Office、图片 OCR、音频/Podcast 转录，以及已支持的公开网页和社媒 adapter。
- 可选本地 Ollama Markdown polish 和 memory cards；核心转换不依赖 LLM。
- Manifest v2、`.omd.json` sidecar、来源 URL 和本地处理记录。
- MCP surface，默认限制可访问路径和非公开网络地址。

不承诺：绕过付费墙、登录、平台限制；完整保存动态网页；把 AI 生成内容视为原始证据。

## 自动化发布 Gate

以下项目必须全部通过：

- [x] `pyproject.toml` 与 `omd.__version__` 均为 `0.3.0b2`。
- [x] CI 覆盖 Python 3.10、3.11、3.12、3.13。
- [x] CI 构建 wheel，并在干净 venv 中 smoke-test `omd` 与 `omd-mcp`。
- [x] CI 审计本地 UI 环境和 Hugging Face demo requirements。
- [x] Hosted demo 和 MCP 默认拒绝 loopback、private、link-local、reserved 与无法解析的 URL。
- [x] 本地 UI 只允许本机 Ollama；CLI 远程模型需要 `--allow-remote-ollama` 与 HTTPS。
- [x] 版本一致性有回归测试。
- [x] 当前 worktree 的全量测试、compile、wheel smoke、dependency audit 全部通过并记录结果。
- [ ] Strix 在排除 secrets、cookies、vault 和输出文件的隔离源码副本上完成快速扫描。
- [x] 独立 code reviewer 与 security reviewer 没有 code-level release blocker。
- [ ] 最终 release verifier 在 Strix artifact 存在、CI workflow 被纳入 commit 后返回 PASS。

2026-07-15 自动化证据：

- `.venv/bin/python -m pytest -q`：`637 passed in 7.22s`。
- `compileall` 与 `git diff --check`：通过。
- 构建 `omd-0.3.0b2-py3-none-any.whl`，在 Python 3.12 环境重新安装后，
  `omd --help`、`omd-mcp --help`、版本导入和 `pip check` 均通过。
- `pip-audit`：依赖项没有已知漏洞；本地未发布的 `omd 0.3.0b2` 包本身按工具规则跳过。
- Hugging Face Space 提交 `4c49452` 在 CPU Basic 上为 `RUNNING`；公网 smoke test
  已通过文件上传、公共网页转换，以及 cookies、Douyin、本地 Ollama 拒绝边界。
- 独立 code reviewer：`CLEAR`；独立 security reviewer：`CLEAR`，其安全定向测试为
  `122 passed, 127 deselected`。
- Strix 1.0.4 只收到无 credentials、cookies、vault、日志、数据库或输出文件的 1.6 MB
  临时源码副本，telemetry 已关闭。官方工具警告本机 `qwen3:4b-instruct` 不是推荐模型；
  默认 4096 context 无法容纳约 33.3k-token 的首轮请求。40,960 context 虽可在 16 GB
  机器上以 8.6 GB、100% GPU 载入，但单轮仍需数分钟并会占用用户的 Ollama/UI 资源，
  因此扫描被明确停止，未产生有效 finding artifact。`0 vulnerabilities / 0 tokens` 不计为通过。

Strix gate 的可接受完成方式：在隔离快照上使用 Strix 推荐的模型，或在能高效承载长上下文
本地模型的机器上重新执行；不得为了过 gate 在未获授权时把私有源码发送到远程模型。

维护者命令：

```bash
PYTHONPATH=. python -m pytest -q
python -m compileall -q -x '(^|/)\._' omd tests
python -m pip check
python -m pip_audit --local --progress-spinner off
python -m pip_audit -r demo/huggingface-space/requirements.txt --progress-spinner off
python -m pip wheel --no-deps --wheel-dir dist .
```

## 人工 UI Smoke Test

只使用临时输出目录或测试 vault：

```bash
mkdir -p /tmp/omd-beta-smoke-vault /tmp/omd-beta-output
cd /Volumes/Transcend_q/APPS/AI/omd
.venv/bin/python -m omd.ui
```

### 1. 首屏与输入

- [ ] 默认 All tab 显示主要流程，其他 panel 折叠但没有消失。
- [ ] 标题、输入说明、按钮和警告不重叠，窗口缩窄后仍可阅读。
- [ ] 粘贴 URL、share text 或本地路径时，用户能马上看懂下一步。
- [ ] `Saved URL list file` 只在 Advanced settings 内出现。
- [ ] OCR 说明明确它用于读取图片或扫描件中的文字，默认语言是 `eng`。
- [ ] 中文/英文 OCR 示例显示 `chi_sim+eng`。

### 2. 多文件队列

- [ ] 拖入或选择 1 个 PDF，显示 `1 file queued`。
- [ ] 用 `+ Add files` 连续加入第 2、3、4、5 个文件。
- [ ] 每个文件只显示一次；按钮文本居中，文件名和大小不遮挡删除按钮。
- [ ] 第 6 个文件被清楚拒绝，并且前五个仍保留。
- [ ] 单个 `x` 只删除对应文件；`Clear` 清空整个队列。
- [ ] PDF、图片和另一种非 URL 格式能组成同一队列。

### 3. 转换结果

- [ ] Run panel 运行前显示 queued 总数，运行中显示当前 item 和总数。
- [ ] `item 1/5` 不与 `0/5 done` 造成“已完成”的误解。
- [ ] 结束时显示总耗时、成功数、失败数和 partial-output 状态。
- [ ] 单个失败不阻止其他文件继续处理。
- [ ] 过大的 Markdown 无法 polish 时保留原始转换结果，并标为 partial success，而不是两个 items。
- [ ] `Open output folder` 能打开正确目录。
- [ ] `Download Markdown` 对单文件可用；多文件结果可逐项取得或有明确说明。

### 4. Vault / Obsidian

- [ ] `Capture to vault note` 明确表示写入 Obsidian-compatible vault 文件夹。
- [ ] vault 设为 `/tmp/omd-beta-smoke-vault` 后，note 写入 `Sources/<type>/`。
- [ ] 标题用于可读文件名，front matter 只保留用户有用字段。
- [ ] 详细 trace/debug 信息位于相邻 `.omd.json`，不会污染阅读正文。
- [ ] 重复 capture 不覆盖旧 note。

### 5. 本地模型与 fallback

- [ ] 未启动 Ollama 时，黄色 warning 清楚说明在 Terminal 运行 `ollama pull <model>`。
- [ ] UI 根据本机总内存给出保守 instruct 模型建议，但不自动下载。
- [ ] UI 默认输出语言是 English，用户能找到输出语言设置。
- [ ] 关闭 Markdown polish 后，转换不调用 Ollama。
- [ ] 模型超时或某个 chunk 失败时，原始 Markdown 保留，后续 items 继续执行。
- [ ] warning 区分模型缺失、Ollama 未运行、超时、缺少 tags/Evidence。
- [ ] UI 拒绝非 localhost Ollama；CLI 只有 HTTPS + 明确 opt-in 才允许远程 endpoint。

### 6. URL adapters

- [ ] 普通文章保留标题、作者、日期、来源链接、标题层级、引用、代码块、脚注与内容图片 URL。
- [ ] Reddit 短链接先解析 canonical URL；403 时显示平台限制和 fallback 结果。
- [ ] Reddit 的 `OP only` 与 `OP + Top comments` 均按选择输出作者、时间、层级、permalink 和删除/编辑标记。
- [ ] X 长帖不只保存第一段；若公开 endpoint 返回截断内容，UI 明确标记 partial，而不是假装完整。
- [ ] Podcast 输出 show、episode、主持人/嘉宾、发布日期、原链接、speaker/timestamp 和 transcript source。
- [ ] Podcast 语言/质量检查可识别明显重复或来源串线，失败时保留 raw transcript 并警告。
- [ ] 来源包含 Douyin 或 XHS 但没有对应 cookie 文件时，运行前显示针对该平台的 warning。

### 7. 安全与产品边界

- [ ] Personal note notice 使用清晰的 NZ English，说明输出可能遗漏、重排或重新格式化。
- [ ] notice 说明用户负责访问、处理和保存内容的权利。
- [ ] notice 明确 OMD 不绕过 paywall、access control 或 platform restriction。
- [ ] public/hosted 模式拒绝 `localhost`、局域网 IP 和重定向到私网的 URL。
- [ ] 日志和下载文件不包含 cookie 内容、token 或用户 vault 路径之外的数据。

## 发布操作

1. 检查 diff 和未跟踪文件，确认没有 cookies、vault 内容、模型文件或安全扫描原始 secrets。
2. 记录最终自动化验证与人工 smoke 结果。
3. 更新 `CHANGELOG.md` 中的已知限制。
4. 使用 Lore commit message 提交，再创建 annotated tag：

```bash
git tag -a v0.3.0-beta.1 -m "OMD 0.3.0 beta 1"
git show --stat v0.3.0-beta.1
```

5. 只有本地 gate 与远程 CI 都通过后，才 push tag 或恢复 Hosted demo 链接。

## 不发布条件

出现任一项就暂停 public beta：

- 转换失败会覆盖或删除用户原文件/旧 note。
- cookie、token、私有文档内容进入仓库、公开日志或 hosted demo。
- hosted/MCP URL 能访问私网或云 metadata endpoint。
- 远程模型能在无明确 opt-in 时收到源内容。
- 主要输入类型在 UI 中没有可理解的失败说明或 fallback。
- wheel 无法在干净环境安装，或 CI / dependency audit 未通过。
- Hugging Face Space 不是 `RUNNING`，或 hosted smoke test 未通过。
