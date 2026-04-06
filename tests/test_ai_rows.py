"""Tests for AI-powered row filters using a fake provider."""

from __future__ import annotations

from typing import Any

from providers.base import AIProvider
from plex_client import MovieMeta
from rows.easy_watch import filter_easy_watch
from rows.existential import filter_existential
from rows.adrenaline import filter_adrenaline
from tests.conftest import make_movie


class FakeProvider(AIProvider):
    """Returns whichever ratingKeys are passed in at construction time."""

    def __init__(self, keys: list[int]) -> None:
        self._keys = keys

    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        return self._keys

    def curate(self, movies: list[dict[str, Any]]) -> tuple[str, list[int]]:
        return ("Curator's Pick", self._keys)


def _keys(movies: list[MovieMeta]) -> list[int]:
    return [m.ratingKey for m in movies]


def test_easy_watch_returns_provider_keys():
    movies = [make_movie(ratingKey=i) for i in range(1, 6)]
    provider = FakeProvider([1, 3, 5])
    assert filter_easy_watch(movies, provider) == [1, 3, 5]


def test_easy_watch_passes_all_movies_to_provider():
    movies = [make_movie(ratingKey=i) for i in range(1, 4)]
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            received.append(m)
            return []
        def curate(self, m):
            return ("", [])

    filter_easy_watch(movies, CapturingProvider())
    assert len(received[0]) == 3
    assert all("ratingKey" in m for m in received[0])


def test_existential_returns_provider_keys():
    movies = [make_movie(ratingKey=i) for i in range(1, 5)]
    provider = FakeProvider([2, 4])
    assert filter_existential(movies, provider) == [2, 4]


def test_existential_passes_all_movies_to_provider():
    movies = [make_movie(ratingKey=i) for i in range(1, 4)]
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            received.append(m)
            return []
        def curate(self, m):
            return ("", [])

    filter_existential(movies, CapturingProvider())
    assert len(received[0]) == 3


def test_adrenaline_returns_provider_keys():
    movies = [make_movie(ratingKey=i) for i in range(1, 5)]
    provider = FakeProvider([1, 2])
    assert filter_adrenaline(movies, provider) == [1, 2]


def test_adrenaline_passes_all_movies_to_provider():
    movies = [make_movie(ratingKey=i) for i in range(1, 4)]
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            received.append(m)
            return []
        def curate(self, m):
            return ("", [])

    filter_adrenaline(movies, CapturingProvider())
    assert len(received[0]) == 3


def test_empty_library_passes_empty_list_to_provider():
    """With no movies, an empty payload is sent — provider decides what to return."""
    received: list[list[dict]] = []

    class CapturingProvider(AIProvider):
        def categorize(self, m, prompt):
            received.append(m)
            return []
        def curate(self, m):
            return ("", [])

    for fn in [filter_easy_watch, filter_existential, filter_adrenaline]:
        received.clear()
        fn([], CapturingProvider())
        assert received[0] == []
