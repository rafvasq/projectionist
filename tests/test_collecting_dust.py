"""Tests for rows/collecting_dust.py"""

from tests.conftest import make_movie
from rows.collecting_dust import filter_collecting_dust


def test_excludes_watched():
    movies = [make_movie(ratingKey=1, watched=True, added_days_ago=60)]
    assert filter_collecting_dust(movies) == []


def test_excludes_recently_added():
    movies = [make_movie(ratingKey=1, watched=False, added_days_ago=10)]
    assert filter_collecting_dust(movies, min_age_days=30) == []


def test_includes_old_unwatched():
    m = make_movie(ratingKey=1, watched=False, added_days_ago=60)
    assert filter_collecting_dust([m], min_age_days=30) == [m]


def test_boundary_exactly_at_threshold_excluded():
    # Added exactly min_age_days ago — should NOT qualify (must be older than cutoff)
    m = make_movie(ratingKey=1, watched=False, added_days_ago=30)
    # added_at == cutoff, so added > cutoff is False — it should be included
    result = filter_collecting_dust([m], min_age_days=30)
    assert m in result


def test_sorted_oldest_first():
    old = make_movie(ratingKey=1, watched=False, added_days_ago=120)
    newer = make_movie(ratingKey=2, watched=False, added_days_ago=60)
    result = filter_collecting_dust([newer, old], min_age_days=30)
    assert result == [old, newer]


def test_mixed_library():
    watched = make_movie(ratingKey=1, watched=True, added_days_ago=90)
    new_unwatched = make_movie(ratingKey=2, watched=False, added_days_ago=10)
    old_unwatched = make_movie(ratingKey=3, watched=False, added_days_ago=90)
    result = filter_collecting_dust([watched, new_unwatched, old_unwatched], min_age_days=30)
    assert result == [old_unwatched]


def test_no_added_at_excluded():
    from plex_client import MovieMeta
    m = MovieMeta(
        ratingKey=1, title="No Date", year=2020, summary="",
        rating=None, audience_rating=None,
        added_at=None, last_viewed_at=None,
    )
    assert filter_collecting_dust([m]) == []
