# OMD demo surfaces

This folder splits OMD demos into two trust zones.

## Hosted Sample Demo

`demo/huggingface-space/` runs the existing Gradio UI in hosted-safe mode:

```bash
OMD_PUBLIC_DEMO=1 python app.py
```

Use this for a sample try-it-online link. It accepts public URLs plus
document/image uploads, writes outputs to temporary server storage, returns a
Markdown preview, and exposes a download file. Media transcription is disabled
by default. Do not upload sensitive, private, or regulated files to the hosted
demo.

Hosted mode intentionally blocks:

- browser cookie extraction
- raw cookie file paths
- Douyin/XHS authenticated conversion
- local filesystem paths typed into the source box
- local Ollama polish
- media retention
- audio/video transcription until a Linux whisper backend is wired and enabled

## Full Power Demo

`demo/full-power/` is the local machine path launched from the public page. It
keeps sensitive operations on the user's computer:

```text
public page -> user starts local OMD -> http://127.0.0.1:7860 -> local files/cookies/Ollama
```

Use this for real work: Douyin/XHS cookies, browser cookie extraction, local
file paths, local output folders, local Ollama, vault capture, and long-running
local conversions.

## Public Page

`demo/public-page/` is a static GitHub Pages launcher. It can link to or embed a
Hugging Face Space and explain when users should switch to Full Power Demo.
