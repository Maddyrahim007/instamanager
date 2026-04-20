"""
InstaManager Configuration Module.

Handles loading of environment variables, auto-generation of secret keys,
and path configuration for sessions, media, and the database.
Supports Meta Graph API v21.0 via OAuth 2.0.
"""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv, set_key

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).resolve().parent
ENV_FILE: Path = BASE_DIR / ".env"
LOG_FILE: Path = BASE_DIR / "instamanager.log"

# Ensure directories exist
(BASE_DIR / "media").mkdir(exist_ok=True)


def _ensure_env_key(key: str, generator) -> str:
    """Read *key* from the environment; if missing, generate & persist it."""
    value = os.getenv(key)
    if not value:
        value = generator()
        # Create .env if it doesn't exist
        if not ENV_FILE.exists():
            ENV_FILE.touch()
        set_key(str(ENV_FILE), key, value)
    return value


# Load existing .env first
load_dotenv(ENV_FILE)

# Auto-generate keys on first run
SECRET_KEY: str = _ensure_env_key(
    "SECRET_KEY", lambda: secrets.token_hex(32)
)
ENCRYPTION_KEY: str = _ensure_env_key(
    "ENCRYPTION_KEY", lambda: Fernet.generate_key().decode()
)


class Config:
    """Flask application configuration."""

    SECRET_KEY: str = SECRET_KEY
    ENCRYPTION_KEY: str = ENCRYPTION_KEY

    # ── Database ─────────────────────────────────────────────────────────
    # PostgreSQL in production, SQLite fallback for development
    _db_url = os.getenv("DATABASE_URL")
    SQLALCHEMY_DATABASE_URI: str = _db_url if _db_url else f"sqlite:///{BASE_DIR / 'instamanager.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ── Meta / Facebook / Instagram Graph API ────────────────────────────
    META_APP_ID: str = os.getenv("META_APP_ID", "")
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    META_REDIRECT_URI: str = os.getenv(
        "META_REDIRECT_URI", "http://localhost:5000/auth/facebook/callback"
    )
    META_GRAPH_API_VERSION: str = "v21.0"
    META_GRAPH_BASE_URL: str = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"

    # OAuth 2.0 scopes required for content publishing
    META_SCOPES: str = (
        "instagram_basic,"
        "instagram_content_publish,"
        "pages_show_list,"
        "pages_read_engagement"
    )

    # ── Paths ────────────────────────────────────────────────────────────
    LOG_FILE: Path = LOG_FILE
    MEDIA_DIR: Path = BASE_DIR / "media"

    # Upload limits (16 MB max)
    MAX_CONTENT_LENGTH: int = 16 * 1024 * 1024

    # WTF CSRF
    WTF_CSRF_ENABLED: bool = True
