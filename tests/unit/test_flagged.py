"""Unit tests for core/flagged.py: the deletion queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.flagged import deletion_date, dequeue, due_keys, reconcile
from core.retention import Flag
from core.storage import load_flagged, save_flagged
from tests.conftest import make_movie

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def flag(key, rule="idle"):
    return Flag(make_movie(ratingKey=key, title=f"Movie {key}", year=2000), rule, "idle 130 days")


def test_reconcile_keeps_old_stamps_adds_new_and_drops_unflagged():
    queue = {
        "1": {"title": "Movie 1", "flagged_at": "2026-10-01T00:00:00+00:00"},
        "2": {"title": "Movie 2", "flagged_at": "2026-10-02T00:00:00+00:00"},
    }
    new_queue, added = reconcile([flag(1), flag(3)], queue, NOW)

    assert set(new_queue) == {"1", "3"}                                  # 2 dropped
    assert new_queue["1"]["flagged_at"] == "2026-10-01T00:00:00+00:00"   # stamp kept
    assert new_queue["3"]["flagged_at"] == NOW.isoformat()               # newly stamped
    assert [e["title"] for e in added] == ["Movie 3"]
    assert new_queue["3"]["rule"] == "idle" and new_queue["3"]["reason"] == "idle 130 days"


def test_due_keys_respects_grace_window_and_orders_oldest_first():
    queue = {
        "a": {"flagged_at": (NOW - timedelta(days=31)).isoformat()},
        "b": {"flagged_at": (NOW - timedelta(days=45)).isoformat()},
        "c": {"flagged_at": (NOW - timedelta(days=30)).isoformat()},   # exactly at the boundary
        "d": {"flagged_at": (NOW - timedelta(days=29)).isoformat()},
    }
    assert due_keys(queue, 30, NOW) == ["b", "a", "c"]
    assert deletion_date(queue["d"], 30) == NOW + timedelta(days=1)


def test_dequeue_removes_a_saved_entry():
    save_flagged({"7": {"title": "Heat", "flagged_at": NOW.isoformat()}, "8": {"title": "Alien", "flagged_at": NOW.isoformat()}})
    assert dequeue(7) is True
    assert set(load_flagged()) == {"8"}
    assert dequeue(7) is False
