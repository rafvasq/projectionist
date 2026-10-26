"""
tests/bot/test_webhook_auth.py
Webhook authentication with enforcement ON (testing=False):
- Twilio signature verification on /whatsapp and /webhook/twilio
- Overseerr shared secret on /overseerr and /webhook/overseerr
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from twilio.request_validator import RequestValidator

from bot.app import create_app
from bot.security import validate_overseerr_request

TOKEN = "twilio_test_token"
SECRET = "overseerr-shared-secret"
SENDER = "whatsapp:+15551234567"


def build_app(tmp_path, wa_extra=None, token=TOKEN, secret=SECRET):
    state_file = tmp_path / "whatsapp_state.json"
    tracked_file = tmp_path / "tracked_collections.json"
    state_file.write_text("{}", encoding="utf-8")
    tracked_file.write_text("{}", encoding="utf-8")

    wa_cfg = {"allowed_numbers": [SENDER], "twilio_auth_token": token}
    wa_cfg.update(wa_extra or {})
    cfg = {
        "whatsapp": wa_cfg,
        "overseerr": {"url": "http://mock-os:5055", "api_key": "k", "webhook_secret": secret},
    }
    services = {k: MagicMock() for k in ("notifier", "overseerr", "tracker", "plex")}
    patches = [
        patch("core.storage.get_whatsapp_state_file", return_value=state_file),
        patch("core.storage.get_tracked_collections_file", return_value=tracked_file),
    ]
    for p in patches:
        p.start()
    app = create_app(config=cfg, testing=False, **services)
    app.testing = False
    app.config["TESTING"] = False
    return app, services, patches


@pytest.fixture
def secured(tmp_path):
    app, services, patches = build_app(tmp_path)
    yield app.test_client(), services
    for p in patches:
        p.stop()


def sign(url, params, token=TOKEN):
    return RequestValidator(token).compute_signature(url, params)


# ==============================================================================
# Twilio signature
# ==============================================================================

@pytest.mark.parametrize("path", ["/whatsapp", "/webhook/twilio"])
def test_forged_request_without_signature_is_rejected(secured, path):
    client, services = secured
    with patch("bot.handlers.threading.Thread") as mock_thread:
        resp = client.post(path, data={"From": SENDER, "Body": "Interstellar"})
    assert resp.status_code == 403
    mock_thread.assert_not_called()


def test_valid_signature_is_accepted(secured):
    client, _ = secured
    params = {"From": SENDER, "Body": "0"}
    resp = client.post(
        "/whatsapp",
        data=params,
        headers={"X-Twilio-Signature": sign("http://localhost/whatsapp", params)},
    )
    assert resp.status_code == 200
    assert b"Cancelled" in resp.data


def test_signature_for_tampered_body_is_rejected(secured):
    client, _ = secured
    signed = {"From": "whatsapp:+19999999999", "Body": "0"}
    forged = {"From": SENDER, "Body": "0"}
    resp = client.post(
        "/whatsapp",
        data=forged,
        headers={"X-Twilio-Signature": sign("http://localhost/whatsapp", signed)},
    )
    assert resp.status_code == 403


def test_signature_from_wrong_token_is_rejected(secured):
    client, _ = secured
    params = {"From": SENDER, "Body": "0"}
    resp = client.post(
        "/whatsapp",
        data=params,
        headers={"X-Twilio-Signature": sign("http://localhost/whatsapp", params, token="other")},
    )
    assert resp.status_code == 403


def test_signature_checked_against_forwarded_url(secured):
    client, _ = secured
    params = {"From": SENDER, "Body": "0"}
    resp = client.post(
        "/whatsapp",
        data=params,
        headers={
            "X-Twilio-Signature": sign("https://bot.example.com/whatsapp", params),
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "bot.example.com",
        },
    )
    assert resp.status_code == 200


def test_public_url_config_overrides_request_url(tmp_path):
    app, _, patches = build_app(tmp_path, wa_extra={"public_url": "https://bot.example.com/"})
    try:
        client = app.test_client()
        params = {"From": SENDER, "Body": "0"}

        ok = client.post(
            "/webhook/twilio",
            data=params,
            headers={"X-Twilio-Signature": sign("https://bot.example.com/webhook/twilio", params)},
        )
        assert ok.status_code == 200

        wrong = client.post(
            "/webhook/twilio",
            data=params,
            headers={"X-Twilio-Signature": sign("http://localhost/webhook/twilio", params)},
        )
        assert wrong.status_code == 403
    finally:
        for p in patches:
            p.stop()


def test_missing_auth_token_fails_closed(tmp_path):
    app, _, patches = build_app(tmp_path, token="")
    try:
        params = {"From": SENDER, "Body": "0"}
        resp = app.test_client().post(
            "/whatsapp",
            data=params,
            headers={"X-Twilio-Signature": sign("http://localhost/whatsapp", params, token="anything")},
        )
        assert resp.status_code == 403
    finally:
        for p in patches:
            p.stop()


def test_verify_signature_can_be_disabled_for_local_dev(tmp_path):
    app, _, patches = build_app(tmp_path, wa_extra={"verify_signature": False}, token="")
    try:
        resp = app.test_client().post("/whatsapp", data={"From": SENDER, "Body": "0"})
        assert resp.status_code == 200
        assert b"Cancelled" in resp.data
    finally:
        for p in patches:
            p.stop()


def test_unauthorized_number_with_valid_signature_is_still_ignored(secured):
    client, _ = secured
    params = {"From": "whatsapp:+19999999999", "Body": "Inception"}
    with patch("bot.handlers.threading.Thread") as mock_thread:
        resp = client.post(
            "/whatsapp",
            data=params,
            headers={"X-Twilio-Signature": sign("http://localhost/whatsapp", params)},
        )
    assert resp.status_code == 200
    mock_thread.assert_not_called()


def test_health_endpoint_stays_open(secured):
    client, _ = secured
    assert client.get("/health").status_code == 200


# ==============================================================================
# Overseerr shared secret
# ==============================================================================

PAYLOAD = {"notification_type": "MEDIA_AVAILABLE", "subject": "Heat (1995)", "media": {"tmdbId": 949}}


@pytest.mark.parametrize("path", ["/overseerr", "/webhook/overseerr"])
def test_overseerr_without_secret_header_is_rejected(secured, path):
    client, services = secured
    resp = client.post(path, json=PAYLOAD)
    assert resp.status_code == 403
    services["notifier"].send_broadcast.assert_not_called()


def test_overseerr_wrong_secret_is_rejected(secured):
    client, services = secured
    resp = client.post("/overseerr", json=PAYLOAD, headers={"Authorization": "nope"})
    assert resp.status_code == 403
    services["notifier"].send_broadcast.assert_not_called()


@pytest.mark.parametrize("header", [SECRET, f"Bearer {SECRET}"])
def test_overseerr_correct_secret_is_accepted(secured, header):
    client, services = secured
    services["tracker"].handle_media_available.return_value = []
    resp = client.post("/overseerr", json=PAYLOAD, headers={"Authorization": header})
    assert resp.status_code == 200
    services["notifier"].send_broadcast.assert_called_once()


def test_overseerr_without_configured_secret_fails_closed(tmp_path):
    app, services, patches = build_app(tmp_path, secret="")
    try:
        resp = app.test_client().post("/overseerr", json=PAYLOAD, headers={"Authorization": ""})
        assert resp.status_code == 403
        resp = app.test_client().post("/overseerr", json=PAYLOAD, headers={"Authorization": "anything"})
        assert resp.status_code == 403
        services["notifier"].send_broadcast.assert_not_called()
    finally:
        for p in patches:
            p.stop()


def test_validate_overseerr_request_unit():
    req = MagicMock()
    req.testing = False
    req.headers = {"Authorization": "s3cret"}
    assert validate_overseerr_request(req, secret="s3cret") is True
    assert validate_overseerr_request(req, secret="other") is False
    assert validate_overseerr_request(req, secret=None) is False

    req.headers = {"Authorization": "Bearer s3cret"}
    assert validate_overseerr_request(req, secret="s3cret") is True

    req.headers = {}
    assert validate_overseerr_request(req, secret="s3cret") is False
