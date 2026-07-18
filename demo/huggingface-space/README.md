---
title: OMD Public Demo
emoji: 📝
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
fullWidth: true
suggested_hardware: cpu-basic
short_description: Convert public pages and documents to traceable Markdown.
pinned: false
license: mit
tags:
  - markdown
  - document-conversion
  - local-first
---

# Hosted OMD sample demo on Hugging Face Spaces

This Space is a deliberately limited public sample of the current `omd-ui`
Gradio app. It converts public web pages and uploaded documents into Markdown.

Supported uploads include PDF, DOCX, PPTX, XLS/XLSX, HTML, CSV, JSON, XML,
EPUB, MSG, ZIP, and common image formats. Images use local Tesseract OCR.
The public Space does not load an LLM and does not transcribe audio or video.

## Files

- `app.py` starts the Gradio app with `OMD_PUBLIC_DEMO=1`.
- `requirements.txt` installs Python packages needed by the hosted UI.
- `Dockerfile` installs Tesseract and runs as Hugging Face user ID 1000.

## Space setup

Create a Hugging Face Space:

- SDK: Docker
- Visibility: public
- Hardware: CPU Basic (availability depends on the account plan)

Copy the files in this folder into the Space repo root, then commit and push.
The deploy script stages the current local OMD package under `src/` and uploads
that self-contained build context. The Dockerfile installs OMD from `/src`, so
the Space build does not need GitHub branch access.

## Safety defaults

The hosted sample demo blocks cookies, browser-cookie extraction, raw local
paths, local Ollama calls, Douyin/XHS auth, kept media files, and all media
transcription.

Do not upload sensitive, private, or regulated files to the hosted Space. Use
the Full Power local demo for real personal or client material.

Uploaded files are temporary and may disappear whenever the Space restarts.
Do not enable cookies, local-model endpoints, or media transcription on this
public instance. Use the local app for those workflows.

## CLI deployment

After logging in with `hf auth login`, create or update the Space from this
folder:

```bash
SPACE_ID=omd-local/omd-public-demo ./deploy.sh --deploy
```

`SPACE_ID` is required deliberately so a release cannot fall back to a
maintainer's personal Hugging Face account. Replace the example with the Space
owned by the organisation that is publishing the demo.

Build a local deployment context without contacting Hugging Face:

```bash
STAGE_DIR=/tmp/omd-space KEEP_STAGE=1 ./deploy.sh --stage-only
```

The expected hosted sample URL for the public page is:

```text
https://omd-local-omd-public-demo.hf.space/
```

After the Space finishes building, run the hosted smoke test:

```bash
python smoke.py https://omd-local-omd-public-demo.hf.space/
```

The smoke test verifies uploaded HTML conversion, public URL conversion, and
hosted-sample rejection for cookie files, browser cookie extraction, Douyin, and
local Ollama.
