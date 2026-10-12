"""
tests/jobs/test_digest.py
Unit tests for jobs/digest.py:
- Timezone resilience: offset-naive vs offset-aware comparisons against seven_days_ago without TypeError
- Strict deduplication of vault picks (excluding newly added movies)
- Meta WhatsApp template formatting and newline sanitization via core.notifier
- Early exits on disabled WhatsApp or Plex failures
- CLI argument parsing and main entrypoint
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.plex import MovieMeta
from jobs import digest
from tests.conftest import make_movie


# ==============================================================================
# Helper Unit Tests
# ==============================================================================

def test_to_utc_conversions():
    assert digest._to_utc(None) is None

    naive = datetime(2026, 10, 5, 12, 0, 0)
    aware = digest._to_utc(naive)
    assert aware.tzinfo == timezone.utc
    assert aware.hour == 12

    offset_aware = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    utc_conv = digest._to_utc(offset_aware)
    assert utc_conv.tzinfo == timezone.utc
    assert utc_conv.hour == 16


def test_format_movie_full_info():
    m = make_movie(
        ratingKey=1,
        title="Pulp Fiction",
        year=1994,
        genres=["Crime", "Drama"],
        rating=8.5,
        audience_rating=9.2,
        tmdb_id=680,
    )
    result = digest._format_movie(m, emoji="🎬")
    assert "*Pulp Fiction*" in result
    assert "(1994)" in result
    assert "Crime, Drama" in result
    assert "🍅 85%" in result
    assert "👥 92%" in result
    assert "letterboxd.com/tmdb/680" in result


def test_format_movie_no_ratings():
    m = make_movie(
        ratingKey=2,
        title="Mystery Film",
        year=2023,
        genres=["Thriller"],
        rating=None,
        audience_rating=None,
    )
    result = digest._format_movie(m)
    assert "*Mystery Film*" in result
    assert "🍅" not in result
    assert "👥" not in result


def test_format_movie_no_tmdb_id():
    m = make_movie(ratingKey=3, title="Indie Gem", year=2021, tmdb_id=None)
    result = digest._format_movie(m)
    assert "letterboxd.com" not in result


def test_format_movie_truncates_genres():
    m = make_movie(ratingKey=4, title="Genre Blender", genres=["Action", "Comedy", "Drama", "Sci-Fi"])
    result = digest._format_movie(m)
    assert "Action, Comedy, Drama" in result
    assert "Sci-Fi" not in result


def test_format_movie_critic_only():
    m = make_movie(ratingKey=5, title="Critics Only", year=2019,
                   genres=["Drama"], rating=7.0, audience_rating=None)
    result = digest._format_movie(m)
    assert "🍅 70%" in result
    assert "👥" not in result


def test_format_movie_audience_only():
    m = make_movie(ratingKey=6, title="Audience Only", year=2019,
                   genres=["Comedy"], rating=None, audience_rating=6.5)
    result = digest._format_movie(m)
    assert "🍅" not in result
    assert "👥 65%" in result


def test_format_movie_emoji_parameter():
    m = make_movie(ratingKey=7, title="Test", year=2020, genres=["Drama"])
    result_new = digest._format_movie(m, emoji="🎬")
    result_pick = digest._format_movie(m, emoji="🎲")
    assert result_new.startswith("🎬")
    assert result_pick.startswith("🎲")


def test_format_movie_no_year():
    m = make_movie(ratingKey=8, title="No Year", year=2020, genres=["Drama"])
    m.year = None
    result = digest._format_movie(m)
    assert "*No Year*" in result
    assert "()" not in result



# ==============================================================================
# End-to-End Execution & Timezone Resilience Tests
# ==============================================================================

def test_digest_timezone_comparison_does_not_raise_type_error():
    """
    Verify that movies with naive added_at and aware added_at
    do not cause a TypeError when evaluated against seven_days_ago.
    """
    now_utc = datetime.now(timezone.utc)
    naive_recent = datetime.now() - timedelta(days=2)
    aware_old = now_utc - timedelta(days=20)

    movie1 = make_movie(ratingKey=1, title="Naive Recent", added_at=naive_recent)
    movie2 = make_movie(ratingKey=2, title="Aware Old", added_at=aware_old)

    cfg = {
        "whatsapp": {"enabled": True, "twilio_phone_number": "123"},
        "plex": {"url": "http://plex", "token": "tok"},
    }

    mock_client = MagicMock()
    mock_client.fetch_movies.return_value = [movie1, movie2]

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.send_broadcast.return_value = ["SM123"]

    with patch("jobs.digest.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.digest.WhatsAppNotifier.from_config", return_value=mock_notifier):

        res = digest.run(cfg=cfg)
        assert res is not None
        assert res["recent_count"] == 1
        assert "Naive Recent" in res["message"]


def test_digest_strictly_excludes_recent_movies_from_vault_picks():
    """
    Verify that an unwatched movie added <7 days ago is listed in New Movies
    but STRICTLY EXCLUDED from From Your Collection (vault picks).
    """
    now = datetime.now(timezone.utc)
    # Movie 1: added 2 days ago, unwatched
    m1 = make_movie(ratingKey=1, title="Brand New Film", added_at=now - timedelta(days=2), watched=False)
    # Movie 2: added 40 days ago, unwatched
    m2 = make_movie(ratingKey=2, title="Old Vault Film", added_at=now - timedelta(days=40), watched=False)

    cfg = {
        "whatsapp": {"enabled": True},
        "plex": {"url": "http://plex", "token": "tok"},
    }

    mock_client = MagicMock()
    mock_client.fetch_movies.return_value = [m1, m2]

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.send_broadcast.return_value = ["SM123"]

    with patch("jobs.digest.PlexClient.from_config", return_value=mock_client), \
         patch("jobs.digest.WhatsAppNotifier.from_config", return_value=mock_notifier):

        res = digest.run(cfg=cfg)
        assert res is not None
        assert res["recent_count"] == 1
        assert res["vault_count"] == 1

        msg = res["message"]
        assert "*New Movies:*" in msg
        assert "*Brand New Film*" in msg
        assert "*From Your Collection:*" in msg
        assert "*Old Vault Film*" in msg

        parts = msg.split("*From Your Collection:*")
        assert "*Brand New Film*" not in parts[1]


def test_digest_template_variable_sanitization():
    m = make_movie(ratingKey=1, title="Dune\nPart Two", year=2024)
    msg, clean = digest._build_digest_message([m], [])
    assert "\n" not in clean
    assert "🎬 Dune Part Two (2024)" in clean


def test_digest_whatsapp_disabled_exits_early():
    cfg = {"whatsapp": {"enabled": False}}
    with patch("jobs.digest.PlexClient.from_config") as mock_plex:
        res = digest.run(cfg=cfg)
        assert res is None
        mock_plex.assert_not_called()


def test_digest_no_media_skips_digest():
    cfg = {
        "whatsapp": {"enabled": True},
        "plex": {"url": "http://plex", "token": "tok"},
    }
    mock_client = MagicMock()
    mock_client.fetch_movies.return_value = []

    with patch("jobs.digest.PlexClient.from_config", return_value=mock_client):
        res = digest.run(cfg=cfg)
        assert res is None


def test_digest_plex_connection_failure_returns_none():
    cfg = {
        "whatsapp": {"enabled": True},
        "plex": {"url": "http://plex", "token": "tok"},
    }
    with patch("jobs.digest.PlexClient.from_config") as mock_plex_cls:
        mock_client = mock_plex_cls.return_value
        mock_client.connect.side_effect = ConnectionError("Plex down")
        res = digest.run(cfg=cfg)
        assert res is None


# ==============================================================================
# CLI Argument Tests
# ==============================================================================

def test_digest_cli_help():
    with pytest.raises(SystemExit) as exc:
        digest.parse_args(["--help"])
    assert exc.value.code == 0


def test_digest_cli_custom_config():
    args = digest.parse_args(["--config", "custom_digest.yaml"])
    assert args.config_path == "custom_digest.yaml"


def test_digest_main_invocation():
    with patch("jobs.digest.run") as mock_run:
        digest.main(["--config", "my_digest.yaml"])
        mock_run.assert_called_once_with(config_path="my_digest.yaml")
