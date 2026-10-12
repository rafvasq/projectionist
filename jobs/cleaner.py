"""
jobs/cleaner.py
Scheduled cleaner job for Projectionist.
Atomically evaluates retention, deletes expired movies in Radarr, and dispatches
immediate WhatsApp summaries and failure alerts in a single pass without intermediate state files.

Usage:
    python -m jobs.cleaner [--dry-run | --no-dry-run] [--config PATH]
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Optional

from core.config import load_config
from core.notifier import WhatsAppNotifier
from core.plex import PlexClient
from core.retention import get_leaving_soon_movies
from services.radarr import RadarrService

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for cleaner job."""
    parser = argparse.ArgumentParser(
        description="Projectionist Cleaner Job: Atomic retention evaluation, Radarr deletion, and WhatsApp notification."
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Simulate deletion without removing files from disk (supports --dry-run / --no-dry-run).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration YAML file.",
    )
    return parser


def format_summary_message(
    deleted_titles: list[str],
    failed_titles: list[str],
    dry_run: bool,
) -> str:
    """Format plain-text WhatsApp deletion engine summary."""
    def fmt(lst: list[str]) -> str:
        return "\n".join([f"- {t}" for t in lst])

    msg = "🧹 *Deletion Engine Summary*\n\n"
    if dry_run:
        msg += "⚠️ *DRY RUN MODE* (No files were actually deleted)\n\n"

    msg += f"✅ *Removed ({len(deleted_titles)})*\n{fmt(deleted_titles)}\n"

    if failed_titles:
        msg += f"\n❌ *Failed ({len(failed_titles)})*\n{fmt(failed_titles)}"

    return msg


def format_template_summary(
    deleted_titles: list[str],
    failed_titles: list[str],
    dry_run: bool,
) -> str:
    """Format condensed single-variable text for Meta WhatsApp Content API template."""
    summary_text = ""
    if dry_run:
        summary_text += "⚠️ DRY RUN (No files deleted)\n\n"

    summary_text += f"✅ Removed ({len(deleted_titles)}): {', '.join(deleted_titles)[:100]}"
    if failed_titles:
        summary_text += f"\n❌ Failed ({len(failed_titles)}): {', '.join(failed_titles)[:100]}"

    return summary_text


def run_cleaner(
    cfg: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    plex: Optional[PlexClient] = None,
    radarr: Optional[RadarrService] = None,
    notifier: Optional[WhatsAppNotifier] = None,
) -> dict[str, Any]:
    """
    Execute atomic single-pass cleaner job.

    1. Load and validate configuration.
    2. Check deletion enabled (early exit if disabled).
    3. Connect to Plex and evaluate retention candidates via core.retention.
    4. Lookup and delete candidates via services.radarr.RadarrService.
    5. Dispatch immediate WhatsApp summary broadcast if movies deleted/simulated.
    6. Dispatch immediate failure alert via core.notifier if any deletions fail.
    7. Return execution summary dict without creating intermediate state files.
    """
    if cfg is None:
        cfg = load_config(config_path)

    del_cfg = cfg.get("deletion", {}) if isinstance(cfg, dict) else {}
    if not del_cfg.get("enabled", False):
        logger.info("Deletion engine is disabled in config. Exiting.")
        return {
            "status": "disabled",
            "dry_run": False,
            "deleted": [],
            "failed": [],
        }

    # Resolve dry-run mode: explicit argument takes precedence over config
    if dry_run is not None:
        is_dry_run = bool(dry_run)
    else:
        is_dry_run = bool(del_cfg.get("dry_run", True))

    logger.info("Starting cleaner (Dry Run: %s)", is_dry_run)

    # 1. Initialize Plex & fetch movies
    if plex is None:
        plex = PlexClient.from_config(cfg)
    try:
        plex.connect()
        movies = plex.fetch_movies()
    except Exception as exc:
        logger.error("Failed to connect or fetch movies from Plex: %s", exc)
        if notifier is None:
            notifier = WhatsAppNotifier.from_config(cfg)
        if notifier.is_configured:
            notifier.send_alert(f"Cleaner failed to connect to Plex: {exc}")
        return {
            "status": "plex_error",
            "dry_run": is_dry_run,
            "deleted": [],
            "failed": [f"Plex Connection Error: {exc}"],
        }

    # 2. Evaluate retention
    to_delete = get_leaving_soon_movies(movies, cfg)
    if not to_delete:
        logger.info("No movies met the deletion criteria today.")
        return {
            "status": "no_candidates",
            "dry_run": is_dry_run,
            "deleted": [],
            "failed": [],
        }

    # 3. Initialize Radarr service & Notifier
    if radarr is None:
        radarr = RadarrService.from_config(cfg)

    if notifier is None:
        notifier = WhatsAppNotifier.from_config(cfg)

    if not radarr.is_configured:
        logger.error("Radarr URL and API Key must be configured to run the cleaner.")
        if notifier.is_configured:
            notifier.send_alert("Radarr service is not configured (missing URL or API key). Cleaner cannot delete candidates.")
        return {
            "status": "radarr_not_configured",
            "dry_run": is_dry_run,
            "deleted": [],
            "failed": [f"{m.title} (Radarr Not Configured)" for m in to_delete],
        }

    deleted_titles: list[str] = []
    failed_titles: list[str] = []

    # 4. Radarr deletion loop
    for m in to_delete:
        if not m.tmdb_id:
            logger.warning("Could not find TMDB ID for '%s' — skipping", m.title)
            failed_titles.append(f"{m.title} (No TMDB ID)")
            continue

        logger.info("Checking Radarr for '%s' (TMDB: %s)...", m.title, m.tmdb_id)
        try:
            radarr_movie = radarr.get_movie_by_tmdb_id(m.tmdb_id)
            if not radarr_movie:
                logger.warning("'%s' not found in Radarr.", m.title)
                failed_titles.append(f"{m.title} (Not in Radarr)")
                continue

            radarr_id = radarr_movie.get("id")
            if not radarr_id:
                logger.warning("'%s' has invalid Radarr record without ID.", m.title)
                failed_titles.append(f"{m.title} (Invalid Radarr ID)")
                continue

            if is_dry_run:
                logger.info("[DRY RUN] Would delete '%s' (Radarr ID: %s)", m.title, radarr_id)
                deleted_titles.append(m.title)
            else:
                logger.info("[ACTIVE] Deleting '%s' (Radarr ID: %s)...", m.title, radarr_id)
                success = radarr.delete_movie(radarr_id, delete_files=True, add_import_exclusion=False)
                if success:
                    deleted_titles.append(m.title)
                else:
                    logger.error("Failed to delete '%s' via Radarr.", m.title)
                    failed_titles.append(f"{m.title} (API Error)")

        except Exception as exc:
            logger.error("Exception processing deletion for '%s': %s", m.title, exc)
            failed_titles.append(f"{m.title} (Exception: {exc})")

    # 5. Immediate WhatsApp reporting & failure alerting
    wa_cfg = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
    wa_enabled = bool(wa_cfg.get("enabled", False))

    if wa_enabled and notifier.is_configured:
        # 5a. Broadcast deletion summary if movies were deleted / simulated
        if deleted_titles:
            msg_body = format_summary_message(deleted_titles, failed_titles, is_dry_run)
            template_sid = wa_cfg.get("cleaner_template_sid")
            content_vars = None
            if template_sid:
                summary_text = format_template_summary(deleted_titles, failed_titles, is_dry_run)
                content_vars = {"1": summary_text}

            notifier.send_broadcast(
                body=msg_body,
                content_sid=template_sid,
                content_variables=content_vars,
            )
            logger.info("Dispatched WhatsApp deletion summary broadcast.")

        # 5b. Immediate operational failure alert if any deletions failed
        if failed_titles:
            alert_body = (
                f"Cleaner encountered {len(failed_titles)} deletion failure(s):\n"
                + "\n".join([f"- {t}" for t in failed_titles])
            )
            notifier.send_alert(alert_body)
            logger.info("Dispatched immediate WhatsApp operational failure alert.")

    logger.info(
        "Cleaner run completed. Deleted/Simulated: %d, Failed: %d",
        len(deleted_titles),
        len(failed_titles),
    )

    return {
        "status": "completed",
        "dry_run": is_dry_run,
        "deleted": deleted_titles,
        "failed": failed_titles,
    }


def run(
    argv: Optional[list[str]] = None,
    config_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    cfg: Optional[dict[str, Any]] = None,
    plex: Optional[PlexClient] = None,
    radarr: Optional[RadarrService] = None,
    notifier: Optional[WhatsAppNotifier] = None,
) -> dict[str, Any]:
    """Module-level run function supporting both CLI argv and programmatic invocations."""
    if argv is not None:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.config is not None:
            config_path = args.config
        if args.dry_run is not None:
            dry_run = args.dry_run

    return run_cleaner(
        cfg=cfg,
        config_path=config_path,
        dry_run=dry_run,
        plex=plex,
        radarr=radarr,
        notifier=notifier,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI runner entrypoint for cleaner job."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    result = run_cleaner(
        config_path=args.config,
        dry_run=args.dry_run,
    )

    if result.get("status") in ("plex_error", "radarr_not_configured") or result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
