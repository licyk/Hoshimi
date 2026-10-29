"""Exercise verified TLS, pooling, proxies and stream lifetime without Bun."""

import base64
import gzip
import ipaddress
import socket
import socketserver
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from OpenSSL import crypto

from hoshimi.core.net import client as networking
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.net.opencode_tls import OpenCodeTransport
from hoshimi.core.net.policy import NetworkPolicy

EXTENSIONS = {"hoshimi_transport": "opencode"}


@pytest.fixture
def certificates(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))

    def make(*, hostname="localhost", expired=False, tls12=False):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
        names = [x509.DNSName(hostname)]
        if hostname == "localhost":
            names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 1))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_file, key_file = tmp_path / "server.pem", tmp_path / "server.key"
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        if tls12:
            context.maximum_version = ssl.TLSVersion.TLSv1_2
        return context

    return make


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def upstream(certificates):
    release = threading.Event()
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path == "/stall-upload":
                release.wait(5)
                self.close_connection = True
                return
            data = self.rfile.read(int(self.headers.get("content-length", 0)))
            received.append((self.path, data, dict(self.headers), self.client_address))
            self.send_response(200)
            if self.path == "/stream":
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"D\r\ndata: first\n\n\r\n")
                self.wfile.flush()
                release.wait(5)
                self.close_connection = True
            elif self.path == "/broken":
                self.send_header("content-length", "100")
                self.end_headers()
                self.wfile.write(b"partial")
                self.close_connection = True
            else:
                compressed = gzip.compress(data)
                self.send_header("content-encoding", "gzip")
                self.send_header("content-length", str(len(compressed)))
                self.end_headers()
                self.wfile.write(compressed)

    @contextmanager
    def start(**cert_options):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = certificates(**cert_options).wrap_socket(server.socket, server_side=True)
        try:
            with running(server):
                yield f"https://localhost:{server.server_port}", received, release
        finally:
            release.set()

    return start


def relay(left, right):
    """Tunnel bytes in both directions, including sockets with decrypted TLS data."""

    def copy(source, destination):
        try:
            while chunk := source.recv(65536):
                destination.sendall(chunk)
        except OSError:
            pass
        finally:
            for sock in (source, destination):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    thread = threading.Thread(target=copy, args=(right, left), daemon=True)
    thread.start()
    copy(left, right)
    thread.join(timeout=3)


@pytest.fixture
def proxy(certificates):
    @contextmanager
    def start(scheme, upstream_port, *, reject=False):
        received = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                if scheme in ("socks5", "socks5h"):
                    version, count = self.rfile.read(2)
                    assert version == 5 and 2 in self.rfile.read(count)
                    self.wfile.write(b"\x05\x02")
                    assert self.rfile.read(1) == b"\x01"
                    username = self.rfile.read(self.rfile.read(1)[0])
                    password = self.rfile.read(self.rfile.read(1)[0])
                    assert (username, password) == (b"user", b"secret")
                    self.wfile.write(b"\x01\x00")
                    version, command, reserved, kind = self.rfile.read(4)
                    assert (version, command, reserved, kind) == (5, 1, 0, 3)
                    hostname = self.rfile.read(self.rfile.read(1)[0])
                    port = int.from_bytes(self.rfile.read(2), "big")
                    received.append((hostname.decode(), port))
                    self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                else:
                    first = self.rfile.readline().decode().strip()
                    headers = {}
                    while (line := self.rfile.readline()) != b"\r\n":
                        name, value = line.decode().split(":", 1)
                        headers[name.lower()] = value.strip()
                    received.append((first, headers))
                    assert headers["proxy-authorization"] == "Basic " + base64.b64encode(b"user:secret").decode()
                    if reject:
                        self.wfile.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                        return
                    assert first == f"CONNECT localhost:{upstream_port} HTTP/1.1"
                    self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                with socket.create_connection(("127.0.0.1", upstream_port), timeout=3) as remote:
                    relay(self.connection, remote)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        if scheme == "https":
            server.socket = certificates().wrap_socket(server.socket, server_side=True)
        with running(server):
            yield f"{scheme}://user:secret@localhost:{server.server_address[1]}", received

    return start


def test_upstream_quota_does_not_replay_on_httpx(monkeypatch):
    calls = []
    monkeypatch.setattr(
        networking,
        "OpenCodeTransport",
        lambda **kwargs: httpx.MockTransport(
            lambda request: (calls.append(request), httpx.Response(429, headers={"retry-after": "60"}, text="quota"))[1],
        ),
    )
    monkeypatch.setattr(networking.httpx, "HTTPTransport", lambda **kwargs: pytest.fail("Do not replay on HTTPX"))
    with NetworkClient(provider="opencode") as client:
        response = client.post("https://upstream.test/chat", extensions=EXTENSIONS)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        assert client.diagnostics()[0]["transport"] == "pyopenssl"
    assert len(calls) == 1


def test_direct_tls_ignores_env_proxy_and_reuses_connection(upstream, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    before = ssl.create_default_context().get_ciphers()
    with upstream() as (url, received, _), NetworkClient(provider="opencode", network=NetworkPolicy("direct"), timeout=3) as client:
        for _ in range(2):
            assert client.post(url, json={"text": "你好"}, extensions=EXTENSIONS).json() == {"text": "你好"}
        observation = client.diagnostics()[0]
        assert observation["transport"] == "pyopenssl" and observation["route"] == "direct"
    assert len(received) == 2 and received[0][3] == received[1][3]
    assert before == ssl.create_default_context().get_ciphers()


@pytest.mark.parametrize("tls12", [False, True])
def test_tls_ip_identity(upstream, tls12):
    with upstream(tls12=tls12) as (url, _, _), httpx.Client(transport=OpenCodeTransport(), timeout=3) as client:
        assert client.post(url.replace("localhost", "127.0.0.1"), content=b"ok").text == "ok"


def test_ca_directory_is_used_without_loading_system_roots(upstream, monkeypatch, tmp_path):
    with upstream() as (url, _, _):
        ca_file = tmp_path / "ca.pem"
        cert = crypto.load_certificate(crypto.FILETYPE_PEM, ca_file.read_bytes())
        (tmp_path / f"{cert.subject_name_hash():08x}.0").symlink_to(ca_file)
        monkeypatch.delenv("SSL_CERT_FILE")
        monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
        with httpx.Client(transport=OpenCodeTransport(), timeout=3) as client:
            assert client.post(url, content=b"trusted directory").text == "trusted directory"


@pytest.mark.parametrize("failure", ["hostname", "untrusted", "expired"])
def test_bad_certificates_never_send_http(upstream, monkeypatch, failure):
    options = {"hostname": "wrong.example"} if failure == "hostname" else {"expired": failure == "expired"}
    with upstream(**options) as (url, received, _):
        if failure == "untrusted":
            monkeypatch.delenv("SSL_CERT_FILE")
            monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        with httpx.Client(transport=OpenCodeTransport(), timeout=3) as client, pytest.raises(httpx.ConnectError):
            client.post(url, content=b"must not reach server")
        assert not received


@pytest.mark.parametrize("scheme", ["http", "https", "socks5", "socks5h"])
def test_tls_through_authenticated_proxy(upstream, proxy, scheme):
    with upstream() as (url, received, _), proxy(scheme, httpx.URL(url).port) as (proxy_url, connections):
        with httpx.Client(transport=OpenCodeTransport(proxy=proxy_url), timeout=3) as client:
            for _ in range(2):
                assert client.post(url, json={"hello": "world"}).json() == {"hello": "world"}
        assert len(connections) == 1 and len(received) == 2
        assert all("Proxy-Authorization" not in item[2] for item in received)


def test_proxy_failure_never_connects_directly(upstream, proxy):
    with upstream() as (url, received, _), proxy("http", httpx.URL(url).port, reject=True) as (proxy_url, connections):
        with httpx.Client(transport=OpenCodeTransport(proxy=proxy_url), timeout=3) as client, pytest.raises(httpx.ProxyError):
            client.post(url, content=b"private")
        assert len(connections) == 1 and not received


@pytest.mark.parametrize("timeout", [False, True])
def test_stream_is_incremental_and_close_releases_connection(upstream, timeout):
    with upstream() as (url, _, release):
        transport = OpenCodeTransport()
        with httpx.Client(transport=transport, timeout=0.2 if timeout else 3) as client:
            with client.stream("POST", url + "/stream") as response:
                chunks = response.iter_raw()
                assert next(chunks) == b"data: first\n\n"
                assert not release.is_set()
                if timeout:
                    with pytest.raises(httpx.ReadTimeout):
                        next(chunks)
            assert not transport.pool.connections
        release.set()


def test_truncated_tls_response_is_an_error(upstream):
    with upstream() as (url, _, _), httpx.Client(transport=OpenCodeTransport(), timeout=3) as client, pytest.raises((httpx.ReadError, httpx.RemoteProtocolError)):
        client.post(url + "/broken")


def test_write_deadline_releases_connection(upstream):
    with upstream() as (url, _, release):
        transport = OpenCodeTransport()
        with httpx.Client(transport=transport, timeout=httpx.Timeout(3, write=0.1)) as client, pytest.raises(httpx.WriteTimeout):
            client.post(url + "/stall-upload", content=b"x" * (16 * 1024 * 1024))
        release.set()
        assert not transport.pool.connections


def test_clienthello_preserves_both_cipher_lists():
    records = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            header = self.rfile.read(5)
            records.append(self.rfile.read(int.from_bytes(header[3:5], "big")))

    with (
        running(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)) as server,
        httpx.Client(transport=OpenCodeTransport(), timeout=1) as client,
        pytest.raises(httpx.ConnectError),
    ):
        client.post(f"https://localhost:{server.server_address[1]}")
    hello = records[0]
    assert hello[0] == 1
    offset = 39 + hello[38]  # Handshake header, legacy version, random, session ID.
    size = int.from_bytes(hello[offset : offset + 2], "big")
    suites = [int.from_bytes(hello[i : i + 2], "big") for i in range(offset + 2, offset + 2 + size, 2)]
    assert [cipher for cipher in suites if cipher != 0x00FF] == [
        0x1301,
        0x1302,
        0x1303,  # TLS 1.3, AES128 first.
        0xC02B,
        0xC02F,
        0xC02C,
        0xC030,
        0xCCA9,
        0xCCA8,
        0xC009,
        0xC013,
        0xC00A,
        0xC014,
        0x009C,
        0x009D,
        0x002F,
        0x0035,
    ]


def test_handshake_deadline_closes_connection():
    disconnected = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            while self.request.recv(65536):
                pass
            disconnected.set()

    with running(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)) as server:
        transport = OpenCodeTransport()
        with httpx.Client(transport=transport, timeout=0.1) as client, pytest.raises(httpx.ConnectTimeout):
            client.post(f"https://localhost:{server.server_address[1]}")
        assert disconnected.wait(1)
        assert not transport.pool.connections
