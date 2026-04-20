"""
InstaManager — Flask Routes.

Defines all HTTP endpoints for the web UI: dashboard, account management,
composer, bulk posting, scheduler view, templates, and live logs.
All routes require authentication via Flask-Login.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
    jsonify,
)
from flask_login import current_user, login_required

from app import db
from app.instagram.scheduler import schedule_post, cancel_post, get_scheduled_jobs
from app.models import InstagramAccount, ScheduledPost, Template, BulkJob

logger = logging.getLogger("instamanager")

main_bp = Blueprint("main", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/")
@login_required
def dashboard():
    """Dashboard — account health overview and quick stats."""
    accounts = InstagramAccount.query.filter_by(
        user_id=current_user.id
    ).all()

    total = len(accounts)
    healthy = sum(1 for a in accounts if a.token_health == "healthy")
    warning = sum(1 for a in accounts if a.token_health in ("warning", "critical"))
    expired = sum(1 for a in accounts if a.token_health == "expired")

    # Today's published count
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    published_today = ScheduledPost.query.filter(
        ScheduledPost.status == "Published",
        ScheduledPost.published_at >= today_start,
    ).count()

    recent_posts = (
        ScheduledPost.query
        .join(InstagramAccount)
        .filter(InstagramAccount.user_id == current_user.id)
        .order_by(ScheduledPost.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard.html",
        total=total,
        healthy=healthy,
        warning=warning,
        expired=expired,
        published_today=published_today,
        accounts=accounts,
        recent_posts=recent_posts,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/accounts")
@login_required
def accounts():
    """List all connected Instagram Business accounts."""
    accs = InstagramAccount.query.filter_by(
        user_id=current_user.id
    ).order_by(InstagramAccount.ig_username).all()

    meta_configured = bool(current_app.config.get("META_APP_ID"))

    return render_template(
        "accounts.html",
        accounts=accs,
        meta_configured=meta_configured,
    )


@main_bp.route("/accounts/<int:account_id>/refresh", methods=["POST"])
@login_required
def refresh_account_token(account_id: int):
    """Manually refresh an account's access token."""
    account = db.session.get(InstagramAccount, account_id)
    if not account or account.user_id != current_user.id:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))

    from app.instagram.graph_api import MetaGraphClient, TokenManager, GraphAPIError

    graph = MetaGraphClient(
        app_id=current_app.config["META_APP_ID"],
        app_secret=current_app.config["META_APP_SECRET"],
        api_version=current_app.config["META_GRAPH_API_VERSION"],
    )
    token_mgr = TokenManager(current_app.config["ENCRYPTION_KEY"])

    try:
        old_token = token_mgr.decrypt(account.access_token)
        result = graph.refresh_long_lived_token(old_token)
        new_token = result["access_token"]
        expires_in = result.get("expires_in", 5184000)

        account.access_token = token_mgr.encrypt(new_token)
        account.token_expires_at = token_mgr.calculate_expiry(expires_in)
        db.session.commit()

        flash(f"Token refreshed for @{account.ig_username}. Expires in {account.token_days_remaining} days.", "success")
        logger.info("Manually refreshed token for @%s", account.ig_username)
    except GraphAPIError as e:
        flash(f"Token refresh failed: {str(e)}", "error")
        logger.error("Token refresh failed for @%s: %s", account.ig_username, e)
    except Exception as e:
        flash(f"Refresh failed: {str(e)}", "error")

    return redirect(url_for("main.accounts"))


@main_bp.route("/accounts/<int:account_id>/disconnect", methods=["POST"])
@login_required
def disconnect_account(account_id: int):
    """Disconnect (deactivate) an Instagram account."""
    account = db.session.get(InstagramAccount, account_id)
    if not account or account.user_id != current_user.id:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))

    username = account.ig_username
    account.is_active = False
    db.session.commit()

    flash(f"Account @{username} disconnected.", "success")
    logger.info("Disconnected account @%s", username)
    return redirect(url_for("main.accounts"))


@main_bp.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_account(account_id: int):
    """Permanently delete an account and its posts."""
    account = db.session.get(InstagramAccount, account_id)
    if not account or account.user_id != current_user.id:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))

    username = account.ig_username
    ScheduledPost.query.filter_by(account_id=account_id).delete()
    db.session.delete(account)
    db.session.commit()

    flash(f"Account @{username} and all posts deleted.", "success")
    logger.info("Deleted account @%s", username)
    return redirect(url_for("main.accounts"))


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSER
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/composer")
@login_required
def composer():
    """Compose a new post."""
    accounts = InstagramAccount.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()
    templates = Template.query.all()
    return render_template("composer.html", accounts=accounts, templates=templates)


@main_bp.route("/composer/post", methods=["POST"])
@login_required
def composer_post():
    """Handle post creation (immediate or scheduled)."""
    account_ids = request.form.getlist("account_ids")
    caption = request.form.get("caption", "")
    media_url = request.form.get("media_url", "").strip()
    media_type = request.form.get("media_type", "IMAGE")
    action = request.form.get("action", "now")  # 'now' or 'schedule'
    schedule_time_str = request.form.get("schedule_time", "")

    if not account_ids:
        flash("Select at least one account.", "error")
        return redirect(url_for("main.composer"))

    if not media_url:
        flash("Please provide a public media URL.", "error")
        return redirect(url_for("main.composer"))

    # Validate URL starts with http
    if not media_url.startswith(("http://", "https://")):
        flash("Media URL must be a valid public URL (https://...).", "error")
        return redirect(url_for("main.composer"))

    created = 0
    for aid in account_ids:
        post = ScheduledPost(
            account_id=int(aid),
            caption=caption,
            media_url=media_url,
            media_type=media_type,
        )

        if action == "schedule" and schedule_time_str:
            post.status = "Pending"
            post.scheduled_time = datetime.fromisoformat(schedule_time_str).replace(
                tzinfo=timezone.utc
            )
            db.session.add(post)
            db.session.flush()
            schedule_post(post.id, post.scheduled_time, current_app._get_current_object())
        else:
            post.status = "Pending"
            db.session.add(post)
            db.session.flush()

            # Execute immediately in background
            from app.instagram.poster import execute_post
            execute_post(post.id)

        created += 1

    db.session.commit()

    if action == "schedule":
        flash(f"Scheduled {created} post(s) successfully.", "success")
    else:
        flash(f"Submitted {created} post(s) for publishing.", "success")

    return redirect(url_for("main.composer"))


# ═══════════════════════════════════════════════════════════════════════════════
# BULK POSTING
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/bulk")
@login_required
def bulk():
    """Bulk posting page."""
    accounts = InstagramAccount.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()
    templates = Template.query.all()
    return render_template("bulk.html", accounts=accounts, templates=templates)


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/scheduler")
@login_required
def scheduler_view():
    """View all scheduled posts."""
    posts = (
        ScheduledPost.query
        .join(InstagramAccount)
        .filter(
            InstagramAccount.user_id == current_user.id,
            ScheduledPost.status == "Pending",
            ScheduledPost.scheduled_time.isnot(None),
        )
        .order_by(ScheduledPost.scheduled_time.asc())
        .all()
    )
    return render_template("scheduler.html", posts=posts)


@main_bp.route("/scheduler/<int:post_id>/cancel", methods=["POST"])
@login_required
def cancel_scheduled(post_id: int):
    """Cancel a scheduled post."""
    post = db.session.get(ScheduledPost, post_id)
    if not post:
        flash("Post not found.", "error")
        return redirect(url_for("main.scheduler_view"))

    cancel_post(post_id)
    post.status = "Pending"
    post.scheduled_time = None
    db.session.commit()

    flash(f"Scheduled post {post_id} cancelled.", "success")
    return redirect(url_for("main.scheduler_view"))


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/templates")
@login_required
def templates_view():
    """List caption templates."""
    tmpls = Template.query.all()
    return render_template("templates.html", templates=tmpls)


@main_bp.route("/templates/add", methods=["POST"])
@login_required
def add_template():
    """Create a new caption template."""
    name = request.form.get("name", "").strip()
    caption = request.form.get("caption_template", "")
    hashtags_raw = request.form.get("hashtags", "")

    if not name:
        flash("Template name is required.", "error")
        return redirect(url_for("main.templates_view"))

    hashtags = [h.strip() for h in hashtags_raw.split(",") if h.strip()]

    tmpl = Template(
        name=name,
        caption_template=caption,
        hashtags=json.dumps(hashtags),
    )
    db.session.add(tmpl)
    db.session.commit()

    flash(f"Template '{name}' created.", "success")
    return redirect(url_for("main.templates_view"))


@main_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_template(template_id: int):
    """Delete a caption template."""
    tmpl = db.session.get(Template, template_id)
    if tmpl:
        db.session.delete(tmpl)
        db.session.commit()
        flash(f"Template '{tmpl.name}' deleted.", "success")
    return redirect(url_for("main.templates_view"))


# ═══════════════════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/logs")
@login_required
def logs():
    """Real-time log viewer."""
    return render_template("logs.html")


@main_bp.route("/api/logs")
@login_required
def api_logs():
    """Return the last N lines of the log file as JSON."""
    n = request.args.get("n", 100, type=int)
    log_path = current_app.config["LOG_FILE"]

    if not Path(log_path).exists():
        return jsonify({"lines": []})

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    return jsonify({"lines": lines[-n:]})


# ═══════════════════════════════════════════════════════════════════════════════
# API — for AJAX/SocketIO
# ═══════════════════════════════════════════════════════════════════════════════

@main_bp.route("/api/template/<int:template_id>")
@login_required
def api_get_template(template_id: int):
    """Return a template's data as JSON."""
    tmpl = db.session.get(Template, template_id)
    if not tmpl:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": tmpl.id,
        "name": tmpl.name,
        "caption_template": tmpl.caption_template,
        "hashtags": json.loads(tmpl.hashtags),
    })
