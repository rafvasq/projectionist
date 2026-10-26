"""
tests/jobs/test_curator.py
Unit tests for jobs/curator.py:
- _pick() deduplication, capping, and seen update
- _build_payload() structure and rating percentage conversions
- _setup_ai_env() configuration and error handling
- _delete_previous_collections() state cleanup
- CollectionTracker.sync_tracked_collections() startup integration
- Cooldown deduplication and atomic persistence
- The Leaving Soon row is left to the cleaner
- CLI argument parsing (--config, positional, --help) and main entrypoint
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from core.plex import MovieMeta
from jobs import curator
from tests.conftest import make_movie


# ==============================================================================
# Helper Unit Tests
# ==============================================================================

def test_pick_caps_and_excludes_seen():
    seen = {1, 2}
    result = curator._pick([1, 2, 3, 4, 5], seen, max_results=2)
    assert len(result) == 2
    assert set(result).issubset({3, 4, 5})
    assert {1, 2}.isdisjoint(result)


def test_pick_updates_seen_in_place():
    seen: set[int] = set()
    res = curator._pick([10, 20], seen, max_results=5)
    assert set(res) == {10, 20}
    assert seen == {10, 20}


def test_pick_empty_input_returns_empty():
    assert curator._pick([], set(), max_results=10) == []


def test_pick_dedup_across_two_rows():
    seen: set[int] = set()
    row1 = curator._pick([1, 2, 3], seen, max_results=10)
    row2 = curator._pick([2, 3, 4, 5], seen, max_results=10)
    assert set(row1) & set(row2) == set()


def test_build_payload_structure():
    movies = [
        make_movie(ratingKey=1, title="Film A", year=2021, genres=["Sci-Fi"], rating=8.0),
        make_movie(ratingKey=2, title="Film B", year=2022, genres=[], rating=None),
    ]
    payload = curator._build_payload(movies)
    assert len(payload) == 2
    assert payload[0]["ratingKey"] == 1
    assert payload[0]["title"] == "Film A"
    assert payload[0]["rating"] == 80.0
    assert payload[1]["rating"] is None


def test_setup_ai_env_gemini_and_unknown():
    cfg_gemini = {"ai": {"provider": "gemini", "model": "gemini-2.5-flash", "api_key": "dummy_key"}}
    model = curator.setup_ai_env(cfg_gemini)
    assert model == "gemini/gemini-2.5-flash"

    with pytest.raises(ValueError, match="Unknown AI provider"):
        curator.setup_ai_env({"ai": {"provider": "invalid_provider"}})


def test_delete_previous_collections(tmp_path):
    mock_client = MagicMock()
    mock_client._library_name = "Movies"
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps([{"title": "Old Row", "library": "Movies"}]))

    curator._delete_previous_collections(mock_client, state_path=state_file)
    mock_client.delete_collection.assert_called_once_with("Old Row", library_name="Movies")


# ==============================================================================
# End-to-End Execution & Integration Tests
# ==============================================================================

def test_curator_syncs_tracked_collections(tmp_path):
    cfg = {
        "plex": {"url": "http://plex", "token": "tok"},
        "state_path": str(tmp_path / "state.json"),
        "cooldown_path": str(tmp_path / "cooldown.json"),
        "rows": {"count": 0},
        "deletion": {"enabled": False},
    }

    mock_client = MagicMock()
    mock_client.fetch_movies.return_value = []

    with patch("jobs.curator.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.curator.CollectionTracker") as mock_tracker_cls:
        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        curator.run(cfg=cfg)

        mock_tracker_cls.assert_called_once_with(plex=mock_client)
        mock_tracker.sync_tracked_collections.assert_called_once()


def test_curator_run_cooldown_deduplication(tmp_path):
    cooldown_file = tmp_path / "cooldown.json"
    state_file = tmp_path / "state.json"
    cooldown_file.write_text(json.dumps([
        {"date": datetime.now().isoformat()[:10], "keys": [1, 2]}
    ]))

    # 14 unwatched movies available
    movies = [make_movie(ratingKey=k, title=f"Movie {k}", watched=False) for k in range(1, 15)]

    cfg = {
        "plex": {"url": "http://plex", "token": "tok"},
        "state_path": str(state_file),
        "cooldown_path": str(cooldown_file),
        "rows": {"count": 1, "max_results": 5, "cooldown_weeks": 3},
        "ai": {"provider": "gemini", "api_key": "fake"},
        "deletion": {"enabled": False},
    }

    mock_client = MagicMock()
    mock_client._library_name = "Movies"
    mock_client.fetch_movies.return_value = movies

    mock_curation = MagicMock()
    mock_curation.name = "AI Mystery Picks"
    mock_curation.keys = list(range(1, 15))

    with patch("jobs.curator.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.curator.CollectionTracker"), \
         patch("jobs.curator.setup_ai_env", return_value="gemini/gemini-3.5-flash-lite"), \
         patch("jobs.curator.start_session"), \
         patch("jobs.curator.curate_collection", return_value=mock_curation):

        summary = curator.run(cfg=cfg)

        mock_client.upsert_collection.assert_called_once()
        name, keys = mock_client.upsert_collection.call_args[0]
        assert name == "AI Mystery Picks"
        # Keys 1 and 2 were in cooldown, so they must NOT be picked
        assert 1 not in keys
        assert 2 not in keys
        assert len(keys) == 5

        # Verify new keys persisted to cooldown file
        cooldown_data = json.loads(cooldown_file.read_text())
        assert len(cooldown_data) == 2
        assert set(keys).issubset(set(cooldown_data[1]["keys"]))


def test_curator_leaves_the_leaving_soon_row_to_the_cleaner(tmp_path):
    old_movie = make_movie(ratingKey=99, title="Expiring Film", added_days_ago=150)
    cfg = {
        "plex": {"url": "http://plex", "token": "tok"},
        "state_path": str(tmp_path / "state.json"),
        "cooldown_path": str(tmp_path / "cooldown.json"),
        "rows": {"count": 0},
        "deletion": {
            "enabled": True,
            "leaving_soon_row": True,
            "leaving_soon_limit": 10,
            "max_age_days": 120,
        },
    }

    mock_client = MagicMock()
    mock_client._library_name = "Movies"
    mock_client.fetch_movies.return_value = [old_movie]

    with patch("jobs.curator.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.curator.CollectionTracker"):

        curator.run(cfg=cfg)

        mock_client.upsert_collection.assert_not_called()
        mock_client.delete_collection.assert_not_called()


def test_curator_not_enough_eligible_movies_stops_early(tmp_path):
    # Only 5 movies (< 10 required threshold)
    movies = [make_movie(ratingKey=k, title=f"Film {k}", watched=False) for k in range(1, 6)]
    cfg = {
        "plex": {"url": "http://plex", "token": "tok"},
        "state_path": str(tmp_path / "state.json"),
        "cooldown_path": str(tmp_path / "cooldown.json"),
        "rows": {"count": 1},
        "deletion": {"enabled": False},
    }

    mock_client = MagicMock()
    mock_client.fetch_movies.return_value = movies

    with patch("jobs.curator.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.curator.CollectionTracker"), \
         patch("jobs.curator.curate_collection") as mock_curate:

        summary = curator.run(cfg=cfg)
        mock_curate.assert_not_called()
        assert summary["collections_written"] == []


# ==============================================================================
# CLI Argument Tests
# ==============================================================================

def test_curator_cli_help():
    with pytest.raises(SystemExit) as exc:
        curator.parse_args(["--help"])
    assert exc.value.code == 0


def test_curator_cli_custom_config():
    args = curator.parse_args(["--config", "my_config.yaml"])
    assert args.config_path == "my_config.yaml"


def test_curator_main_invocation():
    with patch("jobs.curator.run") as mock_run:
        curator.main(["--config", "special.yaml"])
        mock_run.assert_called_once_with(config_path="special.yaml")
