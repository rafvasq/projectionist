"""
core/retention.py
Core retention engine for Projectionist.
Evaluates movies eligible for deletion ("Leaving Soon") based on age,
watch history, protected tags, and tracked collections with UTC safety.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.plex import MovieMeta
from core.storage import load_tracked_collections

logger = logging.getLogger(__name__)


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure datetime is timezone-aware in UTC.
    - If None, returns None.
    - If naive (tzinfo is None), attaches timezone.utc.
    - If aware, converts to UTC via astimezone(timezone.utc).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_leaving_soon_movies(
    movies: list[MovieMeta],
    cfg: dict[str, Any],
    tracked_collections: Optional[dict[str, Any]] = None,
) -> list[MovieMeta]:
    """
    Filter movies that meet the deletion engine criteria:
    - In library for >= max_age_days
    - Not viewed in the last 7 days
    - Not protected by tags/labels or active protected tracked collections

    Uses timezone-safe UTC comparisons.

    Args:
        movies: List of MovieMeta objects to evaluate.
        cfg: Configuration dictionary (reads 'deletion' section).
        tracked_collections: Optional preloaded tracked collections dict.
            If None, loads via core.storage.load_tracked_collections().

    Returns:
        List of MovieMeta items eligible for deletion / Leaving Soon row.
    """
    del_cfg = cfg.get("deletion", {}) if isinstance(cfg, dict) else {}
    max_age_days = int(del_cfg.get("max_age_days", 120) or 120)

    raw_protect = del_cfg.get("protect_tag", ["Keep", "Keep Forever"])
    if isinstance(raw_protect, str):
        protect_tags = {raw_protect.strip().lower()}
    elif isinstance(raw_protect, (list, tuple, set)):
        protect_tags = {t.strip().lower() for t in raw_protect if isinstance(t, str)}
    else:
        protect_tags = set()
    protect_tags.update({"keep", "keep forever"})

    # Incorporate protected tracked collections
    if tracked_collections is not None:
        tracked = tracked_collections
    else:
        try:
            tracked = load_tracked_collections()
        except Exception as exc:
            logger.warning("Could not load tracked collections: %s", exc)
            tracked = {}

    if isinstance(tracked, dict):
        for coll_title, coll_data in tracked.items():
            if isinstance(coll_data, dict):
                if coll_data.get("protect", True):
                    protect_tags.add(coll_title.strip().lower())
            else:
                protect_tags.add(str(coll_title).strip().lower())

    now = datetime.now(timezone.utc)
    to_delete: list[MovieMeta] = []

    for m in movies:
        movie_tags = {
            t.strip().lower()
            for t in (getattr(m, "collections", None) or []) + (getattr(m, "labels", None) or [])
            if isinstance(t, str)
        }
        if any(pt in movie_tags for pt in protect_tags):
            continue

        last_viewed = _to_utc(getattr(m, "last_viewed_at", None))
        if last_viewed is not None:
            if (now - last_viewed).days <= 7:
                continue

        added = _to_utc(getattr(m, "added_at", None))
        if added is not None:
            age_days = (now - added).days
            if age_days >= max_age_days:
                to_delete.append(m)

    return to_delete
