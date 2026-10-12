"""
Unit tests for core/notifier.py.
Verifies phone normalization, Meta template newline sanitization (" • "),
direct-body fallback on template error, and broadcast dispatch.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from core.notifier import (
    WhatsAppNotifier,
    normalize_whatsapp_number,
    sanitize_template_variable,
)


def test_normalize_whatsapp_number():
    assert normalize_whatsapp_number(None) == ""
    assert normalize_whatsapp_number("") == ""
    assert normalize_whatsapp_number("   ") == ""
    assert normalize_whatsapp_number("+15555551234") == "whatsapp:+15555551234"
    assert normalize_whatsapp_number("whatsapp:+15555551234") == "whatsapp:+15555551234"
    assert normalize_whatsapp_number("  +15555551234  ") == "whatsapp:+15555551234"
    assert normalize_whatsapp_number("  whatsapp:+15555551234  ") == "whatsapp:+15555551234"


def test_sanitize_template_variable():
    assert sanitize_template_variable(None) == ""
    assert sanitize_template_variable("") == ""
    assert sanitize_template_variable("\n   \n") == ""
    assert sanitize_template_variable("Title (2024)") == "Title (2024)"
    assert sanitize_template_variable("Line 1\nLine 2") == "Line 1 • Line 2"
    assert sanitize_template_variable("Line 1\r\nLine 2\r\nLine 3") == "Line 1 • Line 2 • Line 3"
    assert sanitize_template_variable("Line 1\n\nLine 2") == "Line 1 • Line 2"
    assert sanitize_template_variable("\nLine 1\n") == "Line 1"


def test_send_message_direct_body_success():
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.sid = "SM_DIRECT_123"
    mock_client.messages.create.return_value = mock_msg

    notifier = WhatsAppNotifier(
        account_sid="AC123",
        auth_token="AUTH123",
        from_number="+15559876543",
        allowed_numbers=["+15551234567"],
        client=mock_client,
    )

    sid = notifier.send_message(to="+15551234567", body="Hello Direct")
    assert sid == "SM_DIRECT_123"
    mock_client.messages.create.assert_called_once_with(
        from_="whatsapp:+15559876543",
        to="whatsapp:+15551234567",
        body="Hello Direct",
    )


def test_send_message_template_success():
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.sid = "SM_TEMPLATE_456"
    mock_client.messages.create.return_value = mock_msg

    notifier = WhatsAppNotifier(
        account_sid="AC123",
        auth_token="AUTH123",
        from_number="+15559876543",
        client=mock_client,
    )

    variables = {"1": "Movie A\nMovie B", "2": "None"}
    sid = notifier.send_message(
        to="+15551234567",
        body="Fallback Text",
        content_sid="HX_TEMPLATE_789",
        content_variables=variables,
    )

    assert sid == "SM_TEMPLATE_456"
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["from_"] == "whatsapp:+15559876543"
    assert call_kwargs["to"] == "whatsapp:+15551234567"
    assert call_kwargs["content_sid"] == "HX_TEMPLATE_789"
    # Ensure variables were sanitized with bullet
    parsed_vars = json.loads(call_kwargs["content_variables"])
    assert parsed_vars["1"] == "Movie A • Movie B"
    assert parsed_vars["2"] == "None"


def test_send_message_template_fallback_on_failure():
    mock_client = MagicMock()
    mock_msg_fallback = MagicMock()
    mock_msg_fallback.sid = "SM_FALLBACK_999"

    # First call (template) fails, second call (body) succeeds
    mock_client.messages.create.side_effect = [
        RuntimeError("Template rejected by Meta"),
        mock_msg_fallback,
    ]

    notifier = WhatsAppNotifier(
        account_sid="AC123",
        auth_token="AUTH123",
        from_number="+15559876543",
        client=mock_client,
    )

    sid = notifier.send_message(
        to="+15551234567",
        body="Fallback message content",
        content_sid="HX_INVALID",
        content_variables={"1": "test"},
    )

    assert sid == "SM_FALLBACK_999"
    assert mock_client.messages.create.call_count == 2
    # Verify fallback arguments
    second_call_kwargs = mock_client.messages.create.call_args_list[1].kwargs
    assert second_call_kwargs["body"] == "Fallback message content"
    assert "content_sid" not in second_call_kwargs


def test_send_message_unconfigured_returns_none():
    notifier = WhatsAppNotifier(account_sid=None, auth_token=None)
    sid = notifier.send_message(to="+15551234567", body="Test")
    assert sid is None


def test_send_broadcast():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        MagicMock(sid="SM_1"),
        RuntimeError("Network error"),
        MagicMock(sid="SM_3"),
    ]

    notifier = WhatsAppNotifier(
        account_sid="AC123",
        auth_token="AUTH123",
        from_number="+15559876543",
        allowed_numbers=["+111", "+222", "+333"],
        client=mock_client,
    )

    sids = notifier.send_broadcast(body="Broadcast update")
    assert sids == ["SM_1", "SM_3"]
    assert mock_client.messages.create.call_count == 3


def test_notifier_from_config():
    cfg = {
        "whatsapp": {
            "twilio_account_sid": "AC_CONFIG",
            "twilio_auth_token": "TOKEN_CONFIG",
            "twilio_phone_number": "+15559876543",
            "allowed_numbers": ["+15551234567"],
        }
    }
    with patch("twilio.rest.Client"):
        notifier = WhatsAppNotifier.from_config(cfg)
        assert notifier.from_number == "whatsapp:+15559876543"
        assert notifier.allowed_numbers == ["whatsapp:+15551234567"]


def test_send_alert():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM_ALERT_1")

    notifier = WhatsAppNotifier(
        account_sid="AC123",
        auth_token="AUTH123",
        from_number="+15559876543",
        allowed_numbers=["+15551234567"],
        client=mock_client,
    )

    sids = notifier.send_alert("Radarr failed to delete movie")
    assert sids == ["SM_ALERT_1"]
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "🚨 *Projectionist Alert*" in call_kwargs["body"]
    assert "Radarr failed to delete movie" in call_kwargs["body"]
