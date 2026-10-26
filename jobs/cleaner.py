"""
jobs/cleaner.py
Scheduled cleaner job for Projectionist.
Flags movies that match a retention rule, lets each wait out a grace window in the
deletion queue, then deletes the ones that are due via Radarr (at most
`max_deletions_per_run` per run) and reports to WhatsApp in a single message.

Usage:
    python -m jobs.cleaner [--dry-run | --no-dry-run] [--config PATH]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from core.config import load_config
from core.flagged import due_keys, reconcile, sort_key
from core.notifier import WhatsAppNotifier
from core.plex import PlexClient
from core.retention import flag_movies
from core.storage import load_flagged, save_flagged
from services.overseerr import OverseerrService
from services.radarr import RadarrService

logger = logging.getLogger(__name__)

COLLECTION_LEAVING_SOON = "Leaving Soon"
DEFAULT_GRACE_DAYS = 30
DEFAULT_MAX_DELETIONS = 10
DEFAULT_LEAVING_SOON_LIMIT = 25


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for cleaner job."""
    parser = argparse.ArgumentParser(
        description="Projectionist Cleaner Job: flag, wait out the grace window, delete via Radarr, notify."
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


def _entry_title(entry: dict[str, Any]) -> str:
    return f"{entry['title']} ({entry['year']})" if entry.get("year") else str(entry["title"])


def _flagged_lines(flagged: list[dict[str, Any]], per_rule: int = 5) -> str:
    """One line per rule: name, count, and the first few titles."""
    by_rule: dict[str, list[str]] = {}
    for entry in flagged:
        by_rule.setdefault(entry["rule"], []).append(_entry_title(entry))
    lines = []
    for rule, titles in by_rule.items():
        more = f" +{len(titles) - per_rule} more" if len(titles) > per_rule else ""
        lines.append(f"- *{rule}* ({len(titles)}): {', '.join(titles[:per_rule])}{more}")
    return "\n".join(lines)


def format_summary_message(
    deleted_titles: list[str],
    failed_titles: list[str],
    dry_run: bool,
    flagged: Optional[list[dict[str, Any]]] = None,
    deferred: int = 0,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> str:
    """Format the plain-text WhatsApp cleaner summary."""
    def fmt(lst: list[str]) -> str:
        return "\n".join([f"- {t}" for t in lst])

    msg = "🧹 *Deletion Engine Summary*\n\n"
    if dry_run:
        msg += "⚠️ *DRY RUN MODE* (No files were actually deleted)\n\n"

    if deleted_titles:
        msg += f"✅ *Removed ({len(deleted_titles)})*\n{fmt(deleted_titles)}\n"
    if failed_titles:
        msg += f"\n❌ *Failed ({len(failed_titles)})*\n{fmt(failed_titles)}\n"
    if deferred:
        msg += f"\n⏳ {deferred} more are due and will go in later runs.\n"
    if flagged:
        msg += (
            f"\n🆕 *Flagged ({len(flagged)})*: leaving in {grace_days} days unless you /keep them\n"
            f"{_flagged_lines(flagged)}\n"
        )
    return msg.rstrip()


def format_template_summary(
    deleted_titles: list[str],
    failed_titles: list[str],
    dry_run: bool,
    flagged: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Format condensed single-variable text for the Meta WhatsApp Content API template."""
    parts = []
    if dry_run:
        parts.append("⚠️ DRY RUN (No files deleted)")
    if deleted_titles or not flagged:
        parts.append(f"✅ Removed ({len(deleted_titles)}): {', '.join(deleted_titles)[:100]}")
    if failed_titles:
        parts.append(f"❌ Failed ({len(failed_titles)}): {', '.join(failed_titles)[:100]}")
    if flagged:
        shown = ", ".join(_entry_title(e) for e in flagged[:3])
        more = f" +{len(flagged) - 3} more" if len(flagged) > 3 else ""
        parts.append(f"🆕 Flagged ({len(flagged)}): {shown}{more}")
    return " • ".join(parts)


def refresh_leaving_soon_row(plex: PlexClient, queue: dict[str, Any], by_key: dict[str, Any], del_cfg: dict[str, Any]) -> None:
    """Keep the Plex 'Leaving Soon' row in step with the deletion queue, soonest first."""
    if not del_cfg.get("leaving_soon_row", True):
        return
    keys = [int(k) for k in sorted(queue, key=lambda k: sort_key(queue[k])) if k in by_key]
    limit = del_cfg.get("leaving_soon_limit", DEFAULT_LEAVING_SOON_LIMIT)
    if limit and limit > 0:
        keys = keys[:limit]
    try:
        if keys:
            plex.upsert_collection(COLLECTION_LEAVING_SOON, keys)
        else:
            plex.delete_collection(COLLECTION_LEAVING_SOON)
    except Exception as exc:
        logger.warning("Could not refresh the Leaving Soon row: %s", exc)


def run_cleaner(
    cfg: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    plex: Optional[PlexClient] = None,
    radarr: Optional[RadarrService] = None,
    notifier: Optional[WhatsAppNotifier] = None,
    overseerr: Optional[OverseerrService] = None,
) -> dict[str, Any]:
    """
    Execute one cleaner pass.

    1. Load config; exit early if deletion is disabled.
    2. Connect to Plex and flag movies that match a retention rule.
    3. Reconcile the deletion queue (data/flagged.json): new flags are stamped, movies that
       no longer match drop off. Flagging deletes nothing, so it also happens in dry-run.
    4. Delete queued movies whose grace window has passed, oldest first, at most
       `deletion.max_deletions_per_run` per run. Each movie Radarr deletes also has its
       Overseerr requests cleared and is taken off the Plex watchlist, so it cannot be re-requested.
    5. Refresh the Leaving Soon row and send one summary (plus a failure alert if needed).
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

    grace_days = int(del_cfg.get("grace_days", DEFAULT_GRACE_DAYS))
    max_per_run = max(0, int(del_cfg.get("max_deletions_per_run", DEFAULT_MAX_DELETIONS)))
    logger.info("Starting cleaner (Dry Run: %s, grace %d days, max %d per run)", is_dry_run, grace_days, max_per_run)

    if notifier is None:
        notifier = WhatsAppNotifier.from_config(cfg)

    # 1. Initialize Plex & fetch movies
    if plex is None:
        plex = PlexClient.from_config(cfg)
    try:
        plex.connect()
        movies = plex.fetch_movies()
    except Exception as exc:
        logger.error("Failed to connect or fetch movies from Plex: %s", exc)
        if notifier.is_configured:
            notifier.send_alert(f"Cleaner failed to connect to Plex: {exc}")
        return {
            "status": "plex_error",
            "dry_run": is_dry_run,
            "deleted": [],
            "failed": [f"Plex Connection Error: {exc}"],
        }

    # 2. Flag and reconcile the queue
    now = datetime.now(timezone.utc)
    try:
        flags = flag_movies(movies, cfg, now=now)
    except ValueError as exc:
        logger.error("Invalid deletion rules: %s", exc)
        if notifier.is_configured:
            notifier.send_alert(f"Cleaner not run, fix deletion rules in config: {exc}")
        return {"status": "config_error", "dry_run": is_dry_run, "deleted": [], "failed": [str(exc)]}

    queue, added = reconcile(flags, load_flagged(), now)
    save_flagged(queue)
    by_key = {str(m.ratingKey): m for m in movies}
    due = [by_key[k] for k in due_keys(queue, grace_days, now) if k in by_key]
    to_delete, deferred = due[:max_per_run], max(0, len(due) - max_per_run)

    if not to_delete and not added:
        logger.info("Nothing to flag or delete today (%d queued).", len(queue))
        refresh_leaving_soon_row(plex, queue, by_key, del_cfg)
        return {
            "status": "no_candidates",
            "dry_run": is_dry_run,
            "deleted": [],
            "failed": [],
            "queued": len(queue),
        }

    # 3. Initialize Radarr (only needed when something is due)
    if radarr is None:
        radarr = RadarrService.from_config(cfg)

    if to_delete and not radarr.is_configured:
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
    deleted_tmdb: dict[int, str] = {}

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
                    deleted_tmdb[m.tmdb_id] = m.title
                    queue.pop(str(m.ratingKey), None)
                else:
                    logger.error("Failed to delete '%s' via Radarr.", m.title)
                    failed_titles.append(f"{m.title} (API Error)")

        except Exception as exc:
            logger.error("Exception processing deletion for '%s': %s", m.title, exc)
            failed_titles.append(f"{m.title} (Exception: {exc})")

    if deleted_titles and not is_dry_run:
        save_flagged(queue)

    # A failed cleanup never undoes a deletion; it is reported to the admins instead.
    cleanup_issues: list[str] = []
    if deleted_tmdb:
        try:
            overseerr = overseerr or OverseerrService.from_config(cfg)
            if overseerr.is_configured:
                cleanup_issues += overseerr.clear_requests(deleted_tmdb)
        except Exception as exc:
            logger.error("Overseerr cleanup failed: %s", exc)
            cleanup_issues.append(f"Overseerr cleanup failed: {exc}")
        try:
            cleanup_issues += plex.remove_from_watchlist(deleted_tmdb)
        except Exception as exc:
            logger.error("Watchlist cleanup failed: %s", exc)
            cleanup_issues.append(f"Watchlist cleanup failed: {exc}")
    refresh_leaving_soon_row(plex, queue, by_key, del_cfg)

    # 5. Immediate WhatsApp reporting & failure alerting
    wa_cfg = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
    wa_enabled = bool(wa_cfg.get("enabled", False))

    if wa_enabled and notifier.is_configured:
        # 5a. One summary: what was removed, what failed, and what was newly flagged
        if deleted_titles or added:
            msg_body = format_summary_message(
                deleted_titles, failed_titles, is_dry_run, flagged=added, deferred=deferred, grace_days=grace_days
            )
            template_sid = wa_cfg.get("cleaner_template_sid")
            content_vars = None
            if template_sid:
                content_vars = {"1": format_template_summary(deleted_titles, failed_titles, is_dry_run, flagged=added)}

            notifier.send_broadcast(
                body=msg_body,
                recipients=notifier.admin_numbers,
                content_sid=template_sid,
                content_variables=content_vars,
            )
            logger.info("Dispatched WhatsApp cleaner summary broadcast.")

        # 5b. Immediate operational failure alert if any deletions failed
        if failed_titles or cleanup_issues:
            parts = []
            if failed_titles:
                parts.append(
                    f"Cleaner encountered {len(failed_titles)} deletion failure(s):\n"
                    + "\n".join([f"- {t}" for t in failed_titles])
                )
            if cleanup_issues:
                parts.append(
                    "Removed, but cleaning up requests and the watchlist was incomplete:\n"
                    + "\n".join([f"- {i}" for i in cleanup_issues])
                )
            notifier.send_alert("\n\n".join(parts))
            logger.info("Dispatched immediate WhatsApp operational failure alert.")

    logger.info(
        "Cleaner run completed. Deleted/Simulated: %d, Failed: %d, Newly flagged: %d, Queued: %d",
        len(deleted_titles),
        len(failed_titles),
        len(added),
        len(queue),
    )

    return {
        "status": "completed",
        "dry_run": is_dry_run,
        "deleted": deleted_titles,
        "failed": failed_titles,
        "cleanup_issues": cleanup_issues,
        "flagged": [_entry_title(e) for e in added],
        "queued": len(queue),
        "deferred": deferred,
    }


def run(
    argv: Optional[list[str]] = None,
    config_path: Optional[str] = None,
    dry_run: Optional[bool] = None,
    cfg: Optional[dict[str, Any]] = None,
    plex: Optional[PlexClient] = None,
    radarr: Optional[RadarrService] = None,
    notifier: Optional[WhatsAppNotifier] = None,
    overseerr: Optional[OverseerrService] = None,
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
        overseerr=overseerr,
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

    if result.get("status") in ("plex_error", "radarr_not_configured", "config_error") or result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
