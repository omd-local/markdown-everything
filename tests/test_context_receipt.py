from __future__ import annotations

import hashlib
import json

import pytest

from omd.inbox import InboxJob


def _job(**overrides) -> InboxJob:
    values = {
        "job_type": "capture",
        "payload": {"source_identity": "source_example"},
        "source": "desktop",
        "created_at": "2026-07-19T01:00:00Z",
    }
    values.update(overrides)
    return InboxJob(**values)


def _queue(outbox, job: InboxJob | None = None):
    return outbox.queue(
        job or _job(),
        source_type="local_file",
        destination="Obsidian/Inbox",
        privacy_mode="local_only",
    )


def test_queue_persists_a_referenced_receipt_before_processing_starts(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)

    receipt = _queue(outbox)

    assert receipt.state == "queued"
    assert receipt.source_state == "referenced"
    assert receipt.current_stage is None
    assert ContextOutbox(tmp_path).load(receipt.job_id) == receipt


def test_secure_local_source_marks_secured_only_after_atomic_copy_commits(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.pdf"
    content = b"local source bytes"
    source.write_bytes(content)
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)

    secured = outbox.secure_local_source(queued.job_id, source)

    assert secured.state == "source_secured"
    assert secured.source_state == "secured"
    assert secured.content_hash == hashlib.sha256(content).hexdigest()
    assert secured.secured_source is not None
    staged = outbox_root / secured.secured_source
    assert staged.read_bytes() == content
    assert not list(staged.parent.glob("*.tmp"))


def test_failed_source_copy_does_not_claim_that_the_source_is_secured(
    tmp_path, monkeypatch
):
    import omd.context_receipt as context_receipt

    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = context_receipt.ContextOutbox(outbox_root)
    queued = _queue(outbox)

    def fail_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(context_receipt, "_copy_source_atomic", fail_copy)

    with pytest.raises(OSError, match="disk full"):
        outbox.secure_local_source(queued.job_id, source)

    restored = context_receipt.ContextOutbox(outbox_root).load(queued.job_id)
    assert restored.state == "queued"
    assert restored.source_state == "referenced"
    assert restored.content_hash is None


def test_duplicate_queue_keeps_the_latest_receipt_state(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.txt"
    source.write_text("same source", encoding="utf-8")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    job = _job()
    first = _queue(outbox, job)
    secured = outbox.secure_local_source(first.job_id, source)

    duplicate = _queue(outbox, job)

    assert duplicate == secured
    assert outbox.list_receipts() == [secured]


def test_duplicate_job_rejects_changed_receipt_privacy_metadata(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    job = _job()
    _queue(outbox, job)

    with pytest.raises(ValueError, match="receipt metadata"):
        outbox.queue(
            job,
            source_type="local_file",
            destination="Obsidian/Inbox",
            privacy_mode="cloud_for_this_task",
        )


def test_cancelled_job_remains_terminal_after_restart(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    queued = _queue(outbox)

    cancelled = outbox.cancel(queued.job_id)

    restarted = ContextOutbox(tmp_path)
    assert restarted.load(queued.job_id) == cancelled
    assert cancelled.state == "cancelled"
    assert cancelled.cancelled_at is not None
    assert restarted.recover_incomplete() == []


def test_recovery_requeues_an_interrupted_job_without_creating_a_duplicate(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.md"
    source.write_text("recover me", encoding="utf-8")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)
    secured = outbox.secure_local_source(queued.job_id, source)
    processing = outbox.start_stage(secured.job_id, "convert")
    assert processing.state == "processing"

    recovered = ContextOutbox(outbox_root).recover_incomplete()

    assert len(recovered) == 1
    assert recovered[0].job_id == queued.job_id
    assert recovered[0].state == "source_secured"
    assert recovered[0].current_stage == "convert"
    assert recovered[0].stage_attempts == {"convert": 1}
    assert len(ContextOutbox(outbox_root).list_receipts()) == 1


def test_retryable_stage_failure_resumes_the_same_job_with_a_new_attempt(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.txt"
    source.write_text("retry me", encoding="utf-8")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)
    outbox.secure_local_source(queued.job_id, source)
    outbox.start_stage(queued.job_id, "convert")
    failed = outbox.fail_stage(
        queued.job_id,
        error_code="converter_timeout",
        retryable=True,
    )

    retried = outbox.retry_stage(queued.job_id)

    assert failed.state == "needs_action"
    assert failed.recovery_action == "retry"
    assert retried.state == "processing"
    assert retried.current_stage == "convert"
    assert retried.stage_attempts == {"convert": 2}


def test_local_file_cannot_start_processing_before_source_is_secured(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    queued = _queue(outbox)

    with pytest.raises(ValueError, match="secured"):
        outbox.start_stage(queued.job_id, "convert")

    assert outbox.load(queued.job_id).state == "queued"


def test_queue_rejects_sensitive_job_payload_without_writing_an_entry(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    job = _job(payload={"api_key": "secret", "source_identity": "source_example"})

    with pytest.raises(ValueError, match="sensitive"):
        _queue(outbox, job)

    assert outbox.list_receipts() == []


@pytest.mark.parametrize(
    "secret_value",
    [
        "Authorization: Bearer secret-token-value",
        "Cookie: sessionid=private-cookie-value",
        "omd capture --cookies /private/cookies.txt",
        "sk-provider-secret-value",
    ],
)
def test_queue_rejects_sensitive_job_payload_values(tmp_path, secret_value):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    job = _job(payload={"source_identity": "source_example", "note": secret_value})

    with pytest.raises(ValueError, match="sensitive value"):
        _queue(outbox, job)

    assert outbox.list_receipts() == []


def test_queue_rejects_absolute_source_path_in_job_payload(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    job = _job(payload={"path": str(tmp_path / "private.pdf")})

    with pytest.raises(ValueError, match="absolute path"):
        _queue(outbox, job)


def test_queue_rejects_absolute_path_in_top_level_job_source(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    job = _job(source=str(tmp_path / "private-source"))

    with pytest.raises(ValueError, match="job source"):
        _queue(outbox, job)


def test_load_rejects_a_tampered_outbox_envelope(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    receipt = _queue(outbox)
    path = tmp_path / "jobs" / f"{receipt.job_id}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["receipt"]["state"] = "complete"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        outbox.load(receipt.job_id)


def test_load_rejects_a_partial_outbox_envelope(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox = ContextOutbox(tmp_path)
    receipt = _queue(outbox)
    path = tmp_path / "jobs" / f"{receipt.job_id}.json"
    path.write_text('{"schema_version": 1, "job": ', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid outbox entry"):
        outbox.load(receipt.job_id)


def test_secure_local_source_rejects_symlink_without_changing_receipt(tmp_path):
    from omd.context_receipt import ContextOutbox

    target = tmp_path / "target.txt"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)

    with pytest.raises(ValueError, match="non-symlink"):
        outbox.secure_local_source(queued.job_id, link)

    assert outbox.load(queued.job_id) == queued


def test_interrupted_initial_queue_leaves_no_visible_or_partial_job(tmp_path, monkeypatch):
    import omd.context_receipt as context_receipt

    outbox = context_receipt.ContextOutbox(tmp_path)

    def interrupt(*_args, **_kwargs):
        raise OSError("simulated power interruption")

    monkeypatch.setattr(context_receipt.os, "replace", interrupt)

    with pytest.raises(OSError, match="power interruption"):
        _queue(outbox)

    assert list((tmp_path / "jobs").iterdir()) == []


def test_interruption_after_source_commit_does_not_advance_receipt(
    tmp_path, monkeypatch
):
    import omd.context_receipt as context_receipt

    source = tmp_path / "source.bin"
    source.write_bytes(b"durable source")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = context_receipt.ContextOutbox(outbox_root)
    queued = _queue(outbox)

    with monkeypatch.context() as patch:
        patch.setattr(
            context_receipt,
            "_write_text_durable",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("simulated receipt interruption")
            ),
        )
        with pytest.raises(OSError, match="receipt interruption"):
            outbox.secure_local_source(queued.job_id, source)

    assert context_receipt.ContextOutbox(outbox_root).load(queued.job_id) == queued


def test_large_source_is_hashed_and_staged_without_truncation(tmp_path):
    from omd.context_receipt import ContextOutbox

    content = (b"0123456789abcdef" * (1024 * 256)) + b"tail"
    source = tmp_path / "large.pdf"
    source.write_bytes(content)
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)

    receipt = outbox.secure_local_source(queued.job_id, source)

    assert receipt.content_hash == hashlib.sha256(content).hexdigest()
    assert (outbox_root / receipt.secured_source).stat().st_size == len(content)


def test_missing_secured_source_cannot_be_rebound_to_different_bytes(tmp_path):
    from omd.context_receipt import ContextOutbox

    first = tmp_path / "first.pdf"
    first.write_bytes(b"original bytes")
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(b"different bytes")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)
    secured = outbox.secure_local_source(queued.job_id, first)
    (outbox_root / secured.secured_source).unlink()

    with pytest.raises(ValueError, match="does not match the secured source identity"):
        outbox.secure_local_source(queued.job_id, replacement)

    repaired = ContextOutbox(outbox_root).load(queued.job_id)
    assert repaired.source_state == "missing"
    assert repaired.state == "needs_action"
    assert repaired.content_hash == secured.content_hash
    assert repaired.recovery_action == "restore_source"


def test_missing_secured_source_can_be_restored_only_with_matching_bytes(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.wav"
    source.write_bytes(b"same audio bytes")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    queued = _queue(outbox)
    secured = outbox.secure_local_source(queued.job_id, source)
    (outbox_root / secured.secured_source).unlink()
    assert ContextOutbox(outbox_root).load(queued.job_id).source_state == "missing"

    restored = ContextOutbox(outbox_root).secure_local_source(queued.job_id, source)

    assert restored.source_state == "secured"
    assert restored.state == "source_secured"
    assert restored.content_hash == secured.content_hash
    assert (outbox_root / restored.secured_source).read_bytes() == b"same audio bytes"


def test_restart_downgrades_receipt_when_secured_source_is_missing(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.pdf"
    source.write_bytes(b"durable source")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    secured = outbox.secure_local_source(_queue(outbox).job_id, source)
    (outbox_root / secured.secured_source).unlink()

    restarted = ContextOutbox(outbox_root)
    downgraded = restarted.load(secured.job_id)

    assert downgraded.source_state == "missing"
    assert downgraded.state == "needs_action"
    assert downgraded.error_code == "secured_source_missing"
    assert downgraded.recovery_action == "restore_source"
    assert restarted.recover_incomplete() == []


def test_restart_downgrades_receipt_when_secured_source_hash_changed(tmp_path):
    from omd.context_receipt import ContextOutbox

    source = tmp_path / "source.pdf"
    source.write_bytes(b"expected bytes")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = ContextOutbox(outbox_root)
    secured = outbox.secure_local_source(_queue(outbox).job_id, source)
    (outbox_root / secured.secured_source).write_bytes(b"tampered bytes")

    downgraded = ContextOutbox(outbox_root).load(secured.job_id)

    assert downgraded.source_state == "missing"
    assert downgraded.state == "needs_action"
    assert downgraded.error_code == "secured_source_integrity_failed"


def test_new_source_directory_is_synced_in_its_parent(tmp_path, monkeypatch):
    import omd.context_receipt as context_receipt

    source = tmp_path / "source.txt"
    source.write_text("sync directory", encoding="utf-8")
    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    outbox = context_receipt.ContextOutbox(outbox_root)
    queued = _queue(outbox)
    synced = []
    monkeypatch.setattr(context_receipt, "_sync_directory", synced.append)

    outbox.secure_local_source(queued.job_id, source)

    assert outbox_root / "sources" in synced


def test_outbox_rejects_reserved_entry_that_is_not_a_directory(tmp_path):
    from omd.context_receipt import ContextOutbox

    outbox_root = tmp_path / "outbox"
    outbox_root.mkdir()
    (outbox_root / "jobs").write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="directory"):
        ContextOutbox(outbox_root)
