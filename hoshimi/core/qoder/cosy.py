"""COSY request signing: the client identity Qoder's gateway authenticates.

This is the impersonation layer, ported in full from ``pi-provider-qoder``
``src/cosy.ts``. The gateway does not accept a bare bearer token on the agent
endpoints: every request carries an ``Authorization: Bearer COSY.<payload>.<sig>``
header whose payload encrypts the caller's identity, plus a header set that
declares a Qoder CLI client (machine id, machine type, OS, client type, data
policy, login version).

The construction, in order:

1. a random 16-character AES key;
2. the user info (``uid``, ``security_oauth_token``, ``name``, ``aid``, ``email``)
   as JSON, AES-128-CBC encrypted with that key used as both key and IV;
3. the AES key itself RSA-encrypted (PKCS#1 v1.5) with Qoder's published public
   key — that ciphertext is ``Cosy-Key``;
4. a payload ``{version, requestId, info, cosyVersion, ideVersion}`` Base64'd;
5. ``sig = MD5(payloadB64 + "\\n" + cosyKey + "\\n" + timestamp + "\\n" + body +
   "\\n" + sigPath)``, where ``sigPath`` is the URL path with the ``/algo``
   prefix removed and ``body`` is the *encoded* request body actually sent.

The body hash and length are signed too, so the signature is only valid for the
exact bytes on the wire: re-encoding a body (a retry with a new request id)
requires re-signing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from hoshimi.core.paths import default_data_dir

QODER_RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""

# The CLI identity. `5` is the CLI client type; the desktop app uses `10` and
# sees a different catalogue, so this value is part of the impersonation.
QODER_CLIENT_TYPE = "5"
QODER_OPENAPI_COSY_VERSION = "1.0.1"
QODER_MACHINE_TYPE_MAGIC = "5"
QODER_DATA_POLICY = "disagree"
QODER_LOGIN_VERSION = "v2"
MACHINE_ID_FILE = "qoder-machine-id"


def machine_os() -> str:
    """The OS string a Qoder CLI declares, as the plugin spells it.

    Only Windows and Linux shapes are used — the reference client reports the
    Linux string on every non-Windows host, macOS included, and that is the
    identity the gateway has been verified against.
    """
    arch = platform.machine().lower()
    arm = arch in ("arm64", "aarch64")
    if platform.system().lower() == "windows":
        return "aarch64_windows" if arm else "x86_64_windows"
    return "aarch64_linux" if arm else "x86_64_linux"


@dataclass(frozen=True)
class CosyCredentials:
    """The identity a signature is built for.

    ``user_id`` must be the account's real uid: the CN gateway answers
    ``105 Login expired`` when the payload carries a placeholder.
    """

    user_id: str
    auth_token: str
    name: str = ""
    email: str = ""
    machine_id: str = ""


def _public_key() -> Any:
    return serialization.load_pem_public_key(QODER_RSA_PUBLIC_KEY.encode("ascii"))


def rsa_encrypt_base64(data: str) -> str:
    """RSA/ECB/PKCS1Padding, the scheme Qoder's own client uses for the AES key."""
    encrypted = _public_key().encrypt(data.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def aes_encrypt_cbc_base64(plaintext: str, key: str) -> str:
    """AES-128-CBC with the key reused as the IV, PKCS#7 padded, Base64'd."""
    raw = key.encode("ascii")
    encryptor = Cipher(algorithms.AES(raw), modes.CBC(raw)).encryptor()
    data = plaintext.encode("utf-8")
    pad = 16 - (len(data) % 16)
    data += bytes([pad]) * pad
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode("ascii")


def compute_sig_path(url: str) -> str:
    """The signed path: the URL path without the gateway's ``/algo`` prefix."""
    path = urlsplit(url).path
    path = path.removeprefix("/algo")
    return path


def machine_id_path() -> Path:
    return default_data_dir() / MACHINE_ID_FILE


def get_machine_id() -> str:
    """A stable machine id, shared with a local Qoder install when one exists.

    The IDE's own id wins (``~/.qoder/.auth/machine_id``): reusing it keeps the
    account on the device the station already knows. Otherwise one UUID is
    generated and persisted, because the refresh flow binds the device to it.
    """
    candidates = [Path.home() / ".qoder" / ".auth" / "machine_id", machine_id_path()]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    fresh = str(uuid.uuid4())
    try:
        path = machine_id_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
    except OSError:
        # A read-only data directory must not break signing; this process still
        # has a stable id for as long as it lives.
        pass
    return fresh


def build_auth_headers(
    body: bytes | str | None,
    request_url: str,
    credentials: CosyCredentials,
    *,
    cosy_version: str,
) -> dict[str, str]:
    """Sign one request. ``body`` must be the exact bytes that will be sent."""
    if not credentials.user_id:
        raise ValueError("cosy: user id is empty")
    if not credentials.auth_token:
        raise ValueError("cosy: auth token is empty")

    aes_key = uuid.uuid4().hex[:16]
    user_info = {
        "uid": credentials.user_id,
        "security_oauth_token": credentials.auth_token,
        "name": credentials.name or "",
        "aid": "",
        "email": credentials.email or "",
    }
    info_b64 = aes_encrypt_cbc_base64(json.dumps(user_info, separators=(",", ":"), ensure_ascii=False), aes_key)
    cosy_key = rsa_encrypt_base64(aes_key)

    timestamp = str(int(time.time()))
    payload = {
        "version": "v1",
        "requestId": str(uuid.uuid4()),
        "info": info_b64,
        "cosyVersion": cosy_version,
        "ideVersion": "",
    }
    payload_b64 = base64.b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii")
    sig_path = compute_sig_path(request_url)

    raw = b"" if body is None else (body.encode("utf-8") if isinstance(body, str) else body)
    digest = hashlib.md5()
    digest.update(payload_b64.encode("ascii"))
    digest.update(b"\n")
    digest.update(cosy_key.encode("ascii"))
    digest.update(b"\n")
    digest.update(timestamp.encode("ascii"))
    digest.update(b"\n")
    digest.update(raw)
    digest.update(b"\n")
    digest.update(sig_path.encode("utf-8"))
    sig = digest.hexdigest()

    machine = credentials.machine_id or get_machine_id()
    return {
        "Authorization": f"Bearer COSY.{payload_b64}.{sig}",
        "Cosy-Key": cosy_key,
        "Cosy-User": credentials.user_id,
        "Cosy-Date": timestamp,
        "Cosy-Version": cosy_version,
        "Cosy-Machineid": machine,
        "Cosy-Machinetoken": machine,
        "Cosy-Machinetype": QODER_MACHINE_TYPE_MAGIC,
        "Cosy-Machineos": machine_os(),
        "Cosy-Clienttype": QODER_CLIENT_TYPE,
        "Cosy-Clientip": "127.0.0.1",
        "Cosy-Bodyhash": hashlib.md5(raw).hexdigest(),
        "Cosy-Bodylength": str(len(raw)),
        "Cosy-Sigpath": sig_path,
        "Cosy-Data-Policy": QODER_DATA_POLICY,
        "Cosy-Organization-Id": "",
        "Cosy-Organization-Tags": "",
        "Login-Version": QODER_LOGIN_VERSION,
        "X-Request-Id": str(uuid.uuid4()),
    }


def open_api_headers(job_token: str) -> dict[str, str]:
    """The un-signed header set the openapi host accepts for a job token.

    PAT exchange, userinfo and quota are plain bearer calls; only the version
    and client type are declared, and no COSY signature is involved.
    """
    return {
        "Authorization": f"Bearer {job_token}",
        "Accept": "application/json",
        "User-Agent": "qodercli",
        "Cosy-Version": QODER_OPENAPI_COSY_VERSION,
        "Cosy-ClientType": QODER_CLIENT_TYPE,
    }