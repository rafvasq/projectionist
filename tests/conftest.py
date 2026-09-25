"""Shared fixtures for Projectionist tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

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


@pytest.fixture(autouse=True)
def disable_network_modifications(monkeypatch):
    """
    Ensure no tests can make POST, PUT, PATCH, or DELETE requests.
    This prevents the test suite from accidentally requesting new movies
    in Overseerr or deleting files in Radarr/Sonarr.
    """
    def _block_request(*args, **kwargs):
        # We don't want tests to accidentally hit the real Overseerr / Radarr APIs
        raise RuntimeError(f"Network modification blocked in tests! Attempted to call an API with args: {args}")

    monkeypatch.setattr("requests.post", _block_request)
    monkeypatch.setattr("requests.put", _block_request)
    monkeypatch.setattr("requests.patch", _block_request)
    monkeypatch.setattr("requests.delete", _block_request)
    
    monkeypatch.setattr("requests.Session.post", _block_request)
    monkeypatch.setattr("requests.Session.put", _block_request)
    monkeypatch.setattr("requests.Session.patch", _block_request)
    monkeypatch.setattr("requests.Session.delete", _block_request)

