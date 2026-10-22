import builtins
import sys

import pytest

import manage
import run
import wintools
from inout.db import get_db


@pytest.fixture
def fresh_app(tmp_path, clock):
    """An app with no database yet, the way a new server starts out."""
    from inout import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "new.db")})
    app.instance_path = str(tmp_path / "instance")
    return app


@pytest.fixture
def windows_calls(monkeypatch, tmp_path):
    """Record what the wizard would change in Windows instead of changing it."""
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(wintools, "ca_is_trusted", lambda ca: False)
    monkeypatch.setattr(wintools, "trust_ca", lambda ca: calls.append("trust_ca") or True)
    monkeypatch.setattr(wintools, "firewall_rule_exists", lambda: False)
    monkeypatch.setattr(wintools, "add_firewall_rule", lambda h, s: calls.append(("firewall", h, s)) or True)
    monkeypatch.setattr(wintools, "set_autostart", lambda on, startup_dir=None: calls.append(("autostart", on)))
    monkeypatch.setattr(wintools, "create_desktop_shortcut", lambda: calls.append("shortcut") or True)
    monkeypatch.setattr(run, "is_running", lambda: False)
    monkeypatch.setattr(run, "start_background", lambda: calls.append("start") or 0)
    return calls


def answers(monkeypatch, typed, secrets):
    typed, secrets = iter(typed), iter(secrets)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(typed))
    monkeypatch.setattr(manage, "_hidden", lambda prompt: next(secrets))


def test_first_run_sets_everything_up(fresh_app, windows_calls, monkeypatch, capsys):
    answers(monkeypatch,
            typed=["Dana Whitfield", "Boss", "", "", "", ""],  # name, username, then Enter = yes x4
            secrets=["correct-horse", "correct-horse", "9157"])
    with fresh_app.app_context():
        manage.cmd_setup(None)
        admin = get_db().execute("SELECT display_name, username, is_admin FROM users").fetchone()
    assert tuple(admin) == ("Dana Whitfield", "boss", 1)
    assert windows_calls == ["trust_ca", ("firewall", run.HTTP_PORT, run.HTTPS_PORT),
                             ("autostart", True), "shortcut", "start"]
    out = capsys.readouterr().out
    assert "Created." in out and "Setup finished" in out


def test_running_again_skips_finished_steps_and_respects_no(fresh_app, windows_calls, monkeypatch, capsys):
    answers(monkeypatch, typed=["Dana", "boss", "", "", "", ""], secrets=["correct-horse", "correct-horse", ""])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    windows_calls.clear()
    capsys.readouterr()

    answers(monkeypatch, typed=["n", "n", "n", "n"], secrets=[])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    out = capsys.readouterr().out
    assert "Already set up." in out and "An admin already exists" in out
    assert "Reused the existing certificate authority" in out
    assert windows_calls == [("autostart", False), "start"]


def test_autostart_file_points_at_this_install(tmp_path):
    wintools.set_autostart(True, startup_dir=tmp_path)
    text = wintools.startup_file(tmp_path).read_text(encoding="utf-8")
    assert "pythonw.exe" in text and "run.py" in text and "--background" in text
    assert wintools.autostart_enabled(tmp_path)
    wintools.set_autostart(False, startup_dir=tmp_path)
    assert not wintools.autostart_enabled(tmp_path)


def test_firewall_rule_only_admits_local_network_addresses():
    cmd = wintools.firewall_command(8000, 8443)
    assert "-LocalPort 8000,8443" in cmd
    assert "-RemoteAddress 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" in cmd


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")
def test_desktop_shortcut_is_a_real_working_shortcut(tmp_path):
    assert wintools.create_desktop_shortcut(folder=tmp_path)
    link = tmp_path / "In-Out.lnk"
    target = wintools._powershell(
        f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath").stdout.strip()
    assert target == str(wintools.HERE / "Start In-Out.bat")
