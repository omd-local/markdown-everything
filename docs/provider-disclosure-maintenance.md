# Hosted AI disclosure maintenance

OMD must have a current, verified description of provider data handling before
preparing or executing a Hosted AI request. An unavailable disclosure blocks the
request before loading credentials, validating a model, or calling the provider.
It does not authorize sending Vault evidence to investigate the failure.

## Official policy review: 2026-11-02

Each provider below was reviewed independently on **2026-11-02**. The same
calendar date records three separate reviews; each provider owns its
`reviewed_on` value in `omd/provider_disclosure.py`.

| Provider | Result | Official source and supported claims |
| --- | --- | --- |
| OpenAI | `ready` | [API data controls](https://developers.openai.com/api/docs/guides/your-data): API content is not used for training by default unless the customer opts in. Abuse-monitoring logs may contain inputs and outputs and are normally retained for up to 30 days, with legal or safety exceptions. Responses application-state storage, prompt caching, and other model or feature policies have separate rules. |
| Anthropic | `ready` | [Organization data retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data): standard commercial API inputs and outputs are deleted within 30 days, subject to agreement, feature, policy-enforcement, and legal exceptions. Flagged content and feedback can have longer retention. |
| DeepSeek | `unavailable` | [Privacy policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html): the general policy describes processing and storage in the People's Republic of China and retention varying by purpose. It expressly excludes the rules governing downstream developer-application end-user personal data. It therefore does not establish how OMD Vault evidence sent through the API is retained. |

The following supplementary official sources were also reviewed on
**2026-11-02**:

- Anthropic's [commercial model-training policy](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training)
  supports the no-training-by-default statement, with feedback and explicit
  permission exceptions.
- Anthropic's [covered-model retention policy](https://privacy.claude.com/en/articles/15425996-data-retention-practices-for-covered-models)
  describes mandatory safety retention for selected models, including exceptions
  to some zero-data-retention arrangements. Its [API retention documentation](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
  describes endpoint and feature distinctions, including schema caching.
- DeepSeek's [Open Platform terms](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)
  describe downstream developer responsibilities and scope limitations but do
  not establish a verifiable API input/output retention period for OMD's use.
  The [API FAQ](https://api-docs.deepseek.com/faq/) did not yield retrievable policy
  content during this review and is not evidence for a retention claim.

OMD's OpenAI adapter uses `/v1/responses` with `store=false`. This setting is not
a promise of zero retention: abuse-monitoring logs, prompt caches, and applicable
model or feature policies remain relevant. `storage_disabled` records this
adapter setting only. OMD does not configure a provider-approved zero-data-retention
agreement. The Anthropic adapter uses `/v1/messages`; its structured-output schema
does not establish zero retention either.

DeepSeek has `verified=False`. Its fresh review date records an audit that could
not verify the applicable policy, not permission to send data. Keep it blocked
until official sources establish the API data-handling statements needed for
OMD's downstream application. This conclusion does not claim that no such policy
exists; the reviewed sources do not resolve it.

## Freshness and renewal

The default lifetime remains 90 days, inclusive. A verified disclosure is usable
on `reviewed_on` through `reviewed_on + 90 days`. Future-dated reviews, missing
records, empty policy metadata, unverified records, and older reviews fail
closed. Unknown providers also fail closed.

For the reviews above, **2027-01-31** is the last usable day for OpenAI and
Anthropic; **2027-02-01** is the first expired day. DeepSeek remains unavailable
throughout. The default maintenance gate starts failing on **2027-01-17**, 14 days
before the last usable day.

To renew a provider:

1. Read its current official policy pages and the rules for the endpoint and
   features the adapter actually uses. Do not rely on search snippets or
   third-party summaries.
2. Record the official URLs, review date, supported claims, and unresolved
   limitations here. Update only that provider's summary and `reviewed_on`.
3. Set `verified=True` only when the applicable statements can be established.
   Otherwise preserve `verified=False` and document the missing evidence.
4. Run boundary, error-sanitization, capability-contract, and maintenance tests,
   followed by the repository checks. Use synthetic source text and injected
   dates; a live Hosted AI request is not required for policy verification.

Never automatically advance `reviewed_on`, extend the 90-day window to bypass
failure, or treat a fresh date alone as verification.

## Maintenance gate

Run the dependency-free check locally:

```sh
make check-disclosures
python -m omd.provider_disclosure --as-of 2026-11-02
python -m omd.provider_disclosure --as-of 2027-01-17
python -m omd.provider_disclosure --as-of 2027-02-01
```

Without `--as-of`, the check uses the machine's current date. The flag supports
offline simulations and regression tests; it does not renew a disclosure.
`--warn-within-days` defaults to 14. JSON goes to stdout, warnings and failures to
stderr. Exit status is 1 when a review is missing, future-dated, expired, or due
within the warning interval; otherwise it is 0.

A recently reviewed, deliberately unverified provider emits a warning but does
not by itself fail this maintenance check. It still blocks all Hosted AI
requests and becomes review-due on the same schedule. This distinguishes an
explicitly documented unavailable provider from an unattended policy expiry.

`make release-tag` depends on this check. The CI disclosure job also runs on
push/PR events, manual dispatch, and a weekly Monday 09:00 UTC schedule. Scheduled
runs execute this lightweight job without running the other test/build jobs.
GitHub's schedule becomes operational only after the workflow is present on the
default branch and Actions is enabled. Monitor failed workflow notifications;
the schedule is an early warning, not a substitute for runtime fail-closed checks.

## Stable error metadata

The public exception types remain compatible:

- `prepare_text_task()` raises `ProviderDisclosureUnavailableError`.
- `execute_text_task()` translates the same condition to `AIServiceError` before
  accessing credentials, the model validator, or the provider requester.

Both expose `provider`, `code`, and `to_dict()`. For example:

```json
{"provider": "openai", "code": "disclosure_unavailable"}
```

The disclosure exception uses `provider: null` for unknown provider inputs.
Adapters should serialize `to_dict()` for machine error metadata. Do not
serialize exception messages, arguments, causes, or arbitrary attributes:
those are not the safe payload contract and may contain private data in other
error paths. The safe disclosure payload contains no API key, source text,
Vault evidence, or HTTP response body. Existing consent, cancellation, and task
validation failures can still take precedence when those preconditions fail.

## Capabilities contract

Protocol v1 permits additive fields. `omd capabilities --json` now includes
`hosted_ai_disclosure` with its own `schema_version: 1`, `checked_on`,
`max_age_days: 90`, and a `providers` object keyed by `openai`, `anthropic`, and
`deepseek`. Each provider record has exactly:

```json
{
  "status": "ready",
  "code": null,
  "reason": null,
  "reviewed_on": "2026-11-02",
  "expires_on": "2027-01-31",
  "policy_url": "https://developers.openai.com/api/docs/guides/your-data"
}
```

Unavailable records use `status: "unavailable"` and
`code: "disclosure_unavailable"`. Reasons are `missing`, `not_yet_reviewed`,
`expired`, or `unverified`; dates and URL are null when the record is absent.
This block is computed locally without evidence, credentials, model validation,
or provider network access. Readiness is neither consent nor proof of model
availability. The service checks disclosure again at preparation and execution,
so an earlier capability result cannot authorize a later expired request.

## Versioning and client follow-up

This backend change requires a distinct package version. Release `0.3.0b4` distinguishes this fix from the previously published
`0.3.0b2` and `0.3.0b3` identifiers. The wheel and source distribution embed
the release commit in `omd/_build_info.py`; capabilities report it as
`build_revision` without requiring an environment variable. Protocol v1 remains unchanged because
capabilities and safe error metadata are additive. Reinstalling the same unpatched version does not resolve this failure.

OMD Home still needs its own integration change: query disclosure readiness
before collecting Vault evidence, stop immediately with zero evidence when
unavailable, and handle `disclosure_unavailable` from both preparation and
execution. Show a clear **"Nothing was sent"** error with the provider and policy
review context instead of the generic bridge-failed message. Do not fall back to
another provider or retry by sending evidence. An absent or unsupported
readiness contract needs a safe compatibility path, not an assumption of
readiness. These client changes are not implemented by this backend update.
