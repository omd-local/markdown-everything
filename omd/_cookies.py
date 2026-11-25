"""Bounded Netscape cookies.txt validation for authenticated source adapters.

The public inspection result deliberately contains no cookie values and no
local path.  Adapters that need a Cookie header use ``require_cookie_header``
and keep the returned value inside their provider-specific runtime boundary.
"""
from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

MAX_COOKIE_FILE_BYTES = 1024 * 1024

DOUYIN_COOKIE_DOMAINS = (
    "douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
)
XHS_COOKIE_DOMAINS = (
    "xiaohongshu.com",
    "rednote.com",
)
XHS_URL_DOMAINS = (
    "xiaohongshu.com",
    "xhslink.com",
    "rednote.com",
    "xhscdn.com",
)

_STATUS_COPY = {
    "not_provided": (
        "No cookies.txt file was provided.",
        "Choose a Netscape cookies.txt export for this platform, then retry.",
    ),
    "missing": (
        "The selected cookies.txt file could not be found.",
        "Choose the file again, then retry.",
    ),
    "unreadable": (
        "The selected cookies.txt file could not be read.",
        "Check the file permissions or export a new cookies.txt file, then retry.",
    ),
    "invalid_format": (
        "The selected file is not a valid bounded Netscape cookies.txt export.",
        "Export cookies in Netscape format, then retry.",
    ),
    "wrong_domain": (
        "The cookies.txt file does not contain cookies for this platform.",
        "Sign in to the correct platform, export its cookies, then retry.",
    ),
    "expired": (
        "The cookies.txt file contains only expired cookies for this platform.",
        "Sign in again, export fresh cookies, then retry.",
    ),
    "found": (
        "Usable platform cookies were found.",
        None,
    ),
}

_COOKIE_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_COOKIE_VALUE_EXCLUDED = frozenset('";,\\')


@dataclass(frozen=True)
class _CookieRecord:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires: int
    name: str
    value: str = field(repr=False)


class CookieFileError(ValueError):
    """Safe, structured runtime error without secrets or filesystem paths."""

    def __init__(self, status: str, platform: str):
        self.status = status
        self.platform = platform
        self.kind = f"cookies_{status}"
        copy = _STATUS_COPY.get(status, _STATUS_COPY["invalid_format"])
        message = f"{platform}: {copy[0]}"
        if copy[1]:
            message += f" {copy[1]}"
        super().__init__(message)


def host_matches(host: str, allowed_domains: Iterable[str]) -> bool:
    """Match only an allowlisted domain or one of its subdomains."""
    normalized = host.strip().lower().rstrip(".")
    for allowed in allowed_domains:
        root = str(allowed).strip().lower().lstrip(".").rstrip(".")
        if root and (normalized == root or normalized.endswith("." + root)):
            return True
    return False


def inspect_cookie_file(
    path_value: str | Path | None,
    allowed_domains: Iterable[str] = (),
    *,
    now: float | None = None,
    request_url: str | None = None,
) -> dict[str, object]:
    """Return a serializable, secret-free cookies.txt readiness result.

    ``request_url`` makes readiness use the same domain, path, and secure-cookie
    rules as the provider runtime.  Callers resolving a shortlink later may
    omit it and revalidate against the resolved URL before sending cookies.
    """
    result, _header = _inspect_cookie_file(
        path_value,
        allowed_domains,
        now=now,
        request_url=request_url,
    )
    return result


def require_cookie_header(
    path_value: str | Path | None,
    allowed_domains: Iterable[str],
    *,
    platform: str,
    now: float | None = None,
    request_url: str | None = None,
) -> str:
    """Load active platform cookies or raise a structured safe error."""
    result, header = _inspect_cookie_file(
        path_value,
        allowed_domains,
        now=now,
        request_url=request_url,
    )
    status = str(result["status"])
    if status != "found":
        raise CookieFileError(status, platform)
    return header


def write_scoped_cookie_jar(
    path_value: str | Path | None,
    allowed_domains: Iterable[str],
    destination: str | Path,
    *,
    platform: str,
    now: float | None = None,
) -> Path:
    """Write a private Netscape jar containing active platform cookies only.

    Cookie values remain in a cookie jar so the HTTP client can enforce each
    row's domain, include-subdomains, path, Secure, and expiry attributes.
    The source file is read and validated once; unrelated-domain rows are not
    copied into the provider subprocess boundary.
    """
    target = Path(destination)
    result, _header = _inspect_cookie_file(
        path_value,
        allowed_domains,
        now=now,
        request_url=None,
        jar_destination=target,
    )
    status = str(result["status"])
    if status != "found":
        raise CookieFileError(status, platform)
    return target


def _status(status: str, allowed_domains: tuple[str, ...]) -> dict[str, object]:
    message, action = _STATUS_COPY[status]
    return {
        "status": status,
        "message": message,
        "action": action,
        "required_domains": list(allowed_domains),
    }


def _normalized_domains(allowed_domains: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            domain
            for raw in allowed_domains
            if (domain := str(raw).strip().lower().lstrip(".").rstrip("."))
        )
    )


def _inspect_cookie_file(
    path_value: str | Path | None,
    allowed_domains: Iterable[str],
    *,
    now: float | None,
    request_url: str | None,
    jar_destination: Path | None = None,
) -> tuple[dict[str, object], str]:
    domains = _normalized_domains(allowed_domains)
    if path_value is None or not str(path_value).strip():
        return _status("not_provided", domains), ""

    path = Path(path_value).expanduser()
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            return _status("unreadable", domains), ""
        if file_stat.st_size > MAX_COOKIE_FILE_BYTES:
            return _status("invalid_format", domains), ""
        with path.open("rb") as handle:
            raw = handle.read(MAX_COOKIE_FILE_BYTES + 1)
    except FileNotFoundError:
        return _status("missing", domains), ""
    except PermissionError:
        return _status("unreadable", domains), ""
    except OSError:
        return _status("unreadable", domains), ""

    if len(raw) > MAX_COOKIE_FILE_BYTES or b"\x00" in raw:
        return _status("invalid_format", domains), ""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _status("invalid_format", domains), ""

    parsed: list[_CookieRecord] = []
    # Browser exporters can leave isolated unusable rows beside valid cookies.
    # Keep every accepted row strict and reject the file when none survive.
    for source_line in text.splitlines():
        line = source_line
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue

        parts = line.split("\t", 6)
        if len(parts) != 7:
            continue
        domain, include_subdomains, cookie_path, secure, expires, name, value = parts
        domain = domain.strip().lower().lstrip(".").rstrip(".")
        include_subdomains = include_subdomains.upper()
        secure = secure.upper()
        if (
            not domain
            or ".." in domain
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-."
                for character in domain
            )
            or include_subdomains not in {"TRUE", "FALSE"}
            or not cookie_path.startswith("/")
            or any(ord(character) < 0x20 for character in cookie_path)
            or secure not in {"TRUE", "FALSE"}
            or not name
            or any(character not in _COOKIE_NAME_CHARS for character in name)
            or any(
                not 0x21 <= ord(character) <= 0x7E
                or character in _COOKIE_VALUE_EXCLUDED
                for character in value
            )
        ):
            continue
        try:
            expiry = int(expires)
        except ValueError:
            continue
        if expiry < 0:
            continue
        parsed.append(
            _CookieRecord(
                domain=domain,
                include_subdomains=include_subdomains == "TRUE",
                path=cookie_path,
                secure=secure == "TRUE",
                expires=expiry,
                name=name,
                value=value,
            )
        )

    if not parsed:
        return _status("invalid_format", domains), ""

    matching = [
        item for item in parsed
        if not domains or host_matches(item.domain, domains)
    ]
    if request_url:
        matching = [item for item in matching if _cookie_applies(item, request_url)]
    if not matching:
        return _status("wrong_domain", domains), ""

    current_time = time.time() if now is None else now
    active = [
        item for item in matching
        if item.expires == 0 or item.expires > current_time
    ]
    if not active:
        return _status("expired", domains), ""

    if jar_destination is not None:
        _write_private_cookie_jar(jar_destination, active)

    active.sort(key=lambda item: (len(item.path), len(item.domain)), reverse=True)
    selected: list[_CookieRecord] = []
    seen_names: set[str] = set()
    for item in active:
        if item.name in seen_names:
            continue
        seen_names.add(item.name)
        selected.append(item)
    header = "; ".join(f"{item.name}={item.value}" for item in selected)
    return _status("found", domains), header


def _write_private_cookie_jar(
    destination: Path,
    cookies: list[_CookieRecord],
) -> None:
    lines = ["# Netscape HTTP Cookie File\n"]
    for item in cookies:
        domain = f".{item.domain}" if item.include_subdomains else item.domain
        lines.append(
            "\t".join(
                (
                    domain,
                    "TRUE" if item.include_subdomains else "FALSE",
                    item.path,
                    "TRUE" if item.secure else "FALSE",
                    str(item.expires),
                    item.name,
                    item.value,
                )
            )
            + "\n"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.writelines(lines)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _cookie_applies(cookie: _CookieRecord, request_url: str) -> bool:
    parsed = urlparse(request_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if cookie.include_subdomains:
        domain_matches = host == cookie.domain or host.endswith("." + cookie.domain)
    else:
        domain_matches = host == cookie.domain
    if not domain_matches or (cookie.secure and parsed.scheme != "https"):
        return False
    request_path = parsed.path or "/"
    if request_path == cookie.path:
        return True
    if not request_path.startswith(cookie.path):
        return False
    return cookie.path.endswith("/") or request_path[len(cookie.path):].startswith("/")
