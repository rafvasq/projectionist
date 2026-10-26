"""
bot/answers.py
Read-only answers about the library. They take data and return reply text, so the admin
slash commands (/recent, /downloads, /leaving) and, later, plain-language questions can
share them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.flagged import deletion_date, sort_key
from core.retention import _to_utc, flag_movies
from core.storage import load_flagged

MAX_LISTED = 15


def movie_label(movie: Any) -> str:
    year = getattr(movie, "year", None)
    return f"{movie.title} ({year})" if year else str(movie.title)


def _entry_label(entry: dict[str, Any]) -> str:
    return f"{entry['title']} ({entry['year']})" if entry.get("year") else str(entry["title"])


def _more(total: int, shown: int) -> list[str]:
    return [f"…and {total - shown} more"] if total > shown else []


# ==============================================================================
# /recent
# ==============================================================================

def recent(movies: list[Any], days: int = 7, now: Optional[datetime] = None) -> str:
    """Movies added to the library in the last `days` days, newest first."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    added = sorted(
        ((_to_utc(m.added_at), m) for m in movies if m.added_at is not None),
        key=lambda pair: pair[0],
        reverse=True,
    )
    fresh = [(when, m) for when, m in added if when >= since]
    if not fresh:
        return f"Nothing has been added in the last {days} days."

    lines = [f"- {movie_label(m)} · {when:%b %d}" for when, m in fresh[:MAX_LISTED]]
    return f"🆕 *Added in the last {days} days ({len(fresh)})*\n" + "\n".join(lines + _more(len(fresh), MAX_LISTED))


# ==============================================================================
# /downloads
# ==============================================================================

def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _to_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _ago(value: Optional[str], now: datetime) -> str:
    when = _parse_time(value)
    if when is None:
        return "not searched yet"
    hours = int((now - when).total_seconds() // 3600)
    if hours < 1:
        return "searched just now"
    return f"searched {hours}h ago" if hours < 48 else f"searched {hours // 24}d ago"


def _eta(timeleft: Optional[str]) -> str:
    """Radarr's 'd.hh:mm:ss' or 'hh:mm:ss' as '~1h 12m left'."""
    if not timeleft:
        return ""
    try:
        days_part, _, clock = timeleft.rpartition(".")
        hours, minutes, _seconds = (int(x) for x in clock.split(":"))
        total_minutes = (int(days_part) * 24 if days_part else 0) * 60 + hours * 60 + minutes
    except ValueError:
        return ""
    if total_minutes < 1:
        return "almost done"
    h, m = divmod(total_minutes, 60)
    return f"~{h}h {m}m left" if h else f"~{m}m left"


def _queue_line(record: dict[str, Any]) -> str:
    movie = record.get("movie") or {}
    title = movie.get("title") or record.get("title") or "Unknown"
    label = f"{title} ({movie['year']})" if movie.get("year") else str(title)
    size, left = record.get("size") or 0, record.get("sizeleft") or 0
    parts = [f"{round((size - left) / size * 100)}%"] if size else []
    status = record.get("status")
    if status and status != "downloading":
        parts.append(str(status))
    eta = _eta(record.get("timeleft"))
    if eta:
        parts.append(eta)
    warn = "⚠️ " if record.get("trackedDownloadStatus") in ("warning", "error") or record.get("errorMessage") else ""
    return f"- {warn}{label}" + (f" - {' · '.join(parts)}" if parts else "")


def downloading(
    queue: tuple[list[dict[str, Any]], int],
    missing: tuple[list[dict[str, Any]], int],
    now: Optional[datetime] = None,
) -> str:
    """
    What Radarr is downloading, and what it is still waiting on a release for. A movie that
    was searched and found nothing is neither downloading nor failed, so it gets its own list.
    """
    now = now or datetime.now(timezone.utc)
    active, active_total = queue
    queued_ids = {r.get("movieId") for r in active}
    waiting = sorted(
        (m for m in missing[0] if m.get("id") not in queued_ids),
        key=lambda m: str(m.get("added") or ""),
        reverse=True,
    )

    sections = []
    if active:
        lines = [_queue_line(r) for r in active[:MAX_LISTED]]
        sections.append(f"⬇️ *Downloading ({active_total})*\n" + "\n".join(lines + _more(active_total, len(lines))))
    if waiting:
        lines = []
        for m in waiting[:MAX_LISTED]:
            label = f"{m.get('title')} ({m['year']})" if m.get("year") else str(m.get("title"))
            note = _ago(m.get("lastSearchTime"), now)
            if m.get("isAvailable") is False:
                note = "not released yet"
            lines.append(f"- {label} - {note}")
        sections.append(f"⏳ *Waiting for a release ({len(waiting)})*\n" + "\n".join(lines + _more(len(waiting), len(lines))))
    return "\n\n".join(sections) if sections else "Nothing is downloading, and nothing is waiting on a release."


# ==============================================================================
# /leaving
# ==============================================================================

def leaving(movies: list[Any], config: dict[str, Any]) -> str:
    """The deletion queue with dates and reasons, plus the cleaner's mode."""
    del_cfg = (config or {}).get("deletion", {})
    grace_days = int(del_cfg.get("grace_days", 30))
    if not del_cfg.get("enabled", False):
        mode = "Cleaner is disabled in config, so nothing will be deleted."
    elif del_cfg.get("dry_run", True):
        mode = "Cleaner is in dry-run mode in config: nothing is actually deleted."
    else:
        mode = "⚠️ Cleaner is active: these will be deleted on schedule."

    try:
        flags = flag_movies(movies, config)
    except ValueError as exc:
        return f"❌ Problem with the deletion rules in config: {exc}"
    queue = load_flagged()
    in_library = {str(m.ratingKey) for m in movies}
    queued = sorted((e for k, e in queue.items() if k in in_library), key=sort_key)
    unqueued = sum(1 for f in flags if str(f.movie.ratingKey) not in queue)
    if not queued and not unqueued:
        return f"✅ Nothing is flagged for deletion right now.\n\n{mode}"

    lines = [
        f"- {_entry_label(e)} - leaves {deletion_date(e, grace_days):%b %d} ({e['reason']})"
        for e in queued[:MAX_LISTED]
    ]
    lines += _more(len(queued), MAX_LISTED)
    if unqueued:
        lines.append(f"{unqueued} more will be flagged at the next cleaner run.")
    return f"🧹 *Flagged for deletion ({len(queued)})*\n" + "\n".join(lines) + f"\n\n{mode}\nUse /keep <title> to save one."


# ==============================================================================
# /log
# ==============================================================================

def _names(titles: list[str], shown: int = 3) -> str:
    return ", ".join(titles[:shown]) + (f" +{len(titles) - shown}" if len(titles) > shown else "")


def _run_summary(run: dict[str, Any]) -> str:
    status = run.get("status")
    stopped = {
        "plex_error": "couldn't reach Plex",
        "config_error": "stopped: fix the deletion rules",
        "radarr_not_configured": "stopped: Radarr isn't configured",
    }
    if status in stopped:
        return stopped[status]
    deleted, failed, issues = run.get("deleted") or [], run.get("failed") or [], run.get("cleanup_issues") or []
    parts = []
    if deleted:
        parts.append(f"removed {len(deleted)} ({_names(deleted)})")
    if failed:
        parts.append(f"{len(failed)} failed")
    if issues:
        parts.append(f"{len(issues)} cleanup issue{'s' if len(issues) != 1 else ''}")
    if run.get("flagged"):
        parts.append(f"flagged {run['flagged']}")
    if run.get("deferred"):
        parts.append(f"{run['deferred']} deferred")
    if not parts:
        parts.append("nothing to do")
    return " · ".join(parts + [f"{run.get('queued', 0)} queued"])


def cleaner_log(runs: list[dict[str, Any]]) -> str:
    """The cleaner's last few runs, newest first, one line each."""
    if not runs:
        return "The cleaner hasn't run yet."
    lines = []
    for run in runs:
        try:
            stamp = f"{datetime.fromisoformat(run['at']):%b %d %H:%M}"
        except (KeyError, ValueError):
            stamp = "unknown time"
        mode = "dry run" if run.get("dry_run") else "live"
        lines.append(f"- {stamp} · {mode} · {_run_summary(run)}")
    return "📜 *Recent cleaner runs*\n" + "\n".join(lines)
