"""
core/retention.py
Retention rules: which movies are flagged for deletion, and why.

A movie is flagged when every condition of at least one rule matches. Protected movies
(labels, collections, tracked collections) are never flagged. Three conditions exist,
each with a _gte or _lt suffix:
  idle_days          days since the later of "added" and "last watched"
  added_days         days since added
  last_watched_days  days since last watched (never watched counts as infinitely long ago)

Without a `rules` key in the deletion config, the legacy `max_age_days` becomes a single
rule: idle_days_gte: max_age_days.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from core.plex import MovieMeta
from core.storage import load_tracked_collections

logger = logging.getLogger(__name__)

CONDITIONS = ("idle_days", "added_days", "last_watched_days")
DEFAULT_MAX_AGE_DAYS = 120


@dataclass
class Flag:
    movie: MovieMeta
    rule: str
    reason: str
    idle_days: int = 0


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Return dt as timezone-aware UTC (naive datetimes are assumed to be UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_rules(del_cfg: dict[str, Any]) -> list[tuple[str, dict[str, float]]]:
    """Validated (name, conditions) pairs from config. Raises ValueError on a bad rule."""
    raw = del_cfg.get("rules")
    if not raw:
        age = int(del_cfg.get("max_age_days", DEFAULT_MAX_AGE_DAYS) or DEFAULT_MAX_AGE_DAYS)
        return [("idle", {"idle_days_gte": age})]
    if not isinstance(raw, list):
        raise ValueError("deletion.rules must be a list")

    rules = []
    for i, rule in enumerate(raw, 1):
        name = str(rule.get("name") or f"rule{i}") if isinstance(rule, dict) else f"rule{i}"
        when = rule.get("when") if isinstance(rule, dict) else None
        if not isinstance(when, dict) or not when:
            raise ValueError(f"deletion rule '{name}' needs a non-empty 'when' section")
        for key, value in when.items():
            cond, _, op = str(key).rpartition("_")
            if cond not in CONDITIONS or op not in ("gte", "lt"):
                raise ValueError(
                    f"deletion rule '{name}': unknown condition '{key}' "
                    f"(use {', '.join(CONDITIONS)} with a _gte or _lt suffix)"
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"deletion rule '{name}': '{key}' must be a number")
        rules.append((name, when))
    return rules


def _protect_tags(del_cfg: dict[str, Any], tracked: dict[str, Any]) -> set[str]:
    raw = del_cfg.get("protect_tag", ["Keep", "Keep Forever"])
    if isinstance(raw, str):
        tags = {raw.strip().lower()}
    elif isinstance(raw, (list, tuple, set)):
        tags = {t.strip().lower() for t in raw if isinstance(t, str)}
    else:
        tags = set()
    tags.update({"keep", "keep forever"})
    for title, data in tracked.items():
        if not isinstance(data, dict) or data.get("protect", True):
            tags.add(str(title).strip().lower())
    return tags


def _metrics(movie: MovieMeta, now: datetime) -> Optional[dict[str, float]]:
    added = _to_utc(movie.added_at)
    if added is None:
        return None
    viewed = _to_utc(movie.last_viewed_at)
    return {
        "idle_days": (now - max(added, viewed or added)).days,
        "added_days": (now - added).days,
        "last_watched_days": (now - viewed).days if viewed else math.inf,
    }


def _matches(when: dict[str, float], metrics: dict[str, float]) -> bool:
    for key, value in when.items():
        cond, _, op = key.rpartition("_")
        actual = metrics[cond]
        if (op == "gte" and not actual >= value) or (op == "lt" and not actual < value):
            return False
    return True


def _reason(when: dict[str, float], metrics: dict[str, float]) -> str:
    parts: list[str] = []
    for key in when:
        cond = key.rpartition("_")[0]
        if cond == "idle_days":
            text = f"idle {metrics['idle_days']} days"
        elif cond == "added_days":
            text = f"added {metrics['added_days']} days ago"
        elif metrics["last_watched_days"] == math.inf:
            text = "never watched"
        else:
            text = f"last watched {metrics['last_watched_days']} days ago"
        if text not in parts:
            parts.append(text)
    return ", ".join(parts)


def flag_movies(
    movies: list[MovieMeta],
    cfg: dict[str, Any],
    tracked_collections: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> list[Flag]:
    """Movies matching a retention rule, each with the first rule that matched and why."""
    del_cfg = cfg.get("deletion", {}) if isinstance(cfg, dict) else {}
    rules = load_rules(del_cfg)

    if tracked_collections is None:
        try:
            tracked_collections = load_tracked_collections()
        except Exception as exc:
            logger.warning("Could not load tracked collections: %s", exc)
            tracked_collections = {}
    protect = _protect_tags(del_cfg, tracked_collections if isinstance(tracked_collections, dict) else {})

    now = now or datetime.now(timezone.utc)
    flags: list[Flag] = []
    for movie in movies:
        tags = {t.strip().lower() for t in (movie.collections or []) + (movie.labels or []) if isinstance(t, str)}
        if tags & protect:
            continue
        metrics = _metrics(movie, now)
        if metrics is None:
            continue
        for name, when in rules:
            if _matches(when, metrics):
                flags.append(Flag(movie, name, _reason(when, metrics), int(metrics["idle_days"])))
                break
    return flags
