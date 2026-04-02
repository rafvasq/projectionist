"""Collecting Dust — unwatched films that have been sitting in the library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plex_client import MovieMeta

DEFAULT_AGE_THRESHOLD_DAYS = 30


def filter_collecting_dust(
    movies: list[MovieMeta],
    min_age_days: int = DEFAULT_AGE_THRESHOLD_DAYS,
) -> list[MovieMeta]:
    """
    Return unwatched films added to the library more than min_age_days ago.
    Sorted oldest-added first.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=min_age_days)

    results = []
    for m in movies:
        if m.watched:
            continue
        if m.added_at is None:
            continue
        added = m.added_at if m.added_at.tzinfo else m.added_at.replace(tzinfo=timezone.utc)
        if added > cutoff:
            continue
        results.append(m)

    results.sort(key=lambda m: m.added_at or datetime.min)
    return results
