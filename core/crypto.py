"""Runtime string decryption to prevent static analysis of sensitive values.

Sensitive strings (URLs, secrets, API paths) are stored as encrypted
base64 blobs in source code.  At runtime they are decrypted via XOR
with a key derived from scattered byte components.

Usage (build-time — encrypt a new string):
    python -c "from core.crypto import encrypt; print(encrypt('my secret'))"

Usage (runtime — decrypt in application code):
    from core.crypto import decrypt
    URL = decrypt("base64_blob_here")
"""

import hashlib
import base64

# Key material scattered across the module — assembled at runtime.
# Each fragment is meaningless on its own; only the combination + salt
# produces the real key.  Moving them to different modules would add
# another layer, but this is already effective against casual grep /
# strings / hex-dump inspection.
_K1 = b"\x54\x47\x42\x6f\x74"
_K2 = b"\x44\x65\x73\x6b\x74\x6f\x70"
_K3 = b"\x32\x30\x32\x34"
_SALT = b"\x8a\x3f\x91\xc2\x7e\x44\xb1\x05"


def _derive_key() -> bytes:
    raw = _K1 + _SALT[:4] + _K2 + _SALT[4:] + _K3
    return hashlib.sha256(raw).digest()


def decrypt(encrypted_b64: str) -> str:
    """Decrypt a base64-encoded XOR-encrypted string."""
    key = _derive_key()
    data = base64.b64decode(encrypted_b64)
    result = bytearray(len(data))
    for i, b in enumerate(data):
        result[i] = b ^ key[i % len(key)]
    return result.decode("utf-8")


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string — build-time helper.

    Run this once to produce the blob, then paste it into source code
    wrapped in ``decrypt("...")``.
    """
    key = _derive_key()
    data = plaintext.encode("utf-8")
    result = bytearray(len(data))
    for i, b in enumerate(data):
        result[i] = b ^ key[i % len(key)]
    return base64.b64encode(bytes(result)).decode("ascii")
