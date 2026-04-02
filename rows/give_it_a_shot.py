"""Give it a Shot — TV shows that have never been started."""

from __future__ import annotations

from datetime import datetime

from plex_client import ShowMeta


def filter_give_it_a_shot(shows: list[ShowMeta]) -> list[ShowMeta]:
    """
    Return shows with zero episodes watched, sorted by audience rating
    (highest first), then by date added (oldest first).
    """
    results = [s for s in shows if s.never_started]
    results.sort(key=lambda s: (-(s.audience_rating_pct or 0), s.added_at or datetime.min))
    return results
