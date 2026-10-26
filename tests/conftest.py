"""Shared fixtures for Projectionist tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
import requests

from core.plex import MovieMeta



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
    collections: list[str] | None = None,
    labels: list[str] | None = None,
    tmdb_id: int | None = None,
    last_viewed_at: datetime | None = None,
    added_at: datetime | None = None,
    duration_ms: int | None = None,
) -> MovieMeta:
    now = datetime.now(tz=timezone.utc)
    if last_viewed_at is None:
        last_viewed = now - timedelta(days=1) if watched else None
    else:
        last_viewed = last_viewed_at

    resolved_added_at = added_at if added_at is not None else (now - timedelta(days=added_days_ago))

    if duration_ms is not None:
        resolved_duration_ms = duration_ms
    elif duration_minutes is not None:
        resolved_duration_ms = duration_minutes * 60 * 1000
    else:
        resolved_duration_ms = None

    return MovieMeta(
        ratingKey=ratingKey,
        title=title,
        year=year,
        summary=summary,
        rating=rating,
        audience_rating=audience_rating,
        added_at=resolved_added_at,
        last_viewed_at=last_viewed,
        genres=genres or [],
        duration_ms=resolved_duration_ms,
        collections=collections or [],
        labels=labels or [],
        tmdb_id=tmdb_id,
    )


@pytest.fixture(autouse=True)
def isolate_flagged_file(monkeypatch, tmp_path):
    """Keep tests off the real deletion queue in data/flagged.json."""
    monkeypatch.setattr("core.storage.get_flagged_file", lambda: tmp_path / "flagged.json")
    monkeypatch.setattr("core.storage.get_cleaner_log_file", lambda: tmp_path / "cleaner_runs.json")


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
