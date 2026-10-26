"""
tests/jobs/test_cleaner.py
Unit tests for jobs/cleaner.py: flagging into the deletion queue, the grace window,
the per-run throttle, Radarr deletion, the Leaving Soon row, and WhatsApp reporting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.plex import MovieMeta
from core.storage import load_flagged, save_flagged
from jobs import cleaner
from tests.conftest import make_movie


@pytest.fixture
def base_config():
    return {
        "deletion": {"enabled": True, "dry_run": False, "max_age_days": 120, "protect_tag": "Keep Forever"},
        "radarr": {"url": "http://fake-radarr:7878", "api_key": "fake_radarr_key"},
        "whatsapp": {
            "enabled": True,
            "twilio_account_sid": "AC1234567890abcdef",
            "twilio_auth_token": "secret_token",
            "twilio_phone_number": "whatsapp:+15559876543",
            "allowed_numbers": ["whatsapp:+15551234567"],
            "cleaner_template_sid": "HX_CLEANER_123",
        },
    }


def idle_movie(key=1, title="Expired Movie", tmdb_id=101):
    """A movie that matches the default rule: last activity 125 days ago."""
    now = datetime.now(timezone.utc)
    return MovieMeta(
        ratingKey=key, title=title, year=2020, summary="", rating=6.5, audience_rating=7.0,
        added_at=now - timedelta(days=150), last_viewed_at=now - timedelta(days=125),
        genres=[], duration_ms=6000000, collections=[], labels=[], tmdb_id=tmdb_id,
    )


def queue(*movies, days_ago=40):
    """Put movies in the deletion queue, flagged `days_ago` days ago (30-day grace by default)."""
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    save_flagged({
        str(m.ratingKey): {"title": m.title, "year": m.year, "tmdb_id": m.tmdb_id,
                           "rule": "idle", "reason": "idle 125 days", "flagged_at": stamp}
        for m in movies
    })


def env(*movies):
    plex = MagicMock()
    plex.fetch_movies.return_value = list(movies)
    radarr = MagicMock()
    radarr.is_configured = True
    radarr.get_movie_by_tmdb_id.return_value = {"id": 88}
    radarr.delete_movie.return_value = True
    notifier = MagicMock()
    notifier.is_configured = True
    return plex, radarr, notifier


def alert_text(notifier):
    call = notifier.send_alert.call_args
    return call.args[0] if call.args else call.kwargs.get("body", "")


# ==============================================================================
# Early exits
# ==============================================================================

def test_disabled_exits_early(base_config):
    base_config["deletion"]["enabled"] = False
    plex, radarr, notifier = env()
    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    assert result["status"] == "disabled"
    plex.connect.assert_not_called()


def test_nothing_flagged_exits_quietly(base_config):
    plex, radarr, notifier = env(make_movie(ratingKey=2, added_days_ago=10))
    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    assert result["status"] == "no_candidates"
    notifier.send_broadcast.assert_not_called()
    notifier.send_alert.assert_not_called()


def test_invalid_rules_alert_and_do_nothing(base_config):
    base_config["deletion"]["rules"] = [{"name": "bad", "when": {"rating_gte": 5}}]
    plex, radarr, notifier = env(idle_movie())
    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    assert result["status"] == "config_error"
    assert "rating_gte" in alert_text(notifier)
    radarr.delete_movie.assert_not_called()


# ==============================================================================
# Flag, grace window, delete
# ==============================================================================

def test_first_run_flags_but_deletes_nothing(base_config):
    movie = idle_movie(title="Old Classic")
    plex, radarr, notifier = env(movie)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["status"] == "completed"
    assert result["deleted"] == [] and result["flagged"] == ["Old Classic (2020)"]
    radarr.delete_movie.assert_not_called()
    assert set(load_flagged()) == {"1"}
    plex.upsert_collection.assert_called_once_with("Leaving Soon", [1])

    kwargs = notifier.send_broadcast.call_args.kwargs
    assert "Flagged (1)" in kwargs["body"] and "30 days" in kwargs["body"] and "Old Classic" in kwargs["body"]
    assert "Flagged (1)" in kwargs["content_variables"]["1"]


def test_summary_goes_to_admins_only(base_config):
    plex, radarr, notifier = env(idle_movie())
    notifier.admin_numbers = ["whatsapp:+15551111111"]
    cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    assert notifier.send_broadcast.call_args.kwargs["recipients"] == ["whatsapp:+15551111111"]


def test_movie_inside_grace_window_is_not_deleted(base_config):
    movie = idle_movie()
    queue(movie, days_ago=10)
    plex, radarr, notifier = env(movie)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["status"] == "no_candidates" and result["queued"] == 1
    radarr.delete_movie.assert_not_called()
    notifier.send_broadcast.assert_not_called()


def test_due_movie_is_deleted_and_leaves_the_queue(base_config):
    movie = idle_movie(title="Old Classic", tmdb_id=500)
    queue(movie)
    plex, radarr, notifier = env(movie)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["deleted"] == ["Old Classic"] and result["failed"] == []
    radarr.get_movie_by_tmdb_id.assert_called_once_with(500)
    radarr.delete_movie.assert_called_once_with(88, delete_files=True, add_import_exclusion=False)
    assert load_flagged() == {}
    plex.delete_collection.assert_called_once_with("Leaving Soon")

    kwargs = notifier.send_broadcast.call_args.kwargs
    assert "Removed (1)" in kwargs["body"] and "Old Classic" in kwargs["body"]
    assert kwargs["content_sid"] == "HX_CLEANER_123"
    notifier.send_alert.assert_not_called()


def test_dry_run_reports_but_deletes_nothing_and_keeps_the_queue(base_config):
    base_config["deletion"]["dry_run"] = True
    movie = idle_movie(title="Dry Run Movie", tmdb_id=600)
    queue(movie)
    plex, radarr, notifier = env(movie)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["dry_run"] is True and result["deleted"] == ["Dry Run Movie"]
    radarr.delete_movie.assert_not_called()
    assert set(load_flagged()) == {"1"}
    assert "DRY RUN MODE" in notifier.send_broadcast.call_args.kwargs["body"]


def test_cli_dry_run_overrides_config(base_config):
    movie = idle_movie()
    queue(movie)
    plex, radarr, notifier = env(movie)
    result = cleaner.run(argv=["--dry-run"], cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    assert result["dry_run"] is True
    radarr.delete_movie.assert_not_called()


def test_throttle_deletes_oldest_first_and_defers_the_rest(base_config):
    base_config["deletion"]["max_deletions_per_run"] = 2
    movies = [idle_movie(key=i, title=f"Movie {i}", tmdb_id=100 + i) for i in (1, 2, 3)]
    now = datetime.now(timezone.utc)
    save_flagged({
        str(m.ratingKey): {"title": m.title, "year": 2020, "tmdb_id": m.tmdb_id, "rule": "idle", "reason": "idle 125 days",
                           "flagged_at": (now - timedelta(days=days)).isoformat()}
        for m, days in zip(movies, (40, 60, 50))
    })
    plex, radarr, notifier = env(*movies)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["deleted"] == ["Movie 2", "Movie 3"]       # flagged 60 and 50 days ago
    assert result["deferred"] == 1 and set(load_flagged()) == {"1"}
    assert "1 more are due" in notifier.send_broadcast.call_args.kwargs["body"]


def test_movie_that_stops_matching_drops_off_the_queue(base_config):
    movie = idle_movie()
    queue(movie, days_ago=10)
    movie.labels = ["Keep Forever"]
    plex, radarr, notifier = env(movie)

    cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert load_flagged() == {}
    plex.delete_collection.assert_called_once_with("Leaving Soon")


def test_leaving_soon_row_can_be_disabled(base_config):
    base_config["deletion"]["leaving_soon_row"] = False
    plex, radarr, notifier = env(idle_movie())
    cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)
    plex.upsert_collection.assert_not_called()
    plex.delete_collection.assert_not_called()


# ==============================================================================
# Failure alerting
# ==============================================================================

def test_missing_tmdb_id_dispatches_failure_alert(base_config):
    movie = idle_movie(title="No TMDB Movie", tmdb_id=None)
    queue(movie)
    plex, radarr, notifier = env(movie)

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["deleted"] == [] and result["failed"] == ["No TMDB Movie (No TMDB ID)"]
    assert "1 deletion failure" in alert_text(notifier)
    assert set(load_flagged()) == {"1"}   # stays queued so a later run retries


def test_radarr_api_error_dispatches_failure_alert(base_config):
    movie = idle_movie(title="Error Movie", tmdb_id=700)
    queue(movie)
    plex, radarr, notifier = env(movie)
    radarr.delete_movie.return_value = False

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["failed"] == ["Error Movie (API Error)"]
    assert "Error Movie (API Error)" in alert_text(notifier)


def test_partial_failure_sends_summary_and_alert(base_config):
    good, bad = idle_movie(key=1, title="Good Movie", tmdb_id=111), idle_movie(key=2, title="Bad Movie", tmdb_id=222)
    queue(good, bad)
    plex, radarr, notifier = env(good, bad)
    radarr.get_movie_by_tmdb_id.side_effect = lambda tmdb: {"id": 1} if tmdb == 111 else None

    result = cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)

    assert result["deleted"] == ["Good Movie"] and result["failed"] == ["Bad Movie (Not in Radarr)"]
    body = notifier.send_broadcast.call_args.kwargs["body"]
    assert "Removed (1)" in body and "Failed (1)" in body
    assert "Bad Movie" in alert_text(notifier)


def test_unconfigured_radarr_sends_alert_when_something_is_due(base_config):
    movie = idle_movie()
    queue(movie)
    plex, radarr, notifier = env(movie)
    radarr.is_configured = False
    assert cleaner.run(cfg=base_config, plex=plex, radarr=radarr, notifier=notifier)["status"] == "radarr_not_configured"
    notifier.send_alert.assert_called_once()


def test_plex_connection_error_sends_alert(base_config):
    plex, _, notifier = env()
    plex.connect.side_effect = ConnectionError("Cannot reach Plex server")
    result = cleaner.run(cfg=base_config, plex=plex, notifier=notifier)
    assert result["status"] == "plex_error"
    assert "Cannot reach Plex server" in alert_text(notifier)


# ==============================================================================
# CLI
# ==============================================================================

def test_cli_parser_flags():
    parser = cleaner.build_parser()
    assert parser.parse_args(["--dry-run"]).dry_run is True
    assert parser.parse_args(["--no-dry-run"]).dry_run is False
    args = parser.parse_args(["--config", "/path/to/custom.yaml"])
    assert args.config == "/path/to/custom.yaml" and args.dry_run is None


def test_main_returns_zero_on_success(base_config):
    with patch("jobs.cleaner.load_config", return_value=base_config), \
         patch("jobs.cleaner.PlexClient") as plex_cls, \
         patch("jobs.cleaner.RadarrService"), \
         patch("jobs.cleaner.WhatsAppNotifier"):
        plex_cls.from_config.return_value.fetch_movies.return_value = []
        assert cleaner.main(["--dry-run"]) == 0


def test_main_returns_one_on_failure(base_config):
    with patch("jobs.cleaner.load_config", return_value=base_config), \
         patch("jobs.cleaner.PlexClient") as plex_cls, \
         patch("jobs.cleaner.WhatsAppNotifier"):
        plex_cls.from_config.return_value.connect.side_effect = Exception("Plex down")
        assert cleaner.main([]) == 1
