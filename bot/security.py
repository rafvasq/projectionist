"""
bot/security.py
Security utilities for Twilio WhatsApp webhooks:
- Phone number normalization & authorization checking
- Twilio HMAC-SHA1 signature verification (fails closed when no auth token is configured)
- Shared-secret verification for the Overseerr webhook
- Route decorators enforcing both
"""

from __future__ import annotations

import functools
import hmac
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


def _app_config(key: str, default: Any = None) -> Any:
    """Read a Flask app config value, or return default outside an application context."""
    try:
        from flask import current_app
        return current_app.config.get(key, default)
    except Exception:
        return default


def validate_twilio_request(
    request_obj: Any,
    auth_token: Optional[str] = None,
    validator: Optional[RequestValidator] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    Validate Twilio incoming HTTP POST request signature using RequestValidator.

    Bypass Rules:
    - If the Flask app has TESTING=True or the request has testing=True: returns True.
    - If whatsapp.verify_signature is false (TWILIO_VERIFY_SIGNATURE): returns True.
      Intended for local development only.

    Fails closed: with no auth token (and no injected validator) the request is rejected.

    URL used for the signature (Twilio signs the exact public URL it called):
    - Explicit webhook_url argument, if given.
    - Otherwise whatsapp.public_url (TWILIO_PUBLIC_URL) joined with the request path.
    - Otherwise the request URL, rebuilt from X-Forwarded-Proto/Host when running
      behind a reverse proxy such as ngrok, Cloudflare, or Traefik.
    """
    # 1. Test / development bypasses
    if getattr(request_obj, "testing", False) or _app_config("TESTING", False):
        return True
    if not _app_config("TWILIO_VERIFY_SIGNATURE", True):
        return True

    if not auth_token:
        auth_token = _app_config("TWILIO_AUTH_TOKEN")
    if not auth_token and validator is None:
        logger.error(
            "Rejecting Twilio request: no whatsapp.twilio_auth_token configured. "
            "Set it, or set whatsapp.verify_signature: false for local development."
        )
        return False

    # 2. Extract Twilio signature header
    signature = request_obj.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning("Twilio signature validation failed: Missing X-Twilio-Signature header.")
        return False

    val = validator or RequestValidator(auth_token)

    # 3. Determine request URL (accounting for reverse proxies)
    path = getattr(request_obj, "full_path", getattr(request_obj, "path", ""))
    if path.endswith("?"):
        path = path[:-1]

    public_url = _app_config("TWILIO_PUBLIC_URL")
    if webhook_url:
        target_url = webhook_url
    elif public_url:
        target_url = f"{str(public_url).rstrip('/')}{path}"
    else:
        target_url = getattr(request_obj, "url", "")
        proto = request_obj.headers.get("X-Forwarded-Proto")
        host = request_obj.headers.get("X-Forwarded-Host")
        if proto and host:
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


def validate_overseerr_request(request_obj: Any, secret: Optional[str] = None) -> bool:
    """
    Validate the Overseerr webhook shared secret.

    Overseerr sends its configured "Authorization Header" value with each notification.
    Both the raw secret and a "Bearer <secret>" form are accepted. Fails closed: with no
    secret configured (overseerr.webhook_secret / OVERSEERR_WEBHOOK_SECRET) every request
    is rejected. The comparison is constant-time.
    """
    if getattr(request_obj, "testing", False) or _app_config("TESTING", False):
        return True

    if not secret:
        secret = _app_config("OVERSEERR_WEBHOOK_SECRET")
    if not secret:
        logger.error(
            "Rejecting Overseerr webhook: no overseerr.webhook_secret configured. "
            "Set it and add the same value as the Authorization Header in Overseerr's webhook settings."
        )
        return False

    provided = (request_obj.headers.get("Authorization", "") or "").strip()
    if provided.lower().startswith("bearer "):
        provided = provided[7:].strip()
    if not provided:
        logger.warning("Overseerr webhook rejected: missing Authorization header.")
        return False

    is_valid = hmac.compare_digest(provided.encode("utf-8"), str(secret).encode("utf-8"))
    if not is_valid:
        logger.warning("Overseerr webhook rejected: Authorization header mismatch.")
    return is_valid


def overseerr_auth_required(secret: Optional[str] = None):
    """Flask route decorator enforcing the Overseerr webhook shared secret (403 on failure)."""
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import request, Response
            if not validate_overseerr_request(request, secret=secret):
                return Response("Forbidden: Invalid webhook credentials", status=403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
