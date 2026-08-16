from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
import json

import pytest


def _write_markdown(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _paths(hits) -> list[str]:
    return [hit.path for hit in hits]


def test_search_hit_is_a_frozen_dataclass():
    from omd.retrieval import SearchHit

    assert is_dataclass(SearchHit)
    assert SearchHit.__dataclass_params__.frozen is True


def test_search_hit_exposes_contract_fields():
    from omd.retrieval import SearchHit

    assert [field.name for field in fields(SearchHit)] == ["path", "title", "score", "evidence"]


def test_search_hit_rejects_mutation():
    from omd.retrieval import SearchHit

    hit = SearchHit(path="Notes/example.md", title="Example", score=1.0, evidence="match")

    with pytest.raises(FrozenInstanceError):
        hit.score = 2.0


def test_search_notes_returns_no_results_for_blank_query(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# Example\n\nRetrieval appears here.\n")

    assert search_notes(tmp_path, "   \n\t  ") == []


def test_search_notes_rejects_missing_root(tmp_path):
    from omd.retrieval import search_notes

    with pytest.raises(ValueError, match="root"):
        search_notes(tmp_path / "missing", "retrieval")


def test_search_notes_rejects_root_that_is_not_a_directory(tmp_path):
    from omd.retrieval import search_notes

    root_file = _write_markdown(tmp_path / "note.md", "# Example\n\nRetrieval appears here.\n")

    with pytest.raises(ValueError, match="root"):
        search_notes(root_file, "retrieval")


def test_search_notes_recursively_finds_matching_markdown_files(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "nested" / "topic.md", "# Topic\n\nLocal lexical retrieval works.\n")

    hits = search_notes(tmp_path, "lexical")

    assert _paths(hits) == ["nested/topic.md"]


def test_search_notes_ignores_non_markdown_files(tmp_path):
    from omd.retrieval import search_notes

    _write_text(tmp_path / "plain.txt", "local lexical retrieval works")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_ignores_hidden_directories(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / ".hidden" / "secret.md", "# Secret\n\nLocal lexical retrieval works.\n")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_ignores_omd_sidecar_directories(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / ".omd" / "index.md", "# Index\n\nLocal lexical retrieval works.\n")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_matches_tokens_case_insensitively(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# Example\n\nLocal Lexical Retrieval Works.\n")

    hits = search_notes(tmp_path, "lexical retrieval")

    assert _paths(hits) == ["note.md"]


def test_search_notes_matches_multilingual_tokens(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "japanese.md", "# 旅行\n\n東京で静かな喫茶店を見つけた。\n")

    hits = search_notes(tmp_path, "東京")

    assert _paths(hits) == ["japanese.md"]


def test_search_notes_returns_paths_relative_to_root(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "Notes" / "topic.md", "# Topic\n\nLexical retrieval works.\n")

    hits = search_notes(tmp_path, "lexical")

    assert hits[0].path == "Notes/topic.md"


def test_search_notes_ranking_is_deterministic_across_repeated_calls(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "a.md", "# Alpha\n\nretrieval retrieval lexical\n")
    _write_markdown(tmp_path / "b.md", "# Beta\n\nretrieval lexical\n")
    _write_markdown(tmp_path / "c.md", "# Gamma\n\nlexical only once\n")

    first = search_notes(tmp_path, "retrieval lexical")
    second = search_notes(tmp_path, "retrieval lexical")

    assert second == first


def test_search_notes_evidence_contains_the_match(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(
        tmp_path / "note.md",
        "# Example\n\nBefore context. The lexical marker is here. After context.\n",
    )

    hits = search_notes(tmp_path, "marker")

    assert "marker" in hits[0].evidence.lower()


def test_search_notes_evidence_is_bounded_for_long_matches(tmp_path):
    from omd.retrieval import search_notes

    long_prefix = "prefix " * 200
    long_suffix = " suffix" * 200
    _write_markdown(
        tmp_path / "note.md",
        f"# Example\n\n{long_prefix}needle{long_suffix}\n",
    )

    hits = search_notes(tmp_path, "needle")

    assert len(hits[0].evidence) <= 400


def test_search_notes_refuses_symlinked_files_that_resolve_outside_root(tmp_path):
    from omd.retrieval import search_notes

    outside = _write_markdown(tmp_path.parent / "outside-note.md", "# Escape\n\nlexical retrieval\n")
    (tmp_path / "escape.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside root"):
        search_notes(tmp_path, "lexical")


def test_search_notes_ignores_broken_markdown_symlinks(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "valid.md", "# Valid\n\nlocal lexical retrieval\n")
    (tmp_path / "broken.md").symlink_to(tmp_path / "missing.md")

    hits = search_notes(tmp_path, "lexical")

    assert _paths(hits) == ["valid.md"]


def test_find_duplicate_notes_groups_markdown_files_with_identical_content(tmp_path):
    from omd.retrieval import find_duplicate_notes

    text = "# Duplicate\n\nSame note body.\n"
    _write_markdown(tmp_path / "first.md", text)
    _write_markdown(tmp_path / "nested" / "second.md", text)
    _write_markdown(tmp_path / "unique.md", "# Unique\n\nDifferent body.\n")

    duplicates = find_duplicate_notes(tmp_path)

    assert duplicates == [["first.md", "nested/second.md"]]


def test_search_notes_preference_profile_cannot_introduce_unmatched_notes(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "matched.md", "# Reference\n\nlexical retrieval source\n")
    _write_markdown(tmp_path / "unmatched.md", "# Reference\n\ncompletely different words\n")
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    baseline = search_notes(tmp_path, "lexical")
    reranked = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(reranked) == _paths(baseline)


def test_source_type_preference_uses_manifest_not_body_text(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    body_only = _write_markdown(
        tmp_path / "a-body.md", "# Body mention\n\nlexical webpage webpage webpage\n"
    )
    structured = _write_markdown(tmp_path / "b-structured.md", "# Structured\n\nlexical\n")
    structured.with_suffix(".omd.json").write_text(
        json.dumps({"metadata": {"source_type": "webpage", "tags": []}}), encoding="utf-8"
    )
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(hits) == ["b-structured.md", "a-body.md"]
    assert body_only.exists()


def test_source_type_preference_uses_nested_capture_manifest_metadata(tmp_path):
    from omd._manifest import write_manifest_for_output
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    plain = _write_markdown(tmp_path / "a-plain.md", "# Plain\n\nlexical\n")
    captured = _write_markdown(tmp_path / "b-captured.md", "# Captured\n\nlexical\n")
    write_manifest_for_output(
        captured,
        source="https://example.com/article",
        backend="markitdown",
        metadata={"capture": {"source_type": "webpage", "tags": ["research"]}},
    )
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(hits) == ["b-captured.md", "a-plain.md"]
    assert hits[0].score > hits[1].score
    assert plain.exists()


def test_find_duplicate_notes_normalizes_cross_platform_newlines(tmp_path):
    from omd.retrieval import find_duplicate_notes

    (tmp_path / "lf.md").write_bytes(b"# Note\n\nSame body.\n")
    (tmp_path / "crlf.md").write_bytes(b"# Note\r\n\r\nSame body.\r\n")

    assert find_duplicate_notes(tmp_path) == [["crlf.md", "lf.md"]]


def test_search_notes_evidence_preserves_unicode_casefold_location(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# German\n\nA Straße leads to the station.\n")

    hits = search_notes(tmp_path, "strasse")

    assert "Straße" in hits[0].evidence


def test_related_notes_returns_bounded_candidates_without_rewriting_files(tmp_path):
    from omd.retrieval import related_notes

    source = _write_markdown(tmp_path / "source.md", "# Source\n\nLocal retrieval and context.\n")
    related = _write_markdown(tmp_path / "related.md", "# Related\n\nContext retrieval notes.\n")
    before = related.read_bytes()

    hits = related_notes(
        tmp_path,
        "Local context retrieval should connect these notes.",
        exclude_path="source.md",
        limit=3,
    )

    assert _paths(hits) == ["related.md"]
    assert related.read_bytes() == before
    assert source.exists()


def test_related_notes_uses_a_bounded_query_for_large_source_text(tmp_path):
    from omd.retrieval import related_notes

    _write_markdown(tmp_path / "match.md", "# Match\n\nlexical context\n")
    source_text = "lexical context " + "unique-token " * 10_000

    hits = related_notes(tmp_path, source_text, limit=2)

    assert _paths(hits) == ["match.md"]


def test_search_notes_does_not_follow_symlinked_manifest_sidecar(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    note = _write_markdown(tmp_path / "note.md", "# Note\n\nlexical context\n")
    outside = tmp_path.parent / "outside-sidecar.json"
    outside.write_text(json.dumps({"metadata": {"source_type": "webpage"}}), encoding="utf-8")
    note.with_suffix(".omd.json").symlink_to(outside)
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert hits[0].score == 3.0


def test_search_notes_keeps_limit_and_order_deterministic_in_large_vault(tmp_path):
    from omd.retrieval import search_notes

    for index in range(1_000):
        count = index % 7 + 1
        _write_markdown(
            tmp_path / f"folder-{index % 20:02d}" / f"note-{index:04d}.md",
            f"# Note {index}\n\n" + ("context " * count) + "retrieval\n",
        )

    first = search_notes(tmp_path, "context retrieval", limit=12)
    second = search_notes(tmp_path, "context retrieval", limit=12)

    assert len(first) == 12
    assert second == first
    assert [hit.score for hit in first] == sorted(
        (hit.score for hit in first), reverse=True
    )


def _vault_snapshot(root: Path) -> list[tuple[str, bytes | None, int, int]]:
    snapshot = []
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        snapshot.append(
            (
                path.relative_to(root).as_posix(),
                path.read_bytes() if path.is_file() and not path.is_symlink() else None,
                stat.st_mode,
                stat.st_mtime_ns,
            )
        )
    return snapshot


def test_build_vault_catalog_extracts_multilingual_frontmatter_and_inline_tags(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(tmp_path / "Inbox" / "source.md", "# Source\n\n本地 AI 知识工作流\n")
    _write_markdown(
        tmp_path / "Notes" / "local-ai.md",
        """---
title: 本地 AI
aliases:
  - Local AI
  - 本地智能
tags: [ai/local, research]
---
# Ignored H1

本地模型支持知识工作流。 #workflow
```text
#not-a-tag
```
""",
    )

    catalog = build_vault_catalog(
        tmp_path,
        "我正在设计 Local AI 与 #workflow 的知识工作流。",
        exclude_path="Inbox/source.md",
    )

    assert len(catalog.candidates) == 1
    candidate = catalog.candidates[0]
    assert candidate.path == "Notes/local-ai.md"
    assert candidate.title == "本地 AI"
    assert candidate.aliases == ("Local AI", "本地智能")
    assert candidate.tags == ("ai/local", "research", "workflow")
    assert "local-ai.md" not in candidate.id
    assert catalog.vault_tags == ("ai/local", "research", "workflow")


def test_build_vault_catalog_excludes_target_hidden_and_all_symlinks(tmp_path):
    from omd.retrieval import build_vault_catalog

    target = _write_markdown(tmp_path / "Inbox" / "source.md", "# Source\n\nlocal AI\n")
    real = _write_markdown(tmp_path / "Notes" / "real.md", "# Local AI\n\nlocal AI\n")
    _write_markdown(tmp_path / ".obsidian" / "secret.md", "# Local AI\n")
    (tmp_path / "Notes" / "linked.md").symlink_to(real)
    outside = _write_markdown(tmp_path.parent / "outside-catalog.md", "# Local AI\n")
    (tmp_path / "Notes" / "outside.md").symlink_to(outside)

    catalog = build_vault_catalog(
        tmp_path,
        "local AI",
        exclude_path="Inbox/source.md",
    )

    assert [candidate.path for candidate in catalog.candidates] == ["Notes/real.md"]
    assert target.exists()


def test_build_vault_catalog_refuses_post_scan_symlink_swap(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    note = _write_markdown(tmp_path / "Notes" / "candidate.md", "# Safe\n\nlocal AI\n")
    outside = _write_markdown(tmp_path.parent / "outside-race.md", "# Secret\n\nlocal AI\n")
    original = retrieval._catalog_markdown_paths

    def swap_after_scan(root, *, excluded):
        paths = original(root, excluded=excluded)
        note.unlink()
        note.symlink_to(outside)
        return paths

    monkeypatch.setattr(retrieval, "_catalog_markdown_paths", swap_after_scan)

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.build_vault_catalog(tmp_path, "local AI")

    assert excinfo.value.code == "vault_read_failed"


def test_build_vault_catalog_is_deterministic_bounded_and_read_only(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(tmp_path / "source.md", "# Source\n\ncontext retrieval\n")
    for index in range(12):
        _write_markdown(
            tmp_path / "Notes" / f"note-{index:02d}.md",
            f"# Note {index}\n\ncontext retrieval {index}\n",
        )
    before = _vault_snapshot(tmp_path)

    first = build_vault_catalog(tmp_path, "context retrieval", exclude_path="source.md", limit=5)
    second = build_vault_catalog(tmp_path, "context retrieval", exclude_path="source.md", limit=5)

    assert len(first.candidates) == 5
    assert second == first
    assert len({candidate.id for candidate in first.candidates}) == 5
    assert _vault_snapshot(tmp_path) == before


def test_build_vault_catalog_uses_bounded_metadata_for_large_notes(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(
        tmp_path / "large.md",
        "---\ntitle: Large Local AI\ntags: [large]\n---\n" + "x" * (256 * 1024),
    )

    catalog = build_vault_catalog(tmp_path, "Large Local AI")

    assert [candidate.title for candidate in catalog.candidates] == ["Large Local AI"]
    assert "large_note_body_skipped" in catalog.warnings


def test_build_vault_catalog_fails_closed_when_file_gate_is_exceeded(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    monkeypatch.setattr(retrieval, "MAX_CATALOG_NOTES", 2)
    for index in range(3):
        _write_markdown(tmp_path / f"note-{index}.md", f"# Note {index}\n\ncontext\n")

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.build_vault_catalog(tmp_path, "context")

    assert excinfo.value.code == "vault_catalog_too_large"


def test_build_vault_catalog_rejects_non_utf8_markdown(tmp_path):
    from omd.retrieval import VaultCatalogError, build_vault_catalog

    (tmp_path / "invalid.md").write_bytes(b"# Invalid\n\xff\n")

    with pytest.raises(VaultCatalogError) as excinfo:
        build_vault_catalog(tmp_path, "Invalid")

    assert excinfo.value.code == "invalid_utf8"


def test_read_vault_markdown_is_bounded_and_rejects_symlinks(tmp_path):
    from omd.retrieval import VaultCatalogError, read_vault_markdown

    _write_markdown(tmp_path / "Notes" / "safe.md", "# Safe\n")
    assert read_vault_markdown(tmp_path, "Notes/safe.md", max_bytes=64) == "# Safe\n"

    with pytest.raises(VaultCatalogError) as too_large:
        read_vault_markdown(tmp_path, "Notes/safe.md", max_bytes=4)
    assert too_large.value.code == "request_too_large"

    outside = _write_markdown(tmp_path.parent / "outside-read.md", "# Secret\n")
    linked = tmp_path / "Notes" / "linked.md"
    linked.symlink_to(outside)
    with pytest.raises(VaultCatalogError) as unsafe:
        read_vault_markdown(tmp_path, "Notes/linked.md", max_bytes=64)
    assert unsafe.value.code == "note_not_found"


def test_validate_vault_markdown_path_rejects_missing_and_escape(tmp_path):
    from omd.retrieval import VaultCatalogError, validate_vault_markdown_path

    _write_markdown(tmp_path / "Notes" / "safe.md", "# Safe\n")
    validate_vault_markdown_path(tmp_path, "Notes/safe.md")

    with pytest.raises(VaultCatalogError) as missing:
        validate_vault_markdown_path(tmp_path, "Notes/missing.md")
    assert missing.value.code == "note_not_found"

    with pytest.raises(ValueError, match="relative Markdown"):
        validate_vault_markdown_path(tmp_path, "../outside.md")


def test_read_vault_markdown_rechecks_actual_bytes_after_fstat(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(tmp_path / "note.md", "safe")
    original_read = retrieval.os.read

    def grown_read(file_descriptor, count):
        if count == 5:
            return b"grown"
        return original_read(file_descriptor, count)

    monkeypatch.setattr(retrieval.os, "read", grown_read)

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.read_vault_markdown(tmp_path, "note.md", max_bytes=4)

    assert excinfo.value.code == "request_too_large"


def test_catalog_treats_actual_over_limit_read_as_metadata_only(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(tmp_path / "note.md", "# Note\n")
    monkeypatch.setattr(retrieval, "MAX_CATALOG_FILE_BYTES", 8)
    monkeypatch.setattr(retrieval, "_METADATA_PREFIX_BYTES", 4)
    original_read = retrieval.os.read

    def grown_read(file_descriptor, count):
        if count == 9:
            return b"context!!"
        return original_read(file_descriptor, count)

    monkeypatch.setattr(retrieval.os, "read", grown_read)

    catalog = retrieval.build_vault_catalog(tmp_path, "note")

    assert catalog.candidates
    assert "large_note_body_skipped" in catalog.warnings
