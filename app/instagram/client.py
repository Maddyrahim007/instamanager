"""
InstaManager — Instagram Client Manager.

Wraps `instagrapi.Client` with session persistence, proxy support,
password encryption, and robust exception handling.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    LoginRequired,
    TwoFactorRequired,
    PleaseWaitFewMinutes,
)

logger = logging.getLogger("instamanager")


class ClientManager:
    """Manages a pool of instagrapi Client instances for multiple accounts."""

    def __init__(self, encryption_key: str, sessions_dir: Path) -> None:
        self._fernet = Fernet(encryption_key.encode())
        self._sessions_dir = sessions_dir
        self._clients: dict[str, Client] = {}
        self._sessions_dir.mkdir(exist_ok=True)

    # ── Encryption helpers ───────────────────────────────────────────────

    def encrypt_password(self, plain: str) -> str:
        """Encrypt a plaintext password for DB storage."""
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt_password(self, token: str) -> str:
        """Decrypt a stored password token."""
        return self._fernet.decrypt(token.encode()).decode()

    # ── Session helpers ──────────────────────────────────────────────────

    def _session_path(self, username: str) -> Path:
        return self._sessions_dir / f"{username}.json"

    def _save_session(self, username: str, cl: Client) -> str:
        """Persist client session to JSON and return the file path."""
        path = self._session_path(username)
        settings = cl.get_settings()
        path.write_text(json.dumps(settings, indent=2))
        logger.info("Session saved for %s", username)
        return str(path)

    def _load_session(self, username: str, cl: Client) -> bool:
        """Load a previously saved session. Returns True on success."""
        path = self._session_path(username)
        if not path.exists():
            return False
        try:
            settings = json.loads(path.read_text())
            cl.set_settings(settings)
            cl.login(username, "")  # Attempt to reuse session without password
            logger.info("Session restored for %s", username)
            return True
        except Exception:
            logger.warning("Session restore failed for %s, will re-login", username)
            return False

    # ── Login ────────────────────────────────────────────────────────────

    def login(
        self,
        username: str,
        encrypted_password: str,
        proxy: Optional[str] = None,
        totp_code: Optional[str] = None,
    ) -> dict:
        """Log in to Instagram for the given account.

        Args:
            username: Instagram username.
            encrypted_password: Fernet-encrypted password from DB.
            proxy: Optional HTTP proxy string.
            totp_code: Optional 2FA TOTP code.

        Returns:
            dict with keys 'success' (bool), 'status' (str), 'message' (str).
        """
        cl = Client()
        cl.delay_range = [2, 5]  # Human-like delay between requests

        if proxy:
            cl.set_proxy(proxy)

        password = self.decrypt_password(encrypted_password)

        # Try session first
        if self._load_session(username, cl):
            self._clients[username] = cl
            return {
                "success": True,
                "status": "logged_in",
                "message": "Logged in via saved session.",
                "session_path": str(self._session_path(username)),
            }

        # Fresh login with retries
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if totp_code:
                    cl.login(username, password, verification_code=totp_code)
                else:
                    cl.login(username, password)

                session_path = self._save_session(username, cl)
                self._clients[username] = cl
                logger.info("Login successful for %s", username)
                return {
                    "success": True,
                    "status": "logged_in",
                    "message": "Login successful.",
                    "session_path": session_path,
                }

            except BadPassword:
                logger.error("Bad password for %s", username)
                return {
                    "success": False,
                    "status": "error",
                    "message": "Incorrect password.",
                }

            except TwoFactorRequired:
                logger.warning("2FA required for %s", username)
                return {
                    "success": False,
                    "status": "2fa_required",
                    "message": "Two-factor authentication code required.",
                }

            except ChallengeRequired:
                logger.warning("Challenge required for %s", username)
                return {
                    "success": False,
                    "status": "challenge_required",
                    "message": "Instagram challenge required. Please verify on the app.",
                }

            except LoginRequired:
                logger.warning("Login required again for %s (attempt %d)", username, attempt)
                # Clear stale session
                path = self._session_path(username)
                if path.exists():
                    path.unlink()
                continue

            except PleaseWaitFewMinutes:
                wait = min(60 * attempt, 300)
                logger.warning(
                    "Rate limited for %s, waiting %ds (attempt %d/%d)",
                    username, wait, attempt, max_retries,
                )
                time.sleep(wait)
                continue

            except Exception as e:
                logger.exception("Unexpected login error for %s: %s", username, e)
                return {
                    "success": False,
                    "status": "error",
                    "message": f"Login failed: {str(e)}",
                }

        return {
            "success": False,
            "status": "error",
            "message": "Login failed after maximum retries.",
        }

    # ── Client access ────────────────────────────────────────────────────

    def get_client(self, username: str) -> Optional[Client]:
        """Return the logged-in Client for a username, or None."""
        return self._clients.get(username)

    def logout(self, username: str) -> None:
        """Log out and discard the client for a username."""
        cl = self._clients.pop(username, None)
        if cl:
            try:
                cl.logout()
            except Exception:
                pass
        path = self._session_path(username)
        if path.exists():
            path.unlink()
        logger.info("Logged out %s", username)

    def is_logged_in(self, username: str) -> bool:
        """Check if a client is currently authenticated."""
        return username in self._clients
