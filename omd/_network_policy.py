"""Network trust-boundary checks for hosted and agent-facing entrypoints."""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import urllib.request
from contextlib import contextmanager
from typing import Callable, Iterator
from urllib.parse import urlsplit

PUBLIC_NETWORK_POLICY_ENV = "OMD_NETWORK_POLICY"
PUBLIC_NETWORK_POLICY_VALUE = "public"
# Bridge clients use this version to require connection-time loopback pinning.
LOCAL_OLLAMA_TRANSPORT_VERSION = 1

Resolver = Callable[..., list[tuple]]
_SYSTEM_GETADDRINFO = socket.getaddrinfo


def public_network_policy_enabled() -> bool:
    return os.environ.get(PUBLIC_NETWORK_POLICY_ENV, "").strip().lower() == PUBLIC_NETWORK_POLICY_VALUE


def _public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_global
        and not ip.is_link_local
        and not ip.is_loopback
        and not ip.is_multicast
        and not ip.is_private
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def public_only_getaddrinfo(host, port, *args, **kwargs):
    """Resolve a connection target and reject non-public answers at connect time."""
    answers = _SYSTEM_GETADDRINFO(host, port, *args, **kwargs)
    addresses = {str(answer[4][0]) for answer in answers if len(answer) >= 5 and answer[4]}
    if not addresses or any(not _public_address(address) for address in addresses):
        raise socket.gaierror(
            socket.EAI_NONAME,
            "Hosted and agent-facing connections must resolve to the public internet.",
        )
    return answers


def validate_http_url(url: str) -> None:
    """Require an absolute credential-free HTTP(S) URL without control characters."""
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("Only absolute HTTP(S) URLs are allowed.")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("Only absolute HTTP(S) URLs are allowed.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port.") from exc


def validate_public_http_url(url: str, *, resolver: Resolver | None = None) -> None:
    """Reject URL destinations that are not resolved public HTTP(S) addresses."""
    validate_http_url(url)
    parsed = urlsplit(url)
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL contains an invalid port.") from exc

    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".home.arpa")
    ):
        raise ValueError("Hosted and agent-facing inputs must resolve to the public internet.")

    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not _public_address(str(literal)):
            raise ValueError("Hosted and agent-facing inputs must resolve to the public internet.")
        return

    lookup = resolver or socket.getaddrinfo
    try:
        answers = lookup(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise ValueError(f"Could not resolve URL host: {hostname}") from exc
    addresses = {str(answer[4][0]) for answer in answers if len(answer) >= 5 and answer[4]}
    if not addresses:
        raise ValueError(f"Could not resolve URL host: {hostname}")
    if any(not _public_address(address) for address in addresses):
        raise ValueError("Hosted and agent-facing inputs must resolve to the public internet.")


class _PublicOnlyHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, *, resolver: Resolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver

    def http_open(self, request):
        validate_public_http_url(request.full_url, resolver=self._resolver)
        return super().http_open(request)


class _PublicOnlyHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, resolver: Resolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver

    def https_open(self, request):
        validate_public_http_url(request.full_url, resolver=self._resolver)
        return super().https_open(request)


class PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, resolver: Resolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl, resolver=self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep credentials and source bodies bound to their validated destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_no_redirect_opener():
    return urllib.request.build_opener(NoRedirectHandler())


def _is_loopback_host(hostname: str) -> bool:
    hostname = hostname.rstrip(".").lower()
    try:
        return ipaddress.ip_address(hostname.split("%", 1)[0]).is_loopback
    except ValueError:
        return hostname == "localhost" or hostname.endswith(".localhost")


def _is_loopback_socket_address(family: int, address: str) -> bool:
    if family not in {socket.AF_INET, socket.AF_INET6}:
        return False
    try:
        ip = ipaddress.ip_address(socket.inet_pton(family, address))
    except OSError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _create_loopback_connection(destination, timeout, source_address):
    if public_network_policy_enabled():
        raise socket.gaierror(
            socket.EAI_NONAME,
            "Hosted and agent-facing connections must resolve to the public internet.",
        )

    host, port = destination
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(
        socktype != socket.SOCK_STREAM
        or not sockaddr
        or not _is_loopback_socket_address(family, sockaddr[0])
        for family, socktype, _, _, sockaddr in answers
    ):
        raise socket.gaierror(socket.EAI_NONAME, "Local Ollama must resolve only to loopback addresses.")

    last_error: OSError
    for family, socktype, protocol, _, sockaddr in answers:
        sock = None
        try:
            sock = socket.socket(family, socktype, protocol)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    raise last_error


class _LoopbackConnectionMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep the original authority for HTTP Host and TLS hostname verification.
        self._create_connection = _create_loopback_connection


class _LoopbackHTTPConnection(_LoopbackConnectionMixin, http.client.HTTPConnection):
    pass


class _LoopbackHTTPSConnection(_LoopbackConnectionMixin, http.client.HTTPSConnection):
    pass


class _LoopbackHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(_LoopbackHTTPConnection, request)


class _LoopbackHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_LoopbackHTTPSConnection, request, context=self._context)


def build_ollama_opener(endpoint: str):
    """Use a loopback-only transport for local URLs and no redirects for any Ollama URL."""
    if not _is_loopback_host(urlsplit(endpoint).hostname or ""):
        return build_no_redirect_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirectHandler(),
        _LoopbackHTTPHandler(),
        _LoopbackHTTPSHandler(),
    )


def build_public_network_opener(*, resolver: Resolver | None = None):
    return urllib.request.build_opener(
        _PublicOnlyHTTPHandler(resolver=resolver),
        _PublicOnlyHTTPSHandler(resolver=resolver),
        PublicOnlyRedirectHandler(resolver=resolver),
    )


def install_public_network_policy() -> None:
    urllib.request.install_opener(build_public_network_opener())
    socket.getaddrinfo = public_only_getaddrinfo


@contextmanager
def public_network_policy_scope(*, enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
        return
    previous_opener = urllib.request._opener
    previous_resolver = socket.getaddrinfo
    previous_env = os.environ.get(PUBLIC_NETWORK_POLICY_ENV)
    os.environ[PUBLIC_NETWORK_POLICY_ENV] = PUBLIC_NETWORK_POLICY_VALUE
    install_public_network_policy()
    try:
        yield
    finally:
        urllib.request._opener = previous_opener
        socket.getaddrinfo = previous_resolver
        if previous_env is None:
            os.environ.pop(PUBLIC_NETWORK_POLICY_ENV, None)
        else:
            os.environ[PUBLIC_NETWORK_POLICY_ENV] = previous_env


def validate_ollama_host(host: str, *, allow_remote: bool = False) -> None:
    """Keep raw note content local unless a remote HTTPS endpoint is explicit."""
    raw = host.strip()
    if not raw:
        raise ValueError("Ollama host must not be empty.")
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    hostname = (parsed.hostname or "").rstrip(".")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Ollama host must be a URL without embedded credentials.")
    # Accessing port rejects non-numeric and out-of-range values before any request.
    if parsed.port == 0 or parsed.netloc.endswith(":"):
        raise ValueError("Ollama port must be between 1 and 65535.")
    if _is_loopback_host(hostname):
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Local Ollama host must use HTTP or HTTPS.")
        return
    if not allow_remote:
        raise ValueError(
            "Remote Ollama hosts require explicit opt-in because source content is sent to the model endpoint."
        )
    if parsed.scheme.lower() != "https":
        raise ValueError("An explicitly allowed remote Ollama host must use HTTPS.")


def validate_ollama_base_url(host: str, *, allow_remote: bool = False) -> None:
    """Validate an Ollama host and require a base URL without routing components."""
    validate_ollama_host(host, allow_remote=allow_remote)
    raw = host.strip()
    absolute = raw if "://" in raw else f"http://{raw}"
    parsed = urlsplit(absolute)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Ollama host must be a base URL without a path, query, or fragment")
