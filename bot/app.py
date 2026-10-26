"""
bot/app.py
Flask application factory with dependency injection for Projectionist WhatsApp Bot.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from flask import Flask, current_app
from core.config import load_config
from core.notifier import WhatsAppNotifier
from core.plex import PlexClient
from core.roles import load_users
from services.overseerr import OverseerrService
from services.tracker import CollectionTracker

logger = logging.getLogger(__name__)


def create_app(
    config: Optional[dict[str, Any]] = None,
    notifier: Optional[WhatsAppNotifier] = None,
    overseerr: Optional[OverseerrService] = None,
    tracker: Optional[CollectionTracker] = None,
    plex: Optional[PlexClient] = None,
    testing: bool = False,
) -> Flask:
    """
    Application factory creating and configuring the Flask bot instance.
    Injects services into app.extensions for access by request handlers.
    """
    app = Flask(__name__)
    app.config["TESTING"] = testing

    # Load configuration
    cfg = config if config is not None else load_config()
    app.config["PROJECT_CONFIG"] = cfg

    # Configure Twilio auth token
    wa_cfg = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
    app.config["TWILIO_AUTH_TOKEN"] = wa_cfg.get("twilio_auth_token")
    app.config["TWILIO_VERIFY_SIGNATURE"] = bool(wa_cfg.get("verify_signature", True))
    app.config["TWILIO_PUBLIC_URL"] = wa_cfg.get("public_url") or None

    # Who may use the bot, and as what (raises ValueError on a bad whatsapp.users entry)
    app.config["USERS"] = load_users(cfg)

    # Configure Overseerr webhook shared secret
    os_cfg = cfg.get("overseerr", {}) if isinstance(cfg, dict) else {}
    app.config["OVERSEERR_WEBHOOK_SECRET"] = os_cfg.get("webhook_secret") or None

    if not testing:
        if not app.config["TWILIO_VERIFY_SIGNATURE"]:
            logger.warning("Twilio signature verification is DISABLED (whatsapp.verify_signature: false).")
        elif not app.config["TWILIO_AUTH_TOKEN"]:
            logger.warning("whatsapp.twilio_auth_token is not set: the WhatsApp webhook will reject all requests.")
        if not app.config["OVERSEERR_WEBHOOK_SECRET"]:
            logger.warning("overseerr.webhook_secret is not set: the Overseerr webhook will reject all requests.")

    # Configure AI environment & model
    ai_cfg = cfg.get("ai", {}) if isinstance(cfg, dict) else {}
    provider = ai_cfg.get("provider", "gemini")
    model_name = ai_cfg.get("model", "gemini-3.5-flash")
    app.config["AI_MODEL"] = f"{provider}/{model_name}"
    if ai_cfg.get("api_key"):
        os.environ["GEMINI_API_KEY"] = ai_cfg.get("api_key")

    # Service Injection
    # 1. PlexClient
    if plex is not None:
        injected_plex = plex
    else:
        plex_cfg = cfg.get("plex", {}) if isinstance(cfg, dict) else {}
        if plex_cfg.get("url") and plex_cfg.get("token"):
            try:
                injected_plex = PlexClient(
                    url=plex_cfg["url"],
                    token=plex_cfg["token"],
                    library=plex_cfg.get("library", "Movies"),
                )
            except Exception as e:
                logger.warning("Failed to initialize PlexClient in factory: %s", e)
                injected_plex = None
        else:
            injected_plex = None

    # 2. WhatsAppNotifier
    injected_notifier = notifier if notifier is not None else WhatsAppNotifier.from_config(cfg)

    # 3. OverseerrService
    injected_overseerr = overseerr if overseerr is not None else OverseerrService.from_config(cfg)

    # 4. CollectionTracker
    injected_tracker = tracker if tracker is not None else CollectionTracker(
        plex=injected_plex,
        overseerr=injected_overseerr,
    )

    # Register extensions
    app.extensions["notifier"] = injected_notifier
    app.extensions["overseerr"] = injected_overseerr
    app.extensions["tracker"] = injected_tracker
    app.extensions["plex"] = injected_plex

    # Direct attribute accessibility for backwards-compatibility
    app.notifier = injected_notifier
    app.overseerr = injected_overseerr
    app.tracker = injected_tracker
    app.plex = injected_plex

    # Register Route Blueprints
    from bot.handlers import bot_bp
    app.register_blueprint(bot_bp)

    return app


def get_services(app_instance: Optional[Flask] = None) -> dict[str, Any]:
    """Retrieve injected services from application context."""
    target = app_instance or current_app
    return {
        "config": target.config.get("PROJECT_CONFIG", {}),
        "notifier": target.extensions.get("notifier"),
        "overseerr": target.extensions.get("overseerr"),
        "tracker": target.extensions.get("tracker"),
        "plex": target.extensions.get("plex"),
        "ai_model": target.config.get("AI_MODEL", "gemini/gemini-3.5-flash"),
    }
