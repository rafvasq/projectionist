"""TV Collecting Dust — shows started but abandoned for too long."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plex_client import ShowMeta

DEFAULT_IDLE_DAYS = 60


def filter_tv_collecting_dust(
    shows: list[ShowMeta],
    idle_days: int = DEFAULT_IDLE_DAYS,
) -> list[ShowMeta]:
    """
    Return in-progress shows where the last watched episode was
    more than idle_days ago.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=idle_days)

    results = []
    for s in shows:
        if not s.in_progress:
            continue
        if s.last_viewed_at is None:
            continue
        last = s.last_viewed_at if s.last_viewed_at.tzinfo else s.last_viewed_at.replace(tzinfo=timezone.utc)
        if last > cutoff:
            continue
        results.append(s)

    # Oldest-abandoned first
    results.sort(key=lambda s: s.last_viewed_at or datetime.min)
    return results
