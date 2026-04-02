"""Tests for rows/quick_watch.py"""

from tests.conftest import make_movie
from rows.quick_watch import filter_quick_watch


def test_excludes_watched():
    m = make_movie(ratingKey=1, watched=True, duration_minutes=80)
    assert filter_quick_watch([m]) == []


def test_excludes_over_limit():
    m = make_movie(ratingKey=1, watched=False, duration_minutes=91)
    assert filter_quick_watch([m], max_minutes=90) == []


def test_includes_at_limit():
    m = make_movie(ratingKey=1, watched=False, duration_minutes=90)
    assert filter_quick_watch([m], max_minutes=90) == [m]


def test_includes_under_limit():
    m = make_movie(ratingKey=1, watched=False, duration_minutes=75)
    assert filter_quick_watch([m], max_minutes=90) == [m]


def test_excludes_no_duration():
    m = make_movie(ratingKey=1, watched=False, duration_minutes=None)
    assert filter_quick_watch([m]) == []


def test_sorted_shortest_first():
    long = make_movie(ratingKey=1, watched=False, duration_minutes=88)
    short = make_movie(ratingKey=2, watched=False, duration_minutes=72)
    result = filter_quick_watch([long, short], max_minutes=90)
    assert result == [short, long]


def test_custom_max_minutes():
    m60 = make_movie(ratingKey=1, watched=False, duration_minutes=60)
    m90 = make_movie(ratingKey=2, watched=False, duration_minutes=90)
    result = filter_quick_watch([m60, m90], max_minutes=75)
    assert result == [m60]
