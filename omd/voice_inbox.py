"""Durable local voice attachments for the desktop Inbox review workflow."""
from __future__ import annotations

import fcntl
import hashlib
import html
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ._json_contract import canonical_json, json_object
from ._transcript import assess_transcript_quality
from .knowledge_note import KnowledgeNote


VOICE_INBOX_SCHEMA_VERSION = 1
VOICE_ENVELOPE_SCHEMA_VERSION = 1
SUPPORTED_AUDIO_SUFFIXES = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
)
EMPTY_TRANSCRIPT_WARNING = "The local transcript is empty; keep the audio and retry or add your own note."
LOW_CONFIDENCE_WARNING = "Whisper reported low confidence; review the raw transcript before accepting it."

_TRANSCRIPTION_STATES = frozenset({"queued", "transcribing", "needs_review", "failed"})
_REVIEW_STATES = frozenset({"inbox", "accepted", "rejected"})
_TRANSCRIPT_DECISIONS = frozenset({"pending", "keep_raw", "edited", "accepted", "rejected"})
_RECORD_ID_RE = re.compile(r"voice_[0-9a-f]{16}")
_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MARKDOWN_META_RE = re.compile(r"([\\`*{}\[\]()<>#+\-.!_|~])")


@dataclass(frozen=True)
class VoiceInboxRecord:
    record_id: str
    title: str
    attachment_path: str
    attachment_sha256: str
    attachment_bytes: int
    created_at: str
    updated_at: str
    transcription_state: str = "queued"
    review_state: str = "inbox"
    transcript_decision: str = "pending"
    transcription_attempts: int = 0
    transcript_backend: str = ""
    transcript_model: str = ""
    transcript_language: str = ""
    raw_transcript: str = field(default="", repr=False)
    my_notes: str = field(default="", repr=False)
    ai_suggestion: str = field(default="", repr=False)
    quality_warnings: tuple[str, ...] = ()
    error_code: str = ""
    schema_version: int = VOICE_INBOX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VOICE_INBOX_SCHEMA_VERSION:
            raise ValueError(f"unsupported voice inbox schema: {self.schema_version}")
        if not _RECORD_ID_RE.fullmatch(self.record_id):
            raise ValueError("invalid voice record id")
        if not self.title.strip():
            raise ValueError("voice title must not be empty")
        _relative_attachment_path(self.attachment_path)
        if not re.fullmatch(r"[0-9a-f]{64}", self.attachment_sha256):
            raise ValueError("invalid voice attachment checksum")
        if not isinstance(self.attachment_bytes, int) or isinstance(self.attachment_bytes, bool) or self.attachment_bytes < 0:
            raise ValueError("attachment_bytes must be a non-negative integer")
        _timestamp(self.created_at)
        _timestamp(self.updated_at)
        if self.transcription_state not in _TRANSCRIPTION_STATES:
            raise ValueError("invalid transcription state")
        if self.review_state not in _REVIEW_STATES:
            raise ValueError("invalid review state")
        if self.transcript_decision not in _TRANSCRIPT_DECISIONS:
            raise ValueError("invalid transcript decision")
        if (
            not isinstance(self.transcription_attempts, int)
            or isinstance(self.transcription_attempts, bool)
            or self.transcription_attempts < 0
        ):
            raise ValueError("transcription_attempts must be a non-negative integer")
        if self.error_code and not _ERROR_CODE_RE.fullmatch(self.error_code):
            raise ValueError("invalid voice error code")
        if not isinstance(self.quality_warnings, tuple) or not all(
            isinstance(value, str) and value.strip() for value in self.quality_warnings
        ):
            raise ValueError("quality_warnings must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "title": self.title,
            "attachment_path": self.attachment_path,
            "attachment_sha256": self.attachment_sha256,
            "attachment_bytes": self.attachment_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transcription_state": self.transcription_state,
            "review_state": self.review_state,
            "transcript_decision": self.transcript_decision,
            "transcription_attempts": self.transcription_attempts,
            "transcript_backend": self.transcript_backend,
            "transcript_model": self.transcript_model,
            "transcript_language": self.transcript_language,
            "raw_transcript": self.raw_transcript,
            "my_notes": self.my_notes,
            "ai_suggestion": self.ai_suggestion,
            "quality_warnings": list(self.quality_warnings),
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "VoiceInboxRecord":
        data = json_object(value, name="voice_record")
        warnings = data.get("quality_warnings", [])
        if not isinstance(warnings, list):
            raise ValueError("quality_warnings must be an array")
        return cls(
            schema_version=data.get("schema_version", VOICE_INBOX_SCHEMA_VERSION),
            record_id=data.get("record_id"),
            title=data.get("title"),
            attachment_path=data.get("attachment_path"),
            attachment_sha256=data.get("attachment_sha256"),
            attachment_bytes=data.get("attachment_bytes"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            transcription_state=data.get("transcription_state", "queued"),
            review_state=data.get("review_state", "inbox"),
            transcript_decision=data.get("transcript_decision", "pending"),
            transcription_attempts=data.get("transcription_attempts", 0),
            transcript_backend=str(data.get("transcript_backend") or ""),
            transcript_model=str(data.get("transcript_model") or ""),
            transcript_language=str(data.get("transcript_language") or ""),
            raw_transcript=str(data.get("raw_transcript") or ""),
            my_notes=str(data.get("my_notes") or ""),
            ai_suggestion=str(data.get("ai_suggestion") or ""),
            quality_warnings=tuple(warnings),
            error_code=str(data.get("error_code") or ""),
        )


class VoiceInboxStore:
    """Vault-local source of truth for existing audio attachments and review state."""

    def __init__(self, vault: str | Path) -> None:
        root = Path(vault).expanduser()
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("vault must be an existing non-symlink directory")
        self.root = root.resolve(strict=True)
        self.inbox = _safe_directory(self.root, "Inbox")
        attachments = _safe_directory(self.inbox, "_attachments")
        self.attachments = _safe_directory(attachments, "voice")
        metadata = _safe_directory(self.inbox, ".omd")
        self.metadata = _safe_directory(metadata, "voice")
        self.locks = _safe_directory(self.metadata, "locks")

    def create(
        self,
        source: str | Path,
        *,
        title: str,
        my_notes: str = "",
    ) -> VoiceInboxRecord:
        source_path = _audio_source(source)
        attachment, digest, size = _copy_audio_durable(source_path, self.attachments)
        now = _now()
        record_id = "voice_" + hashlib.sha256(
            f"{digest}:{now}:{secrets.token_hex(8)}".encode("utf-8")
        ).hexdigest()[:16]
        record = VoiceInboxRecord(
            record_id=record_id,
            title=_required_text(title, name="title"),
            attachment_path=attachment.relative_to(self.root).as_posix(),
            attachment_sha256=digest,
            attachment_bytes=size,
            created_at=now,
            updated_at=now,
            my_notes=_text(my_notes, name="my_notes"),
        )
        self._persist(record)
        return record

    def load(self, record_id: str) -> VoiceInboxRecord:
        normalized = _record_id(record_id)
        with self._lock(normalized):
            record = self._read_record(normalized)
            self._validate_attachment(record)
            return record

    def list_records(self) -> list[VoiceInboxRecord]:
        records = [self.load(path.stem) for path in sorted(self.metadata.glob("voice_*.json"))]
        return sorted(records, key=lambda item: (item.created_at, item.record_id), reverse=True)

    def begin_transcription(
        self,
        record_id: str,
        *,
        backend: str,
        model: str,
        language: str = "",
    ) -> VoiceInboxRecord:
        backend_value = _required_text(backend, name="backend")
        model_value = _required_text(model, name="model")

        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state not in {"queued", "failed", "needs_review"}:
                raise ValueError(f"cannot transcribe from {record.transcription_state}")
            return replace(
                record,
                transcription_state="transcribing",
                transcription_attempts=record.transcription_attempts + 1,
                transcript_backend=backend_value,
                transcript_model=model_value,
                transcript_language=_text(language, name="language"),
                error_code="",
                updated_at=_now(),
            )

        return self._update(record_id, update)

    def retry_transcription(self, record_id: str) -> VoiceInboxRecord:
        record = self.load(record_id)
        if not record.transcript_backend or not record.transcript_model:
            raise ValueError("voice record has no local transcription configuration")
        return self.begin_transcription(
            record_id,
            backend=record.transcript_backend,
            model=record.transcript_model,
            language=record.transcript_language,
        )

    def resume_transcription(self, record_id: str) -> VoiceInboxRecord:
        """Restart a stage left transcribing after an interrupted UI process."""

        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state != "transcribing":
                raise ValueError("voice transcription is not interrupted")
            return replace(
                record,
                transcription_attempts=record.transcription_attempts + 1,
                error_code="",
                updated_at=_now(),
            )

        return self._update(record_id, update)

    def save_transcript(
        self,
        record_id: str,
        transcript: Mapping[str, Any],
    ) -> VoiceInboxRecord:
        transcript_data = json_object(transcript, name="transcript")
        raw_text = str(transcript_data.get("text") or "").strip()
        warnings = list(
            assess_transcript_quality(
                transcript_data,
                expected_language=None,
            )
        )
        reported_warnings = transcript_data.get("quality_warnings", [])
        if isinstance(reported_warnings, list):
            warnings.extend(
                value.strip()
                for value in reported_warnings
                if isinstance(value, str) and value.strip()
            )
        if not raw_text:
            warnings.insert(0, EMPTY_TRANSCRIPT_WARNING)
        confidence = transcript_data.get("confidence")
        if _low_confidence(confidence, transcript_data.get("segments")):
            warnings.append(LOW_CONFIDENCE_WARNING)
        unique_warnings = tuple(dict.fromkeys(warnings))

        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state != "transcribing":
                raise ValueError("voice record is not transcribing")
            return replace(
                record,
                transcription_state="needs_review",
                transcript_decision="pending",
                raw_transcript=raw_text,
                quality_warnings=unique_warnings,
                error_code="",
                updated_at=_now(),
            )

        return self._update(record_id, update)

    def edit_transcript(self, record_id: str, text: str) -> VoiceInboxRecord:
        edited = _text(text, name="transcript").strip()

        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state not in {"needs_review", "failed"}:
                raise ValueError("voice transcript is not ready to edit")
            warnings = tuple(assess_transcript_quality({"text": edited}))
            if not edited:
                warnings = (EMPTY_TRANSCRIPT_WARNING, *warnings)
            return replace(
                record,
                transcription_state="needs_review",
                transcript_decision="edited",
                raw_transcript=edited,
                quality_warnings=tuple(dict.fromkeys(warnings)),
                error_code="",
                updated_at=_now(),
            )

        return self._update(record_id, update)

    def fail_transcription(self, record_id: str, *, error_code: str) -> VoiceInboxRecord:
        normalized_error = _error_code(error_code)

        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state != "transcribing":
                raise ValueError("voice record is not transcribing")
            return replace(
                record,
                transcription_state="failed",
                error_code=normalized_error,
                updated_at=_now(),
            )

        return self._update(record_id, update)

    def set_my_notes(self, record_id: str, notes: str) -> VoiceInboxRecord:
        return self._update(
            record_id,
            lambda record: replace(record, my_notes=_text(notes, name="my_notes"), updated_at=_now()),
        )

    def set_ai_suggestion(self, record_id: str, suggestion: str) -> VoiceInboxRecord:
        return self._update(
            record_id,
            lambda record: replace(
                record,
                ai_suggestion=_text(suggestion, name="ai_suggestion"),
                updated_at=_now(),
            ),
        )

    def keep_raw(self, record_id: str) -> VoiceInboxRecord:
        def update(record: VoiceInboxRecord) -> VoiceInboxRecord:
            if record.transcription_state != "needs_review" or not record.raw_transcript:
                raise ValueError("no raw transcript is available to keep")
            return replace(record, transcript_decision="keep_raw", updated_at=_now())

        return self._update(record_id, update)

    def accept(self, record_id: str) -> VoiceInboxRecord:
        normalized = _record_id(record_id)
        with self._lock(normalized):
            current = self._read_record(normalized)
            self._validate_attachment(current)
            accepted = replace(
                current,
                review_state="accepted",
                transcript_decision=(
                    "accepted"
                    if current.transcript_decision in {"pending", "rejected"}
                    else current.transcript_decision
                ),
                updated_at=_now(),
            )
            self._persist_reviewed_note(accepted)
            self._persist(accepted)
            return accepted

    def reject(self, record_id: str) -> VoiceInboxRecord:
        return self._update(
            record_id,
            lambda record: replace(
                record,
                review_state="rejected",
                transcript_decision="rejected",
                updated_at=_now(),
            ),
        )

    def sidecar_path(self, record_id: str) -> Path:
        return self.metadata / f"{_record_id(record_id)}.json"

    def markdown_path(self, record_id: str) -> Path:
        record = self.load(record_id)
        return self._markdown_path_for(record)

    def reviewed_note_path(self, record_id: str) -> Path:
        record = self.load(record_id)
        return self._reviewed_note_path_for(record)

    def attachment_path(self, record_id: str) -> Path:
        record = self.load(record_id)
        return self.root / record.attachment_path

    def _markdown_path_for(self, record: VoiceInboxRecord) -> Path:
        return self.inbox / f"{record.created_at[:10]}-voice-note-{record.record_id[-8:]}.md"

    def _reviewed_note_path_for(self, record: VoiceInboxRecord) -> Path:
        return self.root / "Notes" / f"{record.created_at[:10]}-voice-note-{record.record_id[-8:]}.md"

    def _persist_reviewed_note(self, record: VoiceInboxRecord) -> None:
        notes = _safe_directory(self.root, "Notes")
        output = self._reviewed_note_path_for(record)
        sidecar = output.with_suffix(".omd.json")
        if output.is_symlink() or sidecar.is_symlink():
            raise ValueError("reviewed voice note must not be a symlink")
        if output.exists() and not sidecar.exists():
            raise ValueError(f"reviewed voice note sidecar is missing: {output.name}")

        note = _reviewed_knowledge_note(record)
        created_sidecar = False
        if sidecar.exists():
            try:
                existing = KnowledgeNote.from_json(sidecar.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError(f"reviewed voice note sidecar is invalid: {output.name}") from exc
            if existing.derived_from != record.record_id:
                raise ValueError(f"reviewed voice note path already exists: {output.name}")
            note = existing
            if output.exists():
                return
        else:
            _write_text_durable(sidecar, note.to_json())
            created_sidecar = True
        try:
            _write_text_durable(output, _render_reviewed_markdown(note))
        except BaseException:
            output.unlink(missing_ok=True)
            if created_sidecar:
                sidecar.unlink(missing_ok=True)
            raise
        _sync_directory(notes)

    def _update(self, record_id: str, update) -> VoiceInboxRecord:
        normalized = _record_id(record_id)
        with self._lock(normalized):
            current = self._read_record(normalized)
            self._validate_attachment(current)
            updated = update(current)
            if not isinstance(updated, VoiceInboxRecord):
                raise TypeError("voice update must return VoiceInboxRecord")
            self._persist(updated)
            return updated

    def _persist(self, record: VoiceInboxRecord) -> None:
        signed = {
            "schema_version": VOICE_ENVELOPE_SCHEMA_VERSION,
            "record": record.to_dict(),
        }
        envelope = dict(signed)
        envelope["checksum"] = hashlib.sha256(
            canonical_json(signed).encode("utf-8")
        ).hexdigest()
        sidecar = self.sidecar_path(record.record_id)
        markdown = self._markdown_path_for(record)
        previous_sidecar = _rollback_snapshot(sidecar)
        previous_markdown = _rollback_snapshot(markdown)
        try:
            _write_text_durable(
                sidecar,
                json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
            _write_text_durable(markdown, _render_markdown(record))
        except BaseException:
            _restore_snapshot(sidecar, previous_sidecar)
            _restore_snapshot(markdown, previous_markdown)
            raise

    def _read_record(self, record_id: str) -> VoiceInboxRecord:
        path = self.sidecar_path(record_id)
        if path.is_symlink():
            raise ValueError("voice sidecar must not be a symlink")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("voice record is corrupt") from exc
        data = json_object(envelope, name="voice_envelope")
        signed = {
            "schema_version": data.get("schema_version"),
            "record": data.get("record"),
        }
        checksum = data.get("checksum")
        expected = hashlib.sha256(canonical_json(signed).encode("utf-8")).hexdigest()
        if not isinstance(checksum, str) or not secrets.compare_digest(checksum, expected):
            raise ValueError("voice record integrity check failed")
        if signed["schema_version"] != VOICE_ENVELOPE_SCHEMA_VERSION:
            raise ValueError("unsupported voice envelope schema")
        return VoiceInboxRecord.from_dict(json_object(signed["record"], name="voice_record"))

    def _validate_attachment(self, record: VoiceInboxRecord) -> None:
        relative = _relative_attachment_path(record.attachment_path)
        candidate = self.root / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.attachments)
        except (OSError, ValueError) as exc:
            raise ValueError("preserved voice attachment is missing or unsafe") from exc
        if candidate.is_symlink() or not resolved.is_file():
            raise ValueError("preserved voice attachment is missing or unsafe")
        try:
            actual_size = resolved.stat().st_size
            actual_hash = _hash_file(resolved)
        except OSError as exc:
            raise ValueError("preserved voice attachment integrity check failed") from exc
        if actual_size != record.attachment_bytes or actual_hash != record.attachment_sha256:
            raise ValueError("preserved voice attachment integrity check failed")

    @contextmanager
    def _lock(self, record_id: str) -> Iterator[None]:
        path = self.locks / f"{_record_id(record_id)}.lock"
        if path.is_symlink():
            raise ValueError("voice lock must not be a symlink")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _render_markdown(record: VoiceInboxRecord) -> str:
    warnings = (
        "\n".join(f"- {_markdown_literal(value)}" for value in record.quality_warnings)
        if record.quality_warnings
        else "_No transcript quality warning._"
    )
    return (
        "---\n"
        "omd_type: voice_inbox\n"
        f"voice_id: {json.dumps(record.record_id)}\n"
        f"review_status: {json.dumps(record.review_state)}\n"
        f"transcription_status: {json.dumps(record.transcription_state)}\n"
        f"captured_at: {json.dumps(record.created_at)}\n"
        "---\n\n"
        f"# {_markdown_literal(record.title)}\n\n"
        "## Source audio\n\n"
        f"![[{record.attachment_path}]]\n\n"
        "## Raw transcript\n\n"
        f"{record.raw_transcript or '_Not transcribed yet._'}\n\n"
        "## My Notes\n\n"
        f"{record.my_notes or '_No personal note yet._'}\n\n"
        "## AI suggestion (review required)\n\n"
        f"{record.ai_suggestion or '_No AI suggestion._'}\n\n"
        "## Transcript checks\n\n"
        f"{warnings}\n"
    )


def _reviewed_knowledge_note(record: VoiceInboxRecord) -> KnowledgeNote:
    return KnowledgeNote(
        source={
            "kind": "voice_memo",
            "title": record.title,
            "raw_text": record.raw_transcript,
            "audio_attachment": record.attachment_path,
            "captured_at": record.created_at,
            "transcript_backend": record.transcript_backend,
            "transcript_model": record.transcript_model,
            "transcript_language": record.transcript_language,
        },
        highlights=(),
        my_notes=(record.my_notes,) if record.my_notes.strip() else (),
        ai_suggestions=(record.ai_suggestion,) if record.ai_suggestion.strip() else (),
        derived_from=record.record_id,
    )


def _render_reviewed_markdown(note: KnowledgeNote) -> str:
    source = note.source
    sections = [
        "---",
        "omd_type: knowledge_note",
        f"note_id: {json.dumps(note.note_id)}",
        f"derived_from: {json.dumps(note.derived_from)}",
        'source_kind: "voice_memo"',
        "---",
        "",
        f"# {_markdown_literal(str(source['title']))}",
        "",
        "## Source audio",
        "",
        f"![[{source['audio_attachment']}]]",
        "",
        "## Raw transcript",
        "",
        str(source["raw_text"]) or "_No transcript was accepted._",
    ]
    if note.my_notes:
        sections.extend(["", "## My Notes", "", *note.my_notes])
    if note.ai_suggestions:
        sections.extend(
            ["", "## AI suggestion (review required)", "", *note.ai_suggestions]
        )
    backend = str(source.get("transcript_backend") or "")
    model = str(source.get("transcript_model") or "")
    language = str(source.get("transcript_language") or "")
    if backend or model or language:
        sections.extend(
            [
                "",
                "## Transcript provenance",
                "",
                f"- Backend: {_markdown_literal(backend or 'not recorded')}",
                f"- Model: {_markdown_literal(model or 'not recorded')}",
                f"- Language hint: {_markdown_literal(language or 'automatic')}",
            ]
        )
    return "\n".join(sections).rstrip() + "\n"


def _markdown_literal(value: str) -> str:
    return _MARKDOWN_META_RE.sub(r"\\\1", html.escape(value, quote=False))


def _audio_source(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise ValueError("audio source must be an existing non-symlink file")
    if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValueError("voice attachment must be a supported audio file")
    return path.resolve(strict=True)


def _copy_audio_durable(source: Path, destination: Path) -> tuple[Path, str, int]:
    fd, temp_name = tempfile.mkstemp(prefix=".voice-", suffix=".tmp", dir=destination)
    temp = Path(temp_name)
    source_fd = -1
    digest = hashlib.sha256()
    size = 0
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError("audio source must be a regular file")
        os.fchmod(fd, 0o600)
        with os.fdopen(source_fd, "rb") as reader, os.fdopen(fd, "wb") as writer:
            source_fd = -1
            fd = -1
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        checksum = digest.hexdigest()
        final = destination / f"{checksum}{source.suffix.lower()}"
        if final.is_symlink():
            raise ValueError("voice attachment destination must not be a symlink")
        if final.exists() and final.is_file() and _hash_file(final) == checksum:
            temp.unlink()
        else:
            os.replace(temp, final)
            _sync_directory(destination)
        return final, checksum, size
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _safe_directory(root: Path, name: str) -> Path:
    child = root / name
    if child.is_symlink():
        raise ValueError(f"voice Inbox directory must not be a symlink: {name}")
    child.mkdir(mode=0o700, exist_ok=True)
    resolved = child.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("voice Inbox directory resolves outside vault") from exc
    return resolved


def _write_text_durable(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _sync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _rollback_snapshot(path: Path) -> str | None:
    if path.is_symlink():
        raise ValueError(f"voice metadata must not be a symlink: {path.name}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"voice metadata must be a regular file: {path.name}")
    return path.read_text(encoding="utf-8")


def _restore_snapshot(path: Path, content: str | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        _sync_directory(path.parent)
        return
    _write_text_durable(path, content)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    finally:
        if fd >= 0:
            os.close(fd)
    return digest.hexdigest()


def _low_confidence(confidence: object, segments: object) -> bool:
    try:
        if confidence is not None and float(confidence) < 0.5:
            return True
    except (TypeError, ValueError):
        pass
    if not isinstance(segments, list):
        return False
    low = 0
    measured = 0
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        value = segment.get("avg_logprob")
        if value is None:
            continue
        try:
            measured += 1
            low += float(value) <= -1.0
        except (TypeError, ValueError):
            continue
    return measured >= 2 and low / measured >= 0.5


def _relative_attachment_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("attachment_path must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("attachment_path must be a relative path")
    expected = ("Inbox", "_attachments", "voice")
    if path.parts[:3] != expected:
        raise ValueError("attachment_path must remain in the voice attachment folder")
    return path


def _record_id(value: object) -> str:
    if not isinstance(value, str) or not _RECORD_ID_RE.fullmatch(value):
        raise ValueError("invalid voice record id")
    return value


def _required_text(value: object, *, name: str) -> str:
    normalized = _text(value, name=name).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value


def _error_code(value: object) -> str:
    if not isinstance(value, str) or not _ERROR_CODE_RE.fullmatch(value):
        raise ValueError("invalid voice error code")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("voice timestamp must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("voice timestamp must be ISO-8601 text") from exc
    if parsed.tzinfo is None:
        raise ValueError("voice timestamp must include a timezone")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
