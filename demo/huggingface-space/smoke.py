#!/usr/bin/env python3
"""Smoke-test the hosted OMD sample demo through its Gradio API."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from gradio_client import Client, handle_file


RUN_API = "/run_with_status"
MERGE_FILES_API = "/_merge_source_file_queue_for_ui"
CLEAR_FILES_API = "/_clear_source_file_queue"
STATE_INPUT_INDEXES = frozenset({1, 7, 25})


def run_args(
    *,
    text_input: str = "",
    file_input: Any = None,
    cookies_file: str = "",
    cookies_browser: str = "(none)",
    ollama_host: str = "",
    xhs_cookies_file: str = "",
) -> list[Any]:
    return [
        text_input,
        file_input,
        "",
        "Convert to .md file",
        "",
        "",
        "",
        "Markdown (.md)",
        False,
        False,
        "",
        False,
        "",
        False,
        "",
        False,
        False,
        False,
        cookies_file,
        cookies_browser,
        "eng",
        "",
        "small",
        ollama_host,
        False,
        False,
        "OP only",
        xhs_cookies_file,
    ]


def api_args(**kwargs: Any) -> list[Any]:
    """Return run arguments exposed by Gradio's API, which omits State inputs."""
    return [value for index, value in enumerate(run_args(**kwargs)) if index not in STATE_INPUT_INDEXES]


def fail(message: str) -> None:
    raise AssertionError(message)


def assert_success(result: tuple[Any, ...], expected: str) -> None:
    log, preview, _out_path, status_html, download = result
    preview_text = preview.get("value", "") if isinstance(preview, dict) else str(preview)
    download_path = download.get("value") if isinstance(download, dict) else download
    if "omd-status-ok" not in str(status_html):
        fail(f"conversion did not report ok status:\n{log}")
    if expected not in preview_text:
        fail(f"preview did not contain {expected!r}:\n{preview_text}")
    if not download_path:
        fail("conversion did not return a download file")


def expect_rejected(client: Client, label: str, args: list[Any], expected: str) -> None:
    try:
        client.predict(*args, api_name=RUN_API)
    except Exception as exc:  # noqa: BLE001 - gradio_client wraps app errors.
        if expected not in str(exc):
            fail(f"{label} rejected with wrong error:\n{exc}")
        print(f"ok reject {label}: {expected}")
        return
    fail(f"{label} was allowed but should have been rejected")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "url",
        help="Hosted Gradio URL to test.",
    )
    parser.add_argument(
        "--public-url",
        default="https://example.com/",
        help="Stable public URL to convert during the smoke test.",
    )
    args = parser.parse_args()

    client = Client(args.url)

    with tempfile.TemporaryDirectory() as tmp:
        upload = Path(tmp) / "omd-smoke-upload.html"
        upload.write_text(
            "<html><body><h1>OMD Smoke Upload</h1><p>Hosted upload works.</p></body></html>",
            encoding="utf-8",
        )
        client.predict(
            [handle_file(str(upload))],
            api_name=MERGE_FILES_API,
        )
        upload_result = client.predict(
            *api_args(),
            api_name=RUN_API,
        )
        assert_success(upload_result, "OMD Smoke Upload")
        print("ok upload file")
        client.predict(api_name=CLEAR_FILES_API)

    public_result = client.predict(
        *api_args(text_input=args.public_url),
        api_name=RUN_API,
    )
    assert_success(public_result, "Example Domain")
    print("ok public url")

    expect_rejected(
        client,
        "cookie file",
        api_args(text_input=args.public_url, cookies_file="/tmp/cookies.txt"),
        "disables cookie files",
    )
    expect_rejected(
        client,
        "browser cookies",
        api_args(text_input=args.public_url, cookies_browser="chrome"),
        "disables browser cookie extraction",
    )
    expect_rejected(
        client,
        "douyin",
        api_args(text_input="https://v.douyin.com/abc123/"),
        "cookie-gated sources",
    )
    expect_rejected(
        client,
        "ollama",
        api_args(text_input=args.public_url, ollama_host="http://localhost:11434"),
        "disables local Ollama",
    )

    print("hosted sample demo smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
