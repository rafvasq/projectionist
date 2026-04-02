"""90-Minute Dash — unwatched films under a runtime threshold."""

from __future__ import annotations

from plex_client import MovieMeta

DEFAULT_MAX_MINUTES = 90


def filter_quick_watch(
    movies: list[MovieMeta],
    max_minutes: int = DEFAULT_MAX_MINUTES,
) -> list[MovieMeta]:
    """
    Return unwatched films with a runtime at or under max_minutes.
    Sorted by runtime ascending (shortest first).
    """
    max_ms = max_minutes * 60 * 1000

    results = [
        m for m in movies
        if not m.watched and m.duration_ms is not None and m.duration_ms <= max_ms
    ]
    results.sort(key=lambda m: m.duration_ms or 0)
    return results
