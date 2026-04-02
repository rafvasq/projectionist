"""Shared fixtures for Projectionist tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plex_client import MovieMeta, ShowMeta


def make_movie(
    ratingKey: int = 1,
    title: str = "Test Movie",
    year: int = 2020,
    watched: bool = False,
    added_days_ago: int = 60,
    rating: float | None = 7.0,
    audience_rating: float | None = 7.5,
    duration_minutes: int | None = 100,
    genres: list[str] | None = None,
    summary: str = "",
) -> MovieMeta:
    from datetime import timedelta
    now = datetime.now(tz=timezone.utc)
    last_viewed = now - timedelta(days=1) if watched else None
    return MovieMeta(
        ratingKey=ratingKey,
        title=title,
        year=year,
        summary=summary,
        rating=rating,
        audience_rating=audience_rating,
        added_at=now - timedelta(days=added_days_ago),
        last_viewed_at=last_viewed,
        genres=genres or [],
        duration_ms=duration_minutes * 60 * 1000 if duration_minutes is not None else None,
    )


def make_show(
    ratingKey: int = 1,
    title: str = "Test Show",
    viewed_episode_count: int = 0,
    total_episode_count: int = 10,
    last_viewed_days_ago: int | None = None,
    added_days_ago: int = 30,
    audience_rating: float | None = 7.5,
) -> ShowMeta:
    from datetime import timedelta
    now = datetime.now(tz=timezone.utc)
    last_viewed = now - timedelta(days=last_viewed_days_ago) if last_viewed_days_ago is not None else None
    return ShowMeta(
        ratingKey=ratingKey,
        title=title,
        year=2020,
        summary="",
        audience_rating=audience_rating,
        added_at=now - timedelta(days=added_days_ago),
        last_viewed_at=last_viewed,
        viewed_episode_count=viewed_episode_count,
        total_episode_count=total_episode_count,
    )
