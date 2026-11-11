"""Qoder's custom Base64 envelope (the ``Encode=1`` wire format).

The gateway does not speak plain JSON on the agent endpoints: a request body is
Base64 with a substituted alphabet, ``$`` for padding, and the three blocks of
the encoded string rotated (tail, middle, head). A few control-plane answers
(``region/endpoints``) use the same scheme, so both directions are needed.

Ported from ``pi-provider-qoder`` ``src/protocol/encoding.ts``; the tables are
built once and applied with ``str.translate`` so a large body costs one pass.
"""

from __future__ import annotations

import base64

CUSTOM_ALPHABET = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
STANDARD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

# Qoder writes "$" where standard Base64 writes "=".
_ENCODE_TABLE = {ord(STANDARD_ALPHABET[i]): ord(CUSTOM_ALPHABET[i]) for i in range(len(STANDARD_ALPHABET))}
_ENCODE_TABLE[ord("=")] = ord("$")
_DECODE_TABLE = {ord(CUSTOM_ALPHABET[i]): ord(STANDARD_ALPHABET[i]) for i in range(len(STANDARD_ALPHABET))}
_DECODE_TABLE[ord("$")] = ord("=")


def encode_body(plaintext: bytes | str) -> bytes:
    """Encode a request body: standard Base64, block rotation, custom alphabet."""
    raw = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    std = base64.b64encode(raw).decode("ascii")
    n = len(std)
    a = n // 3
    reordered = std[n - a:] + std[a:n - a] + std[:a]
    return reordered.translate(_ENCODE_TABLE).encode("ascii")


def decode_body(encoded: bytes | str) -> str:
    """Inverse of :func:`encode_body`; unknown bytes pass through untouched."""
    text = encoded.decode("ascii", "replace") if isinstance(encoded, bytes) else encoded
    n = len(text)
    if n == 0:
        return ""
    a = n // 3
    # Undo `tail + middle + head`: the first `a` characters are the tail, the
    # middle block stays put, and the last `a` characters are the head.
    restored = text[n - a:].translate(_DECODE_TABLE) + text[a:n - a].translate(_DECODE_TABLE) + text[:a].translate(_DECODE_TABLE)
    return base64.b64decode(restored).decode("utf-8", "replace")