"""OpenCode's TLS cipher ordering without a Bun runtime or global SSL changes.

httpcore owns HTTP framing, pooling and proxies. A memory BIO adds verified TLS
to its network streams, including TLS inside an HTTPS proxy's TLS connection.
This matches the cipher lists tested with Bun, not its entire ClientHello.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import partial
from typing import Any, cast

import httpcore
import httpx
from OpenSSL import SSL, crypto
from service_identity import CertificateError, VerificationError
from service_identity.pyopenssl import verify_hostname, verify_ip_address

_CIPHERS = (
    b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    b"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    b"ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
    b"ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:"
    b"ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
    b"AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA"
)
_TLS13_CIPHERS = b"TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256"


def _context() -> SSL.Context:
    context = SSL.Context(SSL.TLS_CLIENT_METHOD)
    context.set_min_proto_version(SSL.TLS1_2_VERSION)
    context.set_cipher_list(_CIPHERS)
    context.set_tls13_ciphersuites(_TLS13_CIPHERS)
    context.set_alpn_protos([b"http/1.1"])
    context.set_verify(SSL.VERIFY_PEER, lambda _conn, _cert, _errno, _depth, valid: bool(valid))
    # Match HTTPX's trust_env=True CA precedence, including lazy CA directories.
    if cafile := os.environ.get("SSL_CERT_FILE"):
        context.load_verify_locations(cafile)
    elif capath := os.environ.get("SSL_CERT_DIR"):
        context.load_verify_locations(None, capath)
    else:
        store = context.get_cert_store()
        assert store is not None
        for der in httpcore.default_ssl_context().get_ca_certs(binary_form=True):
            store.add_cert(crypto.load_certificate(crypto.FILETYPE_ASN1, der))
    return context


class _Stream(httpcore.NetworkStream):
    def __init__(self, stream: httpcore.NetworkStream, context: SSL.Context):
        self.stream = stream
        self.context = context

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self.stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.stream.write(buffer, timeout)

    def close(self) -> None:
        self.stream.close()

    def get_extra_info(self, info: str) -> Any:
        return self.stream.get_extra_info(info)

    def start_tls(self, ssl_context: ssl.SSLContext, server_hostname: str | None = None, timeout: float | None = None) -> httpcore.NetworkStream:
        # All pools using this backend share the immutable, verified context above.
        try:
            if not server_hostname:
                raise httpcore.ConnectError("TLS requires a server hostname")
            return _TLSStream(self, self.context, server_hostname, timeout)
        except BaseException:
            self.close()
            raise


class _TLSStream(_Stream):
    def __init__(self, stream: httpcore.NetworkStream, context: SSL.Context, hostname: str, timeout: float | None):
        super().__init__(stream, context)
        self.connection = SSL.Connection(context, None)
        self.connection.set_connect_state()
        try:
            ipaddress.ip_address(hostname)
            is_ip = True
        except ValueError:
            is_ip = False
            self.connection.set_tlsext_host_name(hostname.encode("idna"))
        self._perform(self.connection.do_handshake, self._deadline(timeout), "connect")
        try:
            if is_ip:
                verify_ip_address(self.connection, hostname)
            else:
                verify_hostname(self.connection, hostname)
        except (CertificateError, VerificationError) as exc:
            raise httpcore.ConnectError("TLS certificate identity verification failed") from exc

    @staticmethod
    def _deadline(timeout: float | None) -> float | None:
        return None if timeout is None else time.monotonic() + timeout

    def _perform(self, action: Callable[[], Any], deadline: float | None, phase: str, *, eof_ok: bool = False) -> Any:
        timeout_error, network_error = {
            "connect": (httpcore.ConnectTimeout, httpcore.ConnectError),
            "read": (httpcore.ReadTimeout, httpcore.ReadError),
            "write": (httpcore.WriteTimeout, httpcore.WriteError),
        }[phase]

        def remaining() -> float | None:
            if deadline is None:
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                raise timeout_error(f"TLS {phase} timed out")
            return left

        try:
            while True:
                remaining()
                want_read = False
                complete = False
                result = None
                try:
                    result = action()
                    complete = True
                except SSL.WantReadError:
                    want_read = True
                except SSL.WantWriteError:
                    pass
                except SSL.ZeroReturnError:
                    if not eof_ok:
                        raise network_error("TLS connection closed") from None
                    result, complete = b"", True
                # Flush handshake messages, application writes and TLS responses.
                while True:
                    try:
                        outgoing = self.connection.bio_read(65536)
                    except SSL.WantReadError:
                        break
                    self.stream.write(outgoing, remaining())
                if complete:
                    return result
                if want_read:
                    incoming = self.stream.read(65536, remaining())
                    if incoming:
                        self.connection.bio_write(incoming)
                    else:
                        self.connection.bio_shutdown()
        except httpcore.TimeoutException as exc:
            raise timeout_error(f"TLS {phase} timed out") from exc
        except (SSL.Error, httpcore.NetworkError, OSError) as exc:
            raise network_error(f"TLS {phase} failed") from exc

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._perform(lambda: self.connection.recv(max_bytes), self._deadline(timeout), "read", eof_ok=True)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        deadline = self._deadline(timeout)
        while buffer:
            sent = self._perform(partial(self.connection.send, buffer), deadline, "write")
            buffer = buffer[sent:]

    def selected_alpn_protocol(self) -> str | None:
        protocol = self.connection.get_alpn_proto_negotiated()
        return protocol.decode("ascii") if protocol else None

    def get_extra_info(self, info: str) -> Any:
        if info == "ssl_object":
            return self
        if info == "is_readable" and self.connection.pending():
            return True
        return super().get_extra_info(info)


class _Backend(httpcore.SyncBackend):
    def __init__(self, context: SSL.Context):
        self.context = context

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None) -> httpcore.NetworkStream:
        return _Stream(super().connect_tcp(host, port, timeout, local_address, socket_options), self.context)

    def connect_unix_socket(self, path, timeout=None, socket_options=None) -> httpcore.NetworkStream:
        return _Stream(super().connect_unix_socket(path, timeout, socket_options), self.context)


# Keep HTTPX's exception types at both the response and streaming boundaries.
_ERRORS = {
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
}


@contextmanager
def _httpx_errors() -> Generator[None]:
    try:
        yield
    except tuple(_ERRORS) as exc:
        for source, target in _ERRORS.items():
            if isinstance(exc, source):
                raise target(str(exc)) from exc
        raise


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, response: httpcore.Response):
        self.response = response

    def __iter__(self):
        with _httpx_errors():
            yield from self.response.iter_stream()

    def close(self) -> None:
        with _httpx_errors():
            self.response.close()


class OpenCodeTransport(httpx.BaseTransport):
    """A pooled Python transport for the anonymous OpenCode inference lane."""

    def __init__(self, *, proxy: str | None = None):
        backend = _Backend(_context())
        options: dict[str, Any] = {"network_backend": backend, "max_connections": 100, "max_keepalive_connections": 20, "keepalive_expiry": 5.0, "retries": 0}
        if proxy is None:
            self.pool = httpcore.ConnectionPool(**options)
        else:
            parsed = httpx.Proxy(proxy)
            if parsed.url.scheme in ("http", "https"):
                self.pool = httpcore.HTTPProxy(proxy_url=str(parsed.url), proxy_auth=parsed.raw_auth, proxy_headers=parsed.headers.raw, **options)
            elif parsed.url.scheme in ("socks5", "socks5h"):
                self.pool = cast(httpcore.ConnectionPool, httpcore.SOCKSProxy(proxy_url=str(parsed.url), proxy_auth=parsed.raw_auth, **options))
            else:
                raise ValueError("Unsupported proxy protocol")

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.SyncByteStream)
        with _httpx_errors():
            response = self.pool.handle_request(
                httpcore.Request(
                    method=request.method,
                    url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port, target=request.url.raw_path),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
        return httpx.Response(response.status, headers=response.headers, stream=_ResponseStream(response), extensions=response.extensions)

    def close(self) -> None:
        with _httpx_errors():
            self.pool.close()
