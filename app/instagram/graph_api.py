"""
InstaManager — Meta Graph API v21.0 Client.

Provides all interactions with the Meta Graph API including:
- OAuth 2.0 token exchange (short → long-lived)
- Token refresh
- Facebook Page and Instagram Business Account discovery
- Media container creation (Phase 1 of publishing)
- Media publishing (Phase 2 of publishing)
- Rate limit handling with exponential backoff
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from cryptography.fernet import Fernet

logger = logging.getLogger("instamanager")

# Meta API rate limit error codes
RATE_LIMIT_CODES = {4, 17, 32, 613}


class GraphAPIError(Exception):
    """Raised when the Meta Graph API returns an error."""

    def __init__(self, message: str, code: int = 0, subcode: int = 0):
        self.code = code
        self.subcode = subcode
        super().__init__(message)


class MetaGraphClient:
    """Client for Meta Graph API v21.0 interactions."""

    def __init__(self, app_id: str, app_secret: str, api_version: str = "v21.0"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{api_version}"

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        data: dict | None = None,
        max_retries: int = 3,
    ) -> dict:
        """Make an API request with rate limit handling and retries.

        Args:
            method: HTTP method (GET, POST).
            endpoint: API endpoint path (e.g., '/me/accounts').
            params: Query parameters.
            data: POST body data.
            max_retries: Maximum retry attempts on rate limit.

        Returns:
            Parsed JSON response.

        Raises:
            GraphAPIError: On API error after retries exhausted.
        """
        url = f"{self.base_url}{endpoint}" if endpoint.startswith("/") else endpoint

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.request(
                    method, url, params=params, data=data, timeout=30
                )
                result = response.json()

                # Check for Graph API error
                if "error" in result:
                    error = result["error"]
                    code = error.get("code", 0)
                    subcode = error.get("error_subcode", 0)
                    message = error.get("message", "Unknown error")

                    # Rate limit — exponential backoff
                    if code in RATE_LIMIT_CODES or response.status_code == 429:
                        wait = min(60 * (2 ** (attempt - 1)), 300)
                        logger.warning(
                            "Rate limited (code %d), waiting %ds (attempt %d/%d)",
                            code, wait, attempt, max_retries,
                        )
                        time.sleep(wait)
                        continue

                    raise GraphAPIError(message, code, subcode)

                return result

            except requests.exceptions.RequestException as e:
                if attempt == max_retries:
                    raise GraphAPIError(f"Network error: {str(e)}")
                time.sleep(5 * attempt)

        raise GraphAPIError("Max retries exhausted")

    # ── OAuth 2.0 Token Exchange ─────────────────────────────────────────

    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict:
        """Exchange an authorization code for a short-lived access token.

        Args:
            code: Authorization code from Facebook Login redirect.
            redirect_uri: Must match the redirect URI used in the login URL.

        Returns:
            dict with 'access_token', 'token_type', 'expires_in'.
        """
        result = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        logger.info("Exchanged auth code for short-lived token")
        return result

    def get_long_lived_token(self, short_lived_token: str) -> dict:
        """Exchange a short-lived token for a long-lived token (60 days).

        Args:
            short_lived_token: The short-lived access token.

        Returns:
            dict with 'access_token', 'token_type', 'expires_in' (seconds).
        """
        result = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
        logger.info(
            "Obtained long-lived token (expires in %d seconds)",
            result.get("expires_in", 0),
        )
        return result

    def refresh_long_lived_token(self, token: str) -> dict:
        """Refresh a long-lived token before it expires.

        Only works for tokens that haven't expired yet.

        Args:
            token: Current long-lived access token.

        Returns:
            dict with new 'access_token' and 'expires_in'.
        """
        result = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": token,
            },
        )
        logger.info("Refreshed long-lived token")
        return result

    # ── Account Discovery ────────────────────────────────────────────────

    def get_user_pages(self, token: str) -> list[dict]:
        """Fetch all Facebook Pages the user manages.

        Args:
            token: User's access token.

        Returns:
            List of dicts with 'id', 'name', 'access_token' per page.
        """
        result = self._request(
            "GET",
            "/me/accounts",
            params={
                "access_token": token,
                "fields": "id,name,access_token",
            },
        )
        pages = result.get("data", [])
        logger.info("Found %d Facebook Page(s)", len(pages))
        return pages

    def get_ig_business_account(self, page_id: str, page_token: str) -> dict | None:
        """Fetch the Instagram Business Account linked to a Facebook Page.

        Args:
            page_id: Facebook Page ID.
            page_token: Page-specific access token.

        Returns:
            dict with 'id', 'username', 'profile_picture_url' or None.
        """
        result = self._request(
            "GET",
            f"/{page_id}",
            params={
                "access_token": page_token,
                "fields": "instagram_business_account{id,username,profile_picture_url}",
            },
        )

        ig_data = result.get("instagram_business_account")
        if ig_data:
            logger.info(
                "Found IG Business Account @%s for Page %s",
                ig_data.get("username", "?"),
                page_id,
            )
        return ig_data

    def discover_all_ig_accounts(self, token: str) -> list[dict]:
        """Discover all Instagram Business Accounts across all pages.

        Args:
            token: User's long-lived access token.

        Returns:
            List of dicts, each containing:
            - ig_user_id, ig_username, profile_picture_url
            - fb_page_id, fb_page_name, page_access_token
        """
        pages = self.get_user_pages(token)
        accounts = []

        for page in pages:
            ig = self.get_ig_business_account(page["id"], page["access_token"])
            if ig:
                accounts.append({
                    "ig_user_id": ig["id"],
                    "ig_username": ig.get("username", ""),
                    "profile_picture_url": ig.get("profile_picture_url", ""),
                    "fb_page_id": page["id"],
                    "fb_page_name": page.get("name", ""),
                    "page_access_token": page["access_token"],
                })

        logger.info("Discovered %d IG Business account(s) total", len(accounts))
        return accounts

    # ── 2-Phase Publishing ───────────────────────────────────────────────

    def create_media_container(
        self,
        ig_user_id: str,
        token: str,
        media_type: str = "IMAGE",
        image_url: str | None = None,
        video_url: str | None = None,
        caption: str = "",
        is_carousel_item: bool = False,
    ) -> str:
        """Phase 1: Create a media container.

        Args:
            ig_user_id: Instagram Business Account ID.
            token: Access token for the account.
            media_type: IMAGE, VIDEO, REELS, or STORIES.
            image_url: Public URL for image content.
            video_url: Public URL for video content.
            caption: Post caption (ignored for carousel items).
            is_carousel_item: True if this is a child of a carousel.

        Returns:
            The creation_id (container ID) string.
        """
        params = {
            "access_token": token,
        }

        if media_type in ("IMAGE", "STORIES"):
            if not image_url:
                raise GraphAPIError("image_url required for IMAGE/STORIES")
            params["image_url"] = image_url
        elif media_type in ("VIDEO", "REELS"):
            if not video_url:
                raise GraphAPIError("video_url required for VIDEO/REELS")
            params["video_url"] = video_url
            params["media_type"] = media_type

        if not is_carousel_item and caption:
            params["caption"] = caption

        if is_carousel_item:
            params["is_carousel_item"] = "true"

        result = self._request("POST", f"/{ig_user_id}/media", params=params)
        container_id = result.get("id")

        if not container_id:
            raise GraphAPIError("No container ID returned from API")

        logger.info(
            "Created %s container %s for @%s",
            media_type, container_id, ig_user_id,
        )
        return container_id

    def create_carousel_container(
        self,
        ig_user_id: str,
        token: str,
        children_ids: list[str],
        caption: str = "",
    ) -> str:
        """Create a carousel (album) parent container.

        Args:
            ig_user_id: Instagram Business Account ID.
            token: Access token.
            children_ids: List of child container IDs.
            caption: Carousel caption.

        Returns:
            The parent container ID string.
        """
        result = self._request(
            "POST",
            f"/{ig_user_id}/media",
            params={
                "access_token": token,
                "media_type": "CAROUSEL",
                "caption": caption,
                "children": ",".join(children_ids),
            },
        )
        container_id = result.get("id")
        logger.info("Created carousel container %s with %d children", container_id, len(children_ids))
        return container_id

    def check_container_status(self, container_id: str, token: str) -> dict:
        """Check the status of a media container (for video processing).

        Args:
            container_id: The container ID to check.
            token: Access token.

        Returns:
            dict with 'status_code' (EXPIRED, ERROR, FINISHED, IN_PROGRESS, PUBLISHED).
        """
        result = self._request(
            "GET",
            f"/{container_id}",
            params={
                "access_token": token,
                "fields": "status_code,status",
            },
        )
        return result

    def wait_for_container_ready(
        self,
        container_id: str,
        token: str,
        timeout: int = 300,
        poll_interval: int = 10,
    ) -> bool:
        """Poll container status until FINISHED or timeout.

        Used for video/reel containers that require transcoding.

        Args:
            container_id: Container to wait for.
            token: Access token.
            timeout: Max seconds to wait.
            poll_interval: Seconds between status checks.

        Returns:
            True if container is ready, False if timed out.
        """
        start = time.time()
        while time.time() - start < timeout:
            status = self.check_container_status(container_id, token)
            code = status.get("status_code", "")

            if code == "FINISHED":
                return True
            elif code in ("ERROR", "EXPIRED"):
                error_msg = status.get("status", {})
                raise GraphAPIError(
                    f"Container {container_id} failed: {code} — {error_msg}"
                )

            logger.info(
                "Container %s status: %s — waiting...", container_id, code
            )
            time.sleep(poll_interval)

        raise GraphAPIError(f"Container {container_id} timed out after {timeout}s")

    def publish_media(self, ig_user_id: str, container_id: str, token: str) -> str:
        """Phase 2: Publish a media container.

        Args:
            ig_user_id: Instagram Business Account ID.
            container_id: The container ID from Phase 1.
            token: Access token.

        Returns:
            The published media ID string.
        """
        result = self._request(
            "POST",
            f"/{ig_user_id}/media_publish",
            params={
                "access_token": token,
                "creation_id": container_id,
            },
        )
        media_id = result.get("id")
        logger.info("Published media %s from container %s", media_id, container_id)
        return media_id

    # ── Convenience: Full publish flow ───────────────────────────────────

    def publish_single_media(
        self,
        ig_user_id: str,
        token: str,
        media_url: str,
        media_type: str = "IMAGE",
        caption: str = "",
    ) -> dict:
        """Full 2-phase publish for a single image, video, reel, or story.

        Args:
            ig_user_id: Instagram Business Account ID.
            token: Access token.
            media_url: Public URL of the media.
            media_type: IMAGE, VIDEO, REELS, or STORIES.
            caption: Post caption.

        Returns:
            dict with 'container_id' and 'media_id'.
        """
        # Phase 1: Create container
        is_video = media_type in ("VIDEO", "REELS")
        container_id = self.create_media_container(
            ig_user_id=ig_user_id,
            token=token,
            media_type=media_type,
            image_url=None if is_video else media_url,
            video_url=media_url if is_video else None,
            caption=caption,
        )

        # Wait for video processing if needed
        if is_video:
            self.wait_for_container_ready(container_id, token)

        # Phase 2: Publish
        media_id = self.publish_media(ig_user_id, container_id, token)

        return {"container_id": container_id, "media_id": media_id}


class TokenManager:
    """Handles encryption and lifecycle management of access tokens."""

    def __init__(self, encryption_key: str):
        self._fernet = Fernet(encryption_key.encode())

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext token for database storage."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a stored token."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def calculate_expiry(expires_in: int) -> datetime:
        """Calculate the expiry datetime from an expires_in value in seconds."""
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in)
