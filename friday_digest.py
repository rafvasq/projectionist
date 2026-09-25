import os
import random
import yaml
import logging
from datetime import datetime, timedelta
from twilio.rest import Client

from plex_client import from_config

logger = logging.getLogger(__name__)


def _format_movie(movie, emoji="🎬"):
    """Format a single movie entry with ratings and Letterboxd link."""
    year_str = f" ({movie.year})" if movie.year else ""
    lines = [f"{emoji} *{movie.title}*{year_str}"]

    # Genres + ratings on one line
    parts = []
    if movie.genres:
        parts.append(", ".join(movie.genres[:3]))
    if movie.rating_pct is not None:
        parts.append(f"🍅 {int(movie.rating_pct)}%")
    if movie.audience_rating_pct is not None:
        parts.append(f"👥 {int(movie.audience_rating_pct)}%")
    if parts:
        lines.append(" · ".join(parts))

    # Letterboxd link via TMDB redirect
    if movie.tmdb_id:
        lines.append(f"letterboxd.com/tmdb/{movie.tmdb_id}")

    return "\n".join(lines)


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    wa_cfg = cfg.get("whatsapp", {})
    if not wa_cfg.get("enabled", False):
        logger.info("WhatsApp is disabled in config. Exiting.")
        return

    try:
        plex = from_config(cfg)
        plex.connect()
    except Exception as e:
        logger.error(f"Failed to connect to Plex: {e}")
        return

    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)

    try:
        movies = plex.fetch_movies()
    except Exception as e:
        logger.error(f"Failed to fetch media from Plex: {e}")
        return

    recent_movies = [m for m in movies if m.added_at and m.added_at >= seven_days_ago]
    unwatched = [m for m in movies if not m.watched]
    random_picks = random.sample(unwatched, min(3, len(unwatched))) if unwatched else []

    if not recent_movies and not random_picks:
        logger.info("No new movies and no unwatched movies. Skipping digest.")
        return

    msg_lines = ["🍿 *Weekly Plex Digest*\n"]

    if recent_movies:
        msg_lines.append("*New Movies:*")
        msg_lines.append("")
        recent_movies.sort(key=lambda x: x.added_at, reverse=True)
        
        display_recent = recent_movies[:7]
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

    twilio_sid = wa_cfg.get("twilio_account_sid")
    twilio_token = wa_cfg.get("twilio_auth_token")

    if not twilio_sid or not twilio_token:
        logger.error("Missing Twilio credentials in config.")
        return

    client = Client(twilio_sid, twilio_token)
    phone_number = wa_cfg.get("twilio_phone_number")
    if phone_number and not phone_number.startswith("whatsapp:"):
        phone_number = f"whatsapp:{phone_number}"

    allowed_numbers = wa_cfg.get("allowed_numbers", [])

    for number in allowed_numbers:
        if not number.startswith("whatsapp:"):
            number = f"whatsapp:{number}"
        try:
            client.messages.create(body=msg, from_=phone_number, to=number)
            logger.info(f"Sent Weekly Plex Digest to {number}")
        except Exception as e:
            logger.error(f"Failed to send WA message: {e}")

if __name__ == "__main__":
    run()
