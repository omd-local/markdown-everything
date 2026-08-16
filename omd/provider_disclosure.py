"""Fail-closed disclosure metadata for task-scoped hosted AI requests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil


_DISCLOSURE_REVIEWED_ON = date(2026, 7, 19)
_DEFAULT_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class ProviderDisclosure:
    provider: str
    destination_domain: str
    policy_url: str
    data_handling_summary: str
    reviewed_on: date
    storage_disabled: bool


@dataclass(frozen=True)
class CloudRequestPreview:
    provider: str
    model: str
    capability: str
    destination_domain: str
    character_count: int
    estimated_input_tokens: int
    sends_attachment: bool
    disclosure_reviewed_on: date
    policy_url: str
    data_handling_summary: str


class ProviderDisclosureUnavailableError(RuntimeError):
    """Raised when OMD cannot show current provider handling information."""


_DISCLOSURES = {
    "openai": ProviderDisclosure(
        provider="openai",
        destination_domain="api.openai.com",
        policy_url=(
            "https://platform.openai.com/docs/models/"
            "default-usage-policies-by-endpoint"
        ),
        data_handling_summary=(
            "Request content is sent to api.openai.com. OMD sets store=false "
            "and does not use background mode, hosted files, or vector stores in "
            "this adapter. Abuse-monitoring or endpoint retention may still apply "
            "under the linked current policy."
        ),
        reviewed_on=_DISCLOSURE_REVIEWED_ON,
        storage_disabled=True,
    ),
    "anthropic": ProviderDisclosure(
        provider="anthropic",
        destination_domain="api.anthropic.com",
        policy_url=(
            "https://privacy.anthropic.com/en/articles/"
            "7996866-how-long-do-you-store-my-organization-s-data"
        ),
        data_handling_summary=(
            "Request content is sent to api.anthropic.com. Anthropic's current "
            "commercial API retention policy applies; OMD does not create a "
            "hosted copy. Review the linked policy before consent."
        ),
        reviewed_on=_DISCLOSURE_REVIEWED_ON,
        storage_disabled=False,
    ),
    "deepseek": ProviderDisclosure(
        provider="deepseek",
        destination_domain="api.deepseek.com",
        policy_url=(
            "https://cdn.deepseek.com/policies/en-US/"
            "deepseek-privacy-policy.html"
        ),
        data_handling_summary=(
            "Request content is sent to api.deepseek.com. DeepSeek's published "
            "policy describes processing and storage in the People's Republic of "
            "China and retention that varies by purpose."
        ),
        reviewed_on=_DISCLOSURE_REVIEWED_ON,
        storage_disabled=False,
    ),
}


def get_provider_disclosure(
    provider: str,
    *,
    as_of: date | None = None,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
) -> ProviderDisclosure:
    if not isinstance(provider, str) or not provider.strip():
        raise ProviderDisclosureUnavailableError("provider disclosure is unavailable")
    if type(max_age_days) is not int or max_age_days <= 0:
        raise ValueError("max_age_days must be a positive integer")
    checked_on = as_of or date.today()
    if not isinstance(checked_on, date):
        raise TypeError("as_of must be a date")

    disclosure = _DISCLOSURES.get(provider.strip().lower())
    if disclosure is None:
        raise ProviderDisclosureUnavailableError(
            f"{provider.strip().lower()} provider disclosure is unavailable"
        )
    age_days = (checked_on - disclosure.reviewed_on).days
    if age_days < 0 or age_days > max_age_days:
        raise ProviderDisclosureUnavailableError(
            f"{disclosure.provider} provider disclosure is out of date"
        )
    return disclosure


def build_cloud_request_preview(
    *,
    provider: str,
    model: str,
    capability: str,
    source_text: str,
    sends_attachment: bool,
    as_of: date | None = None,
    max_disclosure_age_days: int = _DEFAULT_MAX_AGE_DAYS,
) -> CloudRequestPreview:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must not be empty")
    if not isinstance(capability, str) or not capability.strip():
        raise ValueError("capability must not be empty")
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if type(sends_attachment) is not bool:
        raise TypeError("sends_attachment must be a boolean")

    disclosure = get_provider_disclosure(
        provider,
        as_of=as_of,
        max_age_days=max_disclosure_age_days,
    )
    return CloudRequestPreview(
        provider=disclosure.provider,
        model=model.strip(),
        capability=capability.strip(),
        destination_domain=disclosure.destination_domain,
        character_count=len(source_text),
        estimated_input_tokens=_estimate_input_tokens(source_text),
        sends_attachment=sends_attachment,
        disclosure_reviewed_on=disclosure.reviewed_on,
        policy_url=disclosure.policy_url,
        data_handling_summary=disclosure.data_handling_summary,
    )


def _estimate_input_tokens(text: str) -> int:
    if not text:
        return 0
    non_ascii = sum(ord(character) > 127 for character in text)
    ascii_characters = len(text) - non_ascii
    return non_ascii + ceil(ascii_characters / 4)
