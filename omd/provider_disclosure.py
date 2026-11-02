"""Fail-closed disclosure metadata for task-scoped hosted AI requests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil


_DEFAULT_MAX_AGE_DAYS = 90
_HOSTED_PROVIDERS = ("openai", "anthropic", "deepseek")


@dataclass(frozen=True)
class ProviderDisclosure:
    provider: str
    destination_domain: str
    policy_url: str
    data_handling_summary: str
    reviewed_on: date
    # Indicates the adapter's store=false setting, not provider-wide zero retention.
    storage_disabled: bool
    verified: bool = True


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

    code = "disclosure_unavailable"

    def __init__(
        self, message: str = "provider disclosure is unavailable", *,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider if provider in _HOSTED_PROVIDERS else None

    def to_dict(self) -> dict[str, str | None]:
        """Export only bounded metadata, never exception text or source content."""
        return {"provider": self.provider, "code": self.code}


_DISCLOSURES = {
    "openai": ProviderDisclosure(
        provider="openai",
        destination_domain="api.openai.com",
        policy_url="https://developers.openai.com/api/docs/guides/your-data",
        data_handling_summary=(
            "Request content is sent to api.openai.com/v1/responses. API content "
            "is not used for training by default unless you opt in. OMD sets "
            "store=false, but this is not zero data retention: abuse-monitoring "
            "logs may retain content for up to 30 days or longer for legal or "
            "safety reasons. Model and feature retention, including prompt "
            "caching, may also apply."
        ),
        reviewed_on=date(2026, 11, 2),
        storage_disabled=True,
    ),
    "anthropic": ProviderDisclosure(
        provider="anthropic",
        destination_domain="api.anthropic.com",
        policy_url=(
            "https://privacy.claude.com/en/articles/"
            "7996866-how-long-do-you-store-my-organization-s-data"
        ),
        data_handling_summary=(
            "Request content is sent to api.anthropic.com/v1/messages. Commercial "
            "API inputs and outputs are not used for training by default unless "
            "you provide feedback or otherwise permit it. Standard API retention "
            "deletes inputs and outputs within 30 days, with agreement, "
            "policy-enforcement, legal, model, and feature exceptions. OMD does "
            "not configure zero data retention."
        ),
        reviewed_on=date(2026, 11, 2),
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
            "DeepSeek's general privacy policy describes processing and storage "
            "in the People's Republic of China and retention that varies by "
            "purpose. Its scope excludes downstream application end-user "
            "processing rules. The reviewed official API terms do not establish "
            "a verifiable API input/output retention policy for OMD Vault "
            "evidence. Hosted requests remain unavailable pending clarification."
        ),
        reviewed_on=date(2026, 11, 2),
        storage_disabled=False,
        verified=False,
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
    checked_on = _checked_on(as_of)
    provider = provider.strip().lower()
    disclosure = _DISCLOSURES.get(provider) if provider in _HOSTED_PROVIDERS else None
    reason = _unavailable_reason(disclosure, checked_on, max_age_days)
    if reason is not None:
        message = (
            "provider disclosure is out of date"
            if reason in {"expired", "not_yet_reviewed"}
            else "provider disclosure is unavailable"
        )
        raise ProviderDisclosureUnavailableError(
            message, provider=provider,
        )
    assert disclosure is not None
    return disclosure


def _checked_on(as_of: date | None) -> date:
    checked_on = date.today() if as_of is None else as_of
    if type(checked_on) is not date:
        raise TypeError("as_of must be a date")
    return checked_on


def _unavailable_reason(
    disclosure: ProviderDisclosure | None, checked_on: date, max_age_days: int,
) -> str | None:
    if disclosure is None or not disclosure.policy_url or not disclosure.data_handling_summary:
        return "missing"
    age_days = (checked_on - disclosure.reviewed_on).days
    if age_days < 0:
        return "not_yet_reviewed"
    if age_days > max_age_days:
        return "expired"
    if not disclosure.verified:
        return "unverified"
    return None


def disclosure_readiness(*, as_of: date | None = None) -> dict[str, object]:
    """Local-only preflight: no evidence, credentials, model checks, or network."""
    checked_on = _checked_on(as_of)
    providers = {}
    for provider in _HOSTED_PROVIDERS:
        disclosure = _DISCLOSURES.get(provider)
        reason = _unavailable_reason(disclosure, checked_on, _DEFAULT_MAX_AGE_DAYS)
        providers[provider] = {
            "status": "ready" if reason is None else "unavailable",
            "code": None if reason is None else "disclosure_unavailable",
            "reason": reason,
            "reviewed_on": disclosure.reviewed_on.isoformat() if disclosure else None,
            "expires_on": (
                (disclosure.reviewed_on + timedelta(days=_DEFAULT_MAX_AGE_DAYS)).isoformat()
                if disclosure else None
            ),
            "policy_url": disclosure.policy_url if disclosure else None,
        }
    return {
        "schema_version": 1,
        "checked_on": checked_on.isoformat(),
        "max_age_days": _DEFAULT_MAX_AGE_DAYS,
        "providers": providers,
    }


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


def check_disclosures(*, as_of: date | None = None, warn_within_days: int = 14) -> dict[str, object]:
    """Flag overdue reviews, including recent audits that left a provider blocked."""
    if type(warn_within_days) is not int or warn_within_days < 0:
        raise ValueError("warn_within_days must be a non-negative integer")
    checked_on = _checked_on(as_of)
    report = disclosure_readiness(as_of=checked_on)
    review_due = []
    for provider in _HOSTED_PROVIDERS:
        disclosure = _DISCLOSURES.get(provider)
        reason = _unavailable_reason(disclosure, checked_on, _DEFAULT_MAX_AGE_DAYS)
        if reason in {"missing", "expired", "not_yet_reviewed"} or (
            disclosure is not None
            and (disclosure.reviewed_on + timedelta(days=_DEFAULT_MAX_AGE_DAYS) - checked_on).days
            <= warn_within_days
        ):
            review_due.append(provider)
    return {**report, "warn_within_days": warn_within_days, "review_due": review_due}


def main(argv: list[str] | None = None) -> int:
    """Dependency-free maintenance check; never renews a review automatically."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Check Hosted AI disclosure review freshness")
    parser.add_argument("--as-of", type=date.fromisoformat, help="ISO date for an offline simulation")
    parser.add_argument("--warn-within-days", type=int, default=14)
    args = parser.parse_args(argv)
    if args.warn_within_days < 0:
        parser.error("--warn-within-days must be non-negative")
    report = check_disclosures(as_of=args.as_of, warn_within_days=args.warn_within_days)
    print(json.dumps(report, sort_keys=True))
    for provider, status in report["providers"].items():
        if status["status"] == "unavailable":
            print(f"WARNING: {provider}: disclosure_unavailable ({status['reason']}); nothing sent.",
                  file=sys.stderr)
    if report["review_due"]:
        print("ERROR: disclosure review due: " + ", ".join(report["review_due"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
