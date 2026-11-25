"""Shared normalization for AI-generated Obsidian tags."""
from __future__ import annotations

import re
import unicodedata


def canonical_tag_identity(value: str) -> str:
    """Match Home tag identity without casefold expansions such as ß to ss."""
    return unicodedata.normalize("NFC", value.lower())


def normalize_generated_tag(value: object, *, allow_hierarchy: bool = True) -> str:
    tag = str(value).strip().lstrip("#").replace("_", "-").replace(" ", "-")
    tag = canonical_tag_identity(tag)
    separators = "/-" if allow_hierarchy else "-"
    parts: list[str] = []
    replacing = False
    for character in tag:
        if character in separators or unicodedata.category(character)[0] in "LMN":
            parts.append(character)
            replacing = False
        elif not replacing:
            parts.append("-")
            replacing = True
    return "".join(parts).strip("-")


def normalize_generated_tags(value: object, *, limit: int = 12) -> list[str]:
    if isinstance(value, list):
        raw_items: list[object] = value
    elif isinstance(value, str):
        raw_items = re.split(r"[,，;\n]", value)
    else:
        raw_items = []
    tags: list[str] = []
    for raw in raw_items:
        tag = normalize_generated_tag(raw)
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:limit]
