from __future__ import annotations

import math

import pytest


def _sample_note_payload() -> dict[str, object]:
    return {
        "note_id": "kn_immutable-notes",
        "source": {
            "kind": "webpage",
            "title": "Immutable notes",
            "url": "https://example.com/immutable-notes",
            "raw_text": "Line one.\nLine two.\n",
        },
        "highlights": ["Line one."],
        "my_notes": ["User-authored note"],
        "ai_suggestions": ["tag:reference"],
    }


def test_inbox_job_uses_versioned_envelope_fields():
    from omd.inbox import InboxJob

    job = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="capture",
    )

    envelope = job.to_dict()

    assert envelope["schema_version"] == 1
    assert envelope["job_type"] == "knowledge_note"
    assert envelope["source"] == "capture"
    assert envelope["payload"] == _sample_note_payload()


def test_inbox_job_defensively_copies_constructor_input_payload():
    from omd.inbox import InboxJob

    payload = _sample_note_payload()
    job = InboxJob(
        job_type="knowledge_note",
        payload=payload,
        source="capture",
    )

    payload["my_notes"] = ["Mutated after init"]
    payload["source"]["raw_text"] = "Mutated after init"

    assert job.payload["my_notes"] == ["User-authored note"]
    assert job.payload["source"]["raw_text"] == "Line one.\nLine two.\n"


def test_inbox_job_to_dict_returns_defensive_copy():
    from omd.inbox import InboxJob

    job = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="capture",
    )

    envelope = job.to_dict()
    envelope["payload"]["my_notes"] = ["Mutated after export"]
    envelope["payload"]["source"]["raw_text"] = "Mutated after export"

    assert job.payload["my_notes"] == ["User-authored note"]
    assert job.payload["source"]["raw_text"] == "Line one.\nLine two.\n"


def test_inbox_job_json_round_trip_preserves_payload_and_metadata():
    from omd.inbox import InboxJob

    job = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="capture",
        created_at="2026-07-19T00:00:00Z",
    )

    restored = InboxJob.from_json(job.to_json())

    assert restored == job
    assert restored.payload["my_notes"] == ["User-authored note"]
    assert restored.payload["source"]["raw_text"] == "Line one.\nLine two.\n"


def test_inbox_job_rejects_unknown_schema_version():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="schema_version"):
        InboxJob.from_dict(
            {
                "schema_version": 999,
                "job_id": "job_future",
                "job_type": "knowledge_note",
                "source": "capture",
                "payload": _sample_note_payload(),
            }
        )


def test_inbox_job_requires_non_empty_job_type():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="job_type"):
        InboxJob(
            job_type="",
            payload=_sample_note_payload(),
            source="capture",
        )


def test_inbox_job_requires_non_empty_source():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="source"):
        InboxJob(
            job_type="knowledge_note",
            payload=_sample_note_payload(),
            source="",
        )


def test_inbox_job_requires_payload_to_be_json_object():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="payload"):
        InboxJob(
            job_type="knowledge_note",
            payload=["not", "an", "object"],
            source="capture",
        )


def test_inbox_job_uses_stable_id_for_equivalent_envelopes():
    from omd.inbox import InboxJob

    first = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="capture",
    )
    second = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="capture",
    )

    assert first.job_id == second.job_id


def test_inbox_job_does_not_mutate_user_authored_note_content():
    from omd.inbox import InboxJob

    payload = _sample_note_payload()
    payload["my_notes"] = ["Verbatim user note  ", "Second line stays."]

    job = InboxJob(
        job_type="knowledge_note",
        payload=payload,
        source="capture",
    )

    restored = InboxJob.from_dict(job.to_dict())

    assert restored.payload["my_notes"] == ["Verbatim user note  ", "Second line stays."]


def test_inbox_job_rejects_non_iso_created_at():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="created_at"):
        InboxJob(
            job_type="knowledge_note",
            payload=_sample_note_payload(),
            source="capture",
            created_at="yesterday",
        )


def test_inbox_item_preserves_authored_content_and_provenance():
    from omd.inbox import InboxItem

    item = InboxItem(
        capture_surface="my_note",
        provenance_kind="authored",
        title="Field thought",
        raw_content="Keep this exactly  ",
        source_locator={"kind": "manual"},
        captured_at="2026-07-19T00:00:00Z",
    )

    restored = InboxItem.from_json(item.to_json())

    assert restored == item
    assert restored.raw_content == "Keep this exactly  "
    assert restored.capture_surface == "my_note"
    assert restored.provenance_kind == "authored"


def test_inbox_item_id_distinguishes_separate_capture_events():
    from omd.inbox import InboxItem

    first = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Quote",
        raw_content="Exact quote",
        source_locator={"url": "https://example.com/article", "selector": "p:2"},
        captured_at="2026-07-19T00:00:00Z",
    )
    second = InboxItem(
        capture_surface="highlight",
        provenance_kind="excerpt",
        title="Quote",
        raw_content="Exact quote",
        source_locator={"selector": "p:2", "url": "https://example.com/article"},
        captured_at="2026-07-19T01:00:00Z",
    )

    assert first.item_id != second.item_id


def test_inbox_job_id_is_stable_across_delivery_timestamps():
    from omd.inbox import InboxJob

    first = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="mobile",
        created_at="2026-07-19T00:00:00Z",
    )
    second = InboxJob(
        job_type="knowledge_note",
        payload=_sample_note_payload(),
        source="mobile",
        created_at="2026-07-19T01:00:00Z",
    )

    assert first.job_id == second.job_id


def test_inbox_item_rejects_unknown_capture_surface():
    from omd.inbox import InboxItem

    with pytest.raises(ValueError, match="capture_surface"):
        InboxItem(
            capture_surface="automatic_tracking",
            provenance_kind="authored",
            title="Invalid",
            raw_content="content",
            source_locator={"kind": "manual"},
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_inbox_job_rejects_non_finite_json_numbers(value):
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="JSON-compatible"):
        InboxJob(
            job_type="knowledge_note",
            payload={"score": value},
            source="capture",
        )


def test_inbox_job_rejects_non_string_payload_keys():
    from omd.inbox import InboxJob

    with pytest.raises(ValueError, match="string keys"):
        InboxJob(
            job_type="knowledge_note",
            payload={1: "not preserved"},
            source="capture",
        )
