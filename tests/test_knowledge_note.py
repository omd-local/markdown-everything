from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest


def _sample_source() -> dict[str, str]:
    return {
        "kind": "webpage",
        "title": "Immutable notes",
        "url": "https://example.com/immutable-notes",
        "author": "Example Author",
        "captured_at": "2026-07-19T00:00:00Z",
        "raw_text": "Line one.\nLine two.\n",
    }


def test_knowledge_note_is_immutable():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source=_sample_source(),
        highlights=["Line one."],
        my_notes=["Keep the original wording."],
        ai_suggestions=["tag:reference"],
    )

    with pytest.raises(FrozenInstanceError):
        note.my_notes = ["Mutated"]


def test_knowledge_note_defensively_copies_constructor_inputs():
    from omd.knowledge_note import KnowledgeNote

    source = _sample_source()
    highlights = ["Line one."]
    my_notes = ["Keep the original wording."]
    ai_suggestions = ["tag:reference"]

    note = KnowledgeNote(
        source=source,
        highlights=highlights,
        my_notes=my_notes,
        ai_suggestions=ai_suggestions,
    )

    source["raw_text"] = "Mutated after init"
    highlights.append("Injected highlight")
    my_notes.append("Injected note")
    ai_suggestions.append("Injected suggestion")

    assert note.source["raw_text"] == "Line one.\nLine two.\n"
    assert note.highlights == ["Line one."]
    assert note.my_notes == ["Keep the original wording."]
    assert note.ai_suggestions == ["tag:reference"]


def test_knowledge_note_to_dict_returns_defensive_copy():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source=_sample_source(),
        highlights=["Line one."],
        my_notes=["Keep the original wording."],
        ai_suggestions=["tag:reference"],
    )

    payload = note.to_dict()
    payload["source"]["raw_text"] = "Mutated after export"
    payload["highlights"].append("Injected highlight")
    payload["my_notes"].append("Injected note")
    payload["ai_suggestions"].append("Injected suggestion")

    assert note.source["raw_text"] == "Line one.\nLine two.\n"
    assert note.highlights == ["Line one."]
    assert note.my_notes == ["Keep the original wording."]
    assert note.ai_suggestions == ["tag:reference"]


def test_knowledge_note_preserves_explicit_source_highlights_my_notes_and_ai_suggestions():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source=_sample_source(),
        highlights=["Line one."],
        my_notes=["My wording stays separate."],
        ai_suggestions=["Summarize this later."],
    )

    payload = note.to_dict()

    assert payload["source"] == _sample_source()
    assert payload["highlights"] == ["Line one."]
    assert payload["my_notes"] == ["My wording stays separate."]
    assert payload["ai_suggestions"] == ["Summarize this later."]
    assert payload["source"]["raw_text"] == "Line one.\nLine two.\n"


def test_knowledge_note_json_round_trip_preserves_raw_user_authored_content():
    from omd.knowledge_note import KnowledgeNote

    original_notes = ["Verbatim quote: 'Line one.'", "Trailing spaces stay  "]
    original_highlights = ["Line one.", "Line two."]
    original_suggestions = ["draft a title", "suggest tags"]
    note = KnowledgeNote(
        source=_sample_source(),
        highlights=original_highlights,
        my_notes=original_notes,
        ai_suggestions=original_suggestions,
    )

    restored = KnowledgeNote.from_json(note.to_json())

    assert restored == note
    assert restored.my_notes == original_notes
    assert restored.highlights == original_highlights
    assert restored.ai_suggestions == original_suggestions
    assert restored.source["raw_text"] == "Line one.\nLine two.\n"


def test_knowledge_note_round_trip_preserves_editable_tags():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source=_sample_source(),
        highlights=[],
        my_notes=[],
        ai_suggestions=[],
        tags=["agents", "知识管理"],
    )

    restored = KnowledgeNote.from_json(note.to_json())

    assert restored.tags == ["agents", "知识管理"]
    assert restored.to_dict()["tags"] == ["agents", "知识管理"]


def test_knowledge_note_rejects_missing_required_source_fields():
    from omd.knowledge_note import KnowledgeNote

    with pytest.raises(ValueError, match="source.url"):
        KnowledgeNote(
            source={"kind": "webpage", "title": "Untitled", "raw_text": "body"},
            highlights=[],
            my_notes=[],
            ai_suggestions=[],
        )


def test_knowledge_note_accepts_local_file_source_with_path_without_url():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source={
            "kind": "local_file",
            "title": "Local transcript",
            "path": "/tmp/local-transcript.md",
            "raw_text": "Captured locally.",
        },
        highlights=[],
        my_notes=[],
        ai_suggestions=[],
    )

    assert note.source["kind"] == "local_file"
    assert note.source["path"] == "/tmp/local-transcript.md"


def test_knowledge_note_accepts_personal_note_source_without_url():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source={
            "kind": "personal_note",
            "title": "Scratch note",
            "raw_text": "This came from the user.",
        },
        highlights=[],
        my_notes=["User-authored note"],
        ai_suggestions=[],
    )

    assert note.source["kind"] == "personal_note"
    assert note.my_notes == ["User-authored note"]


def test_knowledge_note_rejects_local_file_source_without_path():
    from omd.knowledge_note import KnowledgeNote

    with pytest.raises(ValueError, match="source.path"):
        KnowledgeNote(
            source={
                "kind": "local_file",
                "title": "Local transcript",
                "raw_text": "Captured locally.",
            },
            highlights=[],
            my_notes=[],
            ai_suggestions=[],
        )


def test_knowledge_note_rejects_webpage_source_without_url():
    from omd.knowledge_note import KnowledgeNote

    with pytest.raises(ValueError, match="source.url"):
        KnowledgeNote(
            source={
                "kind": "webpage",
                "title": "Untitled",
                "raw_text": "body",
            },
            highlights=[],
            my_notes=[],
            ai_suggestions=[],
        )


def test_knowledge_note_uses_stable_id_for_equivalent_payloads():
    from omd.knowledge_note import KnowledgeNote

    first = KnowledgeNote(
        source=_sample_source(),
        highlights=["Line one."],
        my_notes=["Keep"],
        ai_suggestions=["tag:reference"],
    )
    second = KnowledgeNote(
        source=_sample_source(),
        highlights=["Line one."],
        my_notes=["Keep"],
        ai_suggestions=["tag:reference"],
    )

    assert first.note_id == second.note_id


def test_knowledge_note_id_does_not_change_when_ai_suggestions_change():
    from omd.knowledge_note import KnowledgeNote

    first = KnowledgeNote(
        source=_sample_source(), highlights=[], my_notes=[], ai_suggestions=["first draft"]
    )
    second = KnowledgeNote(
        source=_sample_source(), highlights=[], my_notes=[], ai_suggestions=["revised draft"]
    )

    assert first.note_id == second.note_id


def test_knowledge_note_preserves_derived_from_in_round_trip():
    from omd.knowledge_note import KnowledgeNote

    note = KnowledgeNote(
        source=_sample_source(),
        highlights=[],
        my_notes=[],
        ai_suggestions=[],
        derived_from="inbox_0123456789abcdef",
    )

    restored = KnowledgeNote.from_json(note.to_json())

    assert restored.derived_from == "inbox_0123456789abcdef"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_knowledge_note_rejects_non_finite_json_numbers(value):
    from omd.knowledge_note import KnowledgeNote

    source = _sample_source()
    source["score"] = value

    with pytest.raises(ValueError, match="JSON-compatible"):
        KnowledgeNote(source=source, highlights=[], my_notes=[], ai_suggestions=[])


def test_knowledge_note_rejects_non_string_source_keys():
    from omd.knowledge_note import KnowledgeNote

    source = _sample_source()
    source[1] = "not preserved"

    with pytest.raises(ValueError, match="string keys"):
        KnowledgeNote(source=source, highlights=[], my_notes=[], ai_suggestions=[])


@pytest.mark.parametrize("kind", [" webpage ", "mystery"])
def test_knowledge_note_rejects_non_canonical_source_kind(kind):
    from omd.knowledge_note import KnowledgeNote

    source = _sample_source()
    source["kind"] = kind

    with pytest.raises(ValueError, match="source.kind"):
        KnowledgeNote(source=source, highlights=[], my_notes=[], ai_suggestions=[])
