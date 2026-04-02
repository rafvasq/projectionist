"""Existential & Atmospheric — philosophical, thought-provoking films."""

from __future__ import annotations

from providers.base import AIProvider
from plex_client import MovieMeta

EXISTENTIAL_PROMPT = """
You are curating an "Existential & Atmospheric" row for a small, personally curated Plex library.
Every film here was hand-picked by the owner, so lean inclusive — if it could fit, include it.

Select films that:
- Grapple with big philosophical questions: identity, mortality, consciousness, the scale of the universe, the meaning of existence
- Have a slow, meditative, or haunting atmosphere that lingers long after the credits
- Reward thought over pure entertainment — the kind of film you find yourself still thinking about days later

Exclude: action-heavy blockbusters, straightforward comedies, or anything primarily driven by plot momentum rather than ideas and atmosphere.

Return ONLY a raw JSON array of ratingKey integers. No markdown, no explanation. Example: [123, 456]
""".strip()


def filter_existential(movies: list[MovieMeta], provider: AIProvider) -> list[int]:
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
    return provider.categorize(payload, EXISTENTIAL_PROMPT)
