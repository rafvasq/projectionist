"""Tests for MovieMeta properties."""

from core.plex import MovieMeta
from tests.conftest import make_movie


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


def test_movie_duration_ms_direct_and_fixture():
    m_direct = make_movie(duration_ms=7200000)
    assert m_direct.duration_ms == 7200000

    m_from_mins = make_movie(duration_minutes=90)
    assert m_from_mins.duration_ms == 5400000

    m_none = make_movie(duration_minutes=None, duration_ms=None)
    assert m_none.duration_ms is None

