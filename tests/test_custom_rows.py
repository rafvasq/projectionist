"""Tests for custom AI rows curation logic."""

from __future__ import annotations

from typing import Any
from providers.base import AIProvider
from curator import _pick
from tests.conftest import make_movie

class FakeCustomCurationProvider(AIProvider):
    """Categorizes movies by matching ratingKeys from a dictionary."""

    def __init__(self, key_map: dict[str, list[int]]) -> None:
        self.key_map = key_map
        self.received_prompts = []

    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        self.received_prompts.append(row_prompt)
        return self.key_map.get(row_prompt, [])

    def curate(self, movies: list[dict[str, Any]], exclude_themes: list[str] | None = None) -> tuple[str, list[int]]:
        return ("", [])


def test_custom_ai_row_integration():
    """Verify that _pick deduplicates across rows correctly with custom rows."""
    movies = [make_movie(ratingKey=i) for i in range(1, 10)]
    provider = FakeCustomCurationProvider({
        "Select scary movies": [1, 2, 3],
        "Select fun movies": [3, 4, 5],
    })

    movie_seen: set[int] = set()

    # Curation of Row 1 (Scary Movies) - should pick 1, 2, 3 and add them to movie_seen
    row1_raw = provider.categorize([], "Select scary movies")
    row1_keys = _pick(row1_raw, movie_seen, max_results=5)
    assert set(row1_keys) == {1, 2, 3}
    assert movie_seen == {1, 2, 3}

    # Curation of Row 2 (Fun Movies) with deduplication enabled (default)
    # Movie 3 is already in movie_seen, so it should be skipped
    row2_raw = provider.categorize([], "Select fun movies")
    row2_keys = _pick(row2_raw, movie_seen, max_results=5)
    assert set(row2_keys) == {4, 5}
    assert movie_seen == {1, 2, 3, 4, 5}


def test_custom_ai_row_no_deduplication():
    """Verify that deduplication can be bypassed for specific custom rows."""
    movies = [make_movie(ratingKey=i) for i in range(1, 10)]
    provider = FakeCustomCurationProvider({
        "Select scary movies": [1, 2, 3],
        "Select fun movies": [3, 4, 5],
    })

    movie_seen: set[int] = {1, 2, 3} # 3 is already seen

    # If deduplicate is False, we use an empty pool set() for _pick instead of movie_seen
    row2_raw = provider.categorize([], "Select fun movies")
    
    # Bypassing deduplication
    row2_keys_no_dedup = _pick(row2_raw, set(), max_results=5)
    assert set(row2_keys_no_dedup) == {3, 4, 5} # 3 is preserved!
