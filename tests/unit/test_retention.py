"""
Unit tests for core/retention.py.
Verifies get_leaving_soon_movies, UTC timezone-safe comparisons,
age calculations, watch history 7-day rule, and collection protection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from core.plex import MovieMeta
from core.retention import _to_utc, get_leaving_soon_movies
from tests.conftest import make_movie


def test_to_utc_helper():
    assert _to_utc(None) is None

    naive = datetime(2026, 5, 1, 12, 0)
    aware_naive = _to_utc(naive)
    assert aware_naive is not None
    assert aware_naive.tzinfo == timezone.utc
    assert aware_naive.hour == 12

    offset_tz = timezone(timedelta(hours=-4))
    aware = datetime(2026, 5, 1, 12, 0, tzinfo=offset_tz)
    converted = _to_utc(aware)
    assert converted is not None
    assert converted.tzinfo == timezone.utc
    assert converted.hour == 16


def test_unwatched_old_movie_selected():
    old_movie = make_movie(ratingKey=1, title="Old Movie", added_days_ago=130, watched=False)
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([old_movie], cfg)
    assert len(result) == 1
    assert result[0].ratingKey == 1


def test_recent_movie_excluded():
    recent_movie = make_movie(ratingKey=2, title="Recent Movie", added_days_ago=30, watched=False)
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([recent_movie], cfg)
    assert len(result) == 0


def test_recently_watched_movie_excluded():
    now = datetime.now(timezone.utc)
    # Added 150 days ago, but watched 3 days ago (within 7 days)
    movie = make_movie(ratingKey=3, title="Watched Recently", added_days_ago=150)
    movie.last_viewed_at = now - timedelta(days=3)
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([movie], cfg)
    assert len(result) == 0


def test_long_ago_watched_movie_selected():
    now = datetime.now(timezone.utc)
    # Added 150 days ago, watched 30 days ago (older than 7 days)
    movie = make_movie(ratingKey=4, title="Watched Long Ago", added_days_ago=150)
    movie.last_viewed_at = now - timedelta(days=30)
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([movie], cfg)
    assert len(result) == 1
    assert result[0].ratingKey == 4


def test_protect_tag_collections_and_labels():
    cfg = {"deletion": {"max_age_days": 120, "protect_tag": ["Keep Forever", "Favorites"]}}

    protected_col = make_movie(ratingKey=5, title="Protected Col", added_days_ago=150, collections=["Keep Forever"])
    protected_lbl = make_movie(ratingKey=6, title="Protected Lbl", added_days_ago=150, labels=["Favorites"])
    protected_lower = make_movie(ratingKey=7, title="Keep Lower", added_days_ago=150, collections=["keep"])

    result = get_leaving_soon_movies([protected_col, protected_lbl, protected_lower], cfg)
    assert len(result) == 0


def test_protect_tracked_collections():
    cfg = {"deletion": {"max_age_days": 120}}
    tracked = {
        "Cyberpunk Essentials": {"protect": True},
        "Temporary Watchlist": {"protect": False},
    }

    protected_movie = make_movie(ratingKey=8, title="Akira", added_days_ago=200, collections=["Cyberpunk Essentials"])
    unprotected_movie = make_movie(ratingKey=9, title="Temp Film", added_days_ago=200, collections=["Temporary Watchlist"])

    result = get_leaving_soon_movies([protected_movie, unprotected_movie], cfg, tracked_collections=tracked)
    assert len(result) == 1
    assert result[0].ratingKey == 9


def test_leaving_soon_storage_fallback():
    m = make_movie(ratingKey=1, added_days_ago=150, collections=["Sci-Fi Greats"])

    with patch("core.retention.load_tracked_collections", return_value={"Sci-Fi Greats": {"protect": True}}):
        res = get_leaving_soon_movies([m], {"deletion": {"max_age_days": 120}})
        assert len(res) == 0


def test_timezone_naive_datetimes_handled_safely():
    now_naive = datetime.now()
    movie = make_movie(ratingKey=10, title="Naive Datetimes", added_days_ago=130)
    movie.added_at = now_naive - timedelta(days=130)
    movie.last_viewed_at = now_naive - timedelta(days=30)
    cfg = {"deletion": {"max_age_days": 120}}

    # Must execute without raising TypeError: can't subtract offset-naive and offset-aware datetimes
    result = get_leaving_soon_movies([movie], cfg)
    assert len(result) == 1
    assert result[0].ratingKey == 10


def test_timezone_offset_aware_datetimes():
    tz_est = timezone(timedelta(hours=-5))
    now_est = datetime.now(tz_est)
    movie = make_movie(ratingKey=11, title="Offset Aware", added_days_ago=130)
    movie.added_at = now_est - timedelta(days=130)
    movie.last_viewed_at = now_est - timedelta(days=30)
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([movie], cfg)
    assert len(result) == 1
    assert result[0].ratingKey == 11


def test_none_added_at_handled_safely():
    movie = make_movie(ratingKey=12, title="No Added At", added_days_ago=130)
    movie.added_at = None
    cfg = {"deletion": {"max_age_days": 120}}

    result = get_leaving_soon_movies([movie], cfg)
    assert len(result) == 0
