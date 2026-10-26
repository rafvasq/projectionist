"""Role gating in the WhatsApp and Overseerr webhooks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.app import create_app
from bot.handlers import handle_letterboxd_url_async

ADMIN, USER, STRANGER = "whatsapp:+15551111111", "whatsapp:+15552222222", "whatsapp:+19999999999"
LIST_URL = "https://letterboxd.com/someone/list/girl-bands/"


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state.json"
    tracked = tmp_path / "tracked.json"
    state.write_text("{}", encoding="utf-8")
    tracked.write_text("{}", encoding="utf-8")
    cfg = {"whatsapp": {"users": [{"number": ADMIN, "role": "admin"}, {"number": USER, "role": "user"}]}}
    services = {name: MagicMock() for name in ("notifier", "overseerr", "tracker", "plex")}
    with patch("core.storage.get_whatsapp_state_file", return_value=state), \
         patch("core.storage.get_tracked_collections_file", return_value=tracked):
        app = create_app(config=cfg, testing=True, **services)
        yield app, services


def post(app, sender, body):
    return app.test_client().post("/whatsapp", data={"From": sender, "Body": body})


def test_user_can_request_in_plain_language(env):
    app, _ = env
    with patch("bot.handlers.threading.Thread") as thread:
        resp = post(app, USER, "Heat 1995")
    assert b"Searching..." in resp.data
    assert thread.call_args.kwargs["target"].__name__ == "process_search_async"


def test_user_slash_commands_are_refused_politely(env):
    app, _ = env
    with patch("bot.handlers.threading.Thread") as thread:
        resp = post(app, USER, "/keep Heat")
    assert b"Commands are for admins" in resp.data
    thread.assert_not_called()


def test_admin_slash_commands_run(env):
    app, _ = env
    with patch("bot.handlers.threading.Thread") as thread:
        post(app, ADMIN, "/keep Heat")
    assert thread.call_args.kwargs["target"].__name__ == "run_command_async"


def test_unknown_numbers_are_ignored(env):
    app, _ = env
    with patch("bot.handlers.threading.Thread") as thread:
        resp = post(app, STRANGER, "Heat")
    assert b"Searching" not in resp.data
    thread.assert_not_called()


@pytest.mark.parametrize("sender, is_admin", [(ADMIN, True), (USER, False)])
def test_letterboxd_links_carry_the_senders_role(env, sender, is_admin):
    app, _ = env
    with patch("bot.handlers.threading.Thread") as thread:
        post(app, sender, LIST_URL)
    assert thread.call_args.kwargs["args"][3] is is_admin


def test_users_cannot_import_lists(env):
    app, services = env
    with patch("bot.handlers.scrape_letterboxd_list") as scrape:
        handle_letterboxd_url_async(app, LIST_URL, USER, is_admin=False)
    scrape.assert_not_called()
    assert "admin-only" in services["notifier"].send_message.call_args[0][1]


def test_users_can_still_request_film_links(env):
    app, services = env
    services["overseerr"].request_media.return_value = True
    with patch("bot.handlers.fetch_letterboxd_html", return_value=("<html>", "https://letterboxd.com/film/heat/")), \
         patch("bot.handlers.extract_letterboxd_film_metadata", return_value=(949, "Heat")):
        handle_letterboxd_url_async(app, "https://boxd.it/abc", USER, is_admin=False)
    services["overseerr"].request_media.assert_called_once_with("movie", 949)


def test_collection_progress_goes_to_the_owner_else_to_admins(env):
    app, services = env
    services["tracker"].handle_media_available.return_value = [
        {"collection_title": "Noir", "ready_count": 1, "total_count": 3, "sender": USER},
        {"collection_title": "Gone", "ready_count": 1, "total_count": 3, "sender": STRANGER},
    ]
    resp = app.test_client().post("/overseerr", json={
        "notification_type": "MEDIA_AVAILABLE", "subject": "Heat", "media": {"tmdbId": 949},
    })
    assert resp.status_code == 200
    sent = [c.kwargs["to"] for c in services["notifier"].send_message.call_args_list]
    assert sent == [USER, ADMIN]


def test_bad_users_config_fails_at_startup():
    with pytest.raises(ValueError):
        create_app(config={"whatsapp": {"users": [{"number": ADMIN, "role": "owner"}]}}, testing=True)
