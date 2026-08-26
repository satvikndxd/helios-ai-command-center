"""
Session vault (Phase W3) — encrypted at-rest storage for browser cookies.

MVP envelope encryption with zero extra dependencies: an HMAC-SHA256-derived
keystream (CTR construction) for confidentiality plus an HMAC-SHA256 tag for
integrity, keyed from `HELIOS_SESSION_VAULT_KEY`.  The enterprise track
swaps this for KMS envelope encryption behind the same two functions.

Rules enforced here:

* no key configured  -> sessions cannot be created at all (fail closed).
* decrypt is only reachable from the browser worker path; raw cookie
  material is never returned by any API route, trace, or prompt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

KEY_ENV = "HELIOS_SESSION_VAULT_KEY"


class VaultError(Exception):
    pass


def _key() -> bytes:
    raw = os.environ.get(KEY_ENV)
    if not raw:
        raise VaultError(
            f"session vault is disabled: set {KEY_ENV} to enable browser sessions"
        )
    return hashlib.sha256(raw.encode()).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    while sum(len(b) for b in blocks) < length:
        blocks.append(
            hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        )
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_profile(profile: dict) -> str:
    """Encrypt a cookie/profile dict -> opaque base64 blob (nonce|tag|ct)."""
    key = _key()
    plaintext = json.dumps(profile).encode()
    nonce = secrets.token_bytes(16)
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext)))
    )
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + ciphertext).decode()


def decrypt_profile(blob: str) -> dict:
    """Worker-only: decrypt the blob. Integrity is verified before use."""
    key = _key()
    raw = base64.b64decode(blob.encode())
    nonce, tag, ciphertext = raw[:16], raw[16:48], raw[48:]
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise VaultError("session vault integrity check failed")
    plaintext = bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(key, nonce, len(ciphertext)))
    )
    return json.loads(plaintext.decode())
