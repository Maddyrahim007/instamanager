"""
InstaManager — Post Execution Engine.

Handles the full lifecycle of publishing a ScheduledPost via
the Meta Graph API 2-phase flow (Container → Publish).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import db
from app.models import InstagramAccount, ScheduledPost

logger = logging.getLogger("instamanager")


def _get_graph_client():
    """Lazily create the MetaGraphClient from app config."""
    from flask import current_app
    from app.instagram.graph_api import MetaGraphClient

    return MetaGraphClient(
        app_id=current_app.config["META_APP_ID"],
        app_secret=current_app.config["META_APP_SECRET"],
        api_version=current_app.config["META_GRAPH_API_VERSION"],
    )


def _get_token_manager():
    """Lazily create the TokenManager from app config."""
    from flask import current_app
    from app.instagram.graph_api import TokenManager

    return TokenManager(current_app.config["ENCRYPTION_KEY"])


def execute_post(post_id: int, app=None) -> dict:
    """Execute a single post via the Meta Graph API 2-phase publish flow.

    Args:
        post_id: Database ID of the ScheduledPost record.
        app: Flask app instance (needed when called from scheduler).

    Returns:
        dict with 'success' (bool) and 'message' (str).
    """
    from flask import current_app

    if app:
        ctx = app.app_context()
        ctx.push()
    else:
        ctx = None

    try:
        post = db.session.get(ScheduledPost, post_id)
        if not post:
            return {"success": False, "message": f"Post {post_id} not found."}

        account = db.session.get(InstagramAccount, post.account_id)
        if not account:
            post.status = "Failed"
            post.error_message = "Account not found."
            db.session.commit()
            return {"success": False, "message": "Account not found."}

        if not account.is_active:
            post.status = "Failed"
            post.error_message = "Account is deactivated."
            db.session.commit()
            return {"success": False, "message": "Account is deactivated."}

        # Check token health
        if account.token_health == "expired":
            post.status = "Failed"
            post.error_message = "Access token has expired. Please reconnect the account."
            db.session.commit()
            return {"success": False, "message": "Token expired."}

        # Decrypt access token
        token_mgr = _get_token_manager()
        try:
            access_token = token_mgr.decrypt(account.access_token)
        except Exception as e:
            post.status = "Failed"
            post.error_message = f"Token decryption failed: {str(e)}"
            db.session.commit()
            return {"success": False, "message": "Token decryption failed."}

        # Update status to Publishing
        post.status = "Publishing"
        db.session.commit()

        graph = _get_graph_client()

        try:
            # Execute the 2-phase publish
            result = graph.publish_single_media(
                ig_user_id=account.ig_user_id,
                token=access_token,
                media_url=post.media_url,
                media_type=post.media_type,
                caption=post.caption,
            )

            # Success — update the post record
            post.status = "Published"
            post.container_id = result["container_id"]
            post.published_media_id = result["media_id"]
            post.published_at = datetime.now(timezone.utc)
            post.error_message = None
            db.session.commit()

            logger.info(
                "Post %d published successfully for @%s (media_id=%s)",
                post_id, account.ig_username, result["media_id"],
            )
            return {
                "success": True,
                "message": f"Published successfully (ID: {result['media_id']}).",
            }

        except Exception as e:
            post.status = "Failed"
            post.error_message = str(e)
            post.retry_count += 1
            db.session.commit()

            logger.error(
                "Post %d failed for @%s: %s",
                post_id, account.ig_username, e,
            )
            return {"success": False, "message": f"Publish failed: {str(e)}"}

    except Exception as e:
        logger.exception("Unexpected error executing post %d: %s", post_id, e)
        return {"success": False, "message": f"Unexpected error: {str(e)}"}

    finally:
        if ctx:
            ctx.pop()
