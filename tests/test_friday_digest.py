"""Tests for the Weekly Plex Digest."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from friday_digest import _format_movie
from tests.conftest import make_movie


# -------------------------------------------------------------------
# _format_movie() tests
# -------------------------------------------------------------------

def test_format_movie_full_info():
    m = make_movie(
        ratingKey=1, title="Pulp Fiction", year=1994,
        genres=["Crime", "Drama"], rating=8.5, audience_rating=9.2,
    )
    # Manually set tmdb_id since make_movie doesn't support it
    m.tmdb_id = 680
    result = _format_movie(m, emoji="🎬")
    assert "*Pulp Fiction*" in result
    assert "(1994)" in result
    assert "Crime, Drama" in result
    assert "🍅 85%" in result
    assert "👥 92%" in result
    assert "letterboxd.com/tmdb/680" in result


def test_format_movie_no_ratings():
    m = make_movie(
        ratingKey=2, title="Mystery Film", year=2023,
        genres=["Thriller"], rating=None, audience_rating=None,
    )
    result = _format_movie(m)
    assert "*Mystery Film*" in result
    assert "Thriller" in result
    assert "🍅" not in result
    assert "👥" not in result


def test_format_movie_no_tmdb_id():
    m = make_movie(ratingKey=3, title="Unknown", year=2020, genres=[])
    m.tmdb_id = None
    result = _format_movie(m)
    assert "letterboxd" not in result


def test_format_movie_critic_only():
    m = make_movie(ratingKey=4, title="Critics Only", year=2019,
                   genres=["Drama"], rating=7.0, audience_rating=None)
    result = _format_movie(m)
    assert "🍅 70%" in result
    assert "👥" not in result


def test_format_movie_audience_only():
    m = make_movie(ratingKey=5, title="Audience Only", year=2019,
                   genres=["Comedy"], rating=None, audience_rating=6.5)
    result = _format_movie(m)
    assert "🍅" not in result
    assert "👥 65%" in result


def test_format_movie_truncates_genres():
    m = make_movie(ratingKey=6, title="Multi Genre", year=2020,
                   genres=["Action", "Comedy", "Drama", "Sci-Fi"], rating=7.0)
    result = _format_movie(m)
    # Should only show first 3
    assert "Action, Comedy, Drama" in result
    assert "Sci-Fi" not in result


def test_format_movie_emoji_parameter():
    m = make_movie(ratingKey=7, title="Test", year=2020, genres=["Drama"])
    result_new = _format_movie(m, emoji="🎬")
    result_pick = _format_movie(m, emoji="🎲")
    assert result_new.startswith("🎬")
    assert result_pick.startswith("🎲")


def test_format_movie_no_year():
    m = make_movie(ratingKey=8, title="No Year", year=2020, genres=["Drama"])
    m.year = None
    result = _format_movie(m)
    assert "*No Year*" in result
    assert "()" not in result
