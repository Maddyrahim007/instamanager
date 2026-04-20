"""
InstaManager — SocketIO Event Handlers.

Provides real-time communication for bulk posting progress
and live log streaming. Uses Meta Graph API for publishing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from flask import current_app
from flask_socketio import emit

from app import db, socketio
from app.models import InstagramAccount, ScheduledPost, BulkJob

logger = logging.getLogger("instamanager")


@socketio.on("connect")
def handle_connect():
    """Client connected."""
    logger.info("SocketIO client connected")
    emit("server_message", {"message": "Connected to InstaManager."})


@socketio.on("disconnect")
def handle_disconnect():
    """Client disconnected."""
    logger.info("SocketIO client disconnected")


@socketio.on("start_bulk_post")
def handle_bulk_post(data: dict):
    """Execute a bulk post across multiple accounts via Graph API.

    Expected data:
        account_ids: list[int]
        caption: str
        media_url: str  (public URL)
        media_type: str (IMAGE, VIDEO, REELS, STORIES)
        template_id: int | None
    """
    account_ids: list[int] = data.get("account_ids", [])
    caption: str = data.get("caption", "")
    media_url: str = data.get("media_url", "")
    media_type: str = data.get("media_type", "IMAGE")
    template_id: int | None = data.get("template_id")

    if not account_ids:
        emit("bulk_error", {"message": "No accounts selected."})
        return

    if not media_url:
        emit("bulk_error", {"message": "No media URL provided."})
        return

    # Create BulkJob record
    job = BulkJob(
        template_id=template_id,
        account_ids=json.dumps(account_ids),
        status="running",
        total_count=len(account_ids),
    )
    db.session.add(job)
    db.session.commit()

    emit("bulk_started", {
        "job_id": job.id,
        "total": len(account_ids),
    })

    from app.instagram.poster import execute_post

    success_count = 0
    fail_count = 0

    for i, aid in enumerate(account_ids, 1):
        account = db.session.get(InstagramAccount, aid)
        if not account:
            emit("bulk_progress", {
                "index": i,
                "username": f"ID:{aid}",
                "status": "error",
                "message": "Account not found.",
            })
            fail_count += 1
            continue

        emit("bulk_progress", {
            "index": i,
            "username": account.ig_username,
            "status": "uploading",
            "message": "Publishing via Graph API...",
        })

        # Create post record
        post = ScheduledPost(
            account_id=aid,
            caption=caption,
            media_url=media_url,
            media_type=media_type,
            status="Pending",
        )
        db.session.add(post)
        db.session.commit()

        # Execute via Graph API
        result = execute_post(post.id)

        if result["success"]:
            success_count += 1
            emit("bulk_progress", {
                "index": i,
                "username": account.ig_username,
                "status": "success",
                "message": result["message"],
            })
        else:
            fail_count += 1
            emit("bulk_progress", {
                "index": i,
                "username": account.ig_username,
                "status": "failed",
                "message": result["message"],
            })

        # Rate-limit delay between accounts (3-5 seconds)
        if i < len(account_ids):
            time.sleep(3)

    job.status = "done" if fail_count == 0 else "failed"
    job.success_count = success_count
    job.fail_count = fail_count
    db.session.commit()

    emit("bulk_complete", {
        "job_id": job.id,
        "success": success_count,
        "failed": fail_count,
        "total": len(account_ids),
    })


@socketio.on("request_logs")
def handle_log_request(data: dict):
    """Stream the last N log lines to the client."""
    n = data.get("lines", 50)
    app = current_app._get_current_object()
    log_path = Path(app.config["LOG_FILE"])

    if not log_path.exists():
        emit("log_lines", {"lines": ["No log file found yet."]})
        return

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    emit("log_lines", {"lines": lines[-n:]})
