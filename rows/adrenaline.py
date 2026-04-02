"""Second-Hand Adrenaline — high-tension, propulsive films."""

from __future__ import annotations

from providers.base import AIProvider
from plex_client import MovieMeta

ADRENALINE_PROMPT = """
You are curating a "Second-Hand Adrenaline" row for a small, personally curated Plex library.
Every film here was hand-picked by the owner, so lean inclusive — if it could fit, include it.

Select films that:
- Sustain high-stakes tension from start to finish — the viewer should feel the urgency
- Drive forward with propulsive pacing: thrillers, heist films, chase films, tightly wound crime or action
- Create excitement through sustained dread or momentum, not jump scares or shock value

Exclude: slow burns where tension is secondary, comedies, romance, horror that relies primarily on gore or the supernatural, animation, family films.

Return ONLY a raw JSON array of ratingKey integers. No markdown, no explanation. Example: [123, 456]
""".strip()


def filter_adrenaline(movies: list[MovieMeta], provider: AIProvider) -> list[int]:
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
    return provider.categorize(payload, ADRENALINE_PROMPT)
