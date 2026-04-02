"""Easy Watch — AI-powered comfort picks."""

from __future__ import annotations

from providers.base import AIProvider
from plex_client import MovieMeta

EASY_WATCH_PROMPT = """
You are curating an "Easy Watch" row for a small, personally curated Plex library.
Every film here was hand-picked by the owner, so lean inclusive — if it could fit, include it.

Select films that are:
- Warm, comfortable, and low-stress to watch
- Good for switching off after a long day — the viewer should feel better, not worse, afterwards
- Accessible: clear narratives, not demanding of intense focus or emotional labour

Exclude: horror, psychological thrillers, films with heavy grief or trauma as a central theme,
or anything that leaves the viewer feeling unsettled or drained.

Return ONLY a raw JSON array of ratingKey integers. No markdown, no explanation. Example: [123, 456]
""".strip()


def filter_easy_watch(movies: list[MovieMeta], provider: AIProvider) -> list[int]:
    """Use the AI provider to select easy watch movies from the library."""
    payload = [
        {
            "ratingKey": m.ratingKey,
            "title": m.title,
            "year": m.year,
            "summary": m.summary,
            "genres": m.genres,
            "rating": m.rating_pct,
            "audience_rating": m.audience_rating_pct,
        }
        for m in movies
    ]
    return provider.categorize(payload, EASY_WATCH_PROMPT)
