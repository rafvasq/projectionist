"""
Unit tests for core/plex.py.
Verifies MovieMeta properties, collection upsert, deletion, label assignment,
unified _promote_collection helper, and confirms TV dead code pruning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from plexapi.exceptions import NotFound

from core.plex import MovieMeta, PlexClient, from_config


def test_movie_meta_properties():
    now = datetime.now(timezone.utc)

    m = MovieMeta(
        ratingKey=101,
        title="Blade Runner 2049",
        year=2017,
        summary="A young blade runner...",
        rating=8.0,
        audience_rating=8.8,
        added_at=now,
        last_viewed_at=now,
        genres=["Sci-Fi", "Mystery"],
        duration_ms=9840000,
        collections=["Cyberpunk"],
        labels=["Keep Forever"],
        tmdb_id=335984,
    )

    assert m.watched is True
    assert m.rating_pct == 80.0
    assert m.audience_rating_pct == 88.0
    assert m.tmdb_id == 335984

    # Unwatched with None ratings
    m_unwatched = MovieMeta(
        ratingKey=102,
        title="Primer",
        year=2004,
        summary="",
        rating=None,
        audience_rating=None,
        added_at=now,
        last_viewed_at=None,
    )
    assert m_unwatched.watched is False
    assert m_unwatched.rating_pct is None
    assert m_unwatched.audience_rating_pct is None


def test_tv_show_dead_code_eliminated():
    """Verify dead ShowMeta and TV functions are pruned from core.plex."""
    import core.plex as plex_module
    assert not hasattr(plex_module, "ShowMeta"), "ShowMeta must be pruned as dead code"
    assert not hasattr(plex_module.PlexClient, "fetch_shows"), "fetch_shows must be pruned as dead code"
    assert not hasattr(plex_module.PlexClient, "_show_to_meta"), "_show_to_meta must be pruned as dead code"


def test_plex_client_from_config():
    cfg = {
        "plex": {
            "url": "http://plex.local:32400",
            "token": "TOKEN_123",
            "library": "Feature Films",
        }
    }
    client1 = from_config(cfg)
    assert client1._url == "http://plex.local:32400"
    assert client1._token == "TOKEN_123"
    assert client1._library_name == "Feature Films"

    client2 = PlexClient.from_config(cfg)
    assert client2._token == "TOKEN_123"


def test_promote_collection_helper():
    mock_coll = MagicMock()
    mock_coll.title = "Curator's Pick"

    # Standard promotion calls via classmethod
    PlexClient._promote_collection(mock_coll)
    mock_coll.visibility().promoteHome.assert_called_once()
    mock_coll.visibility().promoteRecommended.assert_called_once()

    # Promotion calls via instance method
    client = PlexClient("http://fake", "tok")
    mock_coll2 = MagicMock()
    client._promote_collection(mock_coll2, "Top Picks")
    mock_coll2.visibility().promoteHome.assert_called_once()
    mock_coll2.visibility().promoteRecommended.assert_called_once()

    # Exception safety: does not crash if visibility methods raise
    mock_coll_error = MagicMock()
    mock_coll_error.visibility.side_effect = RuntimeError("Visibility unsupported")
    PlexClient._promote_collection(mock_coll_error)  # Does not raise


def test_upsert_collection_workflow():
    mock_item = MagicMock()
    mock_item.ratingKey = 1

    mock_collection = MagicMock()
    mock_section = MagicMock()
    mock_section.title = "Movies"
    mock_section.all.return_value = [mock_item]
    mock_section.collection.return_value = mock_collection
    mock_section.createCollection.return_value = mock_collection

    mock_server = MagicMock()
    mock_server.library.section.return_value = mock_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient("http://localhost:32400", "token", library="Movies")
        client.upsert_collection("Weekly Picks", [1])

        mock_section.createCollection.assert_called_once_with("Weekly Picks", items=[mock_item])
        mock_collection.sortUpdate.assert_called_once_with(sort="custom")
        mock_collection.visibility().promoteHome.assert_called()


def test_add_to_collection():
    mock_item = MagicMock()
    mock_item.ratingKey = 1

    mock_collection = MagicMock()
    mock_section = MagicMock()
    mock_section.title = "Movies"
    mock_section.all.return_value = [mock_item]
    mock_section.collection.return_value = mock_collection

    mock_server = MagicMock()
    mock_server.library.section.return_value = mock_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient("http://localhost:32400", "token")
        client.add_to_collection("Existing Row", [1], promote=True)

        mock_collection.addItems.assert_called_once_with([mock_item])
        mock_collection.visibility().promoteHome.assert_called()


def test_delete_collection():
    mock_collection = MagicMock()
    mock_section = MagicMock()
    mock_section.collection.return_value = mock_collection
    mock_server = MagicMock()
    mock_server.library.section.return_value = mock_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient("http://localhost:32400", "token")

        # Existing collection deleted
        assert client.delete_collection("Old Row") is True
        mock_collection.delete.assert_called_once()

        # Nonexistent collection returns False
        mock_section.collection.side_effect = NotFound("Missing")
        assert client.delete_collection("Missing Row") is False


def test_add_label():
    mock_item = MagicMock()
    mock_item.ratingKey = 1
    mock_section = MagicMock()
    mock_section.title = "Movies"
    mock_section.all.return_value = [mock_item]
    mock_server = MagicMock()
    mock_server.library.section.return_value = mock_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient("http://localhost:32400", "token")
        assert client.add_label([1], label="Keep Forever") == 1
        mock_item.addLabel.assert_called_once_with("Keep Forever")


def test_remove_label_counts_successes():
    good = MagicMock()
    good.ratingKey = 1
    bad = MagicMock()
    bad.ratingKey = 2
    bad.removeLabel.side_effect = RuntimeError("plex error")
    mock_section = MagicMock()
    mock_section.title = "Movies"
    mock_section.all.return_value = [good, bad]
    mock_server = MagicMock()
    mock_server.library.section.return_value = mock_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient("http://localhost:32400", "token")
        assert client.remove_label([1, 2], label="Keep Forever") == 1
        good.removeLabel.assert_called_once_with("Keep Forever")
        assert client.remove_label([], label="Keep Forever") == 0


def test_movie_to_meta_tmdb_id_parsing():
    mock_raw = MagicMock()
    mock_raw.ratingKey = 42
    mock_raw.title = "The Matrix"
    mock_raw.year = 1999
    mock_raw.summary = "A computer hacker..."
    mock_raw.rating = 8.7
    mock_raw.audienceRating = 9.0
    mock_raw.addedAt = None
    mock_raw.lastViewedAt = None
    mock_raw.genres = []
    mock_raw.duration = 8160000
    mock_raw.collections = []
    mock_raw.labels = []

    guid_imdb = MagicMock()
    guid_imdb.id = "imdb://tt0133093"
    guid_tmdb = MagicMock()
    guid_tmdb.id = "tmdb://603"
    mock_raw.guids = [guid_imdb, guid_tmdb]

    meta = PlexClient._movie_to_meta(mock_raw)
    assert meta.ratingKey == 42
    assert meta.tmdb_id == 603


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
    movies = [
        FakePlexMovie(101, "Movie A"),
        FakePlexMovie(102, "Movie B"),
        FakePlexMovie(103, "Movie C"),
    ]
    fake_section = FakePlexSection("Movies", movies)

    mock_server = MagicMock()
    mock_server.library.section.return_value = fake_section

    with patch("core.plex.PlexServer", return_value=mock_server):
        client = PlexClient(url="http://localhost:32400", token="fake_token", library="Movies")

        # 1. First fetch_movies call (should fetch and cache)
        movie_metas = client.fetch_movies()
        assert len(movie_metas) == 3
        assert movie_metas[0].title == "Movie A"
        assert fake_section.all_called_count == 1

        # 2. Second fetch_movies call (should use cache, not call section.all again)
        movie_metas_cached = client.fetch_movies()
        assert len(movie_metas_cached) == 3
        assert fake_section.all_called_count == 1

        # 3. Test _fetch_items_by_keys uses cache and maintains order
        fetched = client._fetch_items_by_keys(fake_section, [103, 101])
        assert len(fetched) == 2
        assert fetched[0].title == "Movie C"
        assert fetched[1].title == "Movie A"
        assert fake_section.all_called_count == 1

        # 4. Requesting non-existent key should be safely skipped
        fetched_with_missing = client._fetch_items_by_keys(fake_section, [999, 102])
        assert len(fetched_with_missing) == 1
        assert fetched_with_missing[0].title == "Movie B"

