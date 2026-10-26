"""
core/notifier.py
Centralized WhatsApp notification service via Twilio REST API.
Handles phone number normalization, Meta template variable newline sanitization (" • "),
and automatic fallback to direct body messages.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


def normalize_whatsapp_number(number: Optional[Union[str, int]]) -> str:
    """
    Normalize phone number to WhatsApp Twilio URI scheme ('whatsapp:+...').
    Strips whitespace and preserves already-prefixed numbers.
    Returns empty string if input is None or blank.
    """
    if number is None:
        return ""
    cleaned = str(number).strip()
    if not cleaned:
        return ""
    if cleaned.startswith("whatsapp:"):
        return cleaned
    return f"whatsapp:{cleaned}"


def sanitize_template_variable(text: Optional[str]) -> str:
    """
    Sanitize text for Meta WhatsApp Content API template variables.
    Meta WhatsApp templates reject newlines (\\r, \\n) in variables.
    This function converts any multiline text into an inline bullet-separated string,
    replacing newline sequences with ' • ' and omitting blank lines.
    """
    if text is None:
        return ""
    cleaned = str(text).strip()
    if not cleaned:
        return ""
    parts = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not parts:
        return ""
    return " • ".join(parts)


class WhatsAppNotifier:
    """
    Centralized WhatsApp notification client.

    Features:
    - Normalizes recipient and sender numbers.
    - Meta template variable sanitization (" • ").
    - Automatic direct body fallback if template dispatch fails.
    - Broadcast dispatching to allowed_numbers or custom recipient lists.
    - Operational failure alerting.
    """

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        allowed_numbers: Optional[list[str]] = None,
        client: Optional[Any] = None,
        admin_numbers: Optional[list[str]] = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = normalize_whatsapp_number(from_number) if from_number else None
        self.allowed_numbers = [
            normalize_whatsapp_number(n)
            for n in (allowed_numbers or [])
            if normalize_whatsapp_number(n)
        ]
        # Admins receive cleaner summaries and alerts; without roles, everyone is an admin.
        self.admin_numbers = (
            [normalize_whatsapp_number(n) for n in admin_numbers if normalize_whatsapp_number(n)]
            if admin_numbers is not None
            else list(self.allowed_numbers)
        )

        if client is not None:
            self.client = client
        elif self.account_sid and self.auth_token:
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
            except Exception as exc:
                logger.error("Failed to initialize Twilio Client: %s", exc)
                self.client = None
        else:
            self.client = None

    @classmethod
    def from_config(cls, cfg: Optional[dict[str, Any]] = None) -> WhatsAppNotifier:
        if cfg is None:
            from core.config import load_config
            cfg = load_config()
        from core.roles import ADMIN, load_users, numbers

        wa_cfg = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
        users = load_users(cfg)
        return cls(
            account_sid=wa_cfg.get("twilio_account_sid"),
            auth_token=wa_cfg.get("twilio_auth_token"),
            from_number=wa_cfg.get("twilio_phone_number"),
            allowed_numbers=numbers(users),
            admin_numbers=numbers(users, ADMIN),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.client and self.from_number)

    def send_message(
        self,
        to: str,
        body: str,
        content_sid: Optional[str] = None,
        content_variables: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Send a WhatsApp message via Twilio.

        If content_sid is provided:
          - Automatically sanitizes template variable values (" • ").
          - Attempts template dispatch.
          - If template dispatch raises an exception, automatically falls back to sending `body`.
        If content_sid is not provided:
          - Directly dispatches `body`.

        Returns message SID on success, None on failure.
        """
        if not self.is_configured:
            logger.warning("WhatsAppNotifier: Not configured (missing credentials or from_number).")
            return None

        target = normalize_whatsapp_number(to)
        if not target:
            logger.warning("WhatsAppNotifier: Invalid or empty recipient number.")
            return None

        # Stage 1: Attempt template send if content_sid provided
        if content_sid:
            try:
                kwargs: dict[str, Any] = {
                    "from_": self.from_number,
                    "to": target,
                    "content_sid": content_sid,
                }
                if content_variables:
                    sanitized_vars = {
                        str(k): sanitize_template_variable(str(v))
                        for k, v in content_variables.items()
                    }
                    kwargs["content_variables"] = json.dumps(sanitized_vars)

                msg = self.client.messages.create(**kwargs)
                logger.info(
                    "Sent WhatsApp message via template (%s) to %s (SID: %s)",
                    content_sid, target, getattr(msg, "sid", "unknown")
                )
                return getattr(msg, "sid", None)
            except Exception as template_err:
                logger.warning(
                    "WhatsApp template send (%s) failed to %s: %s. Falling back to direct body message.",
                    content_sid, target, template_err
                )

        # Stage 2: Direct body dispatch (fallback or standard)
        try:
            kwargs = {
                "from_": self.from_number,
                "to": target,
                "body": body,
            }
            msg = self.client.messages.create(**kwargs)
            logger.info("Sent WhatsApp direct message to %s (SID: %s)", target, getattr(msg, "sid", "unknown"))
            return getattr(msg, "sid", None)
        except Exception as exc:
            logger.error("Failed to send WhatsApp message to %s: %s", target, exc)
            return None

    def send_broadcast(
        self,
        body: str,
        recipients: Optional[list[str]] = None,
        content_sid: Optional[str] = None,
        content_variables: Optional[dict[str, str]] = None,
    ) -> list[str]:
        """
        Broadcast a message to multiple recipients.
        Defaults to configured allowed_numbers if recipients is None.
        Deduplicates recipients and returns list of successful message SIDs.
        """
        raw_list = recipients if recipients is not None else self.allowed_numbers
        targets: list[str] = []
        seen: set[str] = set()
        for r in raw_list:
            norm = normalize_whatsapp_number(r)
            if norm and norm not in seen:
                seen.add(norm)
                targets.append(norm)

        successful_sids: list[str] = []
        for target in targets:
            sid = self.send_message(
                to=target,
                body=body,
                content_sid=content_sid,
                content_variables=content_variables,
            )
            if sid:
                successful_sids.append(sid)

        return successful_sids

    def send_alert(
        self,
        body: str,
        recipients: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Send an urgent operational alert (e.g. Radarr deletion failures) to recipients.
        """
        alert_body = f"🚨 *Projectionist Alert*\n\n{body}"
        return self.send_broadcast(
            body=alert_body, recipients=recipients if recipients is not None else self.admin_numbers
        )
