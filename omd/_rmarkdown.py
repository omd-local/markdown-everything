"""RMarkdown output helpers."""
from __future__ import annotations

import re
from pathlib import Path

from omd._io import write_atomic


def to_rmarkdown(markdown: str, *, title: str | None = None, output: str = "html_document") -> str:
    """Wrap Markdown in a minimal RMarkdown YAML header.

    RMarkdown is Markdown plus executable chunks and front matter. OMD's
    converters emit ordinary Markdown, so the safest transformation is to add
    valid YAML metadata without rewriting the extracted content.
    """
    text = markdown.lstrip("\ufeff")
    if _has_yaml_front_matter(text):
        return markdown
    resolved_title = title or _infer_title(text) or "OMD Conversion"
    header = (
        "---\n"
        f"title: \"{_yaml_double_quote(resolved_title)}\"\n"
        f"output: {output}\n"
        "---\n\n"
    )
    return header + markdown.lstrip("\n")


def convert_file(path: str | Path, *, title: str | None = None, output: str = "html_document") -> None:
    target = Path(path)
    body = target.read_text(encoding="utf-8", errors="replace")
    resolved_title = title or _infer_title(body) or _title_from_path(target)
    write_atomic(target, to_rmarkdown(body, title=resolved_title, output=output))


def _infer_title(markdown: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if not match:
        return ""
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title.strip("#").strip()


def _has_yaml_front_matter(markdown: str) -> bool:
    if not markdown.startswith("---\n"):
        return False
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return False
    header = markdown[4:end].strip()
    return any(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line) for line in header.splitlines())


def _title_from_path(path: Path) -> str:
    return re.sub(r"[-_]+", " ", path.stem).strip().title()


def _yaml_double_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
