"""
core/roles.py
Who may use the bot, and as what. There are two roles: admins can use slash commands and
import Letterboxd lists, users can request films and shows in plain language. What each
role may do is decided in code, never in config; config only assigns roles.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.notifier import normalize_whatsapp_number

logger = logging.getLogger(__name__)

ADMIN = "admin"
USER = "user"


def load_users(cfg: Optional[dict[str, Any]]) -> dict[str, str]:
    """
    Map each normalized WhatsApp number to its role.

    `whatsapp.users` ([{number, role}]) wins; a missing role defaults to the less
    privileged "user". Without it, each legacy `allowed_numbers` entry is an admin.
    Raises ValueError for an entry with no number or an unknown role.
    """
    wa = cfg.get("whatsapp", {}) if isinstance(cfg, dict) else {}
    users = wa.get("users")
    if not users:
        return {n: ADMIN for n in map(normalize_whatsapp_number, wa.get("allowed_numbers") or []) if n}

    if wa.get("allowed_numbers"):
        logger.warning("whatsapp.allowed_numbers is ignored because whatsapp.users is set")
    result: dict[str, str] = {}
    for entry in users:
        number = normalize_whatsapp_number(entry.get("number")) if isinstance(entry, dict) else ""
        role = (entry.get("role") or USER) if isinstance(entry, dict) else None
        if not number or role not in (ADMIN, USER):
            raise ValueError(f"whatsapp.users entries need a number and a role of admin or user, got: {entry!r}")
        result[number] = role
    return result


def role_of(sender: Optional[str], users: dict[str, str]) -> Optional[str]:
    """The sender's role, or None if they are not allowed to use the bot."""
    return users.get(normalize_whatsapp_number(sender)) if sender else None


def numbers(users: dict[str, str], role: Optional[str] = None) -> list[str]:
    """All allowed numbers, or only those with the given role."""
    return [n for n, r in users.items() if role is None or r == role]
