"""Tests for the _pick() deduplication helper and curator utilities."""

import pytest
from curator import _pick, _build_provider


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
