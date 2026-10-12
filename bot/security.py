"""
bot/security.py
Security utilities for Twilio WhatsApp webhooks:
- Phone number normalization & authorization checking
- Twilio HMAC-SHA1 signature verification
- Optional route decorator for webhook authentication
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Optional, Union

from twilio.request_validator import RequestValidator
from core.notifier import normalize_whatsapp_number

logger = logging.getLogger(__name__)


def is_authorized_number(
    sender: Optional[Union[str, int]],
    allowed_numbers: Optional[list[str]] = None,
) -> bool:
    """
    Verify if incoming sender phone number is authorized.
    Normalizes both sender and allowed numbers to WhatsApp URI format ('whatsapp:+...').
    Returns False if sender or allowed_numbers is empty/invalid.
    """
    if not sender or not allowed_numbers:
        return False

    norm_sender = normalize_whatsapp_number(sender)
    if not norm_sender:
        return False

    norm_allowed = {
        normalize_whatsapp_number(num)
        for num in allowed_numbers
        if normalize_whatsapp_number(num)
    }

    return norm_sender in norm_allowed


def validate_twilio_request(
    request_obj: Any,
    auth_token: Optional[str] = None,
    validator: Optional[RequestValidator] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    Validate Twilio incoming HTTP POST request signature using RequestValidator.

    Bypass Rules:
    - If auth_token and validator are None/empty: bypasses and returns True (dev/mock mode).
    - If Flask app has TESTING=True or request has testing=True: bypasses and returns True.

    Reverse Proxy Handling:
    - Automatically reconstructs absolute external URL using X-Forwarded-Proto
      and X-Forwarded-Host when running behind ngrok, Cloudflare, or Traefik.
    """
    # 1. Test / development mode bypass
    if getattr(request_obj, "testing", False):
        return True
    try:
        from flask import current_app
        if current_app and current_app.config.get("TESTING"):
            return True
        if not auth_token:
            auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    except Exception:
        pass

    if not auth_token and validator is None:
        return True

    # 2. Extract Twilio signature header
    signature = request_obj.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning("Twilio signature validation failed: Missing X-Twilio-Signature header.")
        return False

    val = validator or RequestValidator(auth_token)

    # 3. Determine request URL (accounting for reverse proxies)
    if webhook_url:
        target_url = webhook_url
    else:
        target_url = getattr(request_obj, "url", "")
        proto = request_obj.headers.get("X-Forwarded-Proto")
        host = request_obj.headers.get("X-Forwarded-Host")
        if proto and host:
            path = getattr(request_obj, "full_path", getattr(request_obj, "path", ""))
            if path.endswith("?"):
                path = path[:-1]
            target_url = f"{proto}://{host}{path}"

    # 4. Extract POST form variables
    try:
        post_vars = request_obj.form.to_dict()
    except Exception:
        post_vars = {}

    is_valid = val.validate(target_url, post_vars, signature)
    if not is_valid:
        logger.warning("Twilio signature verification failed for URL: %s", target_url)
    return is_valid


def twilio_auth_required(auth_token: Optional[str] = None):
    """
    Flask route decorator to enforce Twilio signature verification.
    Rejects unauthorized requests with 403 Forbidden.
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import request, Response, current_app
            token = auth_token or current_app.config.get("TWILIO_AUTH_TOKEN")
            if not validate_twilio_request(request, auth_token=token):
                return Response("Forbidden: Invalid Twilio request signature", status=403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
