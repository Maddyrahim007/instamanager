"""
InstaManager — APScheduler Integration.

Manages background scheduling of posts and automated token refresh.
On startup, re-queues any posts with status='Pending' and a future
scheduled_time timestamp. Automatically refreshes tokens expiring
within 10 days.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore

logger = logging.getLogger("instamanager")

# Module-level scheduler instance
scheduler = BackgroundScheduler(
    jobstores={"default": MemoryJobStore()},
    job_defaults={"coalesce": True, "max_instances": 10},
)


def _run_post_job(post_id: int, app) -> None:
    """Callback executed by APScheduler for a scheduled post."""
    from app.instagram.poster import execute_post
    logger.info("Scheduler firing job for post %d", post_id)
    execute_post(post_id, app=app)


def _refresh_expiring_tokens(app) -> None:
    """Refresh all access tokens expiring within 10 days.

    Runs as a daily scheduled job to keep tokens alive.
    """
    with app.app_context():
        from app import db
        from app.models import InstagramAccount
        from app.instagram.graph_api import MetaGraphClient, TokenManager

        threshold = datetime.now(timezone.utc) + timedelta(days=10)

        expiring = InstagramAccount.query.filter(
            InstagramAccount.is_active == True,  # noqa: E712
            InstagramAccount.token_expires_at.isnot(None),
            InstagramAccount.token_expires_at <= threshold,
        ).all()

        if not expiring:
            logger.info("Token refresh: No tokens expiring within 10 days.")
            return

        graph = MetaGraphClient(
            app_id=app.config["META_APP_ID"],
            app_secret=app.config["META_APP_SECRET"],
            api_version=app.config["META_GRAPH_API_VERSION"],
        )
        token_mgr = TokenManager(app.config["ENCRYPTION_KEY"])

        refreshed = 0
        for account in expiring:
            try:
                old_token = token_mgr.decrypt(account.access_token)
                result = graph.refresh_long_lived_token(old_token)
                new_token = result["access_token"]
                expires_in = result.get("expires_in", 5184000)  # Default 60 days

                account.access_token = token_mgr.encrypt(new_token)
                account.token_expires_at = token_mgr.calculate_expiry(expires_in)
                refreshed += 1

                logger.info(
                    "Refreshed token for @%s (new expiry: %s)",
                    account.ig_username, account.token_expires_at,
                )
            except Exception as e:
                logger.error(
                    "Failed to refresh token for @%s: %s",
                    account.ig_username, e,
                )

        db.session.commit()
        logger.info("Token refresh complete: %d/%d refreshed.", refreshed, len(expiring))


def schedule_post(post_id: int, run_at: datetime, app) -> str:
    """Add a post to the scheduler.

    Args:
        post_id: Database ID of the ScheduledPost.
        run_at: When to execute the post (UTC datetime).
        app: Flask app instance for context.

    Returns:
        The APScheduler job ID.
    """
    job_id = f"post_{post_id}"

    # Remove existing job if re-scheduling
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    scheduler.add_job(
        _run_post_job,
        trigger="date",
        run_date=run_at,
        id=job_id,
        args=[post_id, app],
        replace_existing=True,
    )
    logger.info("Scheduled post %d for %s (job_id=%s)", post_id, run_at, job_id)
    return job_id


def cancel_post(post_id: int) -> bool:
    """Remove a scheduled post job.

    Returns:
        True if the job was found and removed, False otherwise.
    """
    job_id = f"post_{post_id}"
    try:
        scheduler.remove_job(job_id)
        logger.info("Cancelled scheduled job %s", job_id)
        return True
    except Exception:
        logger.warning("Job %s not found in scheduler", job_id)
        return False


def get_scheduled_jobs() -> list[dict]:
    """Return a list of all pending scheduler jobs."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time),
            "name": job.name,
        })
    return jobs


def init_scheduler(app) -> None:
    """Start the scheduler and set up recurring jobs."""
    from app import db
    from app.models import ScheduledPost

    if scheduler.running:
        return

    scheduler.start()
    logger.info("APScheduler started.")

    # ── Daily token refresh job (runs at 3:00 AM UTC) ────────────────────
    scheduler.add_job(
        _refresh_expiring_tokens,
        trigger="cron",
        hour=3,
        minute=0,
        id="token_refresh",
        args=[app],
        replace_existing=True,
    )
    logger.info("Scheduled daily token refresh job at 03:00 UTC.")

    # ── Re-queue pending scheduled posts ─────────────────────────────────
    with app.app_context():
        now = datetime.now(timezone.utc)
        pending = ScheduledPost.query.filter(
            ScheduledPost.status == "Pending",
            ScheduledPost.scheduled_time.isnot(None),
            ScheduledPost.scheduled_time > now,
        ).all()

        for post in pending:
            schedule_post(post.id, post.scheduled_time, app)

        if pending:
            logger.info("Re-queued %d scheduled posts.", len(pending))
