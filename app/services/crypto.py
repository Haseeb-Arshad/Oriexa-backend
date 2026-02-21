"""AES-256-GCM encryption compatible with the Node.js implementation.
Format: iv_hex:authTag_hex:ciphertext_base64"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


def _get_key() -> bytes:
    raw = settings.ENCRYPTION_KEY
    if not raw:
        raise RuntimeError("ENCRYPTION_KEY env var not set")
    return bytes.fromhex(raw)


def encrypt_key(plaintext: str) -> str:
    key = _get_key()
    iv = os.urandom(12)  # 96-bit nonce
    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext + 16-byte auth tag appended
    ct_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # Split: last 16 bytes = auth tag, rest = ciphertext
    ciphertext = ct_with_tag[:-16]
    auth_tag = ct_with_tag[-16:]
    ct_b64 = base64.b64encode(ciphertext).decode()
    return f"{iv.hex()}:{auth_tag.hex()}:{ct_b64}"


def decrypt_key(encrypted: str) -> str:
    key = _get_key()
    iv_hex, auth_tag_hex, ct_b64 = encrypted.split(":")
    iv = bytes.fromhex(iv_hex)
    auth_tag = bytes.fromhex(auth_tag_hex)
    ciphertext = base64.b64decode(ct_b64)
    aesgcm = AESGCM(key)
    # Reconstruct ciphertext + tag for AESGCM.decrypt
    plaintext = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    return plaintext.decode("utf-8")
