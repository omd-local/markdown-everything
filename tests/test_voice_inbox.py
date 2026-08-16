from __future__ import annotations

import json
from pathlib import Path

import pytest


def _store(tmp_path):
    from omd.voice_inbox import VoiceInboxStore

    return VoiceInboxStore(tmp_path)


def _audio(tmp_path: Path, name: str = "private voice memo.m4a") -> Path:
    path = tmp_path / name
    path.write_bytes(b"fake local audio bytes")
    return path


def test_create_preserves_audio_in_content_addressed_vault_attachment(tmp_path):
    source = _audio(tmp_path)
    store = _store(tmp_path)

    record = store.create(source, title="Walk reflection", my_notes="Keep this idea.")

    attachment = tmp_path / record.attachment_path
    assert attachment.read_bytes() == source.read_bytes()
    assert source.name not in record.attachment_path
    assert record.transcription_state == "queued"
    assert record.review_state == "inbox"


def test_persisted_receipt_does_not_include_original_absolute_path(tmp_path):
    source = _audio(tmp_path)
    store = _store(tmp_path)

    record = store.create(source, title="Private memo")
    sidecar = store.sidecar_path(record.record_id)

    assert str(source) not in sidecar.read_text(encoding="utf-8")


def test_voice_record_repr_does_not_expose_transcript_or_personal_notes(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Private memo", my_notes="Private reflection")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    record = store.save_transcript(record.record_id, {"text": "Private spoken words"})
    record = store.set_ai_suggestion(record.record_id, "Private AI suggestion")

    representation = repr(record)

    assert "Private spoken words" not in representation
    assert "Private reflection" not in representation
    assert "Private AI suggestion" not in representation


def test_voice_markdown_keeps_source_transcript_notes_and_ai_separate(tmp_path):
    source = _audio(tmp_path)
    store = _store(tmp_path)
    record = store.create(source, title="Four sections", my_notes="My own note")
    record = store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    record = store.save_transcript(record.record_id, {"text": "Raw spoken words."})
    store.set_ai_suggestion(record.record_id, "Suggested connection")

    markdown = store.markdown_path(record.record_id).read_text(encoding="utf-8")

    assert "## Source audio" in markdown
    assert "## Raw transcript" in markdown
    assert "## My Notes" in markdown
    assert "## AI suggestion (review required)" in markdown
    assert markdown.index("Raw spoken words.") < markdown.index("My own note")
    assert markdown.index("My own note") < markdown.index("Suggested connection")


def test_voice_update_rolls_back_sidecar_when_markdown_write_fails(tmp_path, monkeypatch):
    from omd import voice_inbox

    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Atomic update", my_notes="Original note")
    sidecar = store.sidecar_path(record.record_id)
    markdown = store.markdown_path(record.record_id)
    original_sidecar = sidecar.read_bytes()
    original_markdown = markdown.read_bytes()
    real_write = voice_inbox._write_text_durable
    failed = False

    def fail_markdown_once(path, content):
        nonlocal failed
        if Path(path) == markdown and not failed:
            failed = True
            raise OSError("simulated Markdown write failure")
        return real_write(path, content)

    monkeypatch.setattr(voice_inbox, "_write_text_durable", fail_markdown_once)

    with pytest.raises(OSError, match="simulated Markdown write failure"):
        store.set_my_notes(record.record_id, "New note")

    assert sidecar.read_bytes() == original_sidecar
    assert markdown.read_bytes() == original_markdown
    assert store.load(record.record_id).my_notes == "Original note"


def test_voice_markdown_renders_title_as_literal_text(tmp_path):
    store = _store(tmp_path)

    record = store.create(
        _audio(tmp_path),
        title="[Remote title](https://example.com/pixel)",
    )

    markdown = store.markdown_path(record.record_id).read_text(encoding="utf-8")
    assert "# [Remote title](https://example.com/pixel)" not in markdown
    assert "# \\[Remote title\\]\\(https://example\\.com/pixel\\)" in markdown


def test_voice_markdown_renders_backend_warning_as_literal_text(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Warning")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    reviewed = store.save_transcript(
        record.record_id,
        {
            "text": "Spoken words.",
            "quality_warnings": ["![remote beacon](https://example.com/pixel)"],
        },
    )

    markdown = store.markdown_path(reviewed.record_id).read_text(encoding="utf-8")
    assert "- ![remote beacon](https://example.com/pixel)" not in markdown
    assert "- \\!\\[remote beacon\\]\\(https://example\\.com/pixel\\)" in markdown


def test_retry_transcription_increments_attempt_and_keeps_existing_text(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Retry")
    record = store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    record = store.save_transcript(record.record_id, {"text": "First raw attempt."})

    retried = store.retry_transcription(record.record_id)

    assert retried.transcription_state == "transcribing"
    assert retried.transcription_attempts == 2
    assert retried.raw_transcript == "First raw attempt."


def test_interrupted_transcribing_record_can_restart_locally(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Interrupted")
    record = store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    resumed = store.resume_transcription(record.record_id)

    assert resumed.transcription_state == "transcribing"
    assert resumed.transcription_attempts == 2


def test_empty_transcript_requires_review(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Empty")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    reviewed = store.save_transcript(record.record_id, {"text": "   "})

    assert reviewed.transcription_state == "needs_review"
    assert any("empty" in warning.lower() for warning in reviewed.quality_warnings)


def test_low_confidence_transcript_requires_review(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Low confidence")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    reviewed = store.save_transcript(
        record.record_id,
        {"text": "Uncertain spoken words.", "confidence": 0.25},
    )

    assert any("confidence" in warning.lower() for warning in reviewed.quality_warnings)


def test_transcript_preserves_quality_warnings_reported_by_backend(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Backend warning")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")

    reviewed = store.save_transcript(
        record.record_id,
        {"text": "Some words.", "quality_warnings": ["Audio may be incomplete."]},
    )

    assert "Audio may be incomplete." in reviewed.quality_warnings


def test_repetitive_transcript_requires_review(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Repeated")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    repeated = "the model will repeat this phrase again and again " * 12

    reviewed = store.save_transcript(record.record_id, {"text": repeated})

    assert any("repeated" in warning.lower() for warning in reviewed.quality_warnings)


def test_failed_transcription_preserves_attachment_note_and_raw_attempt(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Failure", my_notes="Do not lose this")
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    store.save_transcript(record.record_id, {"text": "Partial raw words."})
    store.retry_transcription(record.record_id)

    failed = store.fail_transcription(record.record_id, error_code="runtime_missing")

    assert failed.transcription_state == "failed"
    assert failed.my_notes == "Do not lose this"
    assert failed.raw_transcript == "Partial raw words."
    assert (tmp_path / failed.attachment_path).exists()


def test_keep_raw_accept_and_reject_are_explicit_distinct_actions(tmp_path):
    store = _store(tmp_path)
    first = store.create(_audio(tmp_path, "first.wav"), title="Keep")
    store.begin_transcription(first.record_id, backend="mlx", model="whisper-test")
    store.save_transcript(first.record_id, {"text": "Keep this raw."})

    kept = store.keep_raw(first.record_id)
    accepted = store.accept(first.record_id)

    second = store.create(_audio(tmp_path, "second.wav"), title="Reject")
    rejected = store.reject(second.record_id)

    assert kept.transcript_decision == "keep_raw"
    assert accepted.review_state == "accepted"
    assert rejected.review_state == "rejected"


def test_reaccepting_rejected_voice_item_clears_rejected_transcript_decision(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Reconsider")
    store.reject(record.record_id)

    accepted = store.accept(record.record_id)

    assert accepted.review_state == "accepted"
    assert accepted.transcript_decision == "accepted"


def test_accept_writes_reviewed_voice_derivative_to_notes(tmp_path):
    store = _store(tmp_path)
    record = store.create(
        _audio(tmp_path),
        title="Reviewed voice idea",
        my_notes="My interpretation.",
    )
    store.begin_transcription(record.record_id, backend="mlx", model="whisper-test")
    store.save_transcript(record.record_id, {"text": "Exact spoken words."})
    store.set_ai_suggestion(record.record_id, "Possible connection.")

    accepted = store.accept(record.record_id)

    reviewed_path = store.reviewed_note_path(record.record_id)
    reviewed = reviewed_path.read_text(encoding="utf-8")
    sidecar = json.loads(reviewed_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert accepted.review_state == "accepted"
    assert reviewed_path.parent == tmp_path / "Notes"
    assert store.markdown_path(record.record_id).parent == tmp_path / "Inbox"
    assert sidecar["derived_from"] == record.record_id
    assert "Exact spoken words." in reviewed
    assert "My interpretation." in reviewed
    assert "Possible connection." in reviewed


def test_accept_does_not_overwrite_an_existing_reviewed_voice_note(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Keep reviewed edits")
    store.accept(record.record_id)
    reviewed_path = store.reviewed_note_path(record.record_id)
    reviewed_path.write_text("# My edited note\n", encoding="utf-8")

    store.accept(record.record_id)

    assert reviewed_path.read_text(encoding="utf-8") == "# My edited note\n"


def test_corrupt_voice_sidecar_is_rejected(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Corrupt")
    sidecar = store.sidecar_path(record.record_id)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["record"]["title"] = "tampered"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="integrity"):
        store.load(record.record_id)


def test_create_rejects_symlinked_audio_source(tmp_path):
    real = _audio(tmp_path, "real.wav")
    linked = tmp_path / "linked.wav"
    linked.symlink_to(real)
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="non-symlink"):
        store.create(linked, title="Linked")


def test_load_detects_missing_preserved_attachment(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Missing")
    (tmp_path / record.attachment_path).unlink()

    with pytest.raises(ValueError, match="attachment"):
        store.load(record.record_id)


def test_load_detects_tampered_preserved_attachment(tmp_path):
    store = _store(tmp_path)
    record = store.create(_audio(tmp_path), title="Tampered")
    (tmp_path / record.attachment_path).write_bytes(b"different bytes")

    with pytest.raises(ValueError, match="integrity"):
        store.load(record.record_id)
