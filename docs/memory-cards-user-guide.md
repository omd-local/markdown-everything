# `--memory-cards` 简易用户手册

## 它是做什么的？

`--memory-cards` 是 OMD 的可选本地 AI 功能。

普通 `omd capture` 会把网页、PDF、Podcast、图片 OCR、短视频 URL 等内容转成 Markdown，并保存到你的本地 vault。

加上 `--memory-cards` 后，OMD 会在保留原始内容的同时，额外让本地 Ollama 模型生成：

- `## Summary`：简短总结
- `## Generated Tags`：自动生成的标签
- `## Memory Cards`：适合 Obsidian / AI 工具长期复用的知识卡片

原始内容仍然会完整保存在：

```markdown
## Full Content
```

所以它不是替换原文，而是在原文前面加一层 AI 整理结果。

## 什么时候应该用？

适合用在：

- 你想把长文章、Podcast、YouTube、PDF 收进 Obsidian vault。
- 你希望以后 Claude / Codex / Cursor / ChatGPT 能更快理解这份资料。
- 你想自动提取概念、人物/机构、claims、问题。
- 你愿意接受“AI 生成内容需要人工复查”。

不建议用在：

- 你只想要原始 Markdown。
- 你不想启动 Ollama。
- 你处理的是非常敏感内容，但 Ollama host 不是本机。
- 你不想让任何模型读这份内容。

## 第一次使用前

OMD 不会自动下载模型。你需要自己明确安装：

```bash
ollama pull qwen3:4b-instruct
```

推荐模型：

- `qwen3:4b-instruct`：16 GB 机器的文本示例，适合中文/英文混合总结和 memory cards。
- OMD 会根据系统总内存保守推荐 1.5B、3B、4B、7B 或 14B instruct 模型；用户显式选择始终优先。
- `gemma3:4b`：更适合图片/OCR 相关增强。
- `bge-m3`：未来用于多语言本地搜索，不是当前 memory cards 必需项。

不建议一开始就用 14B、27B 这类大模型。`--memory-cards` 不是向量检索，
它会把转换后的 Markdown 发给 Ollama 生成 summary、tags 和 cards；模型越大，
首 token 和完整输出都会越慢。16GB 内存/统一内存机器上，27B 模型很容易
表现为 UI 长时间“卡住”，但实际是在等 Ollama 推理或换页。先用 OMD 推荐的模型
验证流程，再按需要手动切换更大的模型。

## 基础用法

把一个来源 capture 到 vault，不启用 AI：

```bash
omd capture report.pdf --vault ~/Obsidian/AI-Memory
```

启用 memory cards：

```bash
omd capture report.pdf \
  --vault ~/Obsidian/AI-Memory \
  --memory-cards
```

指定模型：

```bash
omd capture "https://podcasts.apple.com/..." \
  --vault ~/Obsidian/AI-Memory \
  --memory-cards \
  --memory-model qwen3:4b-instruct
```

指定 Ollama host：

```bash
omd capture "https://example.com/article" \
  --vault ~/Obsidian/AI-Memory \
  --memory-cards \
  --memory-host http://localhost:11434
```

`http://localhost:11434` 的意思是“这台电脑上的 Ollama 服务”。它不是一个
应该用浏览器打开的网站；浏览器打开空白或 404 都不代表 Ollama 不可用。
判断方式是：

```bash
ollama list
```

本地 UI 只接受 localhost。CLI 如需连接其他机器，必须同时使用 HTTPS endpoint
和 `--allow-remote-ollama`；内容会发到那台机器，所以敏感资料建议保持默认 localhost。

## 输出长什么样？

生成的 note 大致会是：

```markdown
---
title: "Example"
source_type: "pdf"
captured_at: "2026-07-04T00:00:00Z"
local_source_path: "/Users/me/Downloads/example.pdf"
tags:
  - "local-ai"
  - "research"
  - "pdf"
---

# Example

> Source: `/Users/me/Downloads/example.pdf` · Captured: `2026-07-04T00:00:00Z`

## Summary

这份资料的简短总结。

## Generated Tags

- `local-ai`
- `research`

## Memory Cards

### Concepts
- [[Local AI Memory]]: ...
  Evidence: source section above.

### Claims
- Claim: ...
  Evidence: source section above.

### Questions
- ...

## Full Content

这里是原始转换出来的完整 Markdown。
```

`memory_model`、`model_endpoint`、`memory_error`、`capture_id` 这类 debug/trace
字段会写入旁边的 `.omd.json` sidecar，不会塞进用户阅读的 note。

## 如果 Ollama 不可用会怎样？

OMD 不会因为 memory cards 失败就丢掉原文。

如果 Ollama 没启动、模型没安装、host 连不上，OMD 会：

- 继续写入原始 capture note
- 保留 `## Full Content`
- 在 front matter 里记录失败
- 提示你在 Terminal 中显式运行推荐的 `ollama pull <model>` 命令

失败时你会看到类似 metadata：

```yaml
memory_attempted: true
memory_cards: false
summary_generated: false
memory_error: "..."
llm_used: "qwen3:4b-instruct"
model_endpoint: "local_ollama"
```

这表示：AI 整理失败了，但原始内容仍然已经保存。

## Drift warning 是什么？

Memory cards 是 AI 生成内容，可能不完整或不可靠。

OMD 会在这些情况下提醒你复查：

- 生成内容太短
- 没有生成 tags
- cards 里缺少 `Evidence:`
- 模型返回空内容
- 原文太长，memory cards 只基于前几个 chunks 生成

看到 warning 时，优先相信 `## Full Content` 里的原文。

## UI 里怎么用？

在本地 UI 中：

1. 打开 OMD UI。
2. Action 选择 `Capture to vault note`。
3. 选择你的 vault folder。
4. 勾选 `Memory cards`。
5. Memory model 保持 OMD 的本机推荐，或填入你已安装的 instruct 模型。
6. Ollama host 通常保持 `http://localhost:11434`。
7. 点击 `Capture to vault`。

注意：hosted sample demo 不支持写本地 vault，也不支持本地 Ollama。

## 安全和隐私

默认情况下，`--memory-cards` 适合本地使用：

```text
OMD -> local Ollama -> local vault
```

如果你明确允许 CLI 把 `--memory-host` 指向远程机器，内容会发送到那个 endpoint：

```bash
--memory-host https://ollama.example.com --allow-remote-ollama
```

这时 metadata 会记录：

```yaml
model_endpoint: "remote_ollama"
```

敏感资料建议只用本机 Ollama。

## 一句话总结

`--memory-cards` 是把“转换成 Markdown”升级成“保存为可长期复用的 AI memory note”的开关；它会生成总结、标签和知识卡片，但永远保留原文，并且不会自动下载模型。
