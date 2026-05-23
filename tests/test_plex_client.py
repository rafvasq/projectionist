"""Tests for PlexClient caching and indexing behaviors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from plexapi.exceptions import NotFound
from plex_client import PlexClient

class FakePlexMovie:
    def __init__(self, ratingKey: int, title: str):
        self.ratingKey = ratingKey
        self.title = title
        self.summary = "Test summary"
        self.year = 2020
        self.genres = []

class FakePlexSection:
    def __init__(self, title: str, items: list):
        self.title = title
        self._items = items
        self.all_called_count = 0

    def all(self):
        self.all_called_count += 1
        return self._items

def test_plex_client_caching_and_indexing():
    # Setup mock items
    movies = [
        FakePlexMovie(101, "Movie A"),
        FakePlexMovie(102, "Movie B"),
        FakePlexMovie(103, "Movie C"),
    ]
    fake_section = FakePlexSection("Movies", movies)
    
    # Mock PlexServer and its library section retrieval
    mock_server = MagicMock()
    mock_server.library.section.return_value = fake_section
    
    with patch("plex_client.PlexServer", return_value=mock_server):
        client = PlexClient(url="http://localhost:32400", token="fake_token", library="Movies")
        
        # 1. First fetch_movies call (should fetch and cache)
        movie_metas = client.fetch_movies()
        assert len(movie_metas) == 3
        assert movie_metas[0].title == "Movie A"
        assert fake_section.all_called_count == 1
        
        # 2. Second fetch_movies call (should use cache, not call section.all again)
        movie_metas_cached = client.fetch_movies()
        assert len(movie_metas_cached) == 3
        assert fake_section.all_called_count == 1  # Still 1 call!
        
        # 3. Test _fetch_items_by_keys uses cache and maintains order
        # Key 103, then 101
        fetched = client._fetch_items_by_keys(fake_section, [103, 101])
        assert len(fetched) == 2
        assert fetched[0].title == "Movie C"
        assert fetched[1].title == "Movie A"
        assert fake_section.all_called_count == 1  # Still 1 call!
        
        # 4. Requesting non-existent key should be safely skipped
        fetched_with_missing = client._fetch_items_by_keys(fake_section, [999, 102])
        assert len(fetched_with_missing) == 1
        assert fetched_with_missing[0].title == "Movie B"
