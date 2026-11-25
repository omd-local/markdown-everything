import io
import socket
import urllib.error
import urllib.request

import pytest

from omd import ollama_runtime
from omd._network_policy import LOCAL_OLLAMA_TRANSPORT_VERSION, public_network_policy_scope, validate_ollama_base_url
from omd.ai_providers import AIProviderError, AIProviderRequest, request_text
from omd.provider_models import ProviderCatalogError, discover_provider_models


_OLLAMA_HOST = "models.localhost"
_OLLAMA_ENDPOINT = f"http://{_OLLAMA_HOST}:11434"
_IPV4_LOOPBACK = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 11434))
_IPV6_LOOPBACK = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", 11434, 0, 0))
_NON_LOOPBACK = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 11434))


def test_local_ollama_transport_advertises_pinned_connections():
    assert LOCAL_OLLAMA_TRANSPORT_VERSION == 1


def _request_json(endpoint=_OLLAMA_ENDPOINT):
    request = urllib.request.Request(f"{endpoint}/api/generate", data=b'{"prompt":"source text"}')
    return ollama_runtime.request_ollama_json(request, timeout=3)


def _request_chat():
    return request_text(
        AIProviderRequest(
            provider="ollama",
            endpoint=_OLLAMA_ENDPOINT,
            model="test",
            task="answer",
            system="system",
            user="source text",
            max_output_tokens=20,
            allow_remote_ollama=True,
        )
    )


def _discover_models():
    return discover_provider_models("ollama", endpoint=_OLLAMA_ENDPOINT, allow_remote_ollama=True)


@pytest.fixture(autouse=True)
def _proxy_environment(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"http": "http://proxy.test:8080", "https": "http://proxy.test:8080"},
    )
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)


@pytest.mark.parametrize("answers", [[], [_NON_LOOPBACK], [_IPV6_LOOPBACK, _NON_LOOPBACK]])
@pytest.mark.parametrize(
    ("request_call", "error"),
    [
        (_request_json, urllib.error.URLError),
        (_request_chat, AIProviderError),
        (_discover_models, ProviderCatalogError),
    ],
)
def test_local_ollama_rejects_non_loopback_resolution_before_connect(
    monkeypatch, answers, request_call, error,
):
    lookups = []

    def resolve(host, port, *args, **kwargs):
        lookups.append((host, port))
        return answers

    def make_socket(*args):
        pytest.fail(f"unexpected socket creation for resolver answers {answers}")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", make_socket)

    with pytest.raises(error) as raised:
        request_call()

    if isinstance(raised.value, urllib.error.URLError):
        assert "loopback" in str(raised.value.reason)
    else:
        assert raised.value.code == "transport_error"
    assert lookups == [(_OLLAMA_HOST, 11434)]


class _FakeSocket:
    def __init__(self, response, *, refuse=False):
        self.response = response
        self.refuse = refuse
        self.connected_to = None
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, timeout):
        assert timeout == 3

    def setsockopt(self, *_args):
        pass

    def connect(self, address):
        self.connected_to = address
        if self.refuse:
            raise ConnectionRefusedError("simulated unavailable address")

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, mode):
        assert mode == "rb"
        return io.BytesIO(self.response)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("port", [11434, 9999, 65535])
def test_local_ollama_bypasses_proxy_and_falls_back_to_verified_ipv4(monkeypatch, port):
    lookups = []
    sockets = []
    response = b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{"result":"ok"}'

    def resolve(host, port, *args, **kwargs):
        lookups.append((host, port))
        return [
            (*_IPV6_LOOPBACK[:4], ("::1", port, 0, 0)),
            (*_IPV4_LOOPBACK[:4], ("127.0.0.1", port)),
        ]

    def make_socket(family, *_args):
        sock = _FakeSocket(response, refuse=family == socket.AF_INET6)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", make_socket)

    assert _request_json(f"http://{_OLLAMA_HOST}:{port}") == {"result": "ok"}
    assert lookups == [(_OLLAMA_HOST, port)]
    assert [sock.connected_to for sock in sockets] == [("::1", port, 0, 0), ("127.0.0.1", port)]
    assert sockets[0].closed
    assert not sockets[0].sent
    assert b"POST /api/generate HTTP/1.1\r\n" in sockets[1].sent
    assert f"Host: models.localhost:{port}\r\n".encode() in sockets[1].sent
    assert b'{"prompt":"source text"}' in sockets[1].sent


@pytest.mark.parametrize("port", ["", "0", "65536", "invalid"])
def test_local_ollama_rejects_invalid_explicit_ports(port):
    with pytest.raises(ValueError, match="(?i)port"):
        validate_ollama_base_url(f"http://localhost:{port}")


def test_local_ollama_does_not_retry_a_post_after_transmission_starts(monkeypatch):
    class BrokenSocket(_FakeSocket):
        def sendall(self, data):
            super().sendall(data)
            if b"source text" in data:
                raise BrokenPipeError("simulated interruption during body transmission")

    sock = BrokenSocket(b"")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kw: [_IPV6_LOOPBACK, _IPV4_LOOPBACK])

    def make_socket(family, *_args):
        assert family == socket.AF_INET6
        return sock

    monkeypatch.setattr(socket, "socket", make_socket)
    with pytest.raises(urllib.error.URLError, match="simulated interruption"):
        _request_json()
    assert sock.sent.count(b'{"prompt":"source text"}') == 1


def test_local_ollama_https_preserves_tls_server_name(monkeypatch):
    import ssl

    response = b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{"result":"ok"}'
    sock = _FakeSocket(response)
    tls_names = []

    def wrap_socket(_context, connected_socket, *, server_hostname):
        assert connected_socket is sock
        assert sock.connected_to == _IPV4_LOOPBACK[4]
        tls_names.append(server_hostname)
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kw: [_IPV4_LOOPBACK])
    monkeypatch.setattr(socket, "socket", lambda *_args: sock)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)

    assert _request_json(f"https://{_OLLAMA_HOST}:11434") == {"result": "ok"}
    assert tls_names == [_OLLAMA_HOST]
    assert b"Host: models.localhost:11434\r\n" in sock.sent


def test_local_ollama_does_not_follow_redirect(monkeypatch):
    lookups = []
    response = (
        b"HTTP/1.1 307 Temporary Redirect\r\n"
        b"Location: http://other.localhost:11434/api/generate\r\n"
        b"Content-Length: 0\r\n\r\n"
    )

    def resolve(host, port, *args, **kwargs):
        lookups.append((host, port))
        return [_IPV4_LOOPBACK]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *_args: _FakeSocket(response))

    with pytest.raises(urllib.error.HTTPError) as raised:
        _request_json()
    raised.value.close()

    assert raised.value.code == 307
    assert lookups == [(_OLLAMA_HOST, 11434)]


def test_opted_in_remote_ollama_keeps_https_and_does_not_follow_redirect(monkeypatch):
    import ssl

    lookups = []
    tls_names = []
    response = (
        b"HTTP/1.1 307 Temporary Redirect\r\n"
        b"Location: https://other.test/api/tags\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    sock = _FakeSocket(response)
    remote_address = ("192.0.2.1", 443)

    def resolve(host, port, *args, **kwargs):
        lookups.append((host, port))
        return [(*_NON_LOOPBACK[:4], remote_address)]

    def wrap_socket(_context, connected_socket, *, server_hostname):
        assert connected_socket is sock
        tls_names.append(server_hostname)
        return sock

    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: True)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *_args: sock)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)

    with pytest.raises(ProviderCatalogError) as raised:
        discover_provider_models(
            "ollama",
            endpoint="https://models.test",
            allow_remote_ollama=True,
            timeout_seconds=3,
        )

    assert raised.value.code == "http_error"
    assert lookups == [("models.test", 443)]
    assert sock.connected_to == remote_address
    assert tls_names == ["models.test"]


def test_local_ollama_respects_public_network_policy(monkeypatch):
    from omd import _network_policy

    monkeypatch.setattr(_network_policy, "_SYSTEM_GETADDRINFO", lambda *_args, **_kw: [_IPV4_LOOPBACK])
    monkeypatch.setattr(socket, "socket", lambda *_args: pytest.fail("unexpected connection"))

    with public_network_policy_scope(), pytest.raises(urllib.error.URLError, match="public internet"):
        _request_json()
