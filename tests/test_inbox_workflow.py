from __future__ import annotations

import json

import pytest

from omd.inbox import InboxItem


def _item(**overrides) -> InboxItem:
    values = {
        "capture_surface": "my_note",
        "provenance_kind": "authored",
        "title": "A field thought",
        "raw_content": "Keep this exact thought.  ",
        "source_locator": {"kind": "manual"},
        "captured_at": "2026-07-19T00:00:00Z",
    }
    values.update(overrides)
    return InboxItem(**values)


def test_save_inbox_item_writes_readable_markdown_and_sidecar(tmp_path):
    from omd.inbox_workflow import save_inbox_item

    path = save_inbox_item(tmp_path, _item())

    assert path.parent == tmp_path / "Inbox"
    assert "Keep this exact thought.  " in path.read_text(encoding="utf-8")
    assert path.with_suffix(".omd.json").exists()


def test_save_inbox_item_is_idempotent_for_same_item(tmp_path):
    from omd.inbox_workflow import save_inbox_item, set_review_status

    item = _item()
    first = save_inbox_item(tmp_path, item)
    set_review_status(tmp_path, item.item_id, "rejected")
    second = save_inbox_item(tmp_path, item)

    assert second == first
    sidecar = json.loads(first.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert sidecar["review_status"] == "rejected"


def test_inbox_markdown_does_not_render_absolute_source_path(tmp_path):
    from omd.inbox_workflow import save_inbox_item

    secret_path = "/Users/example/private/source.pdf"
    path = save_inbox_item(
        tmp_path,
        _item(
            capture_surface="import",
            provenance_kind="imported",
            source_locator={"kind": "local_file", "path": secret_path},
        ),
    )

    assert secret_path not in path.read_text(encoding="utf-8")
    assert secret_path not in path.with_suffix(".omd.json").read_text(encoding="utf-8")


def test_list_inbox_items_returns_newest_first(tmp_path):
    from omd.inbox_workflow import list_inbox_items, save_inbox_item

    save_inbox_item(tmp_path, _item(title="Older", captured_at="2026-07-18T00:00:00Z"))
    save_inbox_item(tmp_path, _item(title="Newer", captured_at="2026-07-19T00:00:00Z"))

    assert [entry.title for entry in list_inbox_items(tmp_path)] == ["Newer", "Older"]


def test_promote_inbox_item_preserves_source_and_writes_derived_note(tmp_path):
    from omd.inbox_workflow import promote_inbox_item, save_inbox_item

    item = _item()
    source_path = save_inbox_item(tmp_path, item)
    note_path = promote_inbox_item(tmp_path, item.item_id, my_notes=["My review"])

    assert source_path.exists()
    assert note_path.parent == tmp_path / "Notes"
    sidecar = json.loads(note_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert sidecar["derived_from"] == item.item_id
    assert sidecar["my_notes"] == ["My review"]
    assert item.item_id in note_path.name


def test_promote_inbox_item_links_an_explicit_validated_vault_markdown_source(tmp_path):
    from omd.inbox_workflow import promote_inbox_item, save_inbox_item

    item = _item(title="Linked review")
    save_inbox_item(tmp_path, item)
    converted = tmp_path / "Sources" / "Web" / "converted article.md"
    converted.parent.mkdir(parents=True)
    converted.write_text("# Converted article\n\nSource body.\n", encoding="utf-8")
    before = converted.read_bytes()

    note_path = promote_inbox_item(
        tmp_path,
        item.item_id,
        linked_source_path="Sources/Web/converted article.md",
        tags=["agents", "知识管理"],
    )

    markdown = note_path.read_text(encoding="utf-8")
    sidecar = json.loads(note_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert markdown.startswith('---\ntags:\n  - "agents"\n  - "知识管理"\n---\n')
    assert "## Linked source" in markdown
    assert "[[Sources/Web/converted article]]" in markdown
    assert sidecar["source"]["linked_markdown_path"] == "Sources/Web/converted article.md"
    assert sidecar["tags"] == ["agents", "知识管理"]
    assert converted.read_bytes() == before


def test_promote_inbox_item_rejects_link_outside_the_vault(tmp_path):
    from omd.inbox_workflow import promote_inbox_item, save_inbox_item

    item = _item(title="Unsafe link")
    save_inbox_item(tmp_path, item)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="relative Markdown path"):
        promote_inbox_item(
            tmp_path,
            item.item_id,
            linked_source_path="../outside.md",
        )


def test_set_review_status_rejects_unknown_status(tmp_path):
    from omd.inbox_workflow import save_inbox_item, set_review_status

    item = _item()
    save_inbox_item(tmp_path, item)

    with pytest.raises(ValueError, match="status"):
        set_review_status(tmp_path, item.item_id, "auto_approved")


def test_save_inbox_item_rejects_symlinked_inbox_directory(tmp_path):
    from omd.inbox_workflow import save_inbox_item

    outside = tmp_path / "outside"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Inbox").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        save_inbox_item(vault, _item())


def test_list_inbox_items_rejects_symlinked_sidecar(tmp_path):
    from omd.inbox_workflow import list_inbox_items

    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"item_id": "item_outside"}', encoding="utf-8")
    (inbox / "linked.omd.json").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        list_inbox_items(tmp_path)


def test_promote_inbox_item_repairs_review_status_on_idempotent_retry(tmp_path):
    from omd.inbox_workflow import (
        promote_inbox_item,
        save_inbox_item,
        set_review_status,
    )

    item = _item()
    source = save_inbox_item(tmp_path, item)
    first = promote_inbox_item(tmp_path, item.item_id)
    set_review_status(tmp_path, item.item_id, "inbox")

    second = promote_inbox_item(tmp_path, item.item_id)

    sidecar = json.loads(source.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert second == first
    assert sidecar["review_status"] == "accepted"


def test_promote_inbox_item_rejects_changed_retry_payload(tmp_path):
    from omd.inbox_workflow import (
        load_inbox_item,
        promote_inbox_item,
        save_inbox_item,
        set_review_status,
    )

    item = _item(title="Retry payload")
    save_inbox_item(tmp_path, item)
    output = promote_inbox_item(tmp_path, item.item_id, tags=["first-tag"])
    set_review_status(tmp_path, item.item_id, "inbox")
    before = {
        path: path.read_bytes()
        for path in (output, output.with_suffix(".omd.json"))
    }

    with pytest.raises(ValueError, match="different content"):
        promote_inbox_item(tmp_path, item.item_id, tags=["replacement-tag"])

    assert load_inbox_item(tmp_path, item.item_id).review_status == "inbox"
    for path, expected in before.items():
        assert path.read_bytes() == expected


def test_promote_inbox_item_rejects_existing_markdown_that_differs_from_sidecar(tmp_path):
    from omd.inbox_workflow import (
        load_inbox_item,
        promote_inbox_item,
        save_inbox_item,
        set_review_status,
    )

    item = _item(title="Tampered reviewed note")
    save_inbox_item(tmp_path, item)
    output = promote_inbox_item(tmp_path, item.item_id)
    set_review_status(tmp_path, item.item_id, "inbox")
    output.write_text("Edited outside the review workflow.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not match its sidecar"):
        promote_inbox_item(tmp_path, item.item_id)

    assert load_inbox_item(tmp_path, item.item_id).review_status == "inbox"
    assert output.read_text(encoding="utf-8") == "Edited outside the review workflow.\n"


def test_promote_inbox_item_rejects_symlinked_existing_reviewed_note(tmp_path):
    from omd.inbox_workflow import promote_inbox_item, save_inbox_item, set_review_status

    item = _item(title="Symlinked reviewed note")
    save_inbox_item(tmp_path, item)
    output = promote_inbox_item(tmp_path, item.item_id)
    set_review_status(tmp_path, item.item_id, "inbox")
    output.unlink()
    outside = tmp_path / "outside-reviewed-note.md"
    outside.write_text("Outside.\n", encoding="utf-8")
    output.symlink_to(outside)

    with pytest.raises(ValueError, match="non-symlink"):
        promote_inbox_item(tmp_path, item.item_id)

    assert outside.read_text(encoding="utf-8") == "Outside.\n"


def test_load_inbox_item_returns_raw_source_and_review_status(tmp_path):
    from omd.inbox_workflow import load_inbox_item, save_inbox_item, set_review_status

    item = _item(raw_content="Exact raw source text.")
    save_inbox_item(tmp_path, item)
    set_review_status(tmp_path, item.item_id, "rejected")

    review = load_inbox_item(tmp_path, item.item_id)

    assert review.item_id == item.item_id
    assert review.raw_content == item.raw_content
    assert review.source_locator == item.source_locator
    assert review.review_status == "rejected"


def test_loaded_inbox_source_locator_cannot_mutate_the_review_record(tmp_path):
    from omd.inbox_workflow import load_inbox_item, save_inbox_item

    item = _item(source_locator={"kind": "manual", "selector": "paragraph-2"})
    save_inbox_item(tmp_path, item)

    review = load_inbox_item(tmp_path, item.item_id)
    exposed = review.source_locator
    exposed["selector"] = "changed"

    assert review.source_locator == {"kind": "manual", "selector": "paragraph-2"}
    assert review.path.startswith("Inbox/")


def test_load_inbox_item_rejects_tampered_item_identity(tmp_path):
    from omd.inbox_workflow import load_inbox_item, save_inbox_item

    item = _item()
    path = save_inbox_item(tmp_path, item)
    sidecar = path.with_suffix(".omd.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["raw_content"] = "Tampered after identity was created"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="integrity"):
        load_inbox_item(tmp_path, item.item_id)
