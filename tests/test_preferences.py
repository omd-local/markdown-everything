from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass

import pytest


def _empty_profile():
    from omd.preferences import PreferenceProfile

    return PreferenceProfile.empty_profile()


def _profile_signals(profile):
    return profile.signals


def test_preference_profile_is_a_frozen_dataclass():
    from omd.preferences import PreferenceProfile

    assert is_dataclass(PreferenceProfile)
    assert PreferenceProfile.__dataclass_params__.frozen is True


def test_preference_profile_empty_profile_starts_with_no_signals():
    profile = _empty_profile()

    assert _profile_signals(profile) == {}


def test_preference_profile_rejects_mutation():
    profile = _empty_profile()

    with pytest.raises(FrozenInstanceError):
        profile.signals = {}


def test_preference_profile_signals_property_returns_defensive_copy():
    profile = _empty_profile()

    exported = _profile_signals(profile)
    exported["tag"] = {"reference": 99}

    assert _profile_signals(profile) == {}


def test_record_feedback_returns_new_profile_for_accept_action():
    from omd.preferences import record_feedback

    original = _empty_profile()

    updated = record_feedback(original, "accept", "tag", "reference")

    assert updated is not original


def test_record_feedback_does_not_mutate_original_profile():
    from omd.preferences import record_feedback

    original = _empty_profile()

    updated = record_feedback(original, "accept", "tag", "reference")

    assert _profile_signals(original) == {}
    assert _profile_signals(updated)["tag"]["reference"] == 1


def test_record_feedback_accept_action_increments_signal_weight():
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", "tag", "reference")
    updated = record_feedback(profile, "accept", "tag", "reference")

    assert _profile_signals(updated)["tag"]["reference"] == 2


def test_record_feedback_reject_action_decrements_signal_weight():
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", "tag", "reference")
    updated = record_feedback(profile, "reject", "tag", "reference")

    assert _profile_signals(updated)["tag"]["reference"] == 0


def test_record_feedback_edit_action_penalizes_old_value():
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", "output_style", "bullet_list")
    updated = record_feedback(
        profile,
        "edit",
        "output_style",
        "bullet_list",
        replacement="short_paragraph",
    )

    assert _profile_signals(updated)["output_style"]["bullet_list"] == 0


def test_record_feedback_edit_action_promotes_replacement_value():
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", "output_style", "bullet_list")
    updated = record_feedback(
        profile,
        "edit",
        "output_style",
        "bullet_list",
        replacement="short_paragraph",
    )

    assert _profile_signals(updated)["output_style"]["short_paragraph"] == 1


def test_record_feedback_rejects_non_explicit_action():
    from omd.preferences import record_feedback

    with pytest.raises(ValueError, match="action"):
        record_feedback(_empty_profile(), "suggested", "tag", "reference")


@pytest.mark.parametrize(
    "signal_kind",
    ["field_name", "summary", "title", "topic", "persona"],
)
def test_record_feedback_rejects_unknown_signal_kind(signal_kind):
    from omd.preferences import record_feedback

    with pytest.raises(ValueError, match="signal_kind"):
        record_feedback(_empty_profile(), "accept", signal_kind, "reference")


@pytest.mark.parametrize(
    ("signal_kind", "value"),
    [
        ("tag", "reference"),
        ("output_style", "short_paragraph"),
        ("note_length", "compact"),
        ("link_style", "wiki"),
        ("source_type", "webpage"),
    ],
)
def test_record_feedback_accepts_supported_signal_kind(signal_kind, value):
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", signal_kind, value)

    assert _profile_signals(profile)[signal_kind][value] == 1


def test_record_feedback_accepts_all_capture_source_types():
    from omd.capture import SOURCE_FOLDERS
    from omd.preferences import record_feedback

    for source_type in (*SOURCE_FOLDERS, "video"):
        profile = record_feedback(_empty_profile(), "accept", "source_type", source_type)

        assert _profile_signals(profile)["source_type"][source_type] == 1


def test_record_feedback_normalizes_legacy_x_source_type():
    from omd.preferences import record_feedback

    profile = record_feedback(_empty_profile(), "accept", "source_type", "x")

    assert _profile_signals(profile)["source_type"] == {"xpost": 1}


@pytest.mark.parametrize(
    "value",
    [
        "First paragraph of the article copied verbatim into preferences.",
        "/Users/example/Obsidian/notes/example.md",
        "cookie.sqlite",
        "browser_cookie_export",
        "http://127.0.0.1:11434/api/generate",
        "sk-test-secret-token",
    ],
)
def test_record_feedback_rejects_sensitive_or_raw_text_like_values(value):
    from omd.preferences import record_feedback

    with pytest.raises(ValueError, match="value"):
        record_feedback(_empty_profile(), "accept", "tag", value)


def test_record_feedback_rejects_sensitive_replacement_values():
    from omd.preferences import record_feedback

    with pytest.raises(ValueError, match="replacement"):
        record_feedback(
            _empty_profile(),
            "edit",
            "tag",
            "reference",
            replacement="/Users/example/secret.md",
        )


def test_reset_preferences_returns_empty_profile():
    from omd.preferences import record_feedback, reset_preferences

    profile = record_feedback(_empty_profile(), "accept", "tag", "reference")

    assert reset_preferences(profile) == _empty_profile()


def test_preference_profile_json_round_trip_preserves_rankings():
    from omd.preferences import PreferenceProfile, record_feedback

    profile = record_feedback(_empty_profile(), "accept", "tag", "reference")
    profile = record_feedback(profile, "accept", "note_length", "compact")

    restored = PreferenceProfile.from_json(profile.to_json())

    assert restored == profile


def test_preference_profile_from_json_returns_empty_profile_for_corrupt_json():
    from omd.preferences import PreferenceProfile

    with pytest.warns(RuntimeWarning, match="preference"):
        restored = PreferenceProfile.from_json("{not json")

    assert restored == PreferenceProfile.empty_profile()


def test_preference_profile_from_json_returns_empty_profile_for_unknown_schema_version():
    from omd.preferences import PreferenceProfile

    with pytest.warns(RuntimeWarning, match="preference"):
        restored = PreferenceProfile.from_json(
            '{"schema_version":999,"signals":{"tag":{"reference":1}}}'
        )

    assert restored == PreferenceProfile.empty_profile()


def test_save_and_load_preference_profile_round_trip(tmp_path):
    from omd.preferences import load_preference_profile, record_feedback, save_preference_profile

    path = tmp_path / "state" / "preferences.json"
    profile = record_feedback(_empty_profile(), "accept", "tag", "reference")

    save_preference_profile(path, profile)

    assert load_preference_profile(path) == profile


def test_load_preference_profile_returns_empty_when_file_is_missing(tmp_path):
    from omd.preferences import load_preference_profile

    assert load_preference_profile(tmp_path / "missing.json") == _empty_profile()


def test_reset_stored_preferences_removes_local_state(tmp_path):
    from omd.preferences import reset_stored_preferences, save_preference_profile

    path = tmp_path / "preferences.json"
    save_preference_profile(path, _empty_profile())

    reset_stored_preferences(path)

    assert not path.exists()


@pytest.mark.parametrize(
    "value",
    ["alice@example.com", r"C:\Users\alice\notes.txt", "eyJhbGciOiJIUzI1NiJ9.payload.signature"],
)
def test_record_feedback_rejects_identifier_and_secret_like_values(value):
    from omd.preferences import record_feedback

    with pytest.raises(ValueError, match="value"):
        record_feedback(_empty_profile(), "accept", "tag", value)


def test_load_preference_profile_warns_and_falls_back_for_corrupt_file(tmp_path):
    from omd.preferences import load_preference_profile

    path = tmp_path / "preferences.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="preference"):
        profile = load_preference_profile(path)

    assert profile == _empty_profile()
