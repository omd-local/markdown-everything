# Security Policy

OMD processes untrusted files, URLs, OCR, transcripts, and web content. Treat
generated Markdown as data, review AI-generated sections, and keep credentials
out of shared logs and bug reports.

## Supported versions

| Version | Security updates |
| --- | --- |
| `0.3.x` beta | Yes |
| `0.2.x` and earlier | No |

Only the newest beta receives security fixes before the first stable release.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository when it is
available. Include the affected version, entrypoint, reproduction steps,
impact, and the smallest non-sensitive proof of concept that demonstrates the
problem. Do not include cookies, tokens, private documents, internal URLs, or
personal data.

If private reporting is unavailable, open a minimal issue asking for a private
security contact. Do not publish exploit details in a public issue. You can
expect acknowledgement within seven days and a remediation/status update
within fourteen days for a validated report.

## Trust boundaries

- The local UI binds to loopback by default. Do not expose it directly to an
  untrusted network.
- Hosted-demo and MCP URL inputs reject loopback, private, link-local,
  multicast, reserved, and unresolved destinations by default.
- `OMD_MCP_ALLOW_PRIVATE_URLS=1` disables the MCP private-network URL guard.
  Use it only inside a trusted, isolated agent workflow.
- UI model calls are local-only. CLI remote Ollama-compatible endpoints require
  both `--allow-remote-ollama` and HTTPS because source content is sent to that
  endpoint.
- Cookies remain local and must never be attached to a vulnerability report.
- OMD does not bypass paywalls, authentication, access controls, or platform
  restrictions.

Application-level URL checks reduce SSRF risk but are not a replacement for
egress filtering on a public deployment. A hosted service should also block
private/link-local network ranges at the container or network layer.

## Maintainer checks

Before a release:

```bash
python -m pytest -q
python -m compileall -q -x '(^|/)\._' omd tests
python -m pip install -e '.[ui,audit]'
python -m pip check
python -m pip_audit --local --progress-spinner off
```

Agentic penetration tests such as Strix must use an explicitly authorised
target, telemetry disabled for private work, and a sanitized source snapshot
that excludes `.git`, `.venv`, `.cookies`, `.env`, vaults, logs, and generated
outputs. Start with a quick, non-destructive scan. A tool-generated report is
supplementary evidence, not a substitute for review and regression tests.
