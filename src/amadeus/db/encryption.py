"""Simple encryption for API keys.

Uses Fernet symmetric encryption with an environment-based key.
This provides basic protection for stored API keys.
"""

from __future__ import annotations

import base64
import hashlib
import os
import platform
from pathlib import Path

# Optional: use cryptography if available, otherwise use simple obfuscation
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


def _get_encryption_secret() -> str:
    """Get the encryption secret from environment or generate one."""
    # First check for explicit encryption key in environment
    secret = os.environ.get("AMADEUS_ENCRYPTION_KEY")
    if secret:
        return secret
    
    # Fallback to machine-specific identifier (for local development)
    components = [
        platform.node(),  # hostname
        platform.system(),  # OS
        platform.machine(),  # architecture
        os.environ.get("USER", os.environ.get("USERNAME", "amadeus")),
    ]
    return "".join(components)


def _derive_key() -> bytes:
    """Derive an encryption key from secret."""
    secret = _get_encryption_secret()
    # Use SHA256 to derive a key
    key_bytes = hashlib.sha256(secret.encode()).digest()
    # Fernet requires a URL-safe base64-encoded 32-byte key
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_key(api_key: str) -> str:
    """Encrypt an API key for storage."""
    if not api_key:
        return ""
    
    if HAS_CRYPTOGRAPHY:
        try:
            fernet = Fernet(_derive_key())
            encrypted = fernet.encrypt(api_key.encode())
            return f"enc:{encrypted.decode()}"
        except Exception:
            # Fall back to obfuscation
            pass
    
    # Simple obfuscation as fallback
    # XOR with machine-specific key
    key = _get_machine_id()
    obfuscated = []
    for i, char in enumerate(api_key):
        key_char = key[i % len(key)]
        obfuscated.append(chr(ord(char) ^ ord(key_char)))
    encoded = base64.b64encode("".join(obfuscated).encode()).decode()
    return f"obf:{encoded}"


def decrypt_key(encrypted_key: str) -> str:
    """Decrypt an API key from storage."""
    if not encrypted_key:
        return ""
    
    # Check prefix
    if encrypted_key.startswith("enc:"):
        # Fernet encrypted
        if HAS_CRYPTOGRAPHY:
            try:
                fernet = Fernet(_derive_key())
                decrypted = fernet.decrypt(encrypted_key[4:].encode())
                return decrypted.decode()
            except Exception:
                return ""
        return ""
    
    if encrypted_key.startswith("obf:"):
        # Simple obfuscation
        try:
            decoded = base64.b64decode(encrypted_key[4:]).decode()
            key = _get_machine_id()
            deobfuscated = []
            for i, char in enumerate(decoded):
                key_char = key[i % len(key)]
                deobfuscated.append(chr(ord(char) ^ ord(key_char)))
            return "".join(deobfuscated)
        except Exception:
            return ""
    
    # Not encrypted (legacy or plaintext)
    return encrypted_key


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted."""
    return value.startswith("enc:") or value.startswith("obf:")
