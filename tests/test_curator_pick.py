"""Tests for the _pick() deduplication helper, cooldown, and curator utilities."""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from curator import _pick, _build_provider, _build_payload, _load_cooldown, _save_cooldown


# -------------------------------------------------------------------
# _pick() tests
# -------------------------------------------------------------------

def test_caps_at_max_results():
    keys = list(range(20))
    result = _pick(keys, set(), max_results=5)
    assert len(result) == 5


def test_excludes_seen():
    keys = [1, 2, 3, 4, 5]
    seen = {1, 2, 3}
    result = _pick(keys, seen, max_results=10)
    assert set(result) == {4, 5}


def test_updates_seen_in_place():
    keys = [1, 2, 3]
    seen: set[int] = set()
    _pick(keys, seen, max_results=10)
    assert seen == {1, 2, 3}


def test_empty_input_returns_empty():
    assert _pick([], set(), max_results=10) == []


def test_all_seen_returns_empty():
    keys = [1, 2, 3]
    seen = {1, 2, 3}
    assert _pick(keys, seen, max_results=10) == []


def test_dedup_across_two_rows():
    seen: set[int] = set()
    row1 = _pick([1, 2, 3], seen, max_results=10)
    row2 = _pick([2, 3, 4, 5], seen, max_results=10)
    assert set(row1) & set(row2) == set()


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown AI provider"):
        _build_provider({"ai": {"provider": "openai"}})


# -------------------------------------------------------------------
# _build_payload() tests
# -------------------------------------------------------------------

def test_build_payload_structure():
    from tests.conftest import make_movie
    movies = [
        make_movie(ratingKey=1, title="Film A", year=2020, genres=["Drama"], rating=7.0),
        make_movie(ratingKey=2, title="Film B", year=2021, genres=["Comedy"], rating=None),
    ]
    payload = _build_payload(movies)
    assert len(payload) == 2
    assert payload[0]["ratingKey"] == 1
    assert payload[0]["title"] == "Film A"
    assert payload[0]["year"] == 2020
    assert payload[0]["genres"] == ["Drama"]
    assert payload[0]["rating"] == 70.0  # rating_pct = 7.0 * 10
    assert payload[1]["rating"] is None


# -------------------------------------------------------------------
# Cooldown tests
# -------------------------------------------------------------------

def test_load_cooldown_missing_file(tmp_path):
    path = tmp_path / "nonexistent.json"
    history, keys = _load_cooldown(path, max_weeks=3)
    assert history == []
    assert keys == set()


def test_load_cooldown_empty_file(tmp_path):
    path = tmp_path / "cooldown.json"
    path.write_text("[]")
    history, keys = _load_cooldown(path, max_weeks=3)
    assert history == []
    assert keys == set()


def test_load_cooldown_corrupt_file(tmp_path):
    path = tmp_path / "cooldown.json"
    path.write_text("not json at all")
    history, keys = _load_cooldown(path, max_weeks=3)
    assert history == []
    assert keys == set()


def test_load_cooldown_prunes_old_entries(tmp_path):
    path = tmp_path / "cooldown.json"
    old_date = (datetime.now() - timedelta(weeks=5)).isoformat()[:10]
    recent_date = (datetime.now() - timedelta(weeks=1)).isoformat()[:10]
    data = [
        {"date": old_date, "keys": [1, 2, 3]},
        {"date": recent_date, "keys": [4, 5]},
    ]
    path.write_text(json.dumps(data))
    history, keys = _load_cooldown(path, max_weeks=3)
    assert len(history) == 1
    assert keys == {4, 5}


def test_load_cooldown_keeps_all_within_window(tmp_path):
    path = tmp_path / "cooldown.json"
    d1 = (datetime.now() - timedelta(weeks=2)).isoformat()[:10]
    d2 = (datetime.now() - timedelta(weeks=1)).isoformat()[:10]
    data = [
        {"date": d1, "keys": [10, 20]},
        {"date": d2, "keys": [30]},
    ]
    path.write_text(json.dumps(data))
    history, keys = _load_cooldown(path, max_weeks=3)
    assert len(history) == 2
    assert keys == {10, 20, 30}


def test_save_cooldown_appends_and_writes(tmp_path):
    path = tmp_path / "cooldown.json"
    existing = [{"date": "2025-01-01", "keys": [1, 2]}]
    _save_cooldown(path, existing, [3, 4])
    data = json.loads(path.read_text())
    assert len(data) == 2
    assert data[0]["keys"] == [1, 2]
    assert data[1]["keys"] == [3, 4]
    assert data[1]["date"] == datetime.now().isoformat()[:10]


def test_save_cooldown_creates_file(tmp_path):
    path = tmp_path / "subdir" / "cooldown.json"
    _save_cooldown(path, [], [100, 200])
    data = json.loads(path.read_text())
    assert len(data) == 1
    assert data[0]["keys"] == [100, 200]


def test_cooldown_keys_excluded_by_pick():
    """Cooldown keys pre-seeded into seen should be excluded by _pick."""
    cooldown = {1, 2, 3}
    seen = set(cooldown)
    result = _pick([1, 2, 3, 4, 5], seen, max_results=10)
    assert set(result) == {4, 5}
