"""Unit tests for core/retention.py: rules, protections, and timezone safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.retention import _to_utc, flag_movies, load_rules
from tests.conftest import make_movie

CFG = {"deletion": {"max_age_days": 120}}


def keys(movies, cfg=CFG, tracked=None):
    flags = flag_movies(movies, cfg, tracked_collections={} if tracked is None else tracked)
    return [f.movie.ratingKey for f in flags]


def days_ago(n):
    return datetime.now(timezone.utc) - timedelta(days=n)


def test_to_utc_helper():
    assert _to_utc(None) is None
    assert _to_utc(datetime(2026, 5, 1, 12, 0)).tzinfo == timezone.utc
    converted = _to_utc(datetime(2026, 5, 1, 12, 0, tzinfo=timezone(timedelta(hours=-4))))
    assert converted.tzinfo == timezone.utc and converted.hour == 16


# ==============================================================================
# Legacy max_age_days becomes one idle rule
# ==============================================================================

@pytest.mark.parametrize("added, watched, flagged", [
    (130, None, True),    # unwatched and old
    (30, None, False),    # recent
    (150, 3, False),      # watched 3 days ago, so not idle
    (150, 30, False),     # watched 30 days ago, so idle only 30 days
    (150, 130, True),     # last activity 130 days ago
])
def test_default_rule_is_idle_days(added, watched, flagged):
    movie = make_movie(ratingKey=1, added_days_ago=added, last_viewed_at=days_ago(watched) if watched else None)
    assert (keys([movie]) == [1]) is flagged


def test_missing_added_date_is_never_flagged():
    movie = make_movie(ratingKey=1, added_days_ago=300)
    movie.added_at = None
    assert keys([movie]) == []


def test_naive_and_offset_aware_datetimes_are_safe():
    naive = make_movie(ratingKey=1, added_days_ago=130)
    naive.added_at = datetime.now() - timedelta(days=130)
    aware = make_movie(ratingKey=2, added_days_ago=130)
    aware.added_at = datetime.now(timezone(timedelta(hours=-5))) - timedelta(days=130)
    assert keys([naive, aware]) == [1, 2]


# ==============================================================================
# Protections
# ==============================================================================

def test_labels_and_collections_protect():
    cfg = {"deletion": {"max_age_days": 120, "protect_tag": ["Keep Forever", "Favorites"]}}
    movies = [
        make_movie(ratingKey=1, added_days_ago=150, collections=["Keep Forever"]),
        make_movie(ratingKey=2, added_days_ago=150, labels=["Favorites"]),
        make_movie(ratingKey=3, added_days_ago=150, collections=["keep"]),
    ]
    assert keys(movies, cfg) == []


def test_tracked_collections_protect_unless_opted_out():
    tracked = {"Cyberpunk Essentials": {"protect": True}, "Temporary Watchlist": {"protect": False}}
    movies = [
        make_movie(ratingKey=1, added_days_ago=200, collections=["Cyberpunk Essentials"]),
        make_movie(ratingKey=2, added_days_ago=200, collections=["Temporary Watchlist"]),
    ]
    assert keys(movies, tracked=tracked) == [2]


def test_tracked_collections_are_loaded_from_storage_when_not_given():
    movie = make_movie(ratingKey=1, added_days_ago=150, collections=["Sci-Fi Greats"])
    with patch("core.retention.load_tracked_collections", return_value={"Sci-Fi Greats": {"protect": True}}):
        assert flag_movies([movie], CFG) == []


# ==============================================================================
# Custom rules
# ==============================================================================

# A huge last_watched threshold only ever matches films that were never watched.
RULES_CFG = {"deletion": {"rules": [
    {"name": "unwatched", "when": {"added_days_gte": 90, "last_watched_days_gte": 9999}},
    {"name": "forgotten", "when": {"last_watched_days_gte": 365}},
    {"name": "window", "when": {"idle_days_gte": 60, "idle_days_lt": 80}},
]}}


def test_rules_use_the_first_match_and_explain_why():
    movies = [
        make_movie(ratingKey=1, added_days_ago=700, last_viewed_at=days_ago(400)),
        make_movie(ratingKey=2, added_days_ago=100),
        make_movie(ratingKey=3, added_days_ago=200, last_viewed_at=days_ago(70)),
        make_movie(ratingKey=4, added_days_ago=70, last_viewed_at=days_ago(10)),
    ]
    flags = {f.movie.ratingKey: f for f in flag_movies(movies, RULES_CFG, tracked_collections={})}
    assert set(flags) == {1, 2, 3}
    assert (flags[1].rule, flags[1].reason) == ("forgotten", "last watched 400 days ago")
    assert (flags[2].rule, flags[2].reason) == ("unwatched", "added 100 days ago, never watched")
    assert (flags[3].rule, flags[3].reason) == ("window", "idle 70 days")


def test_never_watched_counts_as_infinitely_long_ago():
    movie = make_movie(ratingKey=1, added_days_ago=10)
    gte = {"deletion": {"rules": [{"name": "r", "when": {"last_watched_days_gte": 500}}]}}
    lt = {"deletion": {"rules": [{"name": "r", "when": {"last_watched_days_lt": 500}}]}}
    assert keys([movie], gte) == [1]
    assert keys([movie], lt) == []


@pytest.mark.parametrize("rules", [
    "not a list",
    [{"name": "r"}],
    [{"name": "r", "when": {}}],
    [{"name": "r", "when": {"rating_gte": 5}}],
    [{"name": "r", "when": {"idle_days_over": 5}}],
    [{"name": "r", "when": {"idle_days_gte": "120"}}],
])
def test_invalid_rules_raise_a_clear_error(rules):
    with pytest.raises(ValueError):
        load_rules({"rules": rules})
