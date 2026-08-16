"""Recommended local model defaults for optional OMD AI steps."""

from dataclasses import dataclass
import math
import os
import re


TEXT_POLISH_MODEL = "qwen3:4b-instruct"
VISION_MODEL = "gemma3:4b"
EMBEDDING_MODEL = "bge-m3"
LOCAL_TEXT_CONTEXT_TOKENS = 4096
GIB = 1024**3


@dataclass(frozen=True)
class LocalModelRecommendation:
    """Conservative local text-model capacity derived from total system memory."""

    model: str
    max_parameters_billions: float
    total_memory_bytes: int | None


@dataclass(frozen=True)
class LocalModelAssessment:
    """Advisory availability and machine-fit result for one selected model."""

    model: str
    status: str
    reason: str
    installed: bool
    recommended_model: str
    max_parameters_billions: float
    model_parameters_billions: float | None
    total_memory_bytes: int | None


_LOCAL_TEXT_MODEL_TIERS = (
    (12, "qwen2.5:1.5b-instruct", 1.5),
    (16, "qwen2.5:3b-instruct", 3.0),
    (24, TEXT_POLISH_MODEL, 4.0),
    (48, "qwen2.5:7b-instruct", 7.0),
    (None, "qwen2.5:14b-instruct", 14.0),
)
_MODEL_PARAMETERS_RE = re.compile(r"(?:^|[:_-])(\d+(?:\.\d+)?)b(?=$|[-_])", re.IGNORECASE)


def detect_total_memory_bytes() -> int | None:
    """Detect stable total RAM without adding a platform-specific dependency."""
    override = os.environ.get("OMD_SYSTEM_MEMORY_GB", "").strip()
    if override:
        try:
            value = float(override)
        except ValueError:
            pass
        else:
            if math.isfinite(value) and value > 0:
                return int(value * GIB)

    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    total = pages * page_size
    return total if total > 0 else None


def model_recommendation_for_memory(total_memory_bytes: int | None) -> LocalModelRecommendation:
    """Map total RAM to a latency-oriented model tier with OS/workload headroom."""
    if total_memory_bytes is None or total_memory_bytes <= 0:
        return LocalModelRecommendation(TEXT_POLISH_MODEL, 4.0, None)

    memory_gib = total_memory_bytes / GIB
    for upper_memory_gib, model, parameters in _LOCAL_TEXT_MODEL_TIERS:
        if upper_memory_gib is None or memory_gib < upper_memory_gib:
            return LocalModelRecommendation(model, parameters, total_memory_bytes)
    raise AssertionError("local text model tiers must include an unbounded final tier")


def local_text_model_recommendation() -> LocalModelRecommendation:
    """Return the recommendation for the current machine."""
    return model_recommendation_for_memory(detect_total_memory_bytes())


def recommended_local_text_model() -> str:
    """Return the current machine's recommended Ollama text-model tag."""
    return local_text_model_recommendation().model


def model_parameter_billions(model: str) -> float | None:
    """Read a parameter count such as 4B or 14B from an Ollama model tag."""
    match = _MODEL_PARAMETERS_RE.search(model.strip())
    return float(match.group(1)) if match else None


def local_text_model_issue(model: str) -> str | None:
    """Return a user-facing incompatibility reason for known local models."""
    normalized = model.strip().lower()
    if normalized == "qwen3:4b" or "-thinking" in normalized:
        replacement = recommended_local_text_model()
        return (
            f"{model} is a thinking-only model alias and may spend the whole output budget "
            f"on reasoning; use {replacement} for bounded OMD text generation"
        )
    return None


def assess_local_text_model(
    model: str,
    *,
    installed_models: set[str] | frozenset[str],
    total_memory_bytes: int | None = None,
) -> LocalModelAssessment:
    """Assess the selected tag without downloading or silently substituting a model."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(installed_models, (set, frozenset)) or any(
        not isinstance(value, str) for value in installed_models
    ):
        raise TypeError("installed_models must be a set of strings")

    selected = model.strip()
    memory = detect_total_memory_bytes() if total_memory_bytes is None else total_memory_bytes
    recommendation = model_recommendation_for_memory(memory)
    parameters = model_parameter_billions(selected)
    installed = selected in installed_models

    if not installed:
        status = "missing"
        reason = f"{selected} is not installed in Ollama"
    elif issue := local_text_model_issue(selected):
        status = "incompatible"
        reason = issue
    elif parameters is None:
        status = "unknown_size"
        reason = (
            f"OMD cannot infer the parameter size of {selected}; machine fit is unknown"
        )
    elif parameters > recommendation.max_parameters_billions:
        status = "too_large"
        reason = (
            f"{selected} is approximately {parameters:g}B, above this machine's "
            f"conservative {recommendation.max_parameters_billions:g}B text-model tier"
        )
    else:
        status = "ready"
        reason = f"{selected} is installed and within the conservative machine tier"

    return LocalModelAssessment(
        model=selected,
        status=status,
        reason=reason,
        installed=installed,
        recommended_model=recommendation.model,
        max_parameters_billions=recommendation.max_parameters_billions,
        model_parameters_billions=parameters,
        total_memory_bytes=memory,
    )


def estimated_text_tokens(text: str) -> int:
    """Conservatively estimate tokens for mixed CJK and Latin text."""
    cjk_chars = sum(
        1
        for char in text
        if "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
    )
    other_chars = max(0, len(text) - cjk_chars)
    return max(1, cjk_chars + (other_chars + 3) // 4)


def bounded_edit_output_budget(text: str) -> int:
    """Return a bounded Ollama output budget for copy-editing text."""
    return min(2048, max(256, int(estimated_text_tokens(text) * 1.35) + 64))
