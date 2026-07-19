#!/usr/bin/env python3
"""Wrapper around MarkItDown for use inside Overture tasks.

Two modes:

  CLI mode (default, no LLM)
      python scripts/markitdown_convert.py input.pdf -o out.md

  OCR / vision mode (Python API, Ollama vision model via OpenAI-compatible endpoint)
      python scripts/markitdown_convert.py scan.pdf -o out.md \\
          --ocr --vision-model gemma3:4b

Setup (conda env):
    conda create -n markitdown python=3.12 -y
    conda activate markitdown
    pip install 'markitdown[all]==0.1.5'
    pip install markitdown-ocr openai            # only needed for --ocr

Vision backend defaults to local Ollama (http://localhost:11434/v1, model gemma3:4b).
Override via --vision-base-url / --vision-api-key / --vision-model or env vars
MARKITDOWN_VISION_BASE_URL / MARKITDOWN_VISION_API_KEY / MARKITDOWN_VISION_MODEL.
MarkItDown plugins are disabled by default; pass --enable-plugins only for
trusted plugin installations and trusted input files.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_cli(input_path: str, output: str | None, extension: str | None) -> int:
    bin_path = shutil.which("markitdown")
    if not bin_path:
        print(
            "error: `markitdown` not on PATH. Install: "
            "conda activate markitdown && pip install 'markitdown[all]==0.1.5'",
            file=sys.stderr,
        )
        return 127
    cmd = [bin_path, input_path]
    if extension:
        cmd += ["-x", extension]
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-o", output]
    return subprocess.call(cmd)


DEFAULT_OCR_PROMPT = (
    "Extract ALL visible text in this image verbatim, preserving original "
    "language (Chinese / English / etc.) and reading order. If text is in "
    "multiple regions, separate with blank lines. Do NOT translate, summarize, "
    "or add commentary. If there is no text, output exactly: NO_TEXT."
)


def run_ocr(
    input_path: str,
    output: str | None,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str | None,
    enable_plugins: bool = False,
) -> int:
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("error: `markitdown` not importable. pip install 'markitdown[all]'", file=sys.stderr)
        return 127
    try:
        from openai import OpenAI
    except ImportError:
        print("error: `openai` not importable. pip install openai", file=sys.stderr)
        return 127

    client = OpenAI(base_url=base_url, api_key=api_key)
    md = MarkItDown(
        enable_plugins=enable_plugins,
        llm_client=client,
        llm_model=model,
    )
    result = md.convert(input_path, llm_prompt=prompt or DEFAULT_OCR_PROMPT)
    text = result.text_content or ""
    if output:
        from omd._io import write_atomic
        out_path = Path(output)
        write_atomic(out_path, text)
        print(f"wrote {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Convert a file/URL to Markdown via MarkItDown.")
    p.add_argument("input", help="File path or URL (file://, http(s)://, YouTube).")
    p.add_argument("-o", "--output", help="Write markdown to this path instead of stdout.")
    p.add_argument("-x", "--extension", help="Force converter for given extension (e.g. pdf, html).")
    p.add_argument(
        "--ocr",
        action="store_true",
        help="Enable image/PDF/DOCX OCR via vision LLM (markitdown-ocr plugin).",
    )
    p.add_argument(
        "--vision-base-url",
        default=os.environ.get("MARKITDOWN_VISION_BASE_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible base URL (default: local Ollama).",
    )
    p.add_argument(
        "--vision-api-key",
        default=os.environ.get("MARKITDOWN_VISION_API_KEY", "ollama"),
        help="API key (default: 'ollama' placeholder for local).",
    )
    p.add_argument(
        "--vision-model",
        default=os.environ.get("MARKITDOWN_VISION_MODEL", "gemma3:4b"),
        help="Vision model name (default: gemma3:4b).",
    )
    p.add_argument(
        "--vision-prompt",
        default=os.environ.get("MARKITDOWN_VISION_PROMPT"),
        help="Override LLM prompt. Default extracts visible text verbatim.",
    )
    p.add_argument(
        "--enable-plugins",
        action="store_true",
        default=os.environ.get("OMD_MARKITDOWN_ENABLE_PLUGINS", "").lower() in {"1", "true", "yes", "on"},
        help=(
            "Enable installed MarkItDown plugins. Disabled by default because "
            "plugins are a parser/supply-chain surface for untrusted files."
        ),
    )
    args = p.parse_args()

    if args.ocr:
        return run_ocr(
            args.input,
            args.output,
            args.vision_base_url,
            args.vision_api_key,
            args.vision_model,
            args.vision_prompt,
            args.enable_plugins,
        )
    return run_cli(args.input, args.output, args.extension)


if __name__ == "__main__":
    sys.exit(main())
