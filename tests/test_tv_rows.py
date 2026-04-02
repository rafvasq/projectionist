"""Tests for rows/tv_collecting_dust.py and rows/give_it_a_shot.py"""

from tests.conftest import make_show
from rows.tv_collecting_dust import filter_tv_collecting_dust
from rows.give_it_a_shot import filter_give_it_a_shot


# --- tv_collecting_dust ---

def test_dust_excludes_never_started():
    s = make_show(viewed_episode_count=0, total_episode_count=10, last_viewed_days_ago=None)
    assert filter_tv_collecting_dust([s]) == []


def test_dust_excludes_recently_watched():
    s = make_show(viewed_episode_count=3, total_episode_count=10, last_viewed_days_ago=10)
    assert filter_tv_collecting_dust([s], idle_days=60) == []


def test_dust_includes_abandoned():
    s = make_show(viewed_episode_count=3, total_episode_count=10, last_viewed_days_ago=90)
    assert filter_tv_collecting_dust([s], idle_days=60) == [s]


def test_dust_excludes_completed():
    s = make_show(viewed_episode_count=10, total_episode_count=10, last_viewed_days_ago=90)
    assert filter_tv_collecting_dust([s], idle_days=60) == []


def test_dust_sorted_oldest_first():
    older = make_show(ratingKey=1, viewed_episode_count=3, total_episode_count=10, last_viewed_days_ago=120)
    newer = make_show(ratingKey=2, viewed_episode_count=2, total_episode_count=10, last_viewed_days_ago=70)
    result = filter_tv_collecting_dust([newer, older], idle_days=60)
    assert result == [older, newer]


# --- give_it_a_shot ---

def test_shot_includes_never_started():
    s = make_show(viewed_episode_count=0, total_episode_count=10)
    assert filter_give_it_a_shot([s]) == [s]


def test_shot_excludes_in_progress():
    s = make_show(viewed_episode_count=2, total_episode_count=10)
    assert filter_give_it_a_shot([s]) == []


def test_shot_excludes_completed():
    s = make_show(viewed_episode_count=10, total_episode_count=10)
    assert filter_give_it_a_shot([s]) == []


def test_shot_sorted_by_rating_desc():
    low = make_show(ratingKey=1, viewed_episode_count=0, audience_rating=6.0)
    high = make_show(ratingKey=2, viewed_episode_count=0, audience_rating=9.0)
    result = filter_give_it_a_shot([low, high])
    assert result == [high, low]


def test_shot_handles_no_rating():
    s = make_show(ratingKey=1, viewed_episode_count=0, audience_rating=None)
    result = filter_give_it_a_shot([s])
    assert result == [s]
