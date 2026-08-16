"""Deterministic, local-only lexical retrieval for Markdown vaults."""
from __future__ import annotations

import hashlib
import os
import re
import json
import codecs
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterator

from .preferences import PreferenceProfile


_EVIDENCE_LIMIT = 400
MAX_CATALOG_NOTES = 10_000
MAX_CATALOG_FILE_BYTES = 256 * 1024
MAX_CATALOG_CANDIDATES = 80
MAX_CATALOG_TAGS = 500
_METADATA_PREFIX_BYTES = 64 * 1024


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    score: float
    evidence: str


@dataclass(frozen=True)
class VaultCatalogCandidate:
    id: str
    path: str
    title: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    evidence: str


@dataclass(frozen=True)
class VaultCatalog:
    candidates: tuple[VaultCatalogCandidate, ...]
    vault_tags: tuple[str, ...]
    warnings: tuple[str, ...]


class VaultCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _ScoredCatalogCandidate:
    candidate: VaultCatalogCandidate
    score: float


def read_vault_markdown(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> str:
    """Read one vault-relative Markdown file without following symlinks."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    vault = _validated_root(root)
    relative = _relative_note_path(relative_path)
    try:
        with _open_vault_file(vault, relative) as (file_descriptor, file_stat):
            if file_stat.st_size > max_bytes:
                raise VaultCatalogError(
                    "request_too_large",
                    f"Markdown note exceeds {max_bytes} bytes",
                )
            raw = os.read(file_descriptor, max_bytes + 1)
            if len(raw) > max_bytes:
                raise VaultCatalogError(
                    "request_too_large",
                    f"Markdown note exceeds {max_bytes} bytes",
                )
    except VaultCatalogError:
        raise
    except (OSError, RuntimeError) as exc:
        raise VaultCatalogError(
            "note_not_found", f"could not safely read {PurePath(relative).name}"
        ) from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VaultCatalogError(
            "invalid_utf8", f"Markdown note is not UTF-8: {PurePath(relative).name}"
        ) from exc


def validate_vault_markdown_path(root: str | Path, relative_path: str) -> None:
    """Verify that a vault-relative Markdown path currently names a safe regular file."""
    vault = _validated_root(root)
    relative = _relative_note_path(relative_path)
    try:
        with _open_vault_file(vault, relative):
            return
    except (OSError, RuntimeError) as exc:
        raise VaultCatalogError(
            "note_not_found", f"could not safely access {PurePath(relative).name}"
        ) from exc


def extract_obsidian_tags(markdown: str) -> tuple[str, ...]:
    """Extract de-duplicated frontmatter and inline tags from Markdown."""
    if not isinstance(markdown, str):
        raise TypeError("markdown must be a string")
    frontmatter = _simple_frontmatter(markdown)
    return _dedupe_text(
        tuple(_clean_tag(tag) for tag in frontmatter.get("tags", ()))
        + _inline_tags(_frontmatter_body(markdown))
    )


def build_vault_catalog(
    root: str | Path,
    source_text: str,
    *,
    exclude_path: str | None = None,
    limit: int = MAX_CATALOG_CANDIDATES,
) -> VaultCatalog:
    """Build a bounded, deterministic, read-only catalog for one enrichment request."""
    if not isinstance(source_text, str):
        raise ValueError("source_text must be a string")
    if type(limit) is not int or limit <= 0 or limit > MAX_CATALOG_CANDIDATES:
        raise ValueError(f"limit must be between 1 and {MAX_CATALOG_CANDIDATES}")
    vault = _validated_root(root)
    excluded = _relative_note_path(exclude_path) if exclude_path else None
    paths = _catalog_markdown_paths(vault, excluded=excluded)
    if len(paths) > MAX_CATALOG_NOTES:
        raise VaultCatalogError(
            "vault_catalog_too_large",
            f"vault contains more than {MAX_CATALOG_NOTES} eligible Markdown notes",
        )

    terms = _related_terms(source_text)
    source_folded = source_text.casefold()
    scored: list[_ScoredCatalogCandidate] = []
    all_tags: dict[str, str] = {}
    skipped_large_body = False
    for path in paths:
        relative = path.relative_to(vault).as_posix()
        metadata_text, body_text, body_skipped = _read_catalog_note(vault, relative)
        skipped_large_body = skipped_large_body or body_skipped
        title, aliases, tags = _catalog_metadata(metadata_text, path)
        for tag in tags:
            all_tags.setdefault(tag.casefold(), tag)
        score = _catalog_score(
            source_folded,
            terms,
            title=title,
            aliases=aliases,
            tags=tags,
            body=body_text,
        )
        if score <= 0:
            continue
        evidence_source = body_text or metadata_text
        candidate = VaultCatalogCandidate(
            id=_catalog_candidate_id(relative),
            path=relative,
            title=title,
            aliases=aliases,
            tags=tags,
            evidence=_evidence(evidence_source, terms) if terms else "",
        )
        scored.append(_ScoredCatalogCandidate(candidate, score))

    scored.sort(
        key=lambda item: (
            -item.score,
            item.candidate.path.casefold(),
            item.candidate.path,
        )
    )
    ordered_tags = sorted(all_tags.values(), key=lambda tag: (tag.casefold(), tag))
    warnings: list[str] = []
    if skipped_large_body:
        warnings.append("large_note_body_skipped")
    if len(ordered_tags) > MAX_CATALOG_TAGS:
        warnings.append("vault_tags_truncated")
    return VaultCatalog(
        candidates=tuple(item.candidate for item in scored[:limit]),
        vault_tags=tuple(ordered_tags[:MAX_CATALOG_TAGS]),
        warnings=tuple(warnings),
    )


def search_notes(
    root: str | Path,
    query: str,
    *,
    limit: int = 10,
    preference_profile: PreferenceProfile | None = None,
) -> list[SearchHit]:
    vault = _validated_root(root)
    terms = _query_terms(query)
    if not terms:
        return []
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    hits: list[SearchHit] = []
    for path in _markdown_paths(vault):
        text = _read_note(path)
        folded = text.casefold()
        counts = [folded.count(term) for term in terms]
        if not any(counts):
            continue
        matched_terms = sum(count > 0 for count in counts)
        score = float(sum(counts) + matched_terms * 2)
        score += _preference_bonus(path, preference_profile)
        relative = path.relative_to(vault).as_posix()
        hits.append(
            SearchHit(
                path=relative,
                title=_title(text, path),
                score=score,
                evidence=_evidence(text, terms),
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.path.casefold(), hit.path))
    return hits[:limit]


def find_duplicate_notes(root: str | Path) -> list[list[str]]:
    vault = _validated_root(root)
    by_digest: dict[str, list[str]] = {}
    for path in _markdown_paths(vault):
        identity = _manifest_source_id(path) or _normalized_content_identity(path)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        by_digest.setdefault(digest, []).append(path.relative_to(vault).as_posix())
    groups = [sorted(paths) for paths in by_digest.values() if len(paths) > 1]
    return sorted(groups, key=lambda paths: tuple(paths))


def related_notes(
    root: str | Path,
    source_text: str,
    *,
    exclude_path: str | None = None,
    limit: int = 5,
    preference_profile: PreferenceProfile | None = None,
) -> list[SearchHit]:
    """Return read-only lexical candidates derived from a bounded source query."""
    if not isinstance(source_text, str):
        raise ValueError("source_text must be a string")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    excluded = _relative_note_path(exclude_path) if exclude_path else None
    query = " ".join(_related_terms(source_text))
    if not query:
        return []
    candidates = search_notes(
        root,
        query,
        limit=limit + (1 if excluded else 0),
        preference_profile=preference_profile,
    )
    return [hit for hit in candidates if hit.path != excluded][:limit]


def _validated_root(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError("retrieval root must be an existing directory")
    return path.resolve(strict=True)


def _markdown_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if not name.startswith("."))
        current_path = Path(current)
        for filename in sorted(filenames):
            if filename.startswith(".") or Path(filename).suffix.lower() != ".md":
                continue
            candidate = current_path / filename
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Markdown path resolves outside root: {candidate}") from exc
            paths.append(resolved)
    return paths


def _catalog_markdown_paths(root: Path, *, excluded: str | None) -> list[Path]:
    paths: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".") and not (current_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.startswith(".") or Path(filename).suffix.lower() != ".md":
                continue
            candidate = current_path / filename
            if candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=True)
                relative = resolved.relative_to(root).as_posix()
            except (OSError, RuntimeError):
                continue
            except ValueError as exc:
                raise VaultCatalogError(
                    "path_outside_vault", "Markdown path resolves outside vault"
                ) from exc
            if relative != excluded:
                paths.append(resolved)
    return paths


def _read_catalog_note(root: Path, relative: str) -> tuple[str, str, bool]:
    try:
        with _open_vault_file(root, relative) as (file_descriptor, file_stat):
            if file_stat.st_size > MAX_CATALOG_FILE_BYTES:
                raw = os.read(file_descriptor, _METADATA_PREFIX_BYTES + 4)
                prefix = _decode_utf8_prefix(raw, _METADATA_PREFIX_BYTES)
                return prefix, "", True
            raw = os.read(file_descriptor, MAX_CATALOG_FILE_BYTES + 1)
            if len(raw) > MAX_CATALOG_FILE_BYTES:
                prefix = _decode_utf8_prefix(raw, _METADATA_PREFIX_BYTES)
                return prefix, "", True
    except UnicodeDecodeError as exc:
        raise VaultCatalogError(
            "invalid_utf8", f"Markdown note is not UTF-8: {PurePath(relative).name}"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise VaultCatalogError(
            "vault_read_failed", f"could not safely read {PurePath(relative).name}"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VaultCatalogError(
            "invalid_utf8", f"Markdown note is not UTF-8: {PurePath(relative).name}"
        ) from exc
    return text, _frontmatter_body(text), False


@contextmanager
def _open_vault_file(root: Path, relative: str) -> Iterator[tuple[int, os.stat_result]]:
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(root, directory_flags))
        parts = PurePath(relative).parts
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(parts) - 1:
                flags |= os.O_DIRECTORY
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        file_descriptor = descriptors[-1]
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("vault path is not a regular file")
        yield file_descriptor, file_stat
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _decode_utf8_prefix(raw: bytes, limit: int) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    prefix = decoder.decode(raw[:limit], final=False)
    pending = decoder.getstate()[0]
    if not pending:
        return prefix
    for extra in range(1, min(4, len(raw) - limit) + 1):
        try:
            return raw[: limit + extra].decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.start < limit - 3:
                raise VaultCatalogError("invalid_utf8", "Markdown metadata is not UTF-8") from exc
    raise VaultCatalogError("invalid_utf8", "Markdown metadata is not valid UTF-8")


def _catalog_metadata(text: str, path: Path) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    frontmatter = _simple_frontmatter(text)
    body = _frontmatter_body(text)
    title_values = frontmatter.get("title", ())
    title = title_values[0] if title_values else _title(body, path)
    aliases = _dedupe_text(frontmatter.get("aliases", ()))
    tags = extract_obsidian_tags(text)
    return title, aliases, tags


def _simple_frontmatter(text: str) -> dict[str, tuple[str, ...]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}
    result: dict[str, list[str]] = {}
    active: str | None = None
    for line in lines[1:end]:
        list_item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if list_item and active in {"aliases", "tags"}:
            value = _yaml_scalar(list_item.group(1))
            if value:
                result.setdefault(active, []).append(value)
            continue
        field = re.match(r"^(title|aliases|tags)\s*:\s*(.*?)\s*$", line, re.IGNORECASE)
        if not field:
            active = None
            continue
        active = field.group(1).lower()
        raw = field.group(2)
        if not raw:
            result.setdefault(active, [])
            continue
        values = _yaml_list(raw) if raw.startswith("[") and raw.endswith("]") else [_yaml_scalar(raw)]
        result.setdefault(active, []).extend(value for value in values if value)
    return {key: tuple(values) for key, values in result.items()}


def _frontmatter_body(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "".join(lines[index + 1 :])
    return text


def _yaml_list(value: str) -> list[str]:
    return [_yaml_scalar(item) for item in value[1:-1].split(",")]


def _yaml_scalar(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def _inline_tags(text: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        without_code = re.sub(r"`[^`]*`", "", line)
        for match in re.finditer(r"(?<!\S)#([\w\u4e00-\u9fff][\w\u4e00-\u9fff/-]*)", without_code):
            tag = _clean_tag(match.group(1))
            key = tag.casefold()
            if tag and key not in seen:
                tags.append(tag)
                seen.add(key)
    return tuple(tags)


def _clean_tag(value: str) -> str:
    return value.strip().lstrip("#").strip()


def _dedupe_text(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            unique.append(cleaned)
            seen.add(key)
    return tuple(unique)


def _catalog_score(
    source_folded: str,
    terms: list[str],
    *,
    title: str,
    aliases: tuple[str, ...],
    tags: tuple[str, ...],
    body: str,
) -> float:
    score = 0.0
    for identity in (title, *aliases):
        folded = identity.casefold().strip()
        if folded and folded in source_folded:
            score += 100.0
    for tag in tags:
        folded = tag.casefold()
        if folded and (f"#{folded}" in source_folded or folded in source_folded):
            score += 60.0
    folded_body = body.casefold()
    counts = [folded_body.count(term) for term in terms]
    score += float(sum(counts) + sum(count > 0 for count in counts) * 2)
    return score


def _catalog_candidate_id(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFC", relative_path)
    return "candidate-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _read_note(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read Markdown note: {path.name}") from exc


def _query_terms(query: object) -> list[str]:
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    return list(dict.fromkeys(part.casefold() for part in re.findall(r"\S+", query)))


def _title(text: str, path: Path) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1)
    return path.stem


def _evidence(text: str, terms: list[str]) -> str:
    folded, offsets = _fold_with_offsets(text)
    folded_positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    folded_position = min(folded_positions) if folded_positions else 0
    position = offsets[folded_position] if offsets and folded_position < len(offsets) else 0
    start = max(0, position - 160)
    end = min(len(text), position + 220)
    snippet = " ".join(text[start:end].split())
    if start:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet[:_EVIDENCE_LIMIT]


def _preference_bonus(path: Path, profile: PreferenceProfile | None) -> float:
    if profile is None:
        return 0.0
    metadata = _manifest_metadata(path)
    bonus = 0.0
    for kind in ("source_type", "tag"):
        rankings = profile.signals.get(kind, {})
        candidates = metadata.get("tags", []) if kind == "tag" else [metadata.get("source_type")]
        normalized_candidates = {str(item).casefold() for item in candidates if item}
        for value, weight in rankings.items():
            if weight > 0 and value.casefold() in normalized_candidates:
                bonus += min(weight, 10) * 0.05
    return bonus


def _manifest_metadata(path: Path) -> dict:
    sidecar = path.with_suffix(".omd.json")
    if sidecar.is_symlink():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        return {}
    capture_metadata = metadata.get("capture")
    if isinstance(capture_metadata, dict):
        return {**metadata, **capture_metadata}
    return metadata


def _manifest_source_id(path: Path) -> str | None:
    sidecar = path.with_suffix(".omd.json")
    if sidecar.is_symlink():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    source_id = payload.get("source_id") if isinstance(payload, dict) else None
    return f"source:{source_id}" if isinstance(source_id, str) and source_id else None


def _normalized_content_identity(path: Path) -> str:
    text = _read_note(path).replace("\r\n", "\n").replace("\r", "\n")
    return "content:" + text


def _fold_with_offsets(text: str) -> tuple[str, list[int]]:
    folded_parts: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        folded = character.casefold()
        folded_parts.append(folded)
        offsets.extend([index] * len(folded))
    return "".join(folded_parts), offsets


def _related_terms(text: str) -> list[str]:
    bounded = text[:8192]
    terms: list[str] = []
    for term in _query_terms(bounded):
        if len(term) > 64 or term in terms:
            continue
        terms.append(term)
        if len(terms) == 12:
            break
    return terms


def _relative_note_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("exclude_path must be a relative Markdown path")
    path = Path(value.strip())
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError("exclude_path must be a relative Markdown path")
    return path.as_posix()
