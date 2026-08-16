from __future__ import annotations

from omd import _models


GIB = 1024**3


def test_detect_total_memory_uses_environment_override(monkeypatch):
    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")

    assert _models.detect_total_memory_bytes() == 16 * GIB


def test_detect_total_memory_ignores_non_finite_environment_override(monkeypatch):
    values = {"SC_PHYS_PAGES": 1024, "SC_PAGE_SIZE": 4096}
    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "inf")
    monkeypatch.setattr(_models.os, "sysconf", lambda name: values[name])

    assert _models.detect_total_memory_bytes() == 1024 * 4096


def test_local_text_model_recommendation_maps_memory_to_conservative_model_tiers():
    cases = (
        (8, "qwen2.5:1.5b-instruct", 1.5),
        (12, "qwen2.5:3b-instruct", 3.0),
        (16, "qwen3:4b-instruct", 4.0),
        (24, "qwen2.5:7b-instruct", 7.0),
        (32, "qwen2.5:7b-instruct", 7.0),
        (48, "qwen2.5:14b-instruct", 14.0),
        (64, "qwen2.5:14b-instruct", 14.0),
    )

    for memory_gb, model, max_parameters in cases:
        recommendation = _models.model_recommendation_for_memory(memory_gb * GIB)
        assert recommendation.model == model
        assert recommendation.max_parameters_billions == max_parameters


def test_local_text_model_recommendation_uses_safe_fallback_when_memory_is_unknown():
    recommendation = _models.model_recommendation_for_memory(None)

    assert recommendation.model == "qwen3:4b-instruct"
    assert recommendation.max_parameters_billions == 4.0


def test_assess_local_text_model_reports_ready_when_installed_and_within_memory_tier():
    from omd._models import assess_local_text_model

    assessment = assess_local_text_model(
        "qwen2.5:3b-instruct",
        installed_models={"qwen2.5:3b-instruct"},
        total_memory_bytes=16 * GIB,
    )

    assert assessment.status == "ready"
    assert assessment.installed is True
    assert assessment.model_parameters_billions == 3.0
    assert assessment.max_parameters_billions == 4.0


def test_assess_local_text_model_warns_when_model_exceeds_machine_tier():
    from omd._models import assess_local_text_model

    assessment = assess_local_text_model(
        "qwen2.5:14b-instruct",
        installed_models={"qwen2.5:14b-instruct"},
        total_memory_bytes=16 * GIB,
    )

    assert assessment.status == "too_large"
    assert assessment.installed is True
    assert "14" in assessment.reason
    assert assessment.recommended_model == "qwen3:4b-instruct"


def test_assess_local_text_model_never_claims_uninstalled_model_is_ready():
    from omd._models import assess_local_text_model

    assessment = assess_local_text_model(
        "qwen3:4b-instruct",
        installed_models=set(),
        total_memory_bytes=24 * GIB,
    )

    assert assessment.status == "missing"
    assert assessment.installed is False


def test_assess_local_text_model_marks_unknown_size_as_advisory_unknown():
    from omd._models import assess_local_text_model

    assessment = assess_local_text_model(
        "custom-instruct:latest",
        installed_models={"custom-instruct:latest"},
        total_memory_bytes=24 * GIB,
    )

    assert assessment.status == "unknown_size"
    assert assessment.installed is True


def test_assess_local_text_model_rejects_thinking_only_alias_even_when_installed():
    from omd._models import assess_local_text_model

    assessment = assess_local_text_model(
        "qwen3:4b",
        installed_models={"qwen3:4b"},
        total_memory_bytes=24 * GIB,
    )

    assert assessment.status == "incompatible"
    assert "thinking-only" in assessment.reason


def test_model_parameter_billions_reads_ollama_parameter_tag():
    assert _models.model_parameter_billions("qwen2.5:14b-instruct-q4_K_M") == 14.0
    assert _models.model_parameter_billions("custom:latest") is None
