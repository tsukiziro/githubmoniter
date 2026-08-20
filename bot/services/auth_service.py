import os
import hmac
import hashlib
import secrets
import logging
from typing import Optional, Tuple, Dict, Any
import httpx
from bot.services.encryption import encrypt_token, decrypt_token
from bot.database.mongodb import save_user, get_user, delete_user

logger = logging.getLogger(__name__)

# State secret for CSRF protection
STATE_SECRET = os.getenv("STATE_SECRET", "github_guardian_csrf_secret_key_12345")

def _generate_state_signature(telegram_id: int, nonce: str) -> str:
    msg = f"{telegram_id}:{nonce}".encode()
    return hmac.new(STATE_SECRET.encode(), msg, hashlib.sha256).hexdigest()

def generate_oauth_url(telegram_id: int) -> str:
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "")
    
    nonce = secrets.token_hex(8)
    sig = _generate_state_signature(telegram_id, nonce)
    state = f"{telegram_id}.{nonce}.{sig}"
    
    # Scopes: repo, user, write:discussion, admin:repo_hook, read:org
    scopes = "repo user write:discussion admin:repo_hook read:org"
    
    oauth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&state={state}"
    )
    return oauth_url

def verify_state(state: str) -> Optional[int]:
    """Verifies state string and returns telegram_id if valid."""
    try:
        parts = state.split(".")
        if len(parts) != 3:
            return None
        telegram_id_str, nonce, sig = parts
        telegram_id = int(telegram_id_str)
        
        expected_sig = _generate_state_signature(telegram_id, nonce)
        if hmac.compare_digest(sig, expected_sig):
            return telegram_id
        return None
    except Exception as e:
        logger.warning(f"State verification failed: {e}")
        return None

async def exchange_code_for_token(code: str) -> str:
    """Exchanges GitHub authorization code for access token."""
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "")

    url = "https://github.com/login/oauth/access_token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri
    }
    headers = {"Accept": "application/json"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            logger.error(f"GitHub OAuth token exchange HTTP error: {resp.status_code}")
            raise ValueError("Failed to exchange authorization code with GitHub.")
        
        data = resp.json()
        if "error" in data:
            logger.error(f"GitHub OAuth token exchange error: {data.get('error_description')}")
            raise ValueError(f"GitHub OAuth Error: {data.get('error_description', data['error'])}")
            
        token = data.get("access_token")
        if not token:
            raise ValueError("No access token received from GitHub.")
        return token

async def fetch_github_profile(token: str) -> Dict[str, Any]:
    """Fetches user profile from GitHub to validate token."""
    url = "https://api.github.com/user"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-Guardian-Bot"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=10.0)
        if resp.status_code == 401:
            raise ValueError("Invalid or expired GitHub token.")
        elif resp.status_code != 200:
            raise ValueError(f"GitHub API Error: {resp.status_code}")
        return resp.json()

async def complete_oauth_login(telegram_id: int, access_token: str) -> Dict[str, Any]:
    """Validates token, encrypts, and saves user session in DB."""
    profile = await fetch_github_profile(access_token)
    github_id = profile["id"]
    github_username = profile["login"]
    avatar_url = profile.get("avatar_url", "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png")
    
    encrypted = encrypt_token(access_token)
    user_doc = await save_user(
        telegram_id=telegram_id,
        github_id=github_id,
        github_username=github_username,
        encrypted_token=encrypted,
        auth_method="oauth"
    )
    from bot.database.mongodb import update_user_settings
    await update_user_settings(telegram_id, {"avatar_url": avatar_url})
    user_doc["avatar_url"] = avatar_url
    return user_doc

async def authenticate_with_pat(telegram_id: int, pat: str) -> Dict[str, Any]:
    """Validates PAT, encrypts, and saves user session in DB."""
    profile = await fetch_github_profile(pat)
    github_id = profile["id"]
    github_username = profile["login"]
    avatar_url = profile.get("avatar_url", "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png")
    
    encrypted = encrypt_token(pat)
    user_doc = await save_user(
        telegram_id=telegram_id,
        github_id=github_id,
        github_username=github_username,
        encrypted_token=encrypted,
        auth_method="pat"
    )
    from bot.database.mongodb import update_user_settings
    await update_user_settings(telegram_id, {"avatar_url": avatar_url})
    user_doc["avatar_url"] = avatar_url
    return user_doc

async def get_user_decrypted_token(telegram_id: int) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Retrieves user document and decrypted token."""
    user = await get_user(telegram_id)
    if not user or not user.get("encrypted_token"):
        return None, None
    try:
        token = decrypt_token(user["encrypted_token"])
        return token, user
    except Exception as e:
        logger.error(f"Failed to decrypt token for user {telegram_id}: {e}")
        return None, user

async def disconnect_user_account(telegram_id: int) -> bool:
    """Disconnects account and deletes stored tokens & data."""
    return await delete_user(telegram_id)
