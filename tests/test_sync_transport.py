from __future__ import annotations

import json
from pathlib import Path

import pytest


_AUTH_KEY = b"test-only-paired-device-key-with-32-bytes"


@pytest.fixture(autouse=True)
def _isolated_local_state_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _sample_job(*, payload: dict[str, object] | None = None):
    from omd.inbox import InboxJob

    return InboxJob(
        job_type="knowledge_note",
        source="mobile",
        created_at="2026-07-19T00:00:00Z",
        payload=payload
        or {
            "note_id": "kn_sync-foundation",
            "source": {
                "kind": "webpage",
                "title": "Sync foundation",
                "url": "https://example.com/sync-foundation",
            },
            "highlights": ["Atomic envelopes matter."],
            "my_notes": ["Keep transport content-addressed."],
        },
    )


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_documents_under(root: Path) -> list[dict[str, object]]:
    docs: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.json")):
        docs.append(_load_json(path))
    return docs


def test_write_pending_job_writes_atomic_json_envelope_with_schema_checksum_and_job(tmp_path):
    from omd.sync_transport import write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    pending_path = write_pending_job(root, job, auth_key=_AUTH_KEY)

    assert pending_path.exists()
    assert pending_path.suffix == ".json"
    assert not pending_path.name.endswith(".tmp")
    assert set(_load_json(pending_path)) == {"schema", "checksum", "job"}


def test_read_job_returns_original_job_from_written_pending_envelope(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    pending_path = write_pending_job(root, job, auth_key=_AUTH_KEY)

    assert read_job(pending_path, root, auth_key=_AUTH_KEY) == job


def test_list_pending_jobs_returns_sorted_paths_and_ignores_partial_tmp_files(tmp_path):
    from omd.sync_transport import list_pending_jobs, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()

    second = write_pending_job(
        root,
        _sample_job(
            payload={
                "note_id": "kn_second",
                "source": {"kind": "webpage", "url": "https://example.com/second"},
                "my_notes": ["second"],
            }
        ),
        auth_key=_AUTH_KEY,
    )
    first = write_pending_job(
        root,
        _sample_job(
            payload={
                "note_id": "kn_first",
                "source": {"kind": "webpage", "url": "https://example.com/first"},
                "my_notes": ["first"],
            }
        ),
        auth_key=_AUTH_KEY,
    )
    (root / "pending-partial.json.tmp").write_text('{"incomplete": true', encoding="utf-8")

    assert list_pending_jobs(root) == sorted([first, second], key=lambda path: path.as_posix())


def test_mark_job_status_persists_allowed_status(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    write_pending_job(root, job, auth_key=_AUTH_KEY)
    mark_job_status(
        root,
        job.job_id,
        "processing",
        detail="reviewing",
        auth_key=_AUTH_KEY,
    )

    assert any(
        document.get("job_id") == job.job_id and document.get("status") == "processing"
        for document in _json_documents_under(root)
    )


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "failed"])
def test_mark_job_status_accepts_each_allowed_status(status, tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job(payload={"note_id": f"kn_{status}", "status_test": status})

    write_pending_job(root, job, auth_key=_AUTH_KEY)
    mark_job_status(
        root,
        job.job_id,
        status,
        auth_key=_AUTH_KEY,
    )

    assert any(
        document.get("job_id") == job.job_id and document.get("status") == status
        for document in _json_documents_under(root)
    )


def test_mark_job_status_rejects_unknown_status(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    write_pending_job(root, job, auth_key=_AUTH_KEY)

    with pytest.raises(ValueError, match="status"):
        mark_job_status(
            root,
            job.job_id,
            "queued",
            auth_key=_AUTH_KEY,
        )


def test_write_pending_job_redacts_absolute_paths_and_sensitive_payload_keys(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    machine_path = str(tmp_path / "Library" / "Containers" / "secret.txt")
    job = _sample_job(
        payload={
            "note_id": "kn_redacted",
            "source": {
                "kind": "share_sheet",
                "path": machine_path,
                "hostname": "phone.local",
                "endpoint": "https://sync.example.com/private",
            },
            "cookies": "sid=secret",
            "token": "tok-secret",
            "argv": ["omd", "--secret"],
            "secret": "keep-out",
            "my_notes": ["retain note"],
        }
    )

    pending_path = write_pending_job(root, job, auth_key=_AUTH_KEY)
    envelope_text = pending_path.read_text(encoding="utf-8")
    restored = read_job(pending_path, root, auth_key=_AUTH_KEY)

    assert machine_path not in envelope_text
    assert "cookies" not in envelope_text
    assert "token" not in envelope_text
    assert "secret" not in envelope_text
    assert "argv" not in envelope_text
    assert "hostname" not in envelope_text
    assert "endpoint" not in envelope_text
    assert machine_path not in json.dumps(restored.to_dict(), sort_keys=True)
    assert restored.payload["my_notes"] == ["retain note"]


def test_read_job_rejects_tampered_checksum(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    pending_path = write_pending_job(root, _sample_job(), auth_key=_AUTH_KEY)
    envelope = _load_json(pending_path)
    envelope["job"]["source"] = "desktop"
    pending_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        read_job(pending_path, root, auth_key=_AUTH_KEY)


def test_read_job_rejects_unknown_envelope_schema(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    pending_path = write_pending_job(root, _sample_job(), auth_key=_AUTH_KEY)
    envelope = _load_json(pending_path)
    envelope["schema"] = 999
    pending_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        read_job(pending_path, root, auth_key=_AUTH_KEY)


def test_read_job_rejects_path_outside_root(tmp_path):
    from omd.sync_transport import read_job

    root = tmp_path / "sync-root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="root"):
        read_job(outside, root, auth_key=_AUTH_KEY)


def test_read_job_rejects_symlinked_pending_file(tmp_path):
    from omd.sync_transport import read_job

    root = tmp_path / "sync-root"
    root.mkdir()
    external = tmp_path / "external.json"
    external.write_text("{}", encoding="utf-8")
    symlink_path = root / "linked.json"
    symlink_path.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        read_job(symlink_path, root, auth_key=_AUTH_KEY)


def test_write_pending_job_returns_same_path_for_duplicate_delivery(tmp_path):
    from omd.sync_transport import write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    first = write_pending_job(root, job, auth_key=_AUTH_KEY)
    second = write_pending_job(root, job, auth_key=_AUTH_KEY)

    assert second == first
    assert first.exists()


def test_mark_job_status_does_not_persist_sensitive_detail(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()

    write_pending_job(root, job, auth_key=_AUTH_KEY)
    mark_job_status(
        root,
        job.job_id,
        "failed",
        detail={
            "message": "retry later",
            "token": "tok-secret",
            "cookies": "sid=secret",
            "endpoint": "https://sync.example.com/private",
            "path": str(tmp_path / "private" / "trace.log"),
        },
        auth_key=_AUTH_KEY,
    )

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.json"))
    )

    assert "failed" in serialized
    assert "retry later" in serialized
    assert "tok-secret" not in serialized
    assert "sid=secret" not in serialized


def test_read_job_rejects_envelope_signed_by_another_device(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    path = write_pending_job(root, _sample_job(), auth_key=_AUTH_KEY)

    with pytest.raises(ValueError, match="authentication"):
        read_job(path, root, auth_key=b"different-device-key-that-is-long-enough")


def test_write_pending_job_requires_a_strong_pairing_key(tmp_path):
    from omd.sync_transport import write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()

    with pytest.raises(ValueError, match="auth_key"):
        write_pending_job(root, _sample_job(), auth_key=b"short")


def test_mark_job_status_rejects_terminal_status_regression(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()
    write_pending_job(root, job, auth_key=_AUTH_KEY)
    mark_job_status(
        root,
        job.job_id,
        "completed",
        auth_key=_AUTH_KEY,
    )

    with pytest.raises(ValueError, match="transition"):
        mark_job_status(
            root,
            job.job_id,
            "processing",
            auth_key=_AUTH_KEY,
        )


def test_mark_job_status_rejects_status_file_signed_by_another_device(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()
    write_pending_job(root, job, auth_key=_AUTH_KEY)
    mark_job_status(
        root,
        job.job_id,
        "processing",
        auth_key=_AUTH_KEY,
    )

    with pytest.raises(ValueError, match="authentication"):
        mark_job_status(
            root,
            job.job_id,
            "completed",
            auth_key=b"different-device-key-that-is-long-enough",
        )


def test_write_pending_job_redacts_extended_credentials_and_home_paths(tmp_path):
    from omd.sync_transport import write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job(
        payload={
            "note_id": "kn_more-redaction",
            "access_token": "access-secret",
            "api_key": "api-secret",
            "authorization": "Bearer secret",
            "session_cookie": "session-secret",
            "password": "password-secret",
            "source": {"path": "~/private-note.md", "uri": "file:///private/note.md"},
            "my_notes": ["retain this"],
        }
    )

    path = write_pending_job(root, job, auth_key=_AUTH_KEY)
    serialized = path.read_text(encoding="utf-8")

    for secret in (
        "access-secret",
        "api-secret",
        "Bearer secret",
        "session-secret",
        "password-secret",
        "~/private-note.md",
        "file:///private/note.md",
    ):
        assert secret not in serialized
    assert "retain this" in serialized


def test_read_job_rejects_signed_job_transplanted_to_another_filename(tmp_path):
    from omd.sync_transport import read_job, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    original = write_pending_job(root, _sample_job(), auth_key=_AUTH_KEY)
    transplanted = original.with_name("job_0000000000000000.json")
    transplanted.write_bytes(original.read_bytes())

    with pytest.raises(ValueError, match="filename"):
        read_job(transplanted, root, auth_key=_AUTH_KEY)


def test_mark_job_status_rejects_replayed_signed_terminal_state(tmp_path):
    from omd.sync_transport import mark_job_status, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()
    write_pending_job(root, job, auth_key=_AUTH_KEY)
    status_path = mark_job_status(
        root,
        job.job_id,
        "processing",
        auth_key=_AUTH_KEY,
    )
    stale_processing = status_path.read_bytes()
    mark_job_status(
        root,
        job.job_id,
        "completed",
        auth_key=_AUTH_KEY,
    )
    status_path.write_bytes(stale_processing)

    with pytest.raises(ValueError, match="transition"):
        mark_job_status(
            root,
            job.job_id,
            "processing",
            auth_key=_AUTH_KEY,
        )


def test_mark_job_status_keeps_authoritative_state_under_local_omd_data(tmp_path):
    from omd.sync_transport import mark_job_status, trusted_state_path, write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job()
    write_pending_job(root, job, auth_key=_AUTH_KEY)

    mark_job_status(root, job.job_id, "processing", auth_key=_AUTH_KEY)

    trusted = trusted_state_path(root)
    assert trusted.is_dir()
    assert trusted.is_relative_to(Path.home() / ".local" / "share" / "omd")
    assert not trusted.is_relative_to(root)


def test_write_pending_job_redacts_variant_secret_key_names(tmp_path):
    from omd.sync_transport import write_pending_job

    root = tmp_path / "sync-root"
    root.mkdir()
    job = _sample_job(
        payload={
            "note_id": "kn_variant-secrets",
            "client_secret": "client-secret-value",
            "id_token": "id-token-value",
            "x-api-key": "api-key-value",
            "authToken": "auth-token-value",
            "set-cookie": "cookie-value",
            "connection_string": "database-secret-value",
            "my_notes": ["retain this"],
        }
    )

    serialized = write_pending_job(
        root, job, auth_key=_AUTH_KEY
    ).read_text(encoding="utf-8")

    for secret in (
        "client-secret-value",
        "id-token-value",
        "api-key-value",
        "auth-token-value",
        "cookie-value",
        "database-secret-value",
    ):
        assert secret not in serialized
    assert "https://sync.example.com/private" not in serialized
    assert str(tmp_path / "private" / "trace.log") not in serialized
