"""Wildcard — AI invents the theme, name, and film list from scratch."""

from __future__ import annotations

from providers.base import AIProvider
from plex_client import MovieMeta


def filter_wildcard(movies: list[MovieMeta], provider: AIProvider) -> tuple[str, list[int]]:
    """
    Ask the AI to invent a creative collection: it picks the name, theme,
    and films all in one shot.  Returns (collection_name, ratingKeys).
    """
    payload = [
        {
            "ratingKey": m.ratingKey,
            "title": m.title,
            "year": m.year,
            "genres": m.genres,
            "rating": m.rating_pct,
        }
        for m in movies
    ]
    return provider.curate(payload)
