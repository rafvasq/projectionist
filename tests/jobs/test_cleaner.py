"""
tests/jobs/test_cleaner.py
Unit tests for jobs/cleaner.py:
- Atomic single-pass execution (retention -> Radarr -> WhatsApp)
- Immediate operational failure alerting on deletion errors or unconfigured services
- Elimination of data/deleted_summary.json (no intermediate state files)
- Dry-run mode resolution (CLI flag precedence over config)
- CLI argument parsing (--dry-run, --no-dry-run, --config, --help)
- Formatter functions and WhatsApp Content API template variables
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest

from core.plex import MovieMeta
from jobs import cleaner
from tests.conftest import make_movie


@pytest.fixture
def base_config():
    return {
        "deletion": {
            "enabled": True,
            "dry_run": False,
            "max_age_days": 120,
            "protect_tag": "Keep Forever",
        },
        "radarr": {
            "url": "http://fake-radarr:7878",
            "api_key": "fake_radarr_key",
        },
        "whatsapp": {
            "enabled": True,
            "twilio_account_sid": "AC1234567890abcdef",
            "twilio_auth_token": "secret_token",
            "twilio_phone_number": "whatsapp:+15559876543",
            "allowed_numbers": ["whatsapp:+15551234567"],
            "cleaner_template_sid": "HX_CLEANER_123",
        },
    }


def make_expired_movie(title="Expired Movie", tmdb_id=101):
    return MovieMeta(
        ratingKey=1,
        title=title,
        year=2020,
        summary="A movie to delete",
        rating=6.5,
        audience_rating=7.0,
        added_at=datetime.now(timezone.utc) - timedelta(days=150),
        last_viewed_at=datetime.now(timezone.utc) - timedelta(days=30),
        genres=["Drama"],
        duration_ms=6000000,
        collections=[],
        labels=[],
        tmdb_id=tmdb_id,
    )


# ==============================================================================
# Early Exit Tests
# ==============================================================================

def test_cleaner_disabled_exits_early(base_config):
    cfg = dict(base_config)
    cfg["deletion"]["enabled"] = False

    mock_plex = MagicMock()
    mock_radarr = MagicMock()
    mock_notifier = MagicMock()

    result = cleaner.run(cfg=cfg, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["status"] == "disabled"
    assert result["deleted"] == []
    assert result["failed"] == []
    mock_plex.connect.assert_not_called()
    mock_radarr.get_movie_by_tmdb_id.assert_not_called()
    mock_notifier.send_broadcast.assert_not_called()


def test_cleaner_no_candidates_exits_early(base_config):
    mock_plex = MagicMock()
    recent_movie = MovieMeta(
        ratingKey=2,
        title="Recent Movie",
        year=2023,
        summary="",
        rating=7.0,
        audience_rating=8.0,
        added_at=datetime.now(timezone.utc) - timedelta(days=10),
        last_viewed_at=None,
        tmdb_id=202,
    )
    mock_plex.fetch_movies.return_value = [recent_movie]

    mock_radarr = MagicMock()
    mock_notifier = MagicMock()

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["status"] == "no_candidates"
    mock_radarr.get_movie_by_tmdb_id.assert_not_called()
    mock_notifier.send_broadcast.assert_not_called()
    mock_notifier.send_alert.assert_not_called()


# ==============================================================================
# Atomic Single-Pass Deletion Tests
# ==============================================================================

def test_cleaner_atomic_active_deletion_success(base_config, tmp_path):
    movie = make_expired_movie(title="Old Classic", tmdb_id=500)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.return_value = {"id": 88}
    mock_radarr.delete_movie.return_value = True

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["status"] == "completed"
    assert result["dry_run"] is False
    assert result["deleted"] == ["Old Classic"]
    assert result["failed"] == []

    mock_radarr.get_movie_by_tmdb_id.assert_called_once_with(500)
    mock_radarr.delete_movie.assert_called_once_with(88, delete_files=True, add_import_exclusion=False)

    mock_notifier.send_broadcast.assert_called_once()
    broadcast_kwargs = mock_notifier.send_broadcast.call_args.kwargs
    assert "Old Classic" in broadcast_kwargs["body"]
    assert "Removed (1)" in broadcast_kwargs["body"]
    assert broadcast_kwargs["content_sid"] == "HX_CLEANER_123"
    assert "Old Classic" in broadcast_kwargs["content_variables"]["1"]

    mock_notifier.send_alert.assert_not_called()

    # Verify no intermediate file deleted_summary.json was accessed or written
    with patch("builtins.open", wraps=open) as mock_open:
        cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)
        for c in mock_open.call_args_list:
            assert "deleted_summary.json" not in str(c)


def test_cleaner_dry_run_does_not_call_delete_movie(base_config):
    cfg = dict(base_config)
    cfg["deletion"]["dry_run"] = True

    movie = make_expired_movie(title="Dry Run Movie", tmdb_id=600)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.return_value = {"id": 99}

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=cfg, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["status"] == "completed"
    assert result["dry_run"] is True
    assert result["deleted"] == ["Dry Run Movie"]

    mock_radarr.get_movie_by_tmdb_id.assert_called_once_with(600)
    mock_radarr.delete_movie.assert_not_called()

    mock_notifier.send_broadcast.assert_called_once()
    body = mock_notifier.send_broadcast.call_args.kwargs["body"]
    assert "DRY RUN MODE" in body


# ==============================================================================
# Failure Alerting Tests
# ==============================================================================

def test_cleaner_missing_tmdb_id_dispatches_failure_alert(base_config):
    movie = make_expired_movie(title="No TMDB Movie", tmdb_id=None)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["deleted"] == []
    assert len(result["failed"]) == 1
    assert "No TMDB Movie (No TMDB ID)" in result["failed"][0]

    mock_notifier.send_alert.assert_called_once()
    call_args = mock_notifier.send_alert.call_args
    alert_body = call_args.args[0] if call_args.args else call_args.kwargs.get("body", "")
    assert "1 deletion failure" in alert_body
    assert "No TMDB Movie" in alert_body


def test_cleaner_radarr_api_error_dispatches_immediate_failure_alert(base_config):
    movie = make_expired_movie(title="Error Movie", tmdb_id=700)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.return_value = {"id": 77}
    mock_radarr.delete_movie.return_value = False

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["deleted"] == []
    assert len(result["failed"]) == 1
    assert "Error Movie (API Error)" in result["failed"][0]

    mock_notifier.send_alert.assert_called_once()
    call_args = mock_notifier.send_alert.call_args
    alert_body = call_args.args[0] if call_args.args else call_args.kwargs.get("body", "")
    assert "Error Movie (API Error)" in alert_body


def test_cleaner_partial_failure_sends_both_summary_and_alert(base_config):
    movie_good = make_expired_movie(title="Good Movie", tmdb_id=111)
    movie_bad = make_expired_movie(title="Bad Movie", tmdb_id=222)
    movie_bad.ratingKey = 2

    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie_good, movie_bad]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.side_effect = lambda tmdb: {"id": 1} if tmdb == 111 else None

    mock_radarr.delete_movie.return_value = True

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["deleted"] == ["Good Movie"]
    assert result["failed"] == ["Bad Movie (Not in Radarr)"]

    mock_notifier.send_broadcast.assert_called_once()
    summary_body = mock_notifier.send_broadcast.call_args.kwargs["body"]
    assert "Removed (1)" in summary_body
    assert "Failed (1)" in summary_body

    mock_notifier.send_alert.assert_called_once()
    call_args = mock_notifier.send_alert.call_args
    alert_body = call_args.args[0] if call_args.args else call_args.kwargs.get("body", "")
    assert "Bad Movie" in alert_body


def test_cleaner_unconfigured_radarr_sends_alert(base_config):
    movie = make_expired_movie()
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = False

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["status"] == "radarr_not_configured"
    mock_notifier.send_alert.assert_called_once()


def test_cleaner_plex_connection_error_sends_alert(base_config):
    mock_plex = MagicMock()
    mock_plex.connect.side_effect = ConnectionError("Cannot reach Plex server")

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=base_config, plex=mock_plex, notifier=mock_notifier)

    assert result["status"] == "plex_error"
    mock_notifier.send_alert.assert_called_once()
    call_args = mock_notifier.send_alert.call_args
    alert_body = call_args.args[0] if call_args.args else call_args.kwargs.get("body", "")
    assert "Cannot reach Plex server" in alert_body


# ==============================================================================
# CLI and Flag Override Tests
# ==============================================================================

def test_cli_dry_run_flag_overrides_config(base_config):
    cfg = dict(base_config)
    cfg["deletion"]["dry_run"] = False

    movie = make_expired_movie(tmdb_id=999)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.return_value = {"id": 12}

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(cfg=cfg, dry_run=True, plex=mock_plex, radarr=mock_radarr, notifier=mock_notifier)

    assert result["dry_run"] is True
    mock_radarr.delete_movie.assert_not_called()


def test_cleaner_run_argv_handling(base_config):
    movie = make_expired_movie(tmdb_id=999)
    mock_plex = MagicMock()
    mock_plex.fetch_movies.return_value = [movie]

    mock_radarr = MagicMock()
    mock_radarr.is_configured = True
    mock_radarr.get_movie_by_tmdb_id.return_value = {"id": 12}

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True

    result = cleaner.run(
        argv=["--dry-run"],
        cfg=base_config,
        plex=mock_plex,
        radarr=mock_radarr,
        notifier=mock_notifier,
    )

    assert result["dry_run"] is True
    mock_radarr.delete_movie.assert_not_called()


def test_cli_parser_flags():
    parser = cleaner.build_parser()
    args_dry = parser.parse_args(["--dry-run"])
    assert args_dry.dry_run is True

    args_no_dry = parser.parse_args(["--no-dry-run"])
    assert args_no_dry.dry_run is False

    args_custom = parser.parse_args(["--config", "/path/to/custom.yaml"])
    assert args_custom.config == "/path/to/custom.yaml"
    assert args_custom.dry_run is None


def test_main_cli_returns_zero_on_success(base_config):
    with patch("jobs.cleaner.load_config", return_value=base_config), \
         patch("jobs.cleaner.PlexClient") as mock_plex_cls, \
         patch("jobs.cleaner.RadarrService") as mock_radarr_cls, \
         patch("jobs.cleaner.WhatsAppNotifier") as mock_notifier_cls:

        mock_plex = mock_plex_cls.from_config.return_value
        mock_plex.fetch_movies.return_value = []

        code = cleaner.main(["--dry-run"])
        assert code == 0


def test_main_cli_returns_one_on_failure(base_config):
    with patch("jobs.cleaner.load_config", return_value=base_config), \
         patch("jobs.cleaner.PlexClient") as mock_plex_cls, \
         patch("jobs.cleaner.WhatsAppNotifier") as mock_notifier_cls:

        mock_plex = mock_plex_cls.from_config.return_value
        mock_plex.connect.side_effect = Exception("Plex down")

        code = cleaner.main([])
        assert code == 1
