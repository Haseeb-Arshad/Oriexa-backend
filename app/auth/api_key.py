import hashlib
import os
import re

from app.constants import API_KEY_PREFIX, API_KEY_HEX_LENGTH

_HEX_PATTERN = re.compile(r"^[0-9a-f]+$")


def generate_api_key() -> dict[str, str]:
    """Generate a new API key: th_agent_ + 64 hex chars.
    Returns {raw_key, hash, prefix}."""
    random_bytes = os.urandom(32)
    hex_part = random_bytes.hex()  # 64 hex chars
    raw_key = f"{API_KEY_PREFIX}{hex_part}"
    key_hash = hash_api_key(raw_key)
    prefix = raw_key[:14]
    return {"raw_key": raw_key, "hash": key_hash, "prefix": prefix}


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def is_valid_api_key_format(key: str) -> bool:
    """Validate th_agent_ prefix + exactly 64 hex chars."""
    if not key.startswith(API_KEY_PREFIX):
        return False
    hex_part = key[len(API_KEY_PREFIX):]
    return len(hex_part) == API_KEY_HEX_LENGTH and bool(_HEX_PATTERN.match(hex_part))
