"""
InstaManager — Application Factory.

Creates and configures the Flask app, initialises extensions,
and registers blueprints. Uses Meta Graph API v21.0 for
Instagram Business account management.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# ── Extensions (imported by other modules) ───────────────────────────────────
db = SQLAlchemy()
socketio = SocketIO()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_object: str = "config.Config") -> Flask:
    """Application factory.

    Args:
        config_object: Dotted path to the config class.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(
        __name__,
        template_folder="flask_app/templates",
        static_folder="flask_app/static",
    )
    app.config.from_object(config_object)

    # ── Logging ──────────────────────────────────────────────────────────
    _configure_logging(app)

    # ── Extensions ───────────────────────────────────────────────────────
    db.init_app(app)
    socketio.init_app(app, async_mode="eventlet", cors_allowed_origins="*")
    csrf.init_app(app)

    # ── Flask-Login ──────────────────────────────────────────────────────
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.models import User
        return db.session.get(User, int(user_id))

    # ── Blueprints ───────────────────────────────────────────────────────
    from app.flask_app.routes import main_bp
    from app.flask_app.auth import auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)

    # ── Socket events ────────────────────────────────────────────────────
    from app.flask_app import socket_events  # noqa: F401  (registers handlers)

    # ── Database ─────────────────────────────────────────────────────────
    with app.app_context():
        from app import models  # noqa: F401  (registers models)
        db.create_all()

    # ── Scheduler ────────────────────────────────────────────────────────
    from app.instagram.scheduler import init_scheduler
    init_scheduler(app)

    return app


def _configure_logging(app: Flask) -> None:
    """Set up rotating file + console logging."""
    log_file: Path = app.config["LOG_FILE"]
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    )

    # File handler
    fh = RotatingFileHandler(
        str(log_file), maxBytes=2_000_000, backupCount=3
    )
    fh.setFormatter(formatter)
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.setLevel(logging.INFO)

    app.logger.addHandler(fh)
    app.logger.addHandler(ch)
    app.logger.setLevel(logging.INFO)

    # Also configure the root 'instamanager' logger for non-Flask modules
    logger = logging.getLogger("instamanager")
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)
