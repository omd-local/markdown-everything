import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEXT_PATHS = (
    ROOT / ".github",
    ROOT / "demo",
    ROOT / "docs",
    ROOT / "examples",
    ROOT / "omd",
    ROOT / "packaging",
    ROOT / "CHANGELOG.md",
    ROOT / "DESIGN.md",
    ROOT / "LICENSE",
    ROOT / "Makefile",
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "pyproject.toml",
    ROOT / "setup.py",
)
TEXT_SUFFIXES = {
    "",
    ".html",
    ".json",
    ".md",
    ".plist",
    ".py",
    ".rb",
    ".sh",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
EXPECTED_PROJECT_OWNER = "omd-local"
RETIRED_OWNER_DIGEST = "4c9c15e9ed4f6da6aa6bf4f6b5e915cc35c79a6304d5cdda60d500c3293b6189"
RETIRED_LOCAL_USER_DIGEST = "dd2d8c67929c0f9ff86e7cdd37a65efe4cf07bbf94f2b2703efb47340dc00cd8"
RETIRED_PUBLIC_HANDLE_DIGEST = "d8676870c6ed1ee3b28fc1f3883273aba2d523af56684eb76f4806e904839fa5"
RETIRED_PUBLIC_HANDLE_LENGTH = 10
PROJECT_OWNER_RE = re.compile(
    r"github\.com/([^/\s\"')]+)/(?:markdown-everything|homebrew-omd)"
)
LOCAL_USER_RE = re.compile(r"/Users/([^/\s\"')]+)/")


def _public_text_files():
    for path in PUBLIC_TEXT_PATHS:
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if (
                candidate.is_file()
                and not candidate.name.startswith("._")
                and candidate.suffix in TEXT_SUFFIXES
            ):
                yield candidate


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_public_project_urls_use_organisation_owner():
    mismatches = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        for owner in PROJECT_OWNER_RE.findall(text):
            if owner != EXPECTED_PROJECT_OWNER:
                mismatches.append((path.relative_to(ROOT), owner))

    assert mismatches == []


def test_public_project_files_do_not_reference_retired_personal_identifiers():
    matches = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        for owner in PROJECT_OWNER_RE.findall(text):
            if _digest(owner) == RETIRED_OWNER_DIGEST:
                matches.append((path.relative_to(ROOT), "project owner"))
        for username in LOCAL_USER_RE.findall(text):
            if _digest(username) == RETIRED_LOCAL_USER_DIGEST:
                matches.append((path.relative_to(ROOT), "local user path"))
        for token in re.findall(r"[A-Za-z0-9_-]+", text):
            for start in range(len(token) - RETIRED_PUBLIC_HANDLE_LENGTH + 1):
                candidate = token[start : start + RETIRED_PUBLIC_HANDLE_LENGTH]
                if _digest(candidate.lower()) == RETIRED_PUBLIC_HANDLE_DIGEST:
                    matches.append((path.relative_to(ROOT), "public account handle"))

    assert matches == []
