"""Shared normalization for AI-generated Obsidian tags."""
from __future__ import annotations

import re


def normalize_generated_tag(value: object) -> str:
    tag = str(value).strip().lstrip("#").replace("_", "-").replace(" ", "-").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff/-]+", "-", tag).strip("-")


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
