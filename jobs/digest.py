"""
jobs/digest.py
Weekly Friday Plex Digest job:
- Normalizes datetime timestamps to UTC to avoid TypeError when comparing naive and aware datetimes.
- Strictly deduplicates vault picks from newly added movies.
- Centralizes WhatsApp delivery via core.notifier.WhatsAppNotifier with Meta template newline sanitization.

Usage:
    python -m jobs.digest [--config PATH]
"""

from __future__ import annotations

import argparse
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.config import load_config
from core.plex import MovieMeta, PlexClient
from core.notifier import WhatsAppNotifier

logger = logging.getLogger(__name__)


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure datetime is timezone-aware in UTC.
    - If None, returns None.
    - If naive (tzinfo is None), attaches timezone.utc.
    - If aware, converts to UTC via astimezone(timezone.utc).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_movie(movie: Any, emoji: str = "🎬") -> str:
    """Format a single movie entry with ratings and Letterboxd link."""
    year_str = f" ({movie.year})" if getattr(movie, "year", None) else ""
    lines = [f"{emoji} *{movie.title}*{year_str}"]

    parts = []
    genres = getattr(movie, "genres", None) or []
    if genres:
        parts.append(", ".join(genres[:3]))
    rating_pct = getattr(movie, "rating_pct", None)
    if rating_pct is not None:
        parts.append(f"🍅 {int(rating_pct)}%")
    audience_pct = getattr(movie, "audience_rating_pct", None)
    if audience_pct is not None:
        parts.append(f"👥 {int(audience_pct)}%")
    if parts:
        lines.append(" · ".join(parts))

    tmdb_id = getattr(movie, "tmdb_id", None)
    if tmdb_id:
        lines.append(f"letterboxd.com/tmdb/{tmdb_id}")

    return "\n".join(lines)


def _build_digest_message(
    recent_movies: list[MovieMeta],
    random_picks: list[MovieMeta],
) -> tuple[str, str]:
    """
    Construct markdown body and clean inline string for Meta template variable.
    Returns (msg_body, clean_movies_template_var).
    """
    display_recent = recent_movies[:7]

    if recent_movies:
        clean_items = []
        for m in display_recent:
            title_clean = m.title.replace("\n", " ").strip()
            year_part = f" ({m.year})" if getattr(m, "year", None) else ""
            clean_items.append(f"🎬 {title_clean}{year_part}")

        clean_movies = " • ".join(clean_items)
        if len(recent_movies) > len(display_recent):
            clean_movies += f" (+{len(recent_movies) - len(display_recent)} more)"
    else:
        clean_movies = "None this week."

    msg_lines = ["🍿 *Weekly Plex Digest*\n"]

    if recent_movies:
        msg_lines.append("*New Movies:*")
        msg_lines.append("")
        for m in display_recent:
            msg_lines.append(_format_movie(m, emoji="🎬"))
            msg_lines.append("")

        if len(recent_movies) > 7:
            msg_lines.append(f"...and {len(recent_movies) - 7} more movies added this week!")
            msg_lines.append("")

    if random_picks:
        msg_lines.append("*From Your Collection:*")
        msg_lines.append("")
        for m in random_picks:
            msg_lines.append(_format_movie(m, emoji="🎲"))
            msg_lines.append("")

    msg = "\n".join(msg_lines).strip()
    return msg, clean_movies


def run(
    config_path: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """
    Execute Friday 12:00 PM digest generation and broadcast delivery.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    if cfg is None:
        cfg = load_config(config_path)

    wa_cfg = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
    if not wa_cfg.get("enabled", False):
        logger.info("WhatsApp is disabled in config. Exiting.")
        return None

    try:
        plex = PlexClient.from_config(cfg)
        plex.connect()
    except Exception as e:
        logger.error("Failed to connect to Plex: %s", e)
        return None

    try:
        movies = plex.fetch_movies()
    except Exception as e:
        logger.error("Failed to fetch media from Plex: %s", e)
        return None

    # Timezone-safe UTC comparison
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    recent_movies = [
        m for m in movies
        if m.added_at and (_to_utc(m.added_at) >= seven_days_ago)  # type: ignore[operator]
    ]
    recent_movies.sort(
        key=lambda x: _to_utc(x.added_at) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Strictly exclude recent movies from vault candidate pool
    recent_keys = {m.ratingKey for m in recent_movies}
    vault_candidates = [
        m for m in movies
        if not m.watched and m.ratingKey not in recent_keys
    ]
    random_picks = random.sample(vault_candidates, min(3, len(vault_candidates))) if vault_candidates else []

    if not recent_movies and not random_picks:
        logger.info("No new movies and no unwatched movies. Skipping digest.")
        return None

    msg, clean_movies = _build_digest_message(recent_movies, random_picks)
    clean_tv = "None this week."

    notifier = WhatsAppNotifier.from_config(cfg)
    if not notifier.is_configured:
        logger.error("WhatsAppNotifier is not configured (missing credentials or phone number).")
        return None

    template_sid = wa_cfg.get("digest_template_sid")
    content_variables = {
        "1": clean_movies,
        "2": clean_tv,
    } if template_sid else None

    sent_sids = notifier.send_broadcast(
        body=msg,
        content_sid=template_sid,
        content_variables=content_variables,
    )
    logger.info("Sent Weekly Plex Digest to %d recipient(s).", len(sent_sids))

    return {
        "recent_count": len(recent_movies),
        "vault_count": len(random_picks),
        "message": msg,
        "clean_movies": clean_movies,
        "sent_sids": sent_sids,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Build and parse CLI argument parser for digest job."""
    parser = argparse.ArgumentParser(description="Projectionist Weekly Friday Digest Job")
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "positional_config",
        nargs="?",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """CLI runner entrypoint for digest job."""
    args = parse_args(argv)
    cfg_path = args.config_path or args.positional_config
    run(config_path=cfg_path)


if __name__ == "__main__":
    main()
