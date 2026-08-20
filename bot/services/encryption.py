import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

_fernet_instance = None

def get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        key = os.getenv("FERNET_KEY")
        if not key:
            # Generate a reproducible fallback key based on TELEGRAM_BOT_TOKEN or fallback string if FERNET_KEY is missing
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "default_github_guardian_secret_salt")
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"github_guardian_static_salt",
                iterations=100_000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(bot_token.encode())).decode()
            logger.warning("FERNET_KEY not found in environment. Generated fallback key from bot token.")
        
        try:
            # Validate key formatting
            if isinstance(key, str):
                key_bytes = key.encode()
            else:
                key_bytes = key
            _fernet_instance = Fernet(key_bytes)
        except Exception as e:
            logger.error(f"Failed to initialize Fernet with provided key: {e}")
            raise ValueError("Invalid FERNET_KEY provided in configuration.") from e
            
    return _fernet_instance

def encrypt_token(token: str) -> str:
    """Encrypts a plaintext GitHub token."""
    if not token:
        return ""
    f = get_fernet()
    return f.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypts an encrypted GitHub token."""
    if not encrypted_token:
        return ""
    f = get_fernet()
    return f.decrypt(encrypted_token.encode()).decode()

def generate_key() -> str:
    """Generates a new valid Fernet key."""
    return Fernet.generate_key().decode()
