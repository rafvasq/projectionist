"""tests/bot/test_commands.py: slash commands and library title matching."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from bot.commands import (
    COMMANDS,
    CommandContext,
    apply_keep_change,
    dispatch,
    find_movies,
    parse_command,
    split_title_year,
)
from core.storage import load_flagged, save_flagged
from tests.conftest import make_movie

SENDER = "whatsapp:+15551234567"


@pytest.fixture
def storage(tmp_path):
    state, tracked = tmp_path / "state.json", tmp_path / "tracked.json"
    state.write_text("{}", encoding="utf-8")
    tracked.write_text("{}", encoding="utf-8")
    with patch("core.storage.get_whatsapp_state_file", return_value=state), \
         patch("core.storage.get_tracked_collections_file", return_value=tracked):
        yield {"state": state, "tracked": tracked}


def make_ctx(movies, config=None, plex=True, radarr=None):
    p = MagicMock()
    p.fetch_movies.return_value = movies
    p.add_label.return_value = 1
    p.remove_label.return_value = 1
    return CommandContext(sender=SENDER, plex=p if plex else None, config=config or {}, radarr=radarr)


def saved_state(storage):
    return json.loads(storage["state"].read_text(encoding="utf-8"))[SENDER]


# ==============================================================================
# Parsing and matching
# ==============================================================================

@pytest.mark.parametrize("text, expected", [
    ("/keep Heat", ("keep", "Heat")),
    ("  /KEEP  Heat 1995 ", ("keep", "Heat 1995")),
    ("/help", ("help", "")),
    ("/", ("", "")),
    ("Keep the Lights On", None),
    ("keep Heat", None),
    ("", None),
])
def test_parse_command(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Heat 1995", ("Heat", 1995)),
    ("Heat (1995)", ("Heat", 1995)),
    ("Heat", ("Heat", None)),
    ("1917", ("1917", None)),
])
def test_split_title_year(text, expected):
    assert split_title_year(text) == expected


LIBRARY = [
    make_movie(ratingKey=1, title="Amélie", year=2001),
    make_movie(ratingKey=2, title="The Godfather", year=1972),
    make_movie(ratingKey=3, title="Keep the Lights On", year=2012),
    make_movie(ratingKey=4, title="Interstellar", year=2014),
    make_movie(ratingKey=5, title="Blade Runner 2049", year=2017),
    make_movie(ratingKey=6, title="Wheat Fields", year=2000),
]


@pytest.mark.parametrize("query, keys, exact", [
    ("amelie", [1], True),                    # accents folded
    ("godfather", [2], True),                 # leading article ignored
    ("lights keep", [3], False),              # words in any order
    ("Interstelar", [4], False),              # typo
    ("Interstelar 1972", [], False),          # explicit year still narrows typos
    ("Blade Runner 2049", [5], True),         # year-like number that is part of the title
    ("heat", [], False),                      # no match inside other words ("wheat")
    ("zzzzqqq", [], False),
])
def test_find_movies(query, keys, exact):
    found, is_exact = find_movies(LIBRARY, query)
    assert [m.ratingKey for m in found] == keys
    assert is_exact is exact


def test_find_movies_orders_same_title_by_year():
    movies = [make_movie(ratingKey=2, title="Dune", year=2021), make_movie(ratingKey=1, title="Dune", year=1984)]
    assert [m.ratingKey for m in find_movies(movies, "Dune")[0]] == [1, 2]
    assert [m.ratingKey for m in find_movies(movies, "Dune 2021")[0]] == [2]


# ==============================================================================
# Dispatch and /help
# ==============================================================================

def test_dispatch_ignores_non_commands():
    assert dispatch("Keep the Lights On", make_ctx([])) is None


def test_dispatch_unknown_and_help():
    assert "Unknown command /frobnicate" in dispatch("/frobnicate", make_ctx([]))
    help_text = dispatch("/help", make_ctx([]))
    assert all(f"/{name}" in help_text for name in COMMANDS)


def test_dispatch_contains_handler_errors():
    boom = ("/status", "x", MagicMock(side_effect=RuntimeError("boom")))
    with patch.dict(COMMANDS, {"status": boom}):
        assert "Something went wrong running /status" in dispatch("/status", make_ctx([]))


# ==============================================================================
# /keep
# ==============================================================================

def test_keep_usage_and_plex_down():
    assert "Usage: /keep" in dispatch("/keep", make_ctx([]))
    assert "Plex isn't available" in dispatch("/keep Heat", make_ctx([], plex=False))


def test_keep_exact_match_tags_immediately(storage):
    ctx = make_ctx([make_movie(ratingKey=7, title="Heat", year=1995)])
    reply = dispatch("/keep Heat", ctx)
    ctx.plex.add_label.assert_called_once_with([7], "Keep Forever")
    assert "Heat (1995)" in reply and "now tagged" in reply


def test_keep_reports_failed_label_and_missing_movie(storage):
    ctx = make_ctx([make_movie(ratingKey=7, title="Heat")])
    ctx.plex.add_label.return_value = 0
    assert "Couldn't tag" in dispatch("/keep Heat", ctx)
    assert "No movie matching 'Alien'" in dispatch("/keep Alien", ctx)


def test_keep_argument_may_start_with_a_command_word(storage):
    ctx = make_ctx([make_movie(ratingKey=3, title="Keep the Lights On", year=2012)])
    dispatch("/keep Keep the Lights On", ctx)
    ctx.plex.add_label.assert_called_once_with([3], "Keep Forever")


@pytest.mark.parametrize("query, library, expected_prompt", [
    ("Interstelar", [make_movie(ratingKey=5, title="Interstellar", year=2014)], "Did you mean *Interstellar (2014)*?"),
    ("heat", [make_movie(ratingKey=2, title="Heat Wave", year=2005)], "Did you mean *Heat Wave (2005)*?"),
])
def test_keep_inexact_match_asks_before_tagging(storage, query, library, expected_prompt):
    ctx = make_ctx(library)
    reply = dispatch(f"/keep {query}", ctx)
    ctx.plex.add_label.assert_not_called()
    assert expected_prompt in reply
    state = saved_state(storage)
    assert state["status"] == "WAITING_KEEP_CHOICE" and state["action"] == "keep"
    assert state["options"]["1"]["ratingKey"] == library[0].ratingKey


def test_keep_ambiguous_offers_numbered_choices(storage):
    movies = [make_movie(ratingKey=1, title="Dune", year=1984), make_movie(ratingKey=2, title="Dune", year=2021)]
    reply = dispatch("/keep Dune", make_ctx(movies))
    assert "Dune (1984)" in reply and "Dune (2021)" in reply and "Reply 0 to Cancel" in reply
    assert saved_state(storage)["options"]["2"] == {"ratingKey": 2, "title": "Dune (2021)"}


def test_keep_choices_are_capped(storage):
    movies = [make_movie(ratingKey=i, title=f"Star {i:02d}", year=2000 + i) for i in range(8)]
    reply = dispatch("/keep Star", make_ctx(movies))
    assert "…and 3 more" in reply
    assert len(saved_state(storage)["options"]) == 5


# ==============================================================================
# /unkeep and apply_keep_change
# ==============================================================================

def test_unkeep_removes_every_protect_label(storage):
    ctx = make_ctx([make_movie(ratingKey=9, title="Heat", year=1995, labels=["Keep", "Keep Forever", "Noir"])])
    reply = dispatch("/unkeep Heat", ctx)
    assert [c.args for c in ctx.plex.remove_label.call_args_list] == [([9], "Keep"), ([9], "Keep Forever")]
    assert "no longer tagged" in reply


def test_unkeep_untagged_movie(storage):
    ctx = make_ctx([make_movie(ratingKey=9, title="Heat")])
    assert "isn't tagged" in dispatch("/unkeep Heat", ctx)
    ctx.plex.remove_label.assert_not_called()


def test_unkeep_only_considers_tagged_matches(storage):
    movies = [
        make_movie(ratingKey=1, title="Dune", year=1984),
        make_movie(ratingKey=2, title="Dune", year=2021, labels=["Keep Forever"]),
    ]
    ctx = make_ctx(movies)
    dispatch("/unkeep Dune", ctx)
    ctx.plex.remove_label.assert_called_once_with([2], "Keep Forever")


def test_unkeep_with_several_tagged_matches_prompts(storage):
    movies = [make_movie(ratingKey=i, title="Dune", year=1980 + i, labels=["Keep"]) for i in (1, 2)]
    reply = dispatch("/unkeep Dune", make_ctx(movies))
    assert "Which one should I unkeep?" in reply
    assert saved_state(storage)["action"] == "unkeep"


def test_apply_keep_change_edge_cases():
    ctx = make_ctx([make_movie(ratingKey=1)])
    assert "no longer in your Plex library" in apply_keep_change(ctx, "unkeep", 99, "Gone (2000)")

    ctx = make_ctx([make_movie(ratingKey=9, labels=["Keep Forever"])])
    ctx.plex.remove_label.return_value = 0
    assert "Couldn't fully untag" in apply_keep_change(ctx, "unkeep", 9, "X")

    ctx.plex.add_label.side_effect = RuntimeError("plex down")
    assert "Something went wrong" in apply_keep_change(ctx, "keep", 9, "X")


# ==============================================================================
# /leaving and /status
# ==============================================================================

def queue_entry(key, title, year=2001, days_ago=5, reason="idle 130 days"):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return str(key), {"title": title, "year": year, "tmdb_id": None, "rule": "idle", "reason": reason, "flagged_at": stamp}


def test_leaving_lists_the_queue_with_dates_and_reasons(storage):
    save_flagged(dict([queue_entry(1, "Old Movie")]))
    movies = [make_movie(ratingKey=1, title="Old Movie", year=2001, added_days_ago=200)]
    reply = dispatch("/leaving", make_ctx(movies, {"deletion": {"enabled": True, "dry_run": False}}))
    assert "Flagged for deletion (1)" in reply and "Old Movie (2001)" in reply
    assert "leaves" in reply and "idle 130 days" in reply and "Cleaner is active" in reply


def test_leaving_mentions_movies_not_flagged_yet(storage):
    movies = [make_movie(ratingKey=1, title="Old Movie", added_days_ago=200)]
    reply = dispatch("/leaving", make_ctx(movies, {"deletion": {"enabled": True}}))
    assert "1 more will be flagged at the next cleaner run" in reply


@pytest.mark.parametrize("deletion, expected", [
    ({"enabled": True, "dry_run": True}, "dry-run"),
    ({"enabled": False}, "disabled in config"),
])
def test_leaving_reports_cleaner_mode(storage, deletion, expected):
    movies = [make_movie(ratingKey=1, title="Old Movie", added_days_ago=200)]
    assert expected in dispatch("/leaving", make_ctx(movies, {"deletion": deletion}))


def test_leaving_nothing_truncation_and_bad_rules(storage):
    cfg = {"deletion": {"enabled": True}}
    assert "Nothing is flagged" in dispatch("/leaving", make_ctx([make_movie(added_days_ago=5)], cfg))

    many = [make_movie(ratingKey=i, title=f"Movie {i:02d}", added_days_ago=300) for i in range(20)]
    save_flagged(dict(queue_entry(i, f"Movie {i:02d}", days_ago=i + 1) for i in range(20)))
    reply = dispatch("/leaving", make_ctx(many, cfg))
    assert "Flagged for deletion (20)" in reply and "…and 5 more" in reply

    bad = {"deletion": {"rules": [{"name": "r", "when": {"rating_gte": 5}}]}}
    assert "Problem with the deletion rules" in dispatch("/leaving", make_ctx(many, bad))


def test_keep_takes_the_movie_off_the_deletion_queue(storage):
    save_flagged(dict([queue_entry(7, "Heat", 1995)]))
    ctx = make_ctx([make_movie(ratingKey=7, title="Heat", year=1995)])
    assert "off the deletion queue" in dispatch("/keep Heat", ctx)
    assert load_flagged() == {}


def test_status(storage):
    assert "No collections are waiting on downloads" in dispatch("/status", make_ctx([]))
    storage["tracked"].write_text(json.dumps({
        "Girl Bands": {"items": {"1": {"status": "available"}, "2": {"status": "pending"}}},
        "Noir": {"items": {"3": {"status": "available"}}},
    }), encoding="utf-8")
    reply = dispatch("/status", make_ctx([]))
    assert "⏳ *Girl Bands*: 1/2 ready" in reply and "✅ *Noir*: 1/1 ready" in reply


# ==============================================================================
# /recent and /downloads
# ==============================================================================

def test_recent_defaults_to_a_week_and_validates_the_days():
    ctx = make_ctx([
        make_movie(ratingKey=1, title="Fresh Film", added_days_ago=2),
        make_movie(ratingKey=2, title="Older Film", added_days_ago=40),
    ])
    week = dispatch("/recent", ctx)
    assert "Fresh Film" in week and "Older Film" not in week
    assert "Older Film" in dispatch("/recent 60", ctx)
    for bad in ("0", "366", "abc", "-3"):
        assert "Usage: /recent" in dispatch(f"/recent {bad}", ctx)
    assert "Plex isn't available" in dispatch("/recent", make_ctx([], plex=False))


def _radarr(queue=([], 0), missing=([], 0), configured=True):
    radarr = MagicMock()
    radarr.is_configured = configured
    radarr.get_queue.return_value = queue
    radarr.get_missing.return_value = missing
    return radarr


def test_downloads_reports_a_missing_or_unreachable_radarr():
    assert "Radarr isn't configured" in dispatch("/downloads", make_ctx([]))
    assert "Radarr isn't configured" in dispatch("/downloads", make_ctx([], radarr=_radarr(configured=False)))
    broken = _radarr()
    broken.get_queue.return_value = None
    assert "Couldn't reach Radarr" in dispatch("/downloads", make_ctx([], radarr=broken))


def test_downloads_shows_what_is_waiting_for_a_release():
    missing = ([{"id": 2, "title": "Times Square", "year": 1980, "isAvailable": True, "lastSearchTime": None,
                 "added": "2026-10-25T00:00:00Z"}], 1)
    reply = dispatch("/downloads", make_ctx([], radarr=_radarr(missing=missing)))
    assert "Waiting for a release (1)" in reply and "Times Square (1980) - not searched yet" in reply


def test_the_app_hands_radarr_to_the_commands():
    from bot.app import create_app, get_services

    radarr = MagicMock()
    app = create_app(config={"whatsapp": {"allowed_numbers": []}}, radarr=radarr, testing=True)
    with app.app_context():
        assert get_services()["radarr"] is radarr
