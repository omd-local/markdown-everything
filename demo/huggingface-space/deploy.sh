#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---deploy}"
SPACE_ID="${SPACE_ID:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CREATED_STAGE=0
if [[ -z "${STAGE_DIR:-}" ]]; then
  STAGE_DIR="$(mktemp -d)"
  CREATED_STAGE=1
else
  mkdir -p "$STAGE_DIR"
  if [[ -n "$(find "$STAGE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "STAGE_DIR must be empty: $STAGE_DIR" >&2
    exit 2
  fi
fi

if [[ "$MODE" != "--deploy" && "$MODE" != "--stage-only" ]]; then
  echo "usage: $0 [--deploy|--stage-only]" >&2
  exit 2
fi

cleanup() {
  if [[ "$CREATED_STAGE" == "1" && "${KEEP_STAGE:-0}" != "1" ]]; then
    rm -rf "$STAGE_DIR"
  fi
}
trap cleanup EXIT

copy_repo_source() {
  mkdir -p "$STAGE_DIR/src"
  cp "$REPO_ROOT/pyproject.toml" "$STAGE_DIR/src/pyproject.toml"
  cp "$REPO_ROOT/README.md" "$STAGE_DIR/src/README.md"
  cp -R "$REPO_ROOT/omd" "$STAGE_DIR/src/omd"
  find "$STAGE_DIR/src" \( \
    -name "__pycache__" -o \
    -name "*.pyc" -o \
    -name ".pytest_cache" -o \
    -name ".DS_Store" -o \
    -name "._*" \
  \) -prune -exec rm -rf {} +
}

copy_space_files() {
  cp "$SCRIPT_DIR/Dockerfile" "$STAGE_DIR/Dockerfile"
  cp "$SCRIPT_DIR/README.md" "$STAGE_DIR/README.md"
  cp "$SCRIPT_DIR/app.py" "$STAGE_DIR/app.py"
  cp "$SCRIPT_DIR/requirements.txt" "$STAGE_DIR/requirements.txt"
  cp "$SCRIPT_DIR/smoke.py" "$STAGE_DIR/smoke.py"
}

copy_space_files
copy_repo_source

if [[ "$MODE" == "--stage-only" ]]; then
  echo "Staged Hugging Face Space context: $STAGE_DIR"
  exit 0
fi

if [[ -z "$SPACE_ID" ]]; then
  echo "Set SPACE_ID to the organisation-owned Hugging Face Space, for example omd-local/omd-public-demo." >&2
  exit 2
fi

hf auth whoami >/dev/null
if hf spaces info "$SPACE_ID" >/dev/null 2>&1; then
  echo "Updating existing Space: $SPACE_ID"
else
  hf repos create "$SPACE_ID" --type space --space-sdk docker --public --exist-ok
fi

"${PYTHON:-python}" "$SCRIPT_DIR/upload_existing.py" "$SPACE_ID" "$STAGE_DIR"

echo "Space uploaded: https://${SPACE_ID/\//-}.hf.space/"
