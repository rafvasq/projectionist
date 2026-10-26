"""Unit tests for bot/answers.py: the read-only answers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot import answers
from tests.conftest import make_movie

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def days_ago(n):
    return NOW - timedelta(days=n)


# ==============================================================================
# recent
# ==============================================================================

def test_recent_lists_the_window_newest_first_and_skips_the_rest():
    movies = [
        make_movie(ratingKey=1, title="Three Days", year=2001, added_at=days_ago(3)),
        make_movie(ratingKey=2, title="One Day", year=2002, added_at=days_ago(1)),
        make_movie(ratingKey=3, title="Twenty Days", added_at=days_ago(20)),
    ]
    undated = make_movie(ratingKey=4, title="No Date")
    undated.added_at = None

    reply = answers.recent(movies + [undated], days=7, now=NOW)

    assert reply.startswith("🆕 *Added in the last 7 days (2)*")
    assert reply.index("One Day (2002) · Oct 25") < reply.index("Three Days (2001) · Oct 23")
    assert "Twenty Days" not in reply and "No Date" not in reply


def test_recent_with_nothing_new_and_with_a_long_list():
    assert answers.recent([make_movie(added_at=days_ago(30))], days=7, now=NOW) == "Nothing has been added in the last 7 days."
    many = [make_movie(ratingKey=i, title=f"Film {i:02d}", added_at=days_ago(1)) for i in range(20)]
    reply = answers.recent(many, days=7, now=NOW)
    assert "(20)" in reply and "…and 5 more" in reply


# ==============================================================================
# downloading
# ==============================================================================

def test_eta_formats():
    assert answers._eta("00:12:34") == "~12m left"
    assert answers._eta("01:12:00") == "~1h 12m left"
    assert answers._eta("1.02:03:04") == "~26h 3m left"
    assert answers._eta("00:00:30") == "almost done"
    assert answers._eta(None) == "" and answers._eta("soon") == ""


def test_downloading_separates_active_downloads_from_films_waiting_for_a_release():
    queue = ([
        {"movieId": 1, "movie": {"title": "Heat", "year": 1995}, "status": "downloading",
         "size": 1000, "sizeleft": 570, "timeleft": "00:12:34"},
        {"movieId": 5, "movie": {"title": "Stuck", "year": 2001}, "status": "paused",
         "size": 100, "sizeleft": 90, "trackedDownloadStatus": "warning"},
    ], 2)
    missing = ([
        {"id": 1, "title": "Heat", "year": 1995, "added": "2026-10-25T00:00:00Z", "isAvailable": True},    # already downloading
        {"id": 2, "title": "Times Square", "year": 1980, "added": "2026-10-25T20:00:00Z",
         "lastSearchTime": "2026-10-25T20:53:45Z", "isAvailable": True},
        {"id": 3, "title": "Future Film", "year": 2027, "added": "2026-10-20T00:00:00Z", "isAvailable": False},
        {"id": 4, "title": "Never Searched", "added": "2026-10-19T00:00:00Z", "lastSearchTime": None, "isAvailable": True},
    ], 4)

    reply = answers.downloading(queue, missing, now=NOW)

    assert "⬇️ *Downloading (2)*" in reply
    assert "- Heat (1995) - 43% · ~12m left" in reply
    assert "- ⚠️ Stuck (2001) - 10% · paused" in reply
    assert "⏳ *Waiting for a release (3)*" in reply
    assert "- Times Square (1980) - searched 15h ago" in reply
    assert "- Future Film (2027) - not released yet" in reply
    assert "- Never Searched - not searched yet" in reply
    assert reply.count("Heat") == 1          # not listed twice


def test_downloading_with_nothing_going_on_and_with_long_lists():
    assert answers.downloading(([], 0), ([], 0), now=NOW) == "Nothing is downloading, and nothing is waiting on a release."
    waiting = [{"id": i, "title": f"Film {i:02d}", "added": f"2026-10-{i + 1:02d}T00:00:00Z"} for i in range(20)]
    reply = answers.downloading(([], 0), (waiting, 20), now=NOW)
    assert "(20)" in reply and "…and 5 more" in reply
    only_waiting = answers.downloading(([], 0), (waiting[:1], 1), now=NOW)
    assert "Downloading" not in only_waiting


@pytest.mark.parametrize("hours, expected", [(0, "searched just now"), (5, "searched 5h ago"), (60, "searched 2d ago")])
def test_search_age_wording(hours, expected):
    stamp = (NOW - timedelta(hours=hours)).isoformat()
    assert answers._ago(stamp, NOW) == expected


# ==============================================================================
# cleaner_log
# ==============================================================================

def test_cleaner_log_summarises_each_kind_of_run():
    runs = [
        {"at": "2026-11-25T04:00-05:00", "status": "completed", "dry_run": False, "queued": 94,
         "deleted": ["Taste of Cherry", "The Goonies", "Rental Family", "Labyrinth"],
         "failed": ["Bad Movie (Not in Radarr)"], "cleanup_issues": ["x"], "flagged": 2, "deferred": 5},
        {"at": "2026-10-26T12:44-04:00", "status": "no_candidates", "dry_run": True, "queued": 115},
        {"at": "2026-10-26T12:33-04:00", "status": "completed", "dry_run": True, "flagged": 115, "queued": 115},
        {"at": "2026-10-20T04:00-04:00", "status": "plex_error", "dry_run": False},
        {"at": "garbage", "status": "config_error", "dry_run": False},
    ]
    lines = answers.cleaner_log(runs).splitlines()
    assert lines[0] == "📜 *Recent cleaner runs*"
    assert lines[1] == ("- Nov 25 04:00 · live · removed 4 (Taste of Cherry, The Goonies, Rental Family +1) · "
                        "1 failed · 1 cleanup issue · flagged 2 · 5 deferred · 94 queued")
    assert lines[2] == "- Oct 26 12:44 · dry run · nothing to do · 115 queued"
    assert lines[3] == "- Oct 26 12:33 · dry run · flagged 115 · 115 queued"
    assert lines[4] == "- Oct 20 04:00 · live · couldn't reach Plex"
    assert lines[5] == "- unknown time · live · stopped: fix the deletion rules"


def test_cleaner_log_before_the_first_run():
    assert answers.cleaner_log([]) == "The cleaner hasn't run yet."
