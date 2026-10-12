"""
bot/main.py
CLI runner entrypoint for the Projectionist WhatsApp Bot.
Executes startup CollectionTracker synchronization against Plex,
then launches the Flask application server.

Usage:
  python -m bot.main [--config CONFIG_PATH] [--host HOST] [--port PORT] [--skip-sync]
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

from core.ai import setup_ai_env
from core.config import load_config
from core.notifier import WhatsAppNotifier
from core.plex import PlexClient
from services.overseerr import OverseerrService
from services.tracker import CollectionTracker
from bot.app import create_app

logger = logging.getLogger(__name__)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Projectionist WhatsApp Bot Runner")
    parser.add_argument("--config", "-c", default=None, help="Path to config.yaml")
    parser.add_argument("--host", default=None, help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=None, help="Port to listen on (default: 5000)")
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip startup tracked collection synchronization",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Initializing Projectionist Bot...")
    cfg = load_config(args.config)

    # 1. Setup AI Environment
    try:
        setup_ai_env(cfg)
    except Exception as exc:
        logger.warning("AI backend initialization warning: %s", exc)

    # 2. Instantiate Services
    plex = None
    plex_cfg = cfg.get("plex", {}) if isinstance(cfg, dict) else {}
    if plex_cfg.get("url") and plex_cfg.get("token"):
        try:
            plex = PlexClient(
                url=plex_cfg["url"],
                token=plex_cfg["token"],
                library=plex_cfg.get("library", "Movies"),
            )
        except Exception as exc:
            logger.warning("Failed to initialize PlexClient: %s", exc)

    notifier = WhatsAppNotifier.from_config(cfg)
    overseerr = OverseerrService.from_config(cfg)
    tracker = CollectionTracker(plex=plex, overseerr=overseerr)

    # 3. Startup Execution: sync_tracked_collections()
    if not args.skip_sync:
        logger.info("Executing startup sync_tracked_collections()...")
        try:
            tracker.sync_tracked_collections(plex=plex)
            logger.info("Startup sync_tracked_collections() completed successfully.")
        except Exception as exc:
            logger.error("Startup sync_tracked_collections() failed (continuing app launch): %s", exc)

    # 4. Create App
    app = create_app(
        config=cfg,
        notifier=notifier,
        overseerr=overseerr,
        tracker=tracker,
        plex=plex,
    )

    host = args.host or os.environ.get("HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("PORT", "5000"))

    logger.info("Starting Projectionist Bot server on %s:%s...", host, port)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
