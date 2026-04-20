"""
InstaManager — SQLAlchemy Database Models.

Defines User, InstagramAccount, ScheduledPost, Template, and BulkJob tables
for the Meta Graph API v21.0 based Instagram management platform.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


class User(UserMixin, db.Model):
    """Application user for dashboard login."""

    __tablename__ = "users"

    id: int = db.Column(db.Integer, primary_key=True)
    email: str = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username: str = db.Column(db.String(150), unique=True, nullable=False)
    password_hash: str = db.Column(db.Text, nullable=False)
    created_at: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    instagram_accounts = db.relationship(
        "InstagramAccount", backref="owner", lazy="dynamic"
    )

    def set_password(self, password: str) -> None:
        """Hash and store a plaintext password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class InstagramAccount(db.Model):
    """Instagram Business/Creator account connected via Meta OAuth 2.0."""

    __tablename__ = "instagram_accounts"

    id: int = db.Column(db.Integer, primary_key=True)
    user_id: int = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )

    # Instagram Business Account identifiers
    ig_user_id: str = db.Column(db.String(100), unique=True, nullable=False)
    ig_username: str = db.Column(db.String(150), nullable=False)
    profile_picture_url: str | None = db.Column(db.Text, nullable=True)

    # Facebook Page link
    fb_page_id: str = db.Column(db.String(100), nullable=False)
    fb_page_name: str = db.Column(db.String(300), nullable=True)

    # Encrypted Long-Lived Access Token (60 days)
    access_token: str = db.Column(db.Text, nullable=False)  # Fernet-encrypted
    token_expires_at: datetime | None = db.Column(db.DateTime, nullable=True)

    is_active: bool = db.Column(db.Boolean, default=True)
    connected_at: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    posts = db.relationship("ScheduledPost", backref="account", lazy="dynamic")

    @property
    def token_days_remaining(self) -> int | None:
        """Days until the access token expires."""
        if not self.token_expires_at:
            return None
        delta = self.token_expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)

    @property
    def token_health(self) -> str:
        """Token health status: healthy, warning, critical, expired."""
        days = self.token_days_remaining
        if days is None:
            return "unknown"
        if days <= 0:
            return "expired"
        if days <= 7:
            return "critical"
        if days <= 14:
            return "warning"
        return "healthy"

    def __repr__(self) -> str:
        return f"<InstagramAccount @{self.ig_username} [{self.token_health}]>"


class ScheduledPost(db.Model):
    """A post to be published via the Meta Graph API 2-phase flow."""

    __tablename__ = "scheduled_posts"

    id: int = db.Column(db.Integer, primary_key=True)
    account_id: int = db.Column(
        db.Integer, db.ForeignKey("instagram_accounts.id"), nullable=False
    )

    # Media (public URL required by Graph API)
    media_url: str = db.Column(db.Text, nullable=False)
    media_type: str = db.Column(
        db.String(30), default="IMAGE"
    )  # IMAGE, VIDEO, CAROUSEL_ALBUM, STORIES, REELS

    # Content
    caption: str = db.Column(db.Text, default="")

    # Scheduling
    scheduled_time: datetime | None = db.Column(db.DateTime, nullable=True)

    # Status tracking
    status: str = db.Column(
        db.String(30), default="Pending"
    )  # Pending, Publishing, Published, Failed

    # Graph API IDs from the 2-phase publish flow
    container_id: str | None = db.Column(db.String(100), nullable=True)
    published_media_id: str | None = db.Column(db.String(100), nullable=True)

    # Error handling
    error_message: str | None = db.Column(db.Text, nullable=True)
    retry_count: int = db.Column(db.Integer, default=0)

    # Timestamps
    created_at: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    published_at: datetime | None = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<ScheduledPost {self.id} [{self.status}] for @{self.account.ig_username if self.account else 'N/A'}>"


class Template(db.Model):
    """Reusable caption template with hashtags."""

    __tablename__ = "templates"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(200), nullable=False)
    caption_template: str = db.Column(db.Text, default="")
    hashtags: str = db.Column(db.Text, default="[]")  # JSON list

    def __repr__(self) -> str:
        return f"<Template {self.name}>"


class BulkJob(db.Model):
    """Tracks a bulk-posting job across multiple accounts."""

    __tablename__ = "bulk_jobs"

    id: int = db.Column(db.Integer, primary_key=True)
    user_id: int = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    template_id: int | None = db.Column(
        db.Integer, db.ForeignKey("templates.id"), nullable=True
    )
    account_ids: str = db.Column(db.Text, default="[]")  # JSON list
    status: str = db.Column(
        db.String(20), default="pending"
    )  # pending, running, done, failed
    total_count: int = db.Column(db.Integer, default=0)
    success_count: int = db.Column(db.Integer, default=0)
    fail_count: int = db.Column(db.Integer, default=0)
    created_at: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    template = db.relationship("Template", backref="bulk_jobs")

    def __repr__(self) -> str:
        return f"<BulkJob {self.id} [{self.status}]>"
