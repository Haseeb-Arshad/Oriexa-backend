import secrets
import hashlib
from app.config import settings

def generate_api_key():
    """
    Generate a new API key with the th_agent_ prefix + 64 hex chars.
    Returns (raw_key, hash, prefix)
    """
    prefix_base = "th_agent_" # Consistent with Node.js API_KEY_PREFIX
    hex_chars = secrets.token_hex(32) # 64 hex characters
    raw_key = f"{prefix_base}{hex_chars}"
    
    hashed = hash_api_key(raw_key)
    prefix = raw_key[:14] # Consistent with Node.js
    
    return raw_key, hashed, prefix

def hash_api_key(raw_key: str) -> str:
    """
    Compute SHA-256 hash of a raw API key.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()
