"""Unit tests for core/roles.py and role-aware notification recipients."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.notifier import WhatsAppNotifier
from core.roles import ADMIN, USER, load_users, numbers, role_of

ME, MOM = "whatsapp:+15551111111", "whatsapp:+15552222222"


def cfg(**whatsapp):
    return {"whatsapp": whatsapp}


def test_legacy_allowed_numbers_are_all_admins():
    users = load_users(cfg(allowed_numbers=["+15551111111", MOM]))
    assert users == {ME: ADMIN, MOM: ADMIN}


def test_users_assign_roles_default_to_user_and_replace_allowed_numbers(caplog):
    users = load_users(cfg(
        users=[{"number": "+15551111111", "role": "admin"}, {"number": MOM}],
        allowed_numbers=["+15553333333"],
    ))
    assert users == {ME: ADMIN, MOM: USER}
    assert "allowed_numbers is ignored" in caplog.text


@pytest.mark.parametrize("entry", [{"role": "admin"}, {"number": "+15551111111", "role": "owner"}, "+15551111111"])
def test_invalid_users_entries_raise(entry):
    with pytest.raises(ValueError):
        load_users(cfg(users=[entry]))


def test_role_of_and_numbers():
    users = {ME: ADMIN, MOM: USER}
    assert role_of("+15551111111", users) == ADMIN        # prefix optional
    assert role_of(MOM, users) == USER
    assert role_of("whatsapp:+19999999999", users) is None
    assert role_of("", users) is None and role_of(None, users) is None
    assert numbers(users) == [ME, MOM] and numbers(users, ADMIN) == [ME]


def test_notifier_from_config_separates_admins_from_everyone():
    notifier = WhatsAppNotifier.from_config(cfg(users=[{"number": ME, "role": "admin"}, {"number": MOM, "role": "user"}]))
    assert notifier.allowed_numbers == [ME, MOM]
    assert notifier.admin_numbers == [ME]


def test_alerts_go_to_admins_and_broadcasts_to_everyone():
    client = MagicMock()
    notifier = WhatsAppNotifier(
        account_sid="x", auth_token="y", from_number="+15550000000",
        allowed_numbers=[ME, MOM], admin_numbers=[ME], client=client,
    )

    def recipients():
        return [c.kwargs["to"] for c in client.messages.create.call_args_list]

    notifier.send_alert("boom")
    assert recipients() == [ME]
    client.reset_mock()
    notifier.send_broadcast("hello")
    assert recipients() == [ME, MOM]


def test_without_roles_everyone_is_an_admin():
    notifier = WhatsAppNotifier(allowed_numbers=[ME, MOM])
    assert notifier.admin_numbers == [ME, MOM]
