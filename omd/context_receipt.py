"""Durable local Context Receipts and resumable Inbox job state."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ._json_contract import canonical_json, json_object
from .inbox import InboxJob


CONTEXT_RECEIPT_SCHEMA_VERSION = 1
OUTBOX_ENVELOPE_SCHEMA_VERSION = 1

_SOURCE_STATES = frozenset({"referenced", "secured", "missing"})
_RECEIPT_STATES = frozenset(
    {
        "queued",
        "source_secured",
        "processing",
        "needs_action",
        "partial_output",
        "complete",
        "failed",
        "cancelled",
    }
)
_PRIVACY_MODES = frozenset({"local_only", "cloud_for_this_task"})
_TERMINAL_STATES = frozenset({"complete", "failed", "cancelled"})
_RECOVERABLE_STATES = frozenset({"queued", "source_secured", "processing"})
_LOCAL_SOURCE_TYPES = frozenset(
    {"audio", "audio_attachment", "image", "local_file", "office_doc", "pdf"}
)
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "argv",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "endpoint",
        "hostname",
        "password",
        "refresh_token",
        "secret",
        "session_cookie",
        "token",
    }
)
_JOB_ID = re.compile(r"job_[0-9a-f]{16}")
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bauthorization\s*:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:^|[\r\n])\s*(?:cookie|set-cookie)\s*:\s*\S+", re.IGNORECASE),
    re.compile(
        r"(?:^|\s)--(?:cookies?|api[-_]?key|token|password|secret)(?:\s|=)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"#\s*Netscape\s+HTTP\s+Cookie\s+File", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, init=False)
class ContextReceipt:
    """User-visible proof of accepted input and its durable processing state."""

    schema_version: int
    job_id: str
    source_type: str
    source_state: str
    destination: str
    privacy_mode: str
    accepted_at: str
    updated_at: str
    state: str
    current_stage: str | None
    content_hash: str | None
    secured_source: str | None
    recovery_action: str | None
    error_code: str | None
    started_at: str | None
    finished_at: str | None
    cancelled_at: str | None
    _stage_attempts_json: str = field(repr=False)

    def __init__(
        self,
        *,
        job_id: str,
        source_type: str,
        source_state: str,
        destination: str,
        privacy_mode: str,
        accepted_at: str,
        updated_at: str,
        state: str,
        stage_attempts: Mapping[str, int] | None = None,
        current_stage: str | None = None,
        content_hash: str | None = None,
        secured_source: str | None = None,
        recovery_action: str | None = None,
        error_code: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        cancelled_at: str | None = None,
        schema_version: int = CONTEXT_RECEIPT_SCHEMA_VERSION,
    ) -> None:
        if schema_version != CONTEXT_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f"unsupported ContextReceipt schema_version: {schema_version}")
        normalized_job_id = _job_id(job_id)
        normalized_source_state = _choice(
            source_state, name="source_state", allowed=_SOURCE_STATES
        )
        normalized_state = _choice(state, name="state", allowed=_RECEIPT_STATES)
        normalized_privacy = _choice(
            privacy_mode, name="privacy_mode", allowed=_PRIVACY_MODES
        )
        normalized_hash = _optional_hash(content_hash)
        normalized_secured_source = _optional_relative_path(secured_source)
        if normalized_source_state in {"secured", "missing"}:
            if normalized_hash is None or normalized_secured_source is None:
                raise ValueError(
                    "secured or missing source requires content_hash and secured_source"
                )
        elif normalized_hash is not None or normalized_secured_source is not None:
            raise ValueError("referenced source must not claim secured source data")
        if normalized_state == "source_secured" and normalized_source_state != "secured":
            raise ValueError("source_secured state requires secured source bytes")
        if normalized_source_state == "missing" and normalized_state != "needs_action":
            raise ValueError("missing source requires needs_action state")

        attempts = _stage_attempts(stage_attempts or {})
        normalized_current_stage = _optional_string(current_stage, name="current_stage")
        if normalized_current_stage is not None and normalized_current_stage not in attempts:
            raise ValueError("current_stage must have an attempt count")

        values = {
            "schema_version": schema_version,
            "job_id": normalized_job_id,
            "source_type": _required_string(source_type, name="source_type"),
            "source_state": normalized_source_state,
            "destination": _required_string(destination, name="destination"),
            "privacy_mode": normalized_privacy,
            "accepted_at": _timestamp(accepted_at, name="accepted_at"),
            "updated_at": _timestamp(updated_at, name="updated_at"),
            "state": normalized_state,
            "current_stage": normalized_current_stage,
            "content_hash": normalized_hash,
            "secured_source": normalized_secured_source,
            "recovery_action": _optional_string(recovery_action, name="recovery_action"),
            "error_code": _optional_string(error_code, name="error_code"),
            "started_at": _optional_timestamp(started_at, name="started_at"),
            "finished_at": _optional_timestamp(finished_at, name="finished_at"),
            "cancelled_at": _optional_timestamp(cancelled_at, name="cancelled_at"),
            "_stage_attempts_json": canonical_json(attempts),
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def stage_attempts(self) -> dict[str, int]:
        return json.loads(self._stage_attempts_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "source_type": self.source_type,
            "source_state": self.source_state,
            "destination": self.destination,
            "privacy_mode": self.privacy_mode,
            "accepted_at": self.accepted_at,
            "updated_at": self.updated_at,
            "state": self.state,
            "current_stage": self.current_stage,
            "stage_attempts": self.stage_attempts,
            "content_hash": self.content_hash,
            "secured_source": self.secured_source,
            "recovery_action": self.recovery_action,
            "error_code": self.error_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancelled_at": self.cancelled_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ContextReceipt:
        value = json_object(data, name="context_receipt")
        return cls(
            job_id=value.get("job_id"),
            source_type=value.get("source_type"),
            source_state=value.get("source_state"),
            destination=value.get("destination"),
            privacy_mode=value.get("privacy_mode"),
            accepted_at=value.get("accepted_at"),
            updated_at=value.get("updated_at"),
            state=value.get("state"),
            current_stage=value.get("current_stage"),
            stage_attempts=value.get("stage_attempts"),
            content_hash=value.get("content_hash"),
            secured_source=value.get("secured_source"),
            recovery_action=value.get("recovery_action"),
            error_code=value.get("error_code"),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            cancelled_at=value.get("cancelled_at"),
            schema_version=value.get("schema_version", CONTEXT_RECEIPT_SCHEMA_VERSION),
        )


class ContextOutbox:
    """Atomic file outbox for one local OMD installation."""

    def __init__(self, root: str | Path) -> None:
        self.root = _existing_root(root)
        self._entries = _safe_directory(self.root, "jobs")
        self._sources = _safe_directory(self.root, "sources")
        self._locks = _safe_directory(self.root, "locks")

    def queue(
        self,
        job: InboxJob,
        *,
        source_type: str,
        destination: str,
        privacy_mode: str,
    ) -> ContextReceipt:
        _validate_persisted_job(job)
        now = _now()
        candidate = ContextReceipt(
            job_id=job.job_id,
            source_type=source_type,
            source_state="referenced",
            destination=destination,
            privacy_mode=privacy_mode,
            accepted_at=now,
            updated_at=now,
            state="queued",
        )
        with self._job_lock(job.job_id):
            path = self._entry_path(job.job_id)
            if path.exists():
                existing_job, existing_receipt = self._read_entry(path)
                if existing_job != job:
                    raise ValueError(f"conflicting outbox job for {job.job_id}")
                requested_metadata = (
                    candidate.source_type,
                    candidate.destination,
                    candidate.privacy_mode,
                )
                existing_metadata = (
                    existing_receipt.source_type,
                    existing_receipt.destination,
                    existing_receipt.privacy_mode,
                )
                if existing_metadata != requested_metadata:
                    raise ValueError(
                        f"conflicting receipt metadata for {job.job_id}"
                    )
                reconciled = self._reconcile_secured_source(existing_receipt)
                if reconciled != existing_receipt:
                    self._write_entry(existing_job, reconciled)
                return reconciled
            self._write_entry(job, candidate)
        return candidate

    def load(self, job_id: str) -> ContextReceipt:
        with self._job_lock(job_id):
            job, receipt = self._read_entry(
                self._entry_path(job_id), require_exists=True
            )
            reconciled = self._reconcile_secured_source(receipt)
            if reconciled != receipt:
                self._write_entry(job, reconciled)
            return reconciled

    def load_job(self, job_id: str) -> InboxJob:
        job, _ = self._read_entry(self._entry_path(job_id), require_exists=True)
        return job

    def list_receipts(self) -> list[ContextReceipt]:
        receipts = []
        for path in sorted(self._entries.glob("job_*.json")):
            if path.is_symlink():
                raise ValueError("outbox entries must not be symlinks")
            receipts.append(self.load(path.stem))
        return sorted(receipts, key=lambda value: (value.accepted_at, value.job_id))

    def secure_local_source(
        self, job_id: str, source: str | Path
    ) -> ContextReceipt:
        source_path = _regular_source(source)
        with self._job_lock(job_id):
            job, receipt = self._read_entry(
                self._entry_path(job_id), require_exists=True
            )
            reconciled = self._reconcile_secured_source(receipt)
            if reconciled != receipt:
                self._write_entry(job, reconciled)
                receipt = reconciled
            if receipt.source_state == "secured":
                return receipt
            expected_hash = (
                receipt.content_hash if receipt.source_state == "missing" else None
            )
            destination = _safe_directory(self._sources, job_id)
            staged, content_hash = _copy_source_atomic(
                source_path,
                destination,
                expected_hash=expected_hash,
            )
            relative = staged.relative_to(self.root).as_posix()
            updated = _replace_receipt(
                receipt,
                source_state="secured",
                state="source_secured",
                content_hash=content_hash,
                secured_source=relative,
                recovery_action=None,
                error_code=None,
                updated_at=_now(),
            )
            self._write_entry(job, updated)
            return updated

    def _reconcile_secured_source(self, receipt: ContextReceipt) -> ContextReceipt:
        if receipt.source_state != "secured":
            return receipt
        problem = _secured_source_problem(self.root, receipt)
        if problem is None:
            return receipt
        return _replace_receipt(
            receipt,
            source_state="missing",
            state="needs_action",
            recovery_action="restore_source",
            error_code=problem,
            finished_at=None,
            updated_at=_now(),
        )

    def start_stage(self, job_id: str, stage: str) -> ContextReceipt:
        normalized_stage = _required_string(stage, name="stage")

        def update(receipt: ContextReceipt) -> ContextReceipt:
            if receipt.state not in {"queued", "source_secured", "partial_output"}:
                raise ValueError(f"cannot start a stage from {receipt.state}")
            if (
                receipt.source_type in _LOCAL_SOURCE_TYPES
                and receipt.source_state != "secured"
            ):
                raise ValueError("local source must be secured before processing")
            attempts = receipt.stage_attempts
            attempts[normalized_stage] = attempts.get(normalized_stage, 0) + 1
            return _replace_receipt(
                receipt,
                state="processing",
                current_stage=normalized_stage,
                stage_attempts=attempts,
                started_at=receipt.started_at or _now(),
                finished_at=None,
                recovery_action=None,
                error_code=None,
                updated_at=_now(),
            )

        return self._update(job_id, update)

    def fail_stage(
        self, job_id: str, *, error_code: str, retryable: bool
    ) -> ContextReceipt:
        normalized_error = _required_string(error_code, name="error_code")

        def update(receipt: ContextReceipt) -> ContextReceipt:
            if receipt.state != "processing":
                raise ValueError(f"cannot fail a stage from {receipt.state}")
            return _replace_receipt(
                receipt,
                state="needs_action" if retryable else "failed",
                error_code=normalized_error,
                recovery_action="retry" if retryable else "keep_raw",
                finished_at=None if retryable else _now(),
                updated_at=_now(),
            )

        return self._update(job_id, update)

    def retry_stage(self, job_id: str) -> ContextReceipt:
        def update(receipt: ContextReceipt) -> ContextReceipt:
            if (
                receipt.state != "needs_action"
                or receipt.recovery_action != "retry"
                or receipt.current_stage is None
            ):
                raise ValueError("job does not have a retryable stage")
            attempts = receipt.stage_attempts
            attempts[receipt.current_stage] += 1
            return _replace_receipt(
                receipt,
                state="processing",
                stage_attempts=attempts,
                error_code=None,
                recovery_action=None,
                updated_at=_now(),
            )

        return self._update(job_id, update)

    def mark_partial_output(self, job_id: str) -> ContextReceipt:
        return self._set_state(job_id, "partial_output")

    def complete(self, job_id: str) -> ContextReceipt:
        return self._set_state(job_id, "complete", finished=True)

    def cancel(self, job_id: str) -> ContextReceipt:
        def update(receipt: ContextReceipt) -> ContextReceipt:
            if receipt.state == "cancelled":
                return receipt
            if receipt.state in {"complete", "failed"}:
                raise ValueError(f"cannot cancel a terminal {receipt.state} job")
            now = _now()
            return _replace_receipt(
                receipt,
                state="cancelled",
                cancelled_at=now,
                finished_at=now,
                recovery_action=None,
                updated_at=now,
            )

        return self._update(job_id, update)

    def recover_incomplete(self) -> list[ContextReceipt]:
        recovered = []
        for receipt in self.list_receipts():
            if receipt.state not in _RECOVERABLE_STATES:
                continue
            if receipt.state == "processing":
                target = "source_secured" if receipt.source_state == "secured" else "queued"

                def update(value: ContextReceipt, target_state: str = target) -> ContextReceipt:
                    if value.state != "processing":
                        return value
                    return _replace_receipt(
                        value,
                        state=target_state,
                        recovery_action="resume",
                        updated_at=_now(),
                    )

                receipt = self._update(receipt.job_id, update)
            recovered.append(receipt)
        return recovered

    def _set_state(
        self, job_id: str, state: str, *, finished: bool = False
    ) -> ContextReceipt:
        def update(receipt: ContextReceipt) -> ContextReceipt:
            if receipt.state not in {"processing", "partial_output"}:
                raise ValueError(f"cannot mark {state} from {receipt.state}")
            now = _now()
            return _replace_receipt(
                receipt,
                state=state,
                recovery_action=None,
                error_code=None,
                finished_at=now if finished else receipt.finished_at,
                updated_at=now,
            )

        return self._update(job_id, update)

    def _update(self, job_id: str, update) -> ContextReceipt:
        with self._job_lock(job_id):
            job, receipt = self._read_entry(
                self._entry_path(job_id), require_exists=True
            )
            reconciled = self._reconcile_secured_source(receipt)
            if reconciled != receipt:
                self._write_entry(job, reconciled)
                receipt = reconciled
            updated = update(receipt)
            if updated != receipt:
                self._write_entry(job, updated)
            return updated

    def _write_entry(self, job: InboxJob, receipt: ContextReceipt) -> None:
        signed = {
            "schema_version": OUTBOX_ENVELOPE_SCHEMA_VERSION,
            "job": job.to_dict(),
            "receipt": receipt.to_dict(),
        }
        envelope = dict(signed)
        envelope["checksum"] = hashlib.sha256(
            canonical_json(signed).encode("utf-8")
        ).hexdigest()
        _write_text_durable(
            self._entry_path(job.job_id),
            json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )

    def _read_entry(
        self, path: Path, *, require_exists: bool = False
    ) -> tuple[InboxJob, ContextReceipt]:
        if require_exists and not path.exists():
            raise ValueError(f"outbox job does not exist: {path.stem}")
        if path.is_symlink():
            raise ValueError("outbox entry must not be a symlink")
        try:
            envelope = json_object(json.loads(_read_text(path)), name="outbox_envelope")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid outbox entry: {path.name}") from exc
        if envelope.get("schema_version") != OUTBOX_ENVELOPE_SCHEMA_VERSION:
            raise ValueError("unsupported outbox envelope schema")
        signed = {
            "schema_version": envelope.get("schema_version"),
            "job": envelope.get("job"),
            "receipt": envelope.get("receipt"),
        }
        expected = hashlib.sha256(canonical_json(signed).encode("utf-8")).hexdigest()
        if envelope.get("checksum") != expected:
            raise ValueError("outbox entry checksum failed")
        job = InboxJob.from_dict(json_object(signed["job"], name="outbox_job"))
        receipt = ContextReceipt.from_dict(
            json_object(signed["receipt"], name="outbox_receipt")
        )
        if job.job_id != receipt.job_id or path.name != f"{job.job_id}.json":
            raise ValueError("outbox entry identity does not match its filename")
        return job, receipt

    def _entry_path(self, job_id: str) -> Path:
        return self._entries / f"{_job_id(job_id)}.json"

    @contextmanager
    def _job_lock(self, job_id: str) -> Iterator[None]:
        path = self._locks / f"{_job_id(job_id)}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ValueError("outbox lock cannot be opened safely") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _copy_source_atomic(
    source: Path,
    destination: Path,
    *,
    expected_hash: str | None = None,
) -> tuple[Path, str]:
    suffix = source.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    fd, temp_name = tempfile.mkstemp(prefix=".source-", suffix=".tmp", dir=destination)
    temp = Path(temp_name)
    digest = hashlib.sha256()
    source_fd = -1
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError("source must be a regular file")
        os.fchmod(fd, 0o600)
        with os.fdopen(source_fd, "rb") as reader, os.fdopen(fd, "wb") as writer:
            source_fd = -1
            fd = -1
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        content_hash = digest.hexdigest()
        if expected_hash is not None and content_hash != expected_hash:
            raise ValueError("source does not match the secured source identity")
        final = destination / f"{content_hash}{suffix}"
        if final.is_symlink():
            raise ValueError("secured source destination must not be a symlink")
        if final.exists() and _hash_regular_file(final) == content_hash:
            temp.unlink()
        else:
            os.replace(temp, final)
            _sync_directory(destination)
        return final, content_hash
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _replace_receipt(
    receipt: ContextReceipt, **changes: object
) -> ContextReceipt:
    payload = receipt.to_dict()
    payload.update(changes)
    return ContextReceipt.from_dict(payload)


def _write_text_durable(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("outbox entry must not be a symlink")
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


def _read_text(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("outbox entry must be a regular file")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _existing_root(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise ValueError("outbox root must be an existing non-symlink directory")
    return path.resolve(strict=True)


def _safe_directory(root: Path, name: str) -> Path:
    child = root / name
    if child.is_symlink():
        raise ValueError(f"outbox {name} directory must not be a symlink")
    created = False
    try:
        child.mkdir(mode=0o700, parents=False)
        created = True
    except FileExistsError:
        pass
    resolved = child.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"outbox {name} entry must be a directory")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"outbox {name} directory resolves outside root") from exc
    if created:
        _sync_directory(root)
    return resolved


def _regular_source(source: str | Path) -> Path:
    path = Path(source).expanduser()
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise ValueError("source must be an existing non-symlink file")
    return path.resolve(strict=True)


def _secured_source_problem(root: Path, receipt: ContextReceipt) -> str | None:
    if receipt.secured_source is None or receipt.content_hash is None:
        return "secured_source_missing"
    candidate = root / receipt.secured_source
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return "secured_source_missing"
    if _path_has_symlink(root, candidate) or not resolved.is_file():
        return "secured_source_missing"
    try:
        digest = _hash_regular_file(resolved)
    except (OSError, ValueError):
        return "secured_source_missing"
    if digest != receipt.content_hash:
        return "secured_source_integrity_failed"
    return None


def _hash_regular_file(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("secured source must be a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _path_has_symlink(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return True
    return False


def _job_id(value: object) -> str:
    if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
        raise ValueError("job_id is invalid")
    return value


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name=name)


def _choice(value: object, *, name: str, allowed: frozenset[str]) -> str:
    normalized = _required_string(value, name=name)
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _timestamp(value: object, *, name: str) -> str:
    timestamp = _required_string(value, name=name)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return timestamp


def _optional_timestamp(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return _timestamp(value, name=name)


def _optional_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("content_hash must be a SHA-256 digest")
    return value


def _optional_relative_path(value: object) -> str | None:
    if value is None:
        return None
    normalized = _required_string(value, name="secured_source")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("secured_source must be a relative path")
    return path.as_posix()


def _stage_attempts(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("stage_attempts must be an object")
    attempts: dict[str, int] = {}
    for key, count in value.items():
        stage = _required_string(key, name="stage_attempts key")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("stage attempt counts must be positive integers")
        attempts[stage] = count
    return attempts


def _validate_job_payload(value: object, *, key: str | None = None) -> None:
    if key is not None and _sensitive_key(key):
        raise ValueError(f"job payload contains sensitive field: {key}")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise ValueError("job payload must use string keys")
            _validate_job_payload(child, key=child_key)
        return
    if isinstance(value, list):
        for child in value:
            _validate_job_payload(child)
        return
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
            raise ValueError("job payload contains a sensitive value")
        if _absolute_local_path(value):
            raise ValueError("job payload must not contain an absolute path")


def _validate_persisted_job(job: InboxJob) -> None:
    _validate_job_label(job.job_type, name="job type")
    _validate_job_label(job.source, name="job source")
    _validate_job_payload(job.payload)


def _validate_job_label(value: str, *, name: str) -> None:
    if _absolute_local_path(value):
        raise ValueError(f"{name} must not contain an absolute path")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError(f"{name} must be a short application label")


def _sensitive_key(value: str) -> bool:
    normalized = value.casefold()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return normalized in _SENSITIVE_KEYS or compact in {
        "accesstoken",
        "apikey",
        "authorization",
        "connectionstring",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "refreshtoken",
        "secret",
        "sessioncookie",
        "token",
    }


def _absolute_local_path(value: str) -> bool:
    stripped = value.strip()
    return (
        Path(stripped).is_absolute()
        or stripped.startswith(("~/", "~\\", "file://", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
