"""Authenticated folder transport for immutable, review-before-import Inbox jobs."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from ._json_contract import canonical_json, json_object
from .inbox import InboxJob


SYNC_ENVELOPE_SCHEMA = 1
_STATUSES = frozenset({"pending", "processing", "completed", "failed"})
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
_DROP = object()
_MIN_AUTH_KEY_BYTES = 32
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"pending", "processing", "completed", "failed"}),
    "processing": frozenset({"processing", "completed", "failed"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
}


def write_pending_job(
    root: str | Path, job: InboxJob, *, auth_key: bytes
) -> Path:
    key = _auth_key(auth_key)
    transport_root = _transport_root(root)
    pending = _safe_directory(transport_root, "pending")
    sanitized = _sanitized_job(job)
    job_data = sanitized.to_dict()
    checksum = _signature(job_data, key)
    envelope = {"schema": SYNC_ENVELOPE_SCHEMA, "checksum": checksum, "job": job_data}
    destination = pending / f"{sanitized.job_id}.json"
    serialized = _serialized(envelope)
    if not _write_new_file(pending, destination.name, serialized):
        existing = read_job(destination, transport_root, auth_key=key)
        if existing != sanitized:
            raise ValueError(f"conflicting delivery for {sanitized.job_id}")
    return destination


def read_job(path: str | Path, root: str | Path, *, auth_key: bytes) -> InboxJob:
    key = _auth_key(auth_key)
    transport_root = _transport_root(root)
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("sync job path must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(transport_root)
    except (OSError, ValueError) as exc:
        raise ValueError("sync job path must be an existing file under root") from exc
    try:
        envelope = json_object(json.loads(_read_text(resolved)), name="sync_envelope")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("sync job envelope is invalid JSON") from exc
    if envelope.get("schema") != SYNC_ENVELOPE_SCHEMA:
        raise ValueError("unsupported sync envelope schema")
    job_data = json_object(envelope.get("job"), name="sync_envelope.job")
    expected = _signature(job_data, key)
    checksum = envelope.get("checksum")
    if not isinstance(checksum, str) or not hmac.compare_digest(checksum, expected):
        raise ValueError("sync envelope checksum authentication failed")
    job = InboxJob.from_dict(job_data)
    if resolved.name != f"{job.job_id}.json":
        raise ValueError("sync job filename does not match signed job_id")
    return job


def list_pending_jobs(root: str | Path) -> list[Path]:
    transport_root = _transport_root(root)
    pending = _safe_directory(transport_root, "pending")
    paths = []
    for path in pending.glob("*.json"):
        if path.is_symlink():
            raise ValueError("pending sync jobs must not be symlinks")
        paths.append(path)
    return sorted(paths, key=lambda path: path.as_posix())


def mark_job_status(
    root: str | Path,
    job_id: str,
    status: str,
    detail: object | None = None,
    *,
    auth_key: bytes,
) -> Path:
    key = _auth_key(auth_key)
    if status not in _STATUSES:
        raise ValueError("status must be pending, processing, completed, or failed")
    if not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{16}", job_id):
        raise ValueError("job_id is invalid")
    transport_root = _transport_root(root)
    status_dir = _safe_directory(transport_root, "status")
    trusted_root = trusted_state_path(transport_root)
    trusted_status_dir = _safe_directory(trusted_root, "status")
    destination = status_dir / f"{job_id}.json"
    trusted_destination = trusted_status_dir / destination.name
    lock_path = trusted_status_dir / f".{job_id}.lock"
    lock_fd = _open_lock(lock_path)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        previous = (
            _read_status(trusted_destination, key, expected_job_id=job_id)
            if trusted_destination.exists()
            else None
        )
        current = str(previous["status"]) if previous is not None else None
        if current is not None and status not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"invalid status transition from {current} to {status}")
        payload: dict[str, Any] = {
            "job_id": job_id,
            "status": status,
            "version": int(previous["version"]) + 1 if previous else 1,
            "prev_checksum": previous["checksum"] if previous else None,
        }
        if detail is not None:
            sanitized_detail = _sanitize_status_detail(detail)
            if sanitized_detail is not _DROP:
                payload["detail"] = sanitized_detail
        payload["checksum"] = _signature(payload, key)
        serialized = _serialized(payload)
        _replace_file(status_dir, destination.name, serialized)
        _replace_file(trusted_status_dir, trusted_destination.name, serialized)
    finally:
        os.close(lock_fd)
    return destination


def _sanitized_job(job: InboxJob) -> InboxJob:
    sanitized_payload = _sanitize(job.payload)
    if not isinstance(sanitized_payload, dict):
        raise ValueError("sanitized sync payload must remain an object")
    return InboxJob(
        job_type=job.job_type,
        payload=sanitized_payload,
        source=job.source,
        created_at=job.created_at,
    )


def _sanitize(value: object, *, key: str | None = None) -> object:
    if key is not None and _sensitive_key(key):
        return _DROP
    if isinstance(value, dict):
        result = {}
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                continue
            sanitized = _sanitize(child_value, key=child_key)
            if sanitized is not _DROP:
                result[child_key] = sanitized
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            sanitized = _sanitize(child)
            if sanitized is not _DROP:
                result.append(sanitized)
        return result
    if isinstance(value, str) and _absolute_local_path(value):
        return _DROP
    return value


def _sensitive_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    return key.casefold() in _SENSITIVE_KEYS or any(
        marker in compact
        for marker in (
            "apikey",
            "authorization",
            "connectionstring",
            "cookie",
            "credential",
            "password",
            "secret",
            "token",
        )
    )


def _sanitize_status_detail(value: object) -> object:
    if isinstance(value, dict):
        allowed = {}
        for key in ("message", "reason", "code"):
            if key in value:
                sanitized = _sanitize(value[key], key=key)
                if sanitized is not _DROP:
                    allowed[key] = sanitized
        return allowed or _DROP
    return _sanitize(value)


def _absolute_local_path(value: str) -> bool:
    stripped = value.strip()
    return (
        Path(stripped).is_absolute()
        or stripped.startswith(("~/", "~\\", "file://", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))
    )


def _signature(data: dict[str, Any], auth_key: bytes) -> str:
    return hmac.new(
        auth_key, canonical_json(data).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _auth_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < _MIN_AUTH_KEY_BYTES:
        raise ValueError("auth_key must contain at least 32 bytes")
    return value


def _serialized(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _read_text(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("sync file cannot be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("sync file must be a regular file")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _write_new_file(directory: Path, name: str, content: str) -> bool:
    temp_name = f".{name}.{secrets.token_hex(8)}.tmp"
    temp_path = directory / temp_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, directory / name, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                return False
            raise
        return True
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _replace_file(directory: Path, name: str, content: str) -> None:
    temp_name = f".{name}.{secrets.token_hex(8)}.tmp"
    temp_path = directory / temp_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, directory / name)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _open_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError("sync status lock cannot be opened safely") from exc


def _read_status(
    path: Path, auth_key: bytes, *, expected_job_id: str
) -> dict[str, Any]:
    try:
        payload = json_object(json.loads(_read_text(path)), name="sync_status")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("sync status is invalid JSON") from exc
    checksum = payload.get("checksum")
    signed_payload = {key: value for key, value in payload.items() if key != "checksum"}
    expected = _signature(signed_payload, auth_key)
    if not isinstance(checksum, str) or not hmac.compare_digest(checksum, expected):
        raise ValueError("sync status authentication failed")
    status_value = payload.get("status")
    if status_value not in _STATUSES:
        raise ValueError("sync status is invalid")
    if payload.get("job_id") != expected_job_id or path.name != f"{expected_job_id}.json":
        raise ValueError("sync status job_id does not match destination")
    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("sync status version is invalid")
    return payload


def _transport_root(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise ValueError("sync root must be an existing non-symlink directory")
    return path.resolve(strict=True)


def trusted_state_path(root: str | Path) -> Path:
    """Return the per-machine authoritative state path for one sync root."""
    transport_root = _transport_root(root)
    base = Path.home() / ".local" / "share" / "omd" / "trusted-sync-state"
    current = Path.home()
    for part in (".local", "share", "omd", "trusted-sync-state"):
        current = current / part
        if current.is_symlink():
            raise ValueError("local trusted state path must not contain symlinks")
        current.mkdir(mode=0o700, exist_ok=True)
    sync_id = hashlib.sha256(
        str(transport_root).encode("utf-8")
    ).hexdigest()[:24]
    trusted = base / sync_id
    if trusted.is_symlink():
        raise ValueError("local trusted state path must not be a symlink")
    trusted.mkdir(mode=0o700, exist_ok=True)
    return trusted.resolve(strict=True)


def _safe_directory(root: Path, name: str) -> Path:
    child = root / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if child.is_symlink():
        raise ValueError(f"sync {name} directory must not be a symlink")
    resolved = child.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"sync {name} directory resolves outside root") from exc
    return resolved
