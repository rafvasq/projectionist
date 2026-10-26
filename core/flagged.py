"""
core/flagged.py
The deletion queue. Movies matching a retention rule are flagged and wait out a grace
window before the cleaner deletes them; a movie that stops matching drops off the queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.retention import Flag
from core.storage import load_flagged, save_flagged


def reconcile(flags: list[Flag], queue: dict[str, Any], now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Build the new queue from the current flags. Movies still flagged keep their original
    flagged_at, new ones are stamped now, and movies no longer flagged are dropped.
    Returns (new_queue, newly_added_entries).
    """
    fresh: dict[str, Any] = {}
    added: list[dict[str, Any]] = []
    for flag in flags:
        key = str(flag.movie.ratingKey)
        entry = {
            "title": flag.movie.title,
            "year": flag.movie.year,
            "tmdb_id": flag.movie.tmdb_id,
            "rule": flag.rule,
            "reason": flag.reason,
            "idle_days": flag.idle_days,
            "flagged_at": queue.get(key, {}).get("flagged_at") or now.isoformat(),
        }
        fresh[key] = entry
        if key not in queue:
            added.append(entry)
    return fresh, added


def deletion_date(entry: dict[str, Any], grace_days: int) -> datetime:
    return datetime.fromisoformat(entry["flagged_at"]) + timedelta(days=grace_days)


def sort_key(entry: dict[str, Any]) -> tuple[str, int]:
    """Deletion order: oldest flag first, and among movies flagged together, the most idle first."""
    return entry["flagged_at"], -entry.get("idle_days", 0)


def due_keys(queue: dict[str, Any], grace_days: int, now: datetime) -> list[str]:
    """Keys whose grace window has passed, in deletion order."""
    due = [k for k, e in queue.items() if deletion_date(e, grace_days) <= now]
    return sorted(due, key=lambda k: sort_key(queue[k]))


def dequeue(rating_key: Any) -> bool:
    """Remove a movie from the saved queue (e.g. when it is kept). True if it was queued."""
    queue = load_flagged()
    if queue.pop(str(rating_key), None) is None:
        return False
    save_flagged(queue)
    return True
