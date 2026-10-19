"""Bundled EAC relay seal, ported from dsh-our-free-model f0b979f (MIT).

Copyright (c) 2026 Ebony-Vinyl.
Only the relay credential is bundled. Per-user GitHub authorization must still
be completed against the gateway and is stored only in Hoshimi's own data dir.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from hoshimi.core.errors import ValidationError

_SHARDS = (
    ("xTTXnwQTMSTUGA7Ewo2NL776r7ihDlKkjRAj14fVUqw=", "vg34CorUf0wYuGNqZKZEBnQV8MIqg+/hGjLVeukjbvs="),
    ("3YQqhMmuju6xfuYqNpyzpCpR/PcN0e52Z+y7eYG+Dqg=", "tWrIxAA4wmdvVVqVtBE4Jb7u7V4QJ3iYAECR0xLGnhY="),
    ("cTVM9r/NJPHI3cE2ycPt8YzvL4I1wcrwSSCcJ3xllsc=", "2fpD/LDrSNGCZQlpx8DuQw0obZY2mkAafgdiTgf8Wq0="),
)
_SEAL = "NbTGdIKpE6jb2eppCmdIzF2fXNLMiEXwDVCaGdlBfKHQJeUZnglEzXDPpmtS6diboj60Xbf/bkW/JGbzanvIeI5Zux71q1F3JQxVZLFWnAjhCgEa/g0Du4BjkD67plPSMGOPYDZHXjJ+Fus/jhUj52T6Sl6YuT6DGJm0XXnmJMRUGK5WEyj83Q1Ye8KfqGKvwgc2nZiBrCPWD9ZCCvQJ/Jx3GS3AYlHp5/w3VD3J409PIQ=="


def open_bundled_relay() -> dict[str, Any]:
    """Open a fresh credential for one call; never log or persist the plaintext."""
    try:
        parts = []
        for masked, seed in _SHARDS:
            row = base64.b64decode(masked, validate=True)
            mask = HKDF(algorithm=hashes.SHA256(), length=len(row), salt=b"d21f0a5c7be94f13a68d20e4c9b73f58", info=b"shard-mask").derive(base64.b64decode(seed, validate=True))
            parts.append(bytes(a ^ b for a, b in zip(row, mask)))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"ofm-lane-seal/1\x00co-paid\x00desktop-only", info=b"seal-open/ aes-256-gcm/ per-request").derive(b"".join(parts))
        packed = base64.b64decode(_SEAL, validate=True)
        payload = json.loads(AESGCM(key).decrypt(packed[:12], packed[12:], b"ofm-eac-desktop/v1"))
        if payload.get("v") != 1 or payload.get("m") != "eac" or payload.get("t") != "worker":
            raise ValueError("unsupported relay seal")
        base = payload.get("u")
        secret = payload.get("s")
        if not isinstance(base, str) or not base.startswith("https://") or not base.rstrip("/").endswith("/v1"):
            raise ValueError("invalid relay endpoint")
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError("invalid relay credential")
        return {"base_url": base.rstrip("/"), "signing_secret": secret}
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error, InvalidTag):
        raise ValidationError("eac: the bundled relay configuration is unavailable; configure your own worker relay or update Hoshimi") from None
