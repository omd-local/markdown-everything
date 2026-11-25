"""Deterministic, local-first retrieval for Markdown vaults."""
from __future__ import annotations

import hashlib
import math
import os
import re
import json
import codecs
import sqlite3
import stat
import sys
import time
import urllib.request
import unicodedata
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterator

from ._network_policy import validate_ollama_base_url
from .ollama_runtime import request_ollama_json
from .preferences import PreferenceProfile
from .tag_normalization import canonical_tag_identity


_EVIDENCE_LIMIT = 400
_SEARCH_EVIDENCE_LIMIT = 1_600
_MAX_EVIDENCE_HEADINGS = 32
_MAX_EVIDENCE_PASSAGES = 3
_QUERY_TERM_LIMIT = 16
_QUERY_STOPWORDS = frozenset(
    {
        "a", "about", "all", "an", "and", "any", "are", "as", "ask", "at",
        "be", "been", "being", "but", "by", "can", "could", "did", "do", "does",
        "each", "every", "find", "for", "from", "give", "had", "has", "have",
        "how", "i", "if", "in", "is", "it", "list", "many", "may", "me", "might",
        "much", "must", "my", "notes", "of", "on", "or", "our", "please", "search",
        "shall", "should", "show", "some", "summarise", "summarize", "summary", "tell",
        "than", "that", "the", "their", "them", "then", "there", "these", "they",
        "this", "those", "to", "vault", "was", "we", "were", "what", "when", "where",
        "which", "who", "why", "will", "with", "without", "would", "written", "you",
        "your",
    }
)
_SHORT_QUERY_TERMS = frozenset({"ai", "ar", "ci", "db", "hr", "it", "js", "ml", "os", "pm", "qa", "r", "ts", "ui", "ux", "vr"})
_ENUMERATED_HEADING = re.compile(r"^(?:\d+[.)#:-]\s*|mistake\s*#?\s*\d+)", re.IGNORECASE)
_ANSWER_CANDIDATE_LIMIT = 24
_ANSWER_BLOCK_LIMIT = 8
_ANSWER_BLOCK_CHAR_LIMIT = 2_800
_ANSWER_CONTEXT_CHAR_LIMIT = 14_000
_BM25_K1 = 1.2
_BM25_B = 0.75
_SEMANTIC_NOTE_WEIGHT = 10.0
_SEMANTIC_CANDIDATE_WEIGHT = 8.0
_SEMANTIC_TEXT_LIMIT = 1_600
_SEMANTIC_BATCH_SIZE = 16
_SEMANTIC_TIMEOUT_SECONDS = 20.0
_SEMANTIC_MAX_HEADINGS = 12
_SEMANTIC_MAX_BODY_LINES = 24
_SEMANTIC_RRF_K = 60
_SEMANTIC_STRUCTURED_THRESHOLD = 0.55
_SEMANTIC_SECTION_THRESHOLD = 0.82
_EMBEDDING_CACHE_FILENAME = "embedding-cache-v1.sqlite3"
_EMBEDDING_CACHE_MAX_ROWS = 32_768
_EMBEDDING_MEMORY_CACHE_MAX_ROWS = 512
_ANSWER_QUERY_STOPWORDS = _QUERY_STOPWORDS | frozenset(
    {
        "actionable", "across", "advice", "combine", "duplicate", "duplicates",
        "common", "inferred", "once", "overlap", "overlaps", "present", "remove",
        "separate", "shared", "two",
        "according", "answer", "based", "both", "cite", "cited", "cites", "claim",
        "claims", "contained", "content", "discuss", "discusses", "evidence", "explicit",
        "explicitly", "mention", "mentions", "note", "provide", "recommend",
        "recommendation", "recommendations", "source", "sources", "support", "supported",
    }
)
MAX_CATALOG_NOTES = 10_000
MAX_CATALOG_FILE_BYTES = 256 * 1024
MAX_CATALOG_CANDIDATES = 80
MAX_CATALOG_TAGS = 500
_METADATA_PREFIX_BYTES = 64 * 1024
_EMBEDDING_CACHE: OrderedDict[tuple[str, str, str], tuple[float, ...]] = OrderedDict()


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    score: float
    evidence: str


@dataclass(frozen=True)
class EvidenceBlock:
    path: str
    title: str
    heading: str
    kind: str
    score: float
    text: str


@dataclass(frozen=True)
class AnswerContext:
    hits: tuple[SearchHit, ...]
    blocks: tuple[EvidenceBlock, ...]
    candidate_count: int
    retrieval_mode: str = "sparse"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticRecallConfig:
    host: str
    model: str
    rerank: bool = False
    timeout_seconds: float = _SEMANTIC_TIMEOUT_SECONDS
    model_revision: str = ""


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


@dataclass(frozen=True)
class _AnswerCandidate:
    path: str
    title: str
    heading: str
    kind: str
    ranking_text: str
    evidence_text: str
    focus: int
    key: str
    preference_bonus: float
    semantic_bonus: float = 0.0


@dataclass(frozen=True)
class _ScoredAnswerCandidate:
    candidate: _AnswerCandidate
    score: float
    matched_terms: int
    semantic_score: float = 0.0


@dataclass(frozen=True)
class _AnswerNote:
    path: str
    title: str
    body: str
    headings: tuple[str, ...]
    preference_bonus: float
    has_outline: bool
    has_mistake_outline: bool
    has_numbered_outline: bool


@dataclass(frozen=True)
class _ScoredAnswerNote:
    note: _AnswerNote
    score: float
    matched_terms: int
    semantic_score: float = 0.0


def _semantic_note_text(title: str, body: str, headings: list[str]) -> str:
    parts: list[str] = []
    if title.strip():
        parts.append(title.strip())
    parts.extend(heading.strip() for heading in headings[:_SEMANTIC_MAX_HEADINGS] if heading.strip())
    body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    parts.extend(body_lines[:_SEMANTIC_MAX_BODY_LINES])
    text = "\n".join(_plain_markdown_text(part) for part in parts if part)
    return text[:_SEMANTIC_TEXT_LIMIT].strip()


def _semantic_similarity(query_vector: tuple[float, ...], text_vector: tuple[float, ...]) -> float:
    if not query_vector or not text_vector:
        return 0.0
    if len(query_vector) != len(text_vector):
        raise RuntimeError("semantic embedding dimension mismatch")
    dot = sum(left_value * right_value for left_value, right_value in zip(query_vector, text_vector))
    left_norm = math.sqrt(sum(value * value for value in query_vector))
    right_norm = math.sqrt(sum(value * value for value in text_vector))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))


def _semantic_base_url(config: SemanticRecallConfig) -> tuple[str, str, str]:
    if not isinstance(config.host, str):
        raise RuntimeError("semantic embedding host must be loopback")
    raw_host = config.host.strip()
    host = raw_host if "://" in raw_host else f"http://{raw_host}"
    try:
        validate_ollama_base_url(host)
    except ValueError as exc:
        raise RuntimeError("semantic embedding host must be a loopback base URL") from exc
    if not isinstance(config.model, str) or not config.model.strip():
        raise RuntimeError("semantic embedding model must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in config.model):
        raise RuntimeError("semantic embedding model is invalid")
    timeout = config.timeout_seconds
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or float(timeout) <= 0
        or float(timeout) > 120
    ):
        raise RuntimeError("semantic embedding timeout must be between 0 and 120 seconds")
    if type(config.rerank) is not bool:
        raise RuntimeError("semantic rerank flag must be a boolean")
    revision = config.model_revision
    if not isinstance(revision, str):
        raise RuntimeError("semantic embedding model revision is invalid")
    normalized_revision = revision.strip()
    if len(normalized_revision) > 512 or any(
        ord(character) < 32 or ord(character) == 127 for character in normalized_revision
    ):
        raise RuntimeError("semantic embedding model revision is invalid")
    return host.rstrip("/"), config.model.strip(), normalized_revision


def _semantic_cache_model_key(model: str, model_revision: str) -> str:
    if not model_revision:
        return model
    return json.dumps(
        {"model": model, "revision": model_revision},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _memory_cached_embedding(cache_key: tuple[str, str, str]) -> tuple[float, ...] | None:
    embedding = _EMBEDDING_CACHE.get(cache_key)
    if embedding is not None:
        _EMBEDDING_CACHE.move_to_end(cache_key)
    return embedding


def _remember_embedding(cache_key: tuple[str, str, str], embedding: tuple[float, ...]) -> None:
    _EMBEDDING_CACHE[cache_key] = embedding
    _EMBEDDING_CACHE.move_to_end(cache_key)
    while len(_EMBEDDING_CACHE) > _EMBEDDING_MEMORY_CACHE_MAX_ROWS:
        _EMBEDDING_CACHE.popitem(last=False)


def _semantic_text_embeddings(
    config: SemanticRecallConfig,
    texts: list[str],
) -> list[tuple[float, ...]]:
    base_url, model, model_revision = _semantic_base_url(config)
    cache_model = _semantic_cache_model_key(model, model_revision)
    normalized_texts = [" ".join(text.split()).strip() for text in texts]
    if not normalized_texts:
        return []
    embeddings: list[tuple[float, ...] | None] = [None for _ in normalized_texts]
    missing_texts: list[str] = []
    missing_seen: set[str] = set()
    for index, text in enumerate(normalized_texts):
        if not text:
            embeddings[index] = ()
            continue
        cache_key = (base_url, cache_model, text)
        cached = _memory_cached_embedding(cache_key)
        if cached is not None:
            embeddings[index] = cached
            continue
        if text not in missing_seen:
            missing_seen.add(text)
            missing_texts.append(text)

    if not missing_texts:
        return [embedding or () for embedding in embeddings]

    resolved: dict[str, tuple[float, ...]] = {}
    generated: dict[str, tuple[float, ...]] = {}
    with _embedding_cache_connection() as connection:
        if connection is not None:
            resolved.update(_read_cached_embeddings(connection, base_url, cache_model, missing_texts))
        network_texts = [text for text in missing_texts if text not in resolved]
        for start in range(0, len(network_texts), _SEMANTIC_BATCH_SIZE):
            batch = network_texts[start : start + _SEMANTIC_BATCH_SIZE]
            request = urllib.request.Request(
                f"{base_url}/api/embed",
                data=json.dumps({"model": model, "input": batch}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                payload = request_ollama_json(request, timeout=float(config.timeout_seconds))
            except Exception as exc:
                raise RuntimeError("semantic embedding request failed") from exc
            raw_vectors = payload.get("embeddings")
            if not isinstance(raw_vectors, list) or len(raw_vectors) != len(batch):
                raise RuntimeError("semantic embedding response was malformed")
            for text, vector in zip(batch, raw_vectors):
                embedding = _coerce_embedding_vector(vector)
                resolved[text] = embedding
                generated[text] = embedding

        dimensions = {len(embedding) for embedding in resolved.values()}
        if len(dimensions) > 1:
            raise RuntimeError("semantic embedding dimension mismatch")
        if connection is not None and generated:
            _write_cached_embeddings(connection, base_url, cache_model, generated)

    for index, text in enumerate(normalized_texts):
        if not text:
            continue
        embedding = resolved.get(text)
        if embedding is None:
            embedding = _memory_cached_embedding((base_url, cache_model, text))
        if embedding is None:
            raise RuntimeError("semantic embedding cache lookup failed")
        embeddings[index] = embedding
        _remember_embedding((base_url, cache_model, text), embedding)
    return [embedding or () for embedding in embeddings]


def _semantic_query_embedding(config: SemanticRecallConfig, query: str) -> tuple[float, ...]:
    base_url, model, _model_revision = _semantic_base_url(config)
    normalized = " ".join(query.split()).strip()
    if not normalized:
        return ()
    request = urllib.request.Request(
        f"{base_url}/api/embed",
        data=json.dumps({"model": model, "input": [normalized]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        payload = request_ollama_json(request, timeout=float(config.timeout_seconds))
    except Exception as exc:
        raise RuntimeError("semantic embedding request failed") from exc
    raw_vectors = payload.get("embeddings")
    if not isinstance(raw_vectors, list) or len(raw_vectors) != 1:
        raise RuntimeError("semantic embedding response was malformed")
    return _coerce_embedding_vector(raw_vectors[0])


def _semantic_score_map(query: str, texts: list[str], config: SemanticRecallConfig) -> list[float]:
    query_vector = _semantic_query_embedding(config, query)
    if not query_vector:
        return [0.0 for _ in texts]
    text_vectors = _semantic_text_embeddings(config, texts)
    return [_semantic_similarity(query_vector, vector) for vector in text_vectors]


def _semantic_note_scores(
    notes: list[_AnswerNote],
    query: str,
    config: SemanticRecallConfig,
) -> dict[str, float]:
    texts = [_semantic_note_text(note.title, note.body, list(note.headings)) for note in notes]
    scores = _semantic_score_map(query, texts, config)
    return {note.path: score for note, score in zip(notes, scores)}


def _semantic_candidate_scores(
    candidates: list[_AnswerCandidate],
    query: str,
    config: SemanticRecallConfig,
) -> dict[str, float]:
    texts = [
        _semantic_note_text(candidate.title, candidate.evidence_text, [candidate.heading])
        for candidate in candidates
    ]
    scores = _semantic_score_map(query, texts, config)
    return {candidate.key: score for candidate, score in zip(candidates, scores)}


def _effective_match_count(item: _ScoredAnswerCandidate | _ScoredAnswerNote) -> int:
    if item.matched_terms > 0:
        return item.matched_terms
    return 1 if item.semantic_score > 0 else 0


def _coerce_embedding_vector(vector: object) -> tuple[float, ...]:
    if not isinstance(vector, list):
        raise RuntimeError("semantic embedding response was malformed")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in vector):
        raise RuntimeError("semantic embedding response was malformed")
    embedding = tuple(float(value) for value in vector)
    if not embedding or any(not math.isfinite(value) for value in embedding):
        raise RuntimeError("semantic embedding response was malformed")
    return embedding


def _embedding_cache_path() -> Path:
    override = os.environ.get("OMD_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser() / _EMBEDDING_CACHE_FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "omd" / _EMBEDDING_CACHE_FILENAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "omd" / "Cache" / _EMBEDDING_CACHE_FILENAME
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        return Path(xdg_cache).expanduser() / "omd" / _EMBEDDING_CACHE_FILENAME
    return Path.home() / ".cache" / "omd" / _EMBEDDING_CACHE_FILENAME


def _embedding_cache_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _embedding_cache_connection() -> Iterator[sqlite3.Connection | None]:
    path = _embedding_cache_path()
    connection: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                host TEXT NOT NULL,
                model TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (host, model, text_hash)
            )
            """
        )
        connection.commit()
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except (OSError, sqlite3.DatabaseError):
        if connection is not None:
            connection.close()
        yield None
        return
    try:
        yield connection
    finally:
        connection.close()


def _read_cached_embeddings(
    connection: sqlite3.Connection,
    host: str,
    model: str,
    texts: list[str],
) -> dict[str, tuple[float, ...]]:
    hash_to_texts: dict[str, list[str]] = {}
    for text in texts:
        hash_to_texts.setdefault(_embedding_cache_hash(text), []).append(text)
    hashes = list(hash_to_texts)
    if not hashes:
        return {}
    resolved: dict[str, tuple[float, ...]] = {}
    invalid_hashes: list[str] = []
    try:
        for start in range(0, len(hashes), 400):
            batch = hashes[start : start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT text_hash, embedding_json
                FROM embedding_cache
                WHERE host = ? AND model = ? AND text_hash IN ({placeholders})
                """,
                (host, model, *batch),
            ).fetchall()
            for text_hash, serialized in rows:
                if not isinstance(text_hash, str) or not isinstance(serialized, str):
                    continue
                try:
                    payload = json.loads(serialized)
                    embedding = _coerce_embedding_vector(payload)
                except (json.JSONDecodeError, RuntimeError):
                    invalid_hashes.append(text_hash)
                    continue
                for text in hash_to_texts.get(text_hash, ()):
                    resolved[text] = embedding
        if invalid_hashes:
            connection.executemany(
                "DELETE FROM embedding_cache WHERE host = ? AND model = ? AND text_hash = ?",
                ((host, model, text_hash) for text_hash in invalid_hashes),
            )
            connection.commit()
    except sqlite3.DatabaseError:
        return {}
    return resolved


def _write_cached_embeddings(
    connection: sqlite3.Connection,
    host: str,
    model: str,
    embeddings: dict[str, tuple[float, ...]],
) -> None:
    if not embeddings:
        return
    updated_at = time.time_ns()
    try:
        connection.executemany(
            """
            INSERT OR REPLACE INTO embedding_cache (
                host,
                model,
                text_hash,
                embedding_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    host,
                    model,
                    _embedding_cache_hash(text),
                    json.dumps(list(embedding), separators=(",", ":")),
                    updated_at,
                )
                for text, embedding in embeddings.items()
            ),
        )
        connection.execute(
            """
            DELETE FROM embedding_cache
            WHERE rowid IN (
                SELECT rowid
                FROM embedding_cache
                ORDER BY updated_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (_EMBEDDING_CACHE_MAX_ROWS,),
        )
        connection.commit()
    except sqlite3.DatabaseError:
        connection.rollback()


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
        + _inline_tags(_frontmatter_body(markdown)),
        tag_identity=True,
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
            all_tags.setdefault(canonical_tag_identity(tag), tag)
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
    ordered_tags = sorted(all_tags.values(), key=lambda tag: (canonical_tag_identity(tag), tag))
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

    hits_by_identity: dict[str, SearchHit] = {}
    for path in _markdown_paths(vault):
        text = _read_note(path)
        body = _frontmatter_body(text)
        frontmatter = _simple_frontmatter(text)
        title_values = frontmatter.get("title", ())
        title = title_values[0] if title_values else _title(body, path)
        headings = _markdown_headings(body)
        folded = body.casefold()
        counts = [folded.count(term) for term in terms]
        if not any(counts):
            continue
        matched_terms = sum(count > 0 for count in counts)
        title_folded = title.casefold()
        headings_folded = "\n".join(headings).casefold()
        score = float(sum(min(count, 8) for count in counts) + matched_terms * 2)
        score += sum(5 for term in terms if term in title_folded)
        score += sum(3 for term in terms if term in headings_folded)
        score += _preference_bonus(path, preference_profile)
        relative = path.relative_to(vault).as_posix()
        hit = SearchHit(
            path=relative,
            title=title,
            score=score,
            evidence=_search_evidence(body, terms, headings=headings),
        )
        identity = _manifest_source_id(path) or _content_identity(text)
        previous = hits_by_identity.get(identity)
        if previous is None or (-hit.score, hit.path.casefold(), hit.path) < (
            -previous.score,
            previous.path.casefold(),
            previous.path,
        ):
            hits_by_identity[identity] = hit
    hits = list(hits_by_identity.values())
    hits.sort(key=lambda hit: (-hit.score, hit.path.casefold(), hit.path))
    if len(terms) > 1 and hits:
        relative_floor = hits[0].score * 0.30
        hits = [hit for hit in hits if hit.score >= relative_floor]
    return hits[:limit]


def build_answer_context(
    root: str | Path,
    query: str,
    *,
    candidate_limit: int = _ANSWER_CANDIDATE_LIMIT,
    hit_limit: int = _ANSWER_BLOCK_LIMIT,
    block_limit: int = _ANSWER_BLOCK_LIMIT,
    max_context_chars: int = _ANSWER_CONTEXT_CHAR_LIMIT,
    preference_profile: PreferenceProfile | None = None,
    semantic_config: SemanticRecallConfig | None = None,
) -> AnswerContext:
    """Build bounded, section-aware evidence for one read-only vault answer."""
    vault = _validated_root(root)
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 30:
        raise ValueError("candidate_limit must be between 1 and 30")
    if type(hit_limit) is not int or not 1 <= hit_limit <= 20:
        raise ValueError("hit_limit must be between 1 and 20")
    if type(block_limit) is not int or not 1 <= block_limit <= 8:
        raise ValueError("block_limit must be between 1 and 8")
    if type(max_context_chars) is not int or max_context_chars <= 0:
        raise ValueError("max_context_chars must be a positive integer")

    terms = _answer_query_terms(query)
    if not terms:
        return AnswerContext(hits=(), blocks=(), candidate_count=0)
    phrases = _answer_query_phrases(terms)
    list_intent = _is_list_query(query)
    multi_source_intent = _is_multi_source_query(query)
    two_source_intent = _is_two_source_query(query)
    mistake_intent = _is_mistake_query(query) and not multi_source_intent
    comparison_intent = _is_comparison_query(query)
    summary_intent = multi_source_intent and list_intent and not comparison_intent
    # Semantic retrieval is an explicit caller choice. Never infer it from the
    # process environment because callers such as OMD Home need a trustworthy
    # off switch before any vault text can reach a model endpoint.
    resolved_semantic_config = semantic_config
    recalled_notes, retrieval_mode, warnings = _recall_answer_notes(
        vault,
        query,
        terms,
        phrases,
        note_limit=candidate_limit,
        preference_profile=preference_profile,
        list_intent=list_intent,
        mistake_intent=mistake_intent,
        semantic_config=resolved_semantic_config,
    )
    if not recalled_notes:
        return AnswerContext(
            hits=(),
            blocks=(),
            candidate_count=0,
            retrieval_mode=retrieval_mode,
            warnings=warnings,
        )

    candidates: list[_AnswerCandidate] = []
    for item in recalled_notes:
        note = item.note
        candidates.extend(
            _answer_candidates_for_note(
                note.path,
                note.title,
                note.body,
                preference_bonus=note.preference_bonus,
                list_intent=list_intent,
                comparison_intent=comparison_intent,
                summary_intent=summary_intent,
                semantic_bonus=item.semantic_score,
            )
        )
    semantic_scores = {candidate.key: candidate.semantic_bonus for candidate in candidates}
    if (
        retrieval_mode == "hybrid"
        and resolved_semantic_config is not None
        and resolved_semantic_config.rerank
    ):
        try:
            reranked_scores = _semantic_candidate_scores(candidates, query, resolved_semantic_config)
        except RuntimeError:
            warnings = warnings + ("semantic_rerank_unavailable",)
        else:
            semantic_scores.update(
                {
                    key: max(semantic_scores.get(key, 0.0), score)
                    for key, score in reranked_scores.items()
                }
            )

    ranked = _rank_answer_candidates(
        candidates,
        terms,
        phrases,
        list_intent=list_intent,
        mistake_intent=mistake_intent,
        comparison_intent=comparison_intent,
        semantic_scores=semantic_scores,
    )
    if not ranked:
        return AnswerContext(
            hits=(),
            blocks=(),
            candidate_count=0,
            retrieval_mode=retrieval_mode,
            warnings=warnings,
        )
    recalled = _dedupe_ranked_answer_candidates(ranked)[:candidate_limit]
    maximum_coverage = max(_effective_match_count(item) for item in recalled)
    minimum_coverage = maximum_coverage if len(terms) <= 2 else max(1, (maximum_coverage + 1) // 2)
    selection_pool = [
        item
        for item in recalled
        if (
            (
                _effective_match_count(item) >= minimum_coverage
                or (
                    list_intent
                    and item.candidate.kind == "outline"
                    and _effective_match_count(item) >= 1
                )
                or (
                    (
                        (comparison_intent and item.candidate.kind == "overview")
                        or (summary_intent and item.candidate.kind == "digest")
                    )
                    and _effective_match_count(item) >= 1
                )
            )
            and item.score >= recalled[0].score * 0.40
        )
    ]
    if multi_source_intent:
        preferred_kind = "overview" if comparison_intent else "outline" if list_intent else None
        seeded = _seed_multi_source_candidates(recalled, preferred_kind=preferred_kind)
        if seeded:
            seeded_keys = {item.candidate.key for item in seeded}
            supplemental_kind = "digest" if summary_intent else None
            supplemental = (
                _seed_multi_source_candidates(recalled, preferred_kind=supplemental_kind)
                if supplemental_kind is not None and preferred_kind != supplemental_kind
                else []
            )
            supplemental = [item for item in supplemental if item.candidate.key not in seeded_keys]
            seeded_keys.update(item.candidate.key for item in supplemental)
            selection_pool = seeded + supplemental + [
                item for item in selection_pool if item.candidate.key not in seeded_keys
            ]
    if comparison_intent:
        overviews = [item for item in selection_pool if item.candidate.kind == "overview"]
        if len({item.candidate.path for item in overviews}) >= 2:
            selection_pool = overviews
    if summary_intent:
        summaries = [
            item for item in selection_pool if item.candidate.kind in {"outline", "digest"}
        ]
        if (
            len({item.candidate.path for item in summaries}) >= 2
            and any(item.candidate.kind == "digest" for item in summaries)
        ):
            selection_pool = sorted(summaries, key=_summary_candidate_order)
    blocks = _select_answer_blocks(
        selection_pool,
        terms,
        block_limit=block_limit,
        source_limit=min(hit_limit, 2) if two_source_intent else hit_limit,
        max_context_chars=max_context_chars,
        diversify_sources=multi_source_intent and not summary_intent,
    )
    return AnswerContext(
        hits=_answer_hits(blocks),
        blocks=tuple(blocks),
        candidate_count=len(recalled),
        retrieval_mode=retrieval_mode,
        warnings=warnings,
    )


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
        for match in re.finditer(r"(?<!\S)#([\w\u4e00-\u9fff][^\s]*)", without_code):
            characters: list[str] = []
            for character in match.group(1):
                if character not in "_/-" and unicodedata.category(character)[0] not in "LMN":
                    break
                characters.append(character)
            tag = _clean_tag("".join(characters))
            key = canonical_tag_identity(tag)
            if tag and key not in seen:
                tags.append(tag)
                seen.add(key)
    return tuple(tags)


def _clean_tag(value: str) -> str:
    return value.strip().lstrip("#").strip()


def _dedupe_text(values: tuple[str, ...], *, tag_identity: bool = False) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = canonical_tag_identity(cleaned) if tag_identity else cleaned.casefold()
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
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9\u3400-\u9fff]+", query):
        term = raw.casefold()
        if term in _QUERY_STOPWORDS or (
            raw.isascii() and len(term) <= 2 and term not in _SHORT_QUERY_TERMS
        ):
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) == _QUERY_TERM_LIMIT:
            break
    return terms


def _answer_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9\u3400-\u9fff]+", query):
        folded = raw.casefold()
        if folded in _ANSWER_QUERY_STOPWORDS or (
            raw.isascii() and len(folded) <= 2 and folded not in _SHORT_QUERY_TERMS
        ):
            continue
        term = _answer_token(raw)
        if term and term not in terms:
            terms.append(term)
        if len(terms) == _QUERY_TERM_LIMIT:
            break
    return terms


def _answer_token(raw: str) -> str:
    token = raw.casefold()
    if not raw.isascii():
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _answer_tokens(text: str) -> list[str]:
    return [
        _answer_token(raw)
        for raw in re.findall(r"[A-Za-z0-9\u3400-\u9fff]+", text)
        if raw
    ]


def _answer_query_phrases(terms: list[str]) -> tuple[tuple[str, ...], ...]:
    phrases: list[tuple[str, ...]] = []
    for size in (3, 2):
        for index in range(len(terms) - size + 1):
            phrase = tuple(terms[index:index + size])
            if phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) == 10:
                return tuple(phrases)
    return tuple(phrases)


def _is_list_query(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:all|checklist|how\s+many|list|numbered|steps?|tips?|three(?:\s+\w+){0,2}\s+mistakes?)\b",
            query,
            re.IGNORECASE,
        )
        or re.search(r"(?:列出|多少|几条|清单|建议)", query)
    )


def _is_mistake_query(query: str) -> bool:
    return bool(
        re.search(r"\b(?:mistake|mistakes|do\s+instead|corrective|correction)\b", query, re.IGNORECASE)
        or re.search(r"(?:错误|问题|改正|改進|改进)", query)
    )


def _is_multi_source_query(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:across|both|combine|combined|combining|two\s+notes?)\b",
            query,
            re.IGNORECASE,
        )
        or re.search(r"(?:两篇|两则|两条|两份|结合|合并|同时看)", query)
    )


def _is_two_source_query(query: str) -> bool:
    return bool(
        re.search(r"\b(?:both|two(?:\s+\w+){0,3}\s+notes?)\b", query, re.IGNORECASE)
        or re.search(r"(?:两篇|两则|两条|两份)", query)
    )


def _is_comparison_query(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:compare|overlap|overlapping|shared|supported by both)\b",
            query,
            re.IGNORECASE,
        )
        or re.search(r"(?:重叠|共同|同时支持|比较)", query)
    )


def _answer_candidates_for_note(
    path: str,
    title: str,
    body: str,
    *,
    preference_bonus: float,
    semantic_bonus: float,
    list_intent: bool,
    comparison_intent: bool,
    summary_intent: bool = False,
) -> list[_AnswerCandidate]:
    lines = body.splitlines()
    headings = _markdown_heading_positions(lines)
    if not headings:
        if _mostly_navigation(lines):
            return []
        text = _clean_answer_markdown(lines)
        if not text:
            return []
        return [
            _AnswerCandidate(
                path=path,
                title=title,
                heading=title,
                kind="section",
                ranking_text=text,
                evidence_text=text,
                focus=0,
                key=f"{path}:root",
                preference_bonus=preference_bonus,
                semantic_bonus=semantic_bonus if semantic_bonus >= _SEMANTIC_SECTION_THRESHOLD else 0.0,
            )
        ]

    candidates: list[_AnswerCandidate] = []
    has_subheading = any(level > 1 for _line, level, _heading in headings)
    for index, (line_number, level, heading) in enumerate(headings):
        if level == 1 and has_subheading:
            continue
        own_end = _heading_section_end(headings, index, len(lines))
        context_index = _answer_context_heading_index(headings, index)
        context_line, _context_level, context_heading = headings[context_index]
        context_end = _heading_section_end(headings, context_index, len(lines))
        ranking_lines = lines[line_number:own_end]
        if _mostly_navigation(ranking_lines):
            continue
        ranking_text = _clean_answer_markdown(ranking_lines)
        evidence_text = _clean_answer_markdown(lines[context_line:context_end])
        if not ranking_text or not evidence_text:
            continue
        focus = evidence_text.casefold().find(heading.casefold())
        candidates.append(
            _AnswerCandidate(
                path=path,
                title=title,
                heading=context_heading,
                kind="section",
                ranking_text=ranking_text,
                evidence_text=evidence_text,
                focus=max(0, focus),
                key=f"{path}:section:{context_line}",
                preference_bonus=preference_bonus,
                semantic_bonus=semantic_bonus if semantic_bonus >= _SEMANTIC_SECTION_THRESHOLD else 0.0,
            )
        )

    enumerated = [heading for _line, _level, heading in headings if _ENUMERATED_HEADING.match(heading)]
    if list_intent and len(enumerated) >= 2:
        outline_label = _outline_label(enumerated)
        outline = _outline_context_text(title, lines, headings, enumerated, outline_label)
        candidates.append(
            _AnswerCandidate(
                path=path,
                title=title,
                heading=outline_label,
                kind="outline",
                ranking_text=outline,
                evidence_text=outline,
                focus=0,
                key=f"{path}:outline",
                preference_bonus=preference_bonus,
                semantic_bonus=semantic_bonus,
            )
        )
    if comparison_intent:
        overview = _comparison_overview_text(title, lines, headings)
        if overview:
            candidates.append(
                _AnswerCandidate(
                    path=path,
                    title=title,
                    heading="Comparison summary",
                    kind="overview",
                    ranking_text=overview,
                    evidence_text=overview,
                    focus=0,
                    key=f"{path}:overview",
                    preference_bonus=preference_bonus,
                    semantic_bonus=semantic_bonus,
                )
            )
    if summary_intent:
        digest = _source_digest_text(title, lines, headings)
        if digest:
            candidates.append(
                _AnswerCandidate(
                    path=path,
                    title=title,
                    heading="Source summary",
                    kind="digest",
                    ranking_text=digest,
                    evidence_text=digest,
                    focus=0,
                    key=f"{path}:digest",
                    preference_bonus=preference_bonus,
                    semantic_bonus=semantic_bonus,
                )
            )
    return candidates


def _recall_answer_notes(
    vault: Path,
    query: str,
    terms: list[str],
    phrases: tuple[tuple[str, ...], ...],
    *,
    note_limit: int,
    preference_profile: PreferenceProfile | None,
    list_intent: bool,
    mistake_intent: bool,
    semantic_config: SemanticRecallConfig | None,
) -> tuple[list[_ScoredAnswerNote], str, tuple[str, ...]]:
    notes: list[_AnswerNote] = []
    seen_notes: set[str] = set()
    for path in _markdown_paths(vault):
        text = _read_note(path)
        identity = _manifest_source_id(path) or _content_identity(text)
        if identity in seen_notes:
            continue
        seen_notes.add(identity)
        body = _frontmatter_body(text)
        frontmatter = _simple_frontmatter(text)
        title_values = frontmatter.get("title", ())
        title = title_values[0] if title_values else _title(body, path)
        headings = tuple(_markdown_headings(body))
        outline_headings = tuple(heading for heading in headings if _ENUMERATED_HEADING.match(heading))
        has_mistake_outline = len(outline_headings) >= 2 and all(
            heading.casefold().startswith("mistake") for heading in outline_headings
        )
        notes.append(
            _AnswerNote(
                path=path.relative_to(vault).as_posix(),
                title=title,
                body=body,
                headings=headings,
                preference_bonus=_preference_bonus(path, preference_profile),
                has_outline=list_intent and len(outline_headings) >= 2,
                has_mistake_outline=has_mistake_outline,
                has_numbered_outline=len(outline_headings) >= 2 and not has_mistake_outline,
            )
        )

    lexical_ranked = _rank_answer_notes(notes, terms, phrases, mistake_intent=mistake_intent)
    semantic_scores: dict[str, float] = {}
    retrieval_mode = "sparse"
    warnings: list[str] = []
    if semantic_config is not None:
        try:
            semantic_scores = _semantic_note_scores(notes, query, semantic_config)
        except RuntimeError:
            warnings.append("semantic_recall_unavailable")
        else:
            if any(score > 0 for score in semantic_scores.values()):
                retrieval_mode = "hybrid"
    ranked = (
        _fuse_answer_note_ranks(notes, lexical_ranked, semantic_scores)
        if semantic_scores
        else lexical_ranked
    )
    if len(terms) > 1 and ranked:
        relative_floor = ranked[0].score * 0.30
        ranked = [item for item in ranked if item.score >= relative_floor]
    return ranked[:note_limit], retrieval_mode, tuple(warnings)


def _markdown_heading_positions(lines: list[str]) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if not match:
            continue
        heading = _plain_markdown_text(match.group(2))
        if heading and heading.casefold() != "full content":
            headings.append((index, len(match.group(1)), heading))
    return headings


def _heading_section_end(
    headings: list[tuple[int, int, str]],
    index: int,
    line_count: int,
) -> int:
    level = headings[index][1]
    enumerated = bool(_ENUMERATED_HEADING.match(headings[index][2]))
    return next(
        (
            line
            for line, candidate_level, heading in headings[index + 1:]
            if candidate_level <= level or (enumerated and _ENUMERATED_HEADING.match(heading))
        ),
        line_count,
    )


def _answer_context_heading_index(headings: list[tuple[int, int, str]], index: int) -> int:
    current_level = headings[index][1]
    if current_level <= 2 or _ENUMERATED_HEADING.match(headings[index][2]):
        return index
    for candidate_index in range(index - 1, -1, -1):
        candidate_level = headings[candidate_index][1]
        if candidate_level < current_level and candidate_level <= 2:
            return candidate_index
    return index


def _clean_answer_markdown(lines: list[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith(("![", "[![", "> [Source]("))
            or stripped in {"---", "***", "___"}
            or line.count("](") >= 3
        ):
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        bullet = re.match(r"^(?:[*+-])\s+(.+?)\s*$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.+?)\s*$", stripped)
        if heading:
            value = f"{heading.group(1)} {_plain_markdown_text(heading.group(2))}"
        elif bullet:
            value = f"- {_plain_markdown_text(bullet.group(1))}"
        elif numbered:
            value = f"{numbered.group(1)}. {_plain_markdown_text(numbered.group(2))}"
        else:
            value = _plain_markdown_text(stripped)
        if value:
            cleaned.append(value)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    return "\n".join(cleaned).strip()


def _mostly_navigation(lines: list[str]) -> bool:
    content = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if len(content) < 4:
        return False
    linked = sum("[[" in line or "](" in line for line in content)
    return linked / len(content) >= 0.6


def _rank_answer_candidates(
    candidates: list[_AnswerCandidate],
    terms: list[str],
    phrases: tuple[tuple[str, ...], ...],
    *,
    list_intent: bool,
    mistake_intent: bool,
    comparison_intent: bool,
    semantic_scores: dict[str, float] | None = None,
) -> list[_ScoredAnswerCandidate]:
    if not candidates:
        return []
    token_lists = [_answer_tokens(candidate.ranking_text) for candidate in candidates]
    document_frequencies = Counter(
        term
        for tokens in token_lists
        for term in set(tokens)
        if term in terms
    )
    average_length = max(1.0, sum(len(tokens) for tokens in token_lists) / len(token_lists))
    ranked: list[_ScoredAnswerCandidate] = []
    for candidate, tokens in zip(candidates, token_lists):
        counts = Counter(tokens)
        score = _bm25_score(
            counts,
            len(tokens),
            terms,
            document_frequencies,
            document_count=len(candidates),
            average_length=average_length,
        )
        title_tokens = _answer_tokens(candidate.title)
        heading_tokens = _answer_tokens(candidate.heading)
        title_terms = set(title_tokens)
        heading_terms = set(heading_tokens)
        matched_terms = sum(
            term in counts or term in title_terms or term in heading_terms
            for term in terms
        )
        score += sum(2.5 for term in terms if term in title_terms)
        score += sum(1.75 for term in terms if term in heading_terms)
        if any(_contains_token_phrase(title_tokens, phrase) for phrase in phrases):
            score += 8.0
        if any(_contains_token_phrase(heading_tokens, phrase) for phrase in phrases):
            score += 6.0
        if any(_contains_token_phrase(tokens, phrase) for phrase in phrases):
            score += 2.0
        lexical_score = score
        semantic_score = max(
            candidate.semantic_bonus,
            0.0 if semantic_scores is None else semantic_scores.get(candidate.key, 0.0),
        )
        if lexical_score <= 0 or matched_terms == 0:
            structured = candidate.kind in {"outline", "overview", "digest"}
            section_semantic_ok = (
                candidate.kind == "section" and semantic_score >= _SEMANTIC_SECTION_THRESHOLD
            )
            if not ((structured and semantic_score >= _SEMANTIC_STRUCTURED_THRESHOLD) or section_semantic_ok):
                continue
        if semantic_score <= 0 and lexical_score <= 0:
            continue
        if list_intent and candidate.kind == "outline":
            score += 6.0
        if list_intent and candidate.kind == "digest":
            score += 5.0
        if comparison_intent and candidate.kind == "overview":
            score += 7.0
        if mistake_intent:
            if candidate.heading.casefold().startswith("mistake outline"):
                score += 10.0
            elif candidate.heading.casefold().startswith("mistake"):
                score += 6.0
            elif candidate.kind == "outline":
                score -= 4.0
        score += semantic_score * _SEMANTIC_CANDIDATE_WEIGHT
        score += candidate.preference_bonus
        ranked.append(
            _ScoredAnswerCandidate(
                candidate=candidate,
                score=score,
                matched_terms=matched_terms,
                semantic_score=semantic_score,
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.score,
            item.candidate.path.casefold(),
            item.candidate.path,
            item.candidate.key,
        )
    )
    return ranked


def _rank_answer_notes(
    notes: list[_AnswerNote],
    terms: list[str],
    phrases: tuple[tuple[str, ...], ...],
    *,
    mistake_intent: bool,
    semantic_scores: dict[str, float] | None = None,
) -> list[_ScoredAnswerNote]:
    if not notes:
        return []
    token_lists = [
        _answer_tokens("\n".join((note.title, *note.headings, note.body)))
        for note in notes
    ]
    document_frequencies = Counter(
        term
        for tokens in token_lists
        for term in set(tokens)
        if term in terms
    )
    average_length = max(1.0, sum(len(tokens) for tokens in token_lists) / len(token_lists))
    ranked: list[_ScoredAnswerNote] = []
    for note, tokens in zip(notes, token_lists):
        counts = Counter(tokens)
        score = _bm25_score(
            counts,
            len(tokens),
            terms,
            document_frequencies,
            document_count=len(notes),
            average_length=average_length,
        )
        title_tokens = _answer_tokens(note.title)
        heading_tokens = _answer_tokens("\n".join(note.headings))
        title_terms = set(title_tokens)
        headings_terms = set(heading_tokens)
        matched_terms = sum(
            term in counts or term in title_terms or term in headings_terms
            for term in terms
        )
        score += sum(2.5 for term in terms if term in title_terms)
        score += sum(1.75 for term in terms if term in headings_terms)
        if any(_contains_token_phrase(title_tokens, phrase) for phrase in phrases):
            score += 8.0
        if any(_contains_token_phrase(heading_tokens, phrase) for phrase in phrases):
            score += 6.0
        if any(_contains_token_phrase(tokens, phrase) for phrase in phrases):
            score += 2.0
        semantic_score = 0.0 if semantic_scores is None else semantic_scores.get(note.path, 0.0)
        if (score <= 0 or matched_terms == 0) and semantic_score <= 0:
            continue
        if note.has_outline:
            score += 6.0
        if mistake_intent:
            if note.has_mistake_outline:
                score += 10.0
            elif note.has_numbered_outline:
                score -= 4.0
        score += semantic_score * _SEMANTIC_NOTE_WEIGHT
        score += note.preference_bonus
        ranked.append(
            _ScoredAnswerNote(
                note=note,
                score=score,
                matched_terms=matched_terms,
                semantic_score=semantic_score,
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.score,
            item.note.path.casefold(),
            item.note.path,
        )
    )
    return ranked


def _fuse_answer_note_ranks(
    notes: list[_AnswerNote],
    lexical_ranked: list[_ScoredAnswerNote],
    semantic_scores: dict[str, float],
) -> list[_ScoredAnswerNote]:
    note_by_path = {note.path: note for note in notes}
    lexical_by_path = {item.note.path: item for item in lexical_ranked}
    lexical_rank = {item.note.path: index for index, item in enumerate(lexical_ranked, start=1)}
    semantic_ranked_paths = [
        path
        for path, _score in sorted(
            (
                (note.path, semantic_scores.get(note.path, 0.0))
                for note in notes
            ),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )
        if _score > 0
    ]
    semantic_rank = {path: index for index, path in enumerate(semantic_ranked_paths, start=1)}
    combined_paths = set(lexical_rank) | set(semantic_rank)
    fused: list[_ScoredAnswerNote] = []
    for path in combined_paths:
        note = note_by_path[path]
        lexical_item = lexical_by_path.get(path)
        fused_score = 0.0
        if path in lexical_rank:
            fused_score += 1.0 / (_SEMANTIC_RRF_K + lexical_rank[path])
        if path in semantic_rank:
            fused_score += 1.0 / (_SEMANTIC_RRF_K + semantic_rank[path])
        if fused_score <= 0:
            continue
        fused.append(
            _ScoredAnswerNote(
                note=note,
                score=fused_score,
                matched_terms=0 if lexical_item is None else lexical_item.matched_terms,
                semantic_score=semantic_scores.get(path, 0.0),
            )
        )
    fused.sort(
        key=lambda item: (
            -item.score,
            item.note.path.casefold(),
            item.note.path,
        )
    )
    return fused


def _bm25_score(
    counts: Counter[str],
    document_length: int,
    terms: list[str],
    document_frequencies: Counter[str],
    *,
    document_count: int,
    average_length: float,
) -> float:
    score = 0.0
    length_ratio = document_length / average_length
    for term in terms:
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        document_frequency = document_frequencies.get(term, 0)
        inverse_frequency = math.log(
            1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        denominator = frequency + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length_ratio)
        score += inverse_frequency * frequency * (_BM25_K1 + 1.0) / denominator
    return score


def _contains_token_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tuple(tokens[index:index + size]) == phrase for index in range(len(tokens) - size + 1))


def _seed_multi_source_candidates(
    recalled: list[_ScoredAnswerCandidate],
    *,
    preferred_kind: str | None,
) -> list[_ScoredAnswerCandidate]:
    source_paths: list[str] = []
    for item in recalled:
        path = item.candidate.path
        if path not in source_paths:
            source_paths.append(path)
        if len(source_paths) == 2:
            break
    seeded: list[_ScoredAnswerCandidate] = []
    for path in source_paths:
        source_items = [item for item in recalled if item.candidate.path == path]
        preferred = next(
            (item for item in source_items if item.candidate.kind == preferred_kind),
            None,
        )
        if preferred is not None:
            seeded.append(preferred)
        elif source_items:
            seeded.append(source_items[0])
    return seeded


def _summary_candidate_order(item: _ScoredAnswerCandidate) -> tuple[int, int, str]:
    text = item.candidate.evidence_text
    category = 0 if "Answer category: explicit tips" in text else 1
    kind = 0 if item.candidate.kind == "outline" else 1
    return category, kind, item.candidate.path.casefold()


def _select_answer_blocks(
    recalled: list[_ScoredAnswerCandidate],
    terms: list[str],
    *,
    block_limit: int,
    source_limit: int,
    max_context_chars: int,
    diversify_sources: bool,
) -> list[EvidenceBlock]:
    selected: list[EvidenceBlock] = []
    selected_keys: set[str] = set()
    source_counts: Counter[str] = Counter()
    source_paths: set[str] = set()
    used_chars = 0
    per_source_limits = (1, 2, 4, block_limit) if diversify_sources else (block_limit,)
    for per_source_limit in per_source_limits:
        for item in recalled:
            candidate = item.candidate
            if len(selected) >= block_limit or used_chars >= max_context_chars:
                return selected
            if candidate.key in selected_keys or source_counts[candidate.path] >= per_source_limit:
                continue
            if candidate.path not in source_paths and len(source_paths) >= source_limit:
                continue
            remaining = max_context_chars - used_chars
            text = _bounded_answer_text(
                candidate.evidence_text,
                focus=candidate.focus,
                limit=min(_ANSWER_BLOCK_CHAR_LIMIT, remaining),
                terms=terms,
            )
            if not text:
                continue
            selected.append(
                EvidenceBlock(
                    path=candidate.path,
                    title=candidate.title,
                    heading=candidate.heading,
                    kind=candidate.kind,
                    score=item.score,
                    text=text,
                )
            )
            selected_keys.add(candidate.key)
            source_counts[candidate.path] += 1
            source_paths.add(candidate.path)
            used_chars += len(text)
    return selected


def _dedupe_ranked_answer_candidates(
    ranked: list[_ScoredAnswerCandidate],
) -> list[_ScoredAnswerCandidate]:
    deduped: list[_ScoredAnswerCandidate] = []
    seen_keys: set[str] = set()
    for item in ranked:
        key = item.candidate.key
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    return deduped


def _bounded_answer_text(text: str, *, focus: int, limit: int, terms: list[str]) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    folded = text.casefold()
    term_positions = [folded.find(term, max(0, focus - 200)) for term in terms]
    relevant_positions = [position for position in term_positions if position >= 0]
    center = min(relevant_positions, key=lambda position: abs(position - focus)) if relevant_positions else focus
    start = max(0, center - 600)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    if start:
        newline = text.find("\n", start)
        start = newline + 1 if 0 <= newline < end else start
    if end < len(text):
        newline = text.rfind("\n", start, end)
        end = newline if newline > start else end
    excerpt = text[start:end].strip()
    if start:
        first_line = text.splitlines()[0].strip()
        excerpt = f"{first_line}\n…\n{excerpt}"
    if end < len(text):
        excerpt += "\n…"
    return excerpt[:limit].rstrip()


def _answer_hits(blocks: list[EvidenceBlock]) -> tuple[SearchHit, ...]:
    grouped: dict[str, list[EvidenceBlock]] = {}
    for block in blocks:
        grouped.setdefault(block.path, []).append(block)
    hits: list[SearchHit] = []
    for path, source_blocks in grouped.items():
        evidence = "\n\n".join(
            f"Section: {block.heading}\n{block.text}" for block in source_blocks
        )
        hits.append(
            SearchHit(
                path=path,
                title=source_blocks[0].title,
                score=max(block.score for block in source_blocks),
                evidence=evidence,
            )
        )
    return tuple(hits)


def _title(text: str, path: Path) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1)
    return path.stem


def _search_evidence(text: str, terms: list[str], *, headings: list[str] | None = None) -> str:
    outline = _evidence_outline(headings if headings is not None else _markdown_headings(text), terms)
    passages = _evidence_passages(text, terms)
    parts: list[str] = []
    if outline:
        parts.append("Outline:\n" + "\n".join(outline))
    if passages:
        parts.append("Relevant excerpts:\n" + "\n\n".join(passages))
    if not parts:
        return " ".join(text.split())[:_SEARCH_EVIDENCE_LIMIT]
    return "\n\n".join(parts)[:_SEARCH_EVIDENCE_LIMIT].rstrip()


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


def _markdown_headings(text: str) -> list[str]:
    headings: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", stripped)
        if match:
            heading = _plain_markdown_text(match.group(1))
            if heading and heading.casefold() != "full content":
                headings.append(heading)
    return headings


def _evidence_outline(headings: list[str], terms: list[str]) -> list[str]:
    if not headings:
        return []
    enumerated = [heading for heading in headings if _ENUMERATED_HEADING.match(heading)]
    selected: list[str] = []
    for index, heading in enumerate(headings):
        folded = heading.casefold()
        if (
            index == 0
            or any(term in folded for term in terms)
            or (len(enumerated) >= 2 and heading in enumerated)
        ) and heading not in selected:
            selected.append(heading)
        if len(selected) == _MAX_EVIDENCE_HEADINGS:
            break
    return selected


def _evidence_passages(text: str, terms: list[str]) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(re.split(r"\n\s*\n", text)):
        stripped = block.lstrip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith(("![", "[![", "> [Source]("))
            or block.count("](") >= 2
        ):
            continue
        normalized = _plain_markdown_text(block)
        if not normalized:
            continue
        folded = normalized.casefold()
        counts = [folded.count(term) for term in terms]
        if not any(counts):
            continue
        score = sum(min(count, 4) for count in counts) + sum(count > 0 for count in counts) * 3
        scored.append((-score, index, normalized[:500]))
    selected = sorted(scored)[:_MAX_EVIDENCE_PASSAGES]
    return [passage for _, _, passage in sorted(selected, key=lambda item: item[1])]


def _plain_markdown_text(text: str) -> str:
    plain = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
    plain = re.sub(r"https?://\S+", "", plain)
    plain = re.sub(r"[*_`~]+", "", plain)
    return " ".join(plain.split())


def _outline_label(enumerated: tuple[str, ...] | list[str]) -> str:
    return (
        "Mistake outline"
        if all(heading.casefold().startswith("mistake") for heading in enumerated)
        else "Numbered outline"
    )


def _outline_context_text(
    title: str,
    lines: list[str],
    headings: list[tuple[int, int, str]],
    enumerated: tuple[str, ...] | list[str],
    outline_label: str,
) -> str:
    first_enumerated_line = next(
        (line_number for line_number, _level, heading in headings if _ENUMERATED_HEADING.match(heading)),
        len(lines),
    )
    outline_parent = _outline_parent_heading(title, headings, first_enumerated_line)
    prelude = _clean_answer_markdown(lines[:first_enumerated_line])
    if prelude:
        prelude_lines = prelude.splitlines()
        if prelude_lines and prelude_lines[0].casefold() == f"# {title}".casefold():
            prelude = "\n".join(prelude_lines[1:]).strip()
    if prelude:
        prelude_lines = [line for line in prelude.splitlines() if line.strip()]
        prelude = "\n".join(prelude_lines[:4])
        if len(prelude) > 500:
            prelude = prelude[:497].rstrip() + "..."
    category = "inferred lessons" if outline_label == "Mistake outline" else "explicit tips"
    coverage = len(enumerated)
    outline = outline_label + ":\n" + "\n".join(f"- {heading}" for heading in enumerated)
    parts = [f"# {title}"]
    if outline_parent:
        parts.append(f"Section: {outline_parent}")
    if prelude and prelude.casefold() != f"# {title}".casefold():
        parts.append(prelude)
    parts.append(f"Answer category: {category}. Required coverage: {coverage} of {coverage}.")
    parts.append(outline)
    return "\n\n".join(parts)


def _outline_parent_heading(
    title: str,
    headings: list[tuple[int, int, str]],
    first_enumerated_line: int,
) -> str | None:
    for line_number, _level, heading in reversed(headings):
        if line_number >= first_enumerated_line or _ENUMERATED_HEADING.match(heading):
            continue
        if heading.casefold() == title.casefold():
            continue
        return heading
    return None


def _structured_overview_bullets(
    lines: list[str],
    headings: list[tuple[int, int, str]],
) -> list[str]:
    primary = [item for item in headings if _ENUMERATED_HEADING.match(item[2])]
    if len(primary) < 2:
        primary = [item for item in headings if item[1] > 1][:8]
    if not primary:
        return []
    snippet_limit = min(180, max(100, 1_200 // len(primary)))
    bullets: list[str] = []
    for index, (line_number, level, heading) in enumerate(primary):
        section_end = primary[index + 1][0] if index + 1 < len(primary) else len(lines)
        section_end = next(
            (
                child_line
                for child_line, child_level, _value in headings
                if line_number < child_line < section_end and child_level <= level
            ),
            section_end,
        )
        supporting_headings = [
            value
            for child_line, _child_level, value in headings
            if line_number < child_line < section_end and not _ENUMERATED_HEADING.match(value)
        ][:4]
        actions = [
            action
            for line in lines[line_number + 1:section_end]
            if (match := re.match(r"^\s*[-*+]\s+(?:\[[ xX]\]\s+)?(.+)", line))
            if (action := _plain_markdown_text(match.group(1))) and len(action.split()) >= 3
        ][:4]
        section = _clean_answer_markdown(lines[line_number + 1:section_end])
        snippet = " ".join(section.split())[:snippet_limit].rstrip()
        details: list[str] = []
        if supporting_headings:
            details.append("Key points: " + "; ".join(supporting_headings)[:500].rstrip())
        if actions:
            details.append("Key actions: " + "; ".join(actions)[:320].rstrip())
        if snippet:
            details.append("Excerpt: " + snippet)
        bullets.append(f"- {heading}: " + " | ".join(details) if details else f"- {heading}")
    return bullets


def _comparison_overview_text(
    title: str,
    lines: list[str],
    headings: list[tuple[int, int, str]],
) -> str:
    bullets = _structured_overview_bullets(lines, headings)
    if not bullets:
        return ""
    contract = (
        "Comparison rule: Pair semantically aligned explicit actions from this source and "
        "another source even when wording differs; cite both. Report only matches."
    )
    return f"# {title}\n\n{contract}\n\nComparison overview:\n" + "\n".join(bullets)


def _source_digest_text(
    title: str,
    lines: list[str],
    headings: list[tuple[int, int, str]],
) -> str:
    bullets = _structured_overview_bullets(lines, headings)
    if not bullets:
        return ""
    enumerated = [heading for _line, _level, heading in headings if _ENUMERATED_HEADING.match(heading)]
    if enumerated:
        category = (
            "inferred lessons" if _outline_label(enumerated) == "Mistake outline" else "explicit tips"
        )
        requirement = f"Answer category: {category}. Required coverage: {len(enumerated)} of {len(enumerated)}."
        response_rule = (
            f'Create a separate response section named "{category}" with exactly '
            f"{len(enumerated)} numbered items."
        )
    else:
        requirement = "Answer category: supporting evidence."
        response_rule = "Keep this source in a separate supporting-evidence section."
    contract = (
        f"{requirement}\n"
        f"Source-local rule: {response_rule} Never move an item to another category. "
        "Keep one supported action or detail per item. Do not compare "
        "sources or infer overlap."
    )
    return f"# {title}\n\n{contract}\n\nSource summary:\n" + "\n".join(bullets)


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


def _content_identity(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "content-sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
