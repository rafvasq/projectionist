"""
tests/bot/test_bot.py
Comprehensive unit and integration tests for the bot module:
- bot/security.py (authorization & Twilio signature verification)
- bot/app.py (Flask app factory & dependency injection)
- bot/handlers.py (WhatsApp & Overseerr webhooks, state machine, background async tasks)
- bot/main.py (CLI runner & startup sync_tracked_collections)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from flask import Flask, Response

from bot.app import create_app, get_services
from bot.handlers import (
    handle_letterboxd_url_async,
    process_search_async,
)
from bot.main import main
from bot.security import (
    twilio_auth_required,
    validate_twilio_request,
)


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def mock_services():
    mock_notifier = MagicMock()
    mock_overseerr = MagicMock()
    mock_tracker = MagicMock()
    mock_plex = MagicMock()
    return {
        "notifier": mock_notifier,
        "overseerr": mock_overseerr,
        "tracker": mock_tracker,
        "plex": mock_plex,
    }


@pytest.fixture
def test_app(mock_services, tmp_path):
    state_file = tmp_path / "whatsapp_state.json"
    tracked_file = tmp_path / "tracked_collections.json"
    state_file.write_text("{}", encoding="utf-8")
    tracked_file.write_text("{}", encoding="utf-8")

    cfg = {
        "whatsapp": {
            "allowed_numbers": ["whatsapp:+15551234567"],
            "twilio_auth_token": "test_token_123",
            "availability_template_sid": "HX12345",
        },
        "overseerr": {"url": "http://mock-os:5055", "api_key": "os_key"},
        "plex": {"url": "http://mock-plex:32400", "token": "plex_token"},
        "ai": {"provider": "gemini", "model": "gemini-3.5-flash"},
    }

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file), \
         patch("core.storage.get_tracked_collections_file", return_value=tracked_file):
        app = create_app(
            config=cfg,
            notifier=mock_services["notifier"],
            overseerr=mock_services["overseerr"],
            tracker=mock_services["tracker"],
            plex=mock_services["plex"],
            testing=True,
        )
        app.config["STATE_FILE"] = state_file
        app.config["TRACKED_FILE"] = tracked_file
        yield app


@pytest.fixture
def client(test_app):
    return test_app.test_client()


# ==============================================================================
# Security Tests
# ==============================================================================

def test_validate_twilio_request_bypass_in_test_mode():
    mock_req = MagicMock()
    mock_req.testing = True
    assert validate_twilio_request(mock_req) is True

    # Fails closed: with no auth token configured, requests are rejected.
    mock_req.testing = False
    mock_req.headers = {"X-Twilio-Signature": "sig"}
    assert validate_twilio_request(mock_req, auth_token=None) is False


def test_validate_twilio_request_missing_signature():
    mock_req = MagicMock()
    mock_req.testing = False
    mock_req.headers = {}
    assert validate_twilio_request(mock_req, auth_token="token_xyz") is False


def test_validate_twilio_request_signature_check():
    mock_req = MagicMock()
    mock_req.testing = False
    mock_req.headers = {"X-Twilio-Signature": "fake_sig"}
    mock_req.url = "http://example.com/webhook/twilio"
    mock_req.form.to_dict.return_value = {"Body": "hello"}

    mock_validator = MagicMock()
    mock_validator.validate.return_value = True

    assert validate_twilio_request(mock_req, validator=mock_validator) is True
    mock_validator.validate.assert_called_once_with(
        "http://example.com/webhook/twilio", {"Body": "hello"}, "fake_sig"
    )


def test_validate_twilio_request_reverse_proxy_header():
    mock_req = MagicMock()
    mock_req.testing = False
    mock_req.headers = {
        "X-Twilio-Signature": "sig123",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "mybot.ngrok.io",
    }
    mock_req.path = "/whatsapp"
    mock_req.full_path = "/whatsapp?"
    mock_req.form.to_dict.return_value = {}

    mock_validator = MagicMock()
    mock_validator.validate.return_value = True

    assert validate_twilio_request(mock_req, validator=mock_validator) is True
    mock_validator.validate.assert_called_once_with(
        "https://mybot.ngrok.io/whatsapp", {}, "sig123"
    )


def test_twilio_auth_required_decorator(test_app):
    @test_app.route("/test-secure", methods=["POST"])
    @twilio_auth_required(auth_token="secret_token")
    def secure_route():
        return Response("Access Granted", status=200)

    test_app.config["TESTING"] = False
    client = test_app.test_client()

    # Rejection with 403 on invalid signature
    resp = client.post("/test-secure", headers={})
    assert resp.status_code == 403

    # Success when mock validator passes
    with patch("bot.security.RequestValidator") as mock_val_cls:
        mock_val_cls.return_value.validate.return_value = True
        resp_ok = client.post(
            "/test-secure",
            headers={"X-Twilio-Signature": "valid_sig"},
            data={"Body": "test"},
        )
        assert resp_ok.status_code == 200
        assert b"Access Granted" in resp_ok.data


# ==============================================================================
# App Factory & Extension Injection Tests
# ==============================================================================

def test_create_app_injected_services(mock_services):
    app = create_app(
        config={"whatsapp": {"allowed_numbers": []}},
        notifier=mock_services["notifier"],
        overseerr=mock_services["overseerr"],
        tracker=mock_services["tracker"],
        plex=mock_services["plex"],
    )
    assert app.extensions["notifier"] is mock_services["notifier"]
    assert app.extensions["overseerr"] is mock_services["overseerr"]
    assert app.extensions["tracker"] is mock_services["tracker"]
    assert app.extensions["plex"] is mock_services["plex"]


def test_get_services_helper(test_app, mock_services):
    with test_app.app_context():
        services = get_services()
        assert services["notifier"] is mock_services["notifier"]
        assert services["overseerr"] is mock_services["overseerr"]
        assert services["tracker"] is mock_services["tracker"]
        assert services["plex"] is mock_services["plex"]


def test_health_check_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "projectionist-bot"


# ==============================================================================
# WhatsApp Webhook Route & State Machine Tests
# ==============================================================================

def test_unauthorized_number_ignored(client):
    resp = client.post(
        "/whatsapp",
        data={"From": "whatsapp:+19999999999", "Body": "Inception"},
    )
    assert resp.status_code == 200
    assert b"<Response />" in resp.data or b"<Response></Response>" in resp.data


def test_universal_cancel(client, test_app):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({"whatsapp:+15551234567": {"status": "WAITING_CONFIRM"}}),
        encoding="utf-8",
    )

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file):
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "0"},
        )
        assert resp.status_code == 200
        assert b"Search cancelled" in resp.data

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["whatsapp:+15551234567"]["status"] == "IDLE"


def test_idle_triggers_search_async(client, test_app):
    with patch("bot.handlers.threading.Thread") as mock_thread:
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "Interstellar"},
        )
        assert resp.status_code == 200
        assert b"Searching..." in resp.data
        mock_thread.assert_called_once()


def test_process_search_async_execution(test_app, mock_services):
    state_file = test_app.config["STATE_FILE"]
    mock_services["overseerr"].search.return_value = [
        {"id": 157336, "mediaType": "movie", "title": "Interstellar", "releaseDate": "2014-11-05"},
    ]

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file), \
         patch("bot.handlers.parse_movie") as mock_parse, \
         patch("bot.handlers.ai_session"):
        mock_parse.return_value = MagicMock(title="Interstellar", year=2014)

        process_search_async(test_app, "whatsapp:+15551234567", "Interstellar 2014")

        mock_services["notifier"].send_message.assert_called_once()
        sent_msg = mock_services["notifier"].send_message.call_args[0][1]
        assert "Interstellar (2014)" in sent_msg
        assert "Reply with a number to request" in sent_msg

        state = json.loads(state_file.read_text(encoding="utf-8"))
        user_state = state["whatsapp:+15551234567"]
        assert user_state["status"] == "WAITING_CONFIRM"
        assert "1" in user_state["options"]
        assert user_state["options"]["1"]["tmdbId"] == 157336


def test_confirm_movie_request_success(client, test_app, mock_services):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({
            "whatsapp:+15551234567": {
                "status": "WAITING_CONFIRM",
                "options": {
                    "1": {"tmdbId": 157336, "title": "Interstellar (2014)", "mediaType": "movie"}
                },
            }
        }),
        encoding="utf-8",
    )
    mock_services["overseerr"].request_media.return_value = True

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file):
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "1"},
        )
        assert resp.status_code == 200
        assert b"Requested *Interstellar (2014)*!" in resp.data
        mock_services["overseerr"].request_media.assert_called_once_with("movie", 157336)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["whatsapp:+15551234567"]["status"] == "IDLE"


def test_confirm_tv_show_transitions_to_waiting_season(client, test_app):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({
            "whatsapp:+15551234567": {
                "status": "WAITING_CONFIRM",
                "options": {
                    "1": {"tmdbId": 1399, "title": "Game of Thrones", "mediaType": "tv"}
                },
            }
        }),
        encoding="utf-8",
    )

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file):
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "1"},
        )
        assert resp.status_code == 200
        assert b"is a TV Show" in resp.data
        assert b"Reply *1* for All Seasons" in resp.data

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["whatsapp:+15551234567"]["status"] == "WAITING_SEASON"


def test_waiting_season_choices(client, test_app, mock_services):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({
            "whatsapp:+15551234567": {
                "status": "WAITING_SEASON",
                "tmdbId": 1399,
                "title": "Game of Thrones",
            }
        }),
        encoding="utf-8",
    )
    mock_services["overseerr"].get_media_details.return_value = {
        "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}, {"seasonNumber": 3}]
    }
    mock_services["overseerr"].request_media.return_value = True

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file):
        # Choice 2: First season only
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "2"},
        )
        assert resp.status_code == 200
        assert b"Requested *Game of Thrones*!" in resp.data
        mock_services["overseerr"].request_media.assert_called_once_with(
            "tv", 1399, seasons=[1]
        )


def test_letterboxd_list_detection_and_prompt(client, test_app):
    with patch("bot.handlers.threading.Thread") as mock_thread:
        resp = client.post(
            "/webhook/twilio",
            data={
                "From": "whatsapp:+15551234567",
                "Body": "Check this out https://letterboxd.com/user/list/top-films/",
            },
        )
        assert resp.status_code == 200
        mock_thread.assert_called_once()


def test_handle_letterboxd_url_async_list(test_app, mock_services):
    state_file = test_app.config["STATE_FILE"]

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file), \
         patch("bot.handlers.scrape_letterboxd_list", return_value=("Classic Noir", ["The Big Sleep", "Double Indemnity"])):
        handle_letterboxd_url_async(
            test_app,
            "https://letterboxd.com/user/list/classic-noir/",
            "whatsapp:+15551234567",
        )

        state = json.loads(state_file.read_text(encoding="utf-8"))
        user_state = state["whatsapp:+15551234567"]
        assert user_state["status"] == "WAITING_LB_COLLECTION"
        assert user_state["list_title"] == "Classic Noir"

        # Verify prompt sent via notifier
        assert mock_services["notifier"].send_message.call_count == 2
        prompt_call = mock_services["notifier"].send_message.call_args_list[1]
        assert "Classic Noir" in prompt_call[0][1]


def test_letterboxd_confirm_collection_choice(client, test_app):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({
            "whatsapp:+15551234567": {
                "status": "WAITING_LB_COLLECTION",
                "list_title": "Classic Noir",
                "titles": ["The Big Sleep", "Double Indemnity"],
            }
        }),
        encoding="utf-8",
    )

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file), \
         patch("bot.handlers.threading.Thread") as mock_thread:
        resp = client.post(
            "/whatsapp",
            data={"From": "whatsapp:+15551234567", "Body": "1"},
        )
        assert resp.status_code == 200
        assert b"Processing *Classic Noir*" in resp.data
        mock_thread.assert_called_once()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["whatsapp:+15551234567"]["status"] == "IDLE"


# ==============================================================================
# Overseerr Webhook Route Tests
# ==============================================================================

def test_overseerr_media_available_broadcast(client, mock_services):
    payload = {
        "notification_type": "MEDIA_AVAILABLE",
        "subject": "Inception (2010)",
        "media": {"tmdbId": 27205, "title": "Inception", "year": "2010"},
    }
    mock_services["tracker"].handle_media_available.return_value = []

    resp = client.post("/overseerr", json=payload)
    assert resp.status_code == 200
    mock_services["notifier"].send_broadcast.assert_called_once()
    assert "Inception (2010)" in mock_services["notifier"].send_broadcast.call_args[1]["body"]


def test_overseerr_media_available_collection_match(client, mock_services):
    payload = {
        "notification_type": "MEDIA_AVAILABLE",
        "subject": "Fargo (1996)",
        "media": {"tmdbId": 275, "title": "Fargo", "year": "1996"},
    }
    mock_services["tracker"].handle_media_available.return_value = [
        {
            "collection_title": "Coen Brothers",
            "ready_count": 2,
            "total_count": 2,
            "sender": "whatsapp:+15551234567",
        }
    ]

    resp = client.post("/webhook/overseerr", json=payload)
    assert resp.status_code == 200
    mock_services["notifier"].send_message.assert_called_once()
    sent_msg = mock_services["notifier"].send_message.call_args[1]["body"]
    assert "Coen Brothers" in sent_msg
    assert "All 2 films in *Coen Brothers* are now downloaded" in sent_msg


# ==============================================================================
# CLI Runner & Startup Sync Tests
# ==============================================================================

@patch("bot.main.create_app")
@patch("bot.main.CollectionTracker")
@patch("bot.main.load_config")
def test_main_startup_sync_executed(mock_load_cfg, mock_tracker_cls, mock_create_app):
    mock_tracker = MagicMock()
    mock_tracker_cls.return_value = mock_tracker
    mock_app = MagicMock()
    mock_create_app.return_value = mock_app

    main([])

    mock_tracker.sync_tracked_collections.assert_called_once()
    mock_app.run.assert_called_once()


@patch("bot.main.create_app")
@patch("bot.main.CollectionTracker")
@patch("bot.main.load_config")
def test_main_skip_sync_flag(mock_load_cfg, mock_tracker_cls, mock_create_app):
    mock_tracker = MagicMock()
    mock_tracker_cls.return_value = mock_tracker
    mock_app = MagicMock()
    mock_create_app.return_value = mock_app

    main(["--skip-sync"])

    mock_tracker.sync_tracked_collections.assert_not_called()
    mock_app.run.assert_called_once()


@patch("bot.main.create_app")
@patch("bot.main.CollectionTracker")
@patch("bot.main.load_config")
def test_main_startup_sync_error_resilience(mock_load_cfg, mock_tracker_cls, mock_create_app):
    mock_tracker = MagicMock()
    mock_tracker.sync_tracked_collections.side_effect = RuntimeError("Plex unreachable")
    mock_tracker_cls.return_value = mock_tracker
    mock_app = MagicMock()
    mock_create_app.return_value = mock_app

    # Should not raise exception, app.run still called
    main([])

    mock_tracker.sync_tracked_collections.assert_called_once()
    mock_app.run.assert_called_once()


# ==============================================================================
# Slash Command Routing Tests
# ==============================================================================

def _post(client, body, **extra):
    return client.post("/whatsapp", data={"From": "whatsapp:+15551234567", "Body": body, **extra})


def test_slash_command_runs_in_background_and_resets_state(client, test_app):
    state_file = test_app.config["STATE_FILE"]
    state_file.write_text(
        json.dumps({"whatsapp:+15551234567": {"status": "WAITING_CONFIRM", "options": {}}}), encoding="utf-8"
    )
    with patch("core.storage.get_whatsapp_state_file", return_value=state_file),          patch("bot.handlers.threading.Thread") as mock_thread:
        assert _post(client, "/keep Heat").status_code == 200

    assert mock_thread.call_args.kwargs["target"].__name__ == "run_command_async"
    assert mock_thread.call_args.kwargs["args"][2] == "/keep Heat"
    assert json.loads(state_file.read_text(encoding="utf-8"))["whatsapp:+15551234567"]["status"] == "IDLE"


def test_title_starting_with_keep_is_still_a_search(client):
    with patch("bot.handlers.threading.Thread") as mock_thread:
        resp = _post(client, "Keep the Lights On")
    assert b"Searching..." in resp.data
    assert mock_thread.call_args.kwargs["target"].__name__ == "process_search_async"


class _SyncThread:
    """Runs the thread target immediately so a multi-message conversation can be tested."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_keep_typo_conversation_end_to_end(client, test_app, mock_services):
    from tests.conftest import make_movie

    state_file = test_app.config["STATE_FILE"]
    mock_services["plex"].fetch_movies.return_value = [make_movie(ratingKey=77, title="Interstellar", year=2014)]
    mock_services["plex"].add_label.return_value = 1

    with patch("core.storage.get_whatsapp_state_file", return_value=state_file),          patch("bot.handlers.threading.Thread", _SyncThread):
        _post(client, "/keep Interstelar")
        assert "Did you mean *Interstellar (2014)*?" in mock_services["notifier"].send_message.call_args[0][1]
        mock_services["plex"].add_label.assert_not_called()

        _post(client, "7")  # not an option
        mock_services["plex"].add_label.assert_not_called()

        _post(client, "1")
        mock_services["plex"].add_label.assert_called_once_with([77], "Keep Forever")
        assert "now tagged" in mock_services["notifier"].send_message.call_args[0][1]
        assert json.loads(state_file.read_text(encoding="utf-8"))["whatsapp:+15551234567"]["status"] == "IDLE"
