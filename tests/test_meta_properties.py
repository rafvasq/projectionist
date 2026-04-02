"""Tests for MovieMeta and ShowMeta properties."""

from plex_client import MovieMeta, ShowMeta
from tests.conftest import make_movie, make_show


# --- MovieMeta ---

def test_movie_watched_when_last_viewed_set():
    m = make_movie(watched=True)
    assert m.watched is True


def test_movie_not_watched_when_no_last_viewed():
    m = make_movie(watched=False)
    assert m.watched is False


def test_movie_rating_pct():
    m = make_movie(rating=7.5)
    assert m.rating_pct == 75.0


def test_movie_rating_pct_none():
    m = make_movie(rating=None)
    assert m.rating_pct is None


def test_movie_audience_rating_pct():
    m = make_movie(audience_rating=8.2)
    assert m.audience_rating_pct == 82.0


# --- ShowMeta ---

def test_show_never_started():
    s = make_show(viewed_episode_count=0, total_episode_count=10)
    assert s.never_started is True
    assert s.in_progress is False


def test_show_in_progress():
    s = make_show(viewed_episode_count=3, total_episode_count=10)
    assert s.in_progress is True
    assert s.never_started is False


def test_show_completed_not_in_progress():
    s = make_show(viewed_episode_count=10, total_episode_count=10)
    assert s.in_progress is False
    assert s.never_started is False


def test_show_audience_rating_pct():
    s = make_show(audience_rating=8.0)
    assert s.audience_rating_pct == 80.0


def test_show_audience_rating_pct_none():
    s = make_show(audience_rating=None)
    assert s.audience_rating_pct is None
