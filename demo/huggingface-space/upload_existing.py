#!/usr/bin/env python3
"""Upload the staged demo to an existing Hugging Face Space."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import HfApi


REMOTE_DELETE_PATTERNS = ("*",)


def upload_existing_space(space_id: str, stage_dir: Path) -> str:
    stage_dir = stage_dir.resolve()
    if not stage_dir.is_dir():
        raise ValueError(f"staging directory does not exist: {stage_dir}")

    result = HfApi().upload_folder(
        repo_id=space_id,
        repo_type="space",
        folder_path=stage_dir,
        delete_patterns=list(REMOTE_DELETE_PATTERNS),
        commit_message="Deploy OMD hosted sample demo",
    )
    return result.commit_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("space_id", help="Existing Space in owner/name form")
    parser.add_argument("stage_dir", type=Path, help="Allowlisted deployment directory")
    args = parser.parse_args()

    commit_url = upload_existing_space(args.space_id, args.stage_dir)
    print(f"Space commit: {commit_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
