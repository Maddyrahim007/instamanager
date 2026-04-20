"""
InstaManager — Authentication Routes.

Handles user registration, login/logout, and the Facebook
OAuth 2.0 flow for connecting Instagram Business accounts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.instagram.graph_api import GraphAPIError, MetaGraphClient, TokenManager
from app.models import InstagramAccount, User

logger = logging.getLogger("instamanager")

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ═══════════════════════════════════════════════════════════════════════════════
# USER AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("login.html")

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user, remember=True)
            logger.info("User '%s' logged in.", user.username)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """User registration page."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()

        errors = []
        if not username or not email or not password:
            errors.append("All fields are required.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if User.query.filter_by(email=email).first():
            errors.append("Email already registered.")
        if User.query.filter_by(username=username).first():
            errors.append("Username already taken.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html")

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user, remember=True)
        logger.info("New user registered: %s (%s)", username, email)
        flash("Account created successfully! Connect your Instagram accounts below.", "success")
        return redirect(url_for("main.accounts"))

    return render_template("register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logger.info("User '%s' logged out.", current_user.username)
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth.login"))


# ═══════════════════════════════════════════════════════════════════════════════
# FACEBOOK / INSTAGRAM OAUTH 2.0
# ═══════════════════════════════════════════════════════════════════════════════


@auth_bp.route("/facebook")
@login_required
def facebook_login():
    """Redirect the user to Facebook's OAuth dialog.

    This initiates the Facebook Login flow to grant access to
    the user's Instagram Business accounts.
    """
    app_id = current_app.config["META_APP_ID"]
    redirect_uri = current_app.config["META_REDIRECT_URI"]
    scopes = current_app.config["META_SCOPES"]

    if not app_id:
        flash("Meta App ID is not configured. Please set META_APP_ID in .env", "error")
        return redirect(url_for("main.accounts"))

    # Build Facebook OAuth URL
    oauth_url = (
        f"https://www.facebook.com/{current_app.config['META_GRAPH_API_VERSION']}/dialog/oauth"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&state=instamanager"
    )

    logger.info("Redirecting to Facebook OAuth dialog")
    return redirect(oauth_url)


@auth_bp.route("/facebook/callback")
@login_required
def facebook_callback():
    """Handle the Facebook OAuth callback.

    Exchanges the authorization code for tokens, discovers Instagram
    Business accounts, and saves them with encrypted tokens.
    """
    code = request.args.get("code")
    error = request.args.get("error")
    error_reason = request.args.get("error_reason", "")

    if error:
        flash(f"Facebook authorization denied: {error_reason}", "error")
        logger.warning("OAuth denied: %s — %s", error, error_reason)
        return redirect(url_for("main.accounts"))

    if not code:
        flash("No authorization code received.", "error")
        return redirect(url_for("main.accounts"))

    graph = MetaGraphClient(
        app_id=current_app.config["META_APP_ID"],
        app_secret=current_app.config["META_APP_SECRET"],
        api_version=current_app.config["META_GRAPH_API_VERSION"],
    )
    token_mgr = TokenManager(current_app.config["ENCRYPTION_KEY"])

    try:
        # Step 1: Exchange code for short-lived token
        token_data = graph.exchange_code_for_token(
            code=code,
            redirect_uri=current_app.config["META_REDIRECT_URI"],
        )
        short_token = token_data["access_token"]

        # Step 2: Exchange for long-lived token (60 days)
        long_data = graph.get_long_lived_token(short_token)
        long_token = long_data["access_token"]
        expires_in = long_data.get("expires_in", 5184000)  # Default 60 days

        # Step 3: Discover Instagram Business accounts
        ig_accounts = graph.discover_all_ig_accounts(long_token)

        if not ig_accounts:
            flash(
                "No Instagram Business accounts found. "
                "Make sure your Instagram is connected to a Facebook Page "
                "and set as a Business or Creator account.",
                "warning",
            )
            return redirect(url_for("main.accounts"))

        # Step 4: Save/update each discovered account
        connected = 0
        for ig in ig_accounts:
            existing = InstagramAccount.query.filter_by(
                ig_user_id=ig["ig_user_id"]
            ).first()

            encrypted_token = token_mgr.encrypt(long_token)
            token_expiry = token_mgr.calculate_expiry(expires_in)

            if existing:
                # Update existing account
                existing.access_token = encrypted_token
                existing.token_expires_at = token_expiry
                existing.ig_username = ig["ig_username"]
                existing.profile_picture_url = ig.get("profile_picture_url")
                existing.fb_page_name = ig.get("fb_page_name", "")
                existing.is_active = True
                existing.user_id = current_user.id
                logger.info("Updated existing account @%s", ig["ig_username"])
            else:
                # Create new account
                account = InstagramAccount(
                    user_id=current_user.id,
                    ig_user_id=ig["ig_user_id"],
                    ig_username=ig["ig_username"],
                    profile_picture_url=ig.get("profile_picture_url"),
                    fb_page_id=ig["fb_page_id"],
                    fb_page_name=ig.get("fb_page_name", ""),
                    access_token=encrypted_token,
                    token_expires_at=token_expiry,
                    is_active=True,
                )
                db.session.add(account)
                logger.info("Connected new account @%s", ig["ig_username"])

            connected += 1

        db.session.commit()
        flash(f"Successfully connected {connected} Instagram account(s)!", "success")

    except GraphAPIError as e:
        flash(f"Meta API error: {str(e)}", "error")
        logger.error("OAuth callback API error: %s (code=%d)", e, e.code)

    except Exception as e:
        flash(f"Connection failed: {str(e)}", "error")
        logger.exception("OAuth callback unexpected error: %s", e)

    return redirect(url_for("main.accounts"))
