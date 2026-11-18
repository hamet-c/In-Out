import builtins

import pytest

import manage
from .conftest import sign_in_admin


def type_keys(capsys, keys):
    """Feed keystrokes to the masked prompt; return (value, what was shown)."""
    it = iter(keys)
    value = manage._hidden("PW: ", getch=lambda: next(it))
    return value, capsys.readouterr().out


def test_each_character_shows_as_a_star(capsys):
    assert type_keys(capsys, "p@ss w0rd!\r") == ("p@ss w0rd!", "PW: **********\n")


def test_backspace_erases_and_is_harmless_when_empty(capsys):
    value, shown = type_keys(capsys, "\b\babcd\b\bX\r")
    assert value == "abX"
    assert shown == "PW: ****\b \b\b \b*\n"


def test_arrow_and_function_keys_are_ignored(capsys):
    # Windows sends these as two codes: a prefix (\xe0 or \x00) and then the key.
    assert type_keys(capsys, "a\xe0Kb\x00;c\r")[0] == "abc"


def test_ctrl_c_cancels(capsys):
    with pytest.raises(KeyboardInterrupt):
        type_keys(capsys, "ab\x03")


def test_reset_password_gives_an_admin_who_forgot_theirs_a_new_one(app, client, make_user, monkeypatch, capsys):
    make_user("Dana", admin="boss")
    typed = iter(["nobody", "Boss"])
    hidden = iter(["short", "new-password", "new-passw0rd", "new-password", "new-password"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(typed))
    monkeypatch.setattr(manage, "_hidden", lambda prompt: next(hidden))
    with app.app_context():
        manage.cmd_reset_password(None)
    out = capsys.readouterr().out
    assert "Admins: boss" in out and "No admin has that username." in out
    assert "Too short." in out and "Didn't match." in out and "Changed the password for boss." in out
    assert sign_in_admin(client).status_code == 200  # the old password: the sign-in page again, not in
    assert sign_in_admin(client, password="new-password").status_code == 302


def test_reset_password_needs_an_admin_to_exist(app):
    with app.app_context(), pytest.raises(SystemExit, match="create-admin"):
        manage.cmd_reset_password(None)
