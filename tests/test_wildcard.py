"""Tests for the AI-generated wildcard row."""

from __future__ import annotations

from typing import Any

from providers.base import AIProvider
from rows.wildcard import filter_wildcard
from tests.conftest import make_movie


class FakeCurateProvider(AIProvider):
    """Returns a fixed (name, keys) pair from curate()."""

    def __init__(self, name: str, keys: list[int]) -> None:
        self._name = name
        self._keys = keys

    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        return []

    def curate(self, movies: list[dict[str, Any]]) -> tuple[str, list[int]]:
        return (self._name, self._keys)


def test_wildcard_returns_provider_name_and_keys():
    movies = [make_movie(ratingKey=i) for i in range(1, 6)]
    provider = FakeCurateProvider("Midnight in Tokyo", [1, 3, 5])
    name, keys = filter_wildcard(movies, provider)
    assert name == "Midnight in Tokyo"
    assert keys == [1, 3, 5]


def test_wildcard_passes_compact_payload():
    """Payload should include ratingKey, title, year, genres, rating — no summary."""
    movies = [make_movie(ratingKey=1, title="Foo", summary="should not appear")]
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            return []
        def curate(self, m):
            received.append(m)
            return ("", [])

    filter_wildcard(movies, CapturingProvider())
    assert len(received) == 1
    payload = received[0][0]
    assert "ratingKey" in payload
    assert "title" in payload
    assert "year" in payload
    assert "genres" in payload
    assert "rating" in payload
    assert "summary" not in payload


def test_wildcard_empty_library():
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            return []
        def curate(self, m):
            received.append(m)
            return ("Empty Pick", [])

    name, keys = filter_wildcard([], CapturingProvider())
    assert received[0] == []
    assert keys == []


def test_wildcard_passes_all_movies():
    movies = [make_movie(ratingKey=i) for i in range(1, 21)]
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            return []
        def curate(self, m):
            received.append(m)
            return ("", [])

    filter_wildcard(movies, CapturingProvider())
    assert len(received[0]) == 20
