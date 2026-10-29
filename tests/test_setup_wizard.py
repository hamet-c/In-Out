import builtins
import sys
from pathlib import Path

import pytest

import linuxtools
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
    monkeypatch.setattr(wintools, "firewall_rule_exists", lambda: False)
    monkeypatch.setattr(wintools, "add_firewall_rule", lambda port: calls.append(("firewall", port)) or True)
    monkeypatch.setattr(wintools, "set_autostart", lambda on, startup_dir=None: calls.append(("autostart", on)))
    monkeypatch.setattr(wintools, "create_desktop_shortcut", lambda: calls.append("shortcut") or True)
    monkeypatch.setattr(run, "start_background", lambda: calls.append("start") or 0)
    return calls


@pytest.fixture
def linux_calls(monkeypatch):
    """The same for Linux, on a computer with no firewall turned on (Ubuntu's default)."""
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(linuxtools, "active_firewall", lambda: None)
    monkeypatch.setattr(linuxtools, "add_firewall_rule", lambda port: calls.append(("firewall", port)) or True)
    monkeypatch.setattr(linuxtools, "set_autostart", lambda on, startup_dir=None: calls.append(("autostart", on)))
    monkeypatch.setattr(linuxtools, "create_desktop_shortcut", lambda: calls.append("shortcut") or True)
    monkeypatch.setattr(run, "start_background", lambda: calls.append("start") or 0)
    return calls


def answers(monkeypatch, typed, secrets):
    typed, secrets = iter(typed), iter(secrets)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(typed))
    monkeypatch.setattr(manage, "_hidden", lambda prompt: next(secrets))


def test_first_run_sets_everything_up(fresh_app, windows_calls, monkeypatch, capsys):
    answers(monkeypatch,
            typed=["Dana Whitfield", "Boss", "", "", ""],  # name, username, then Enter = yes x3
            secrets=["correct-horse", "correct-horse", "9157"])
    with fresh_app.app_context():
        manage.cmd_setup(None)
        admin = get_db().execute("SELECT display_name, username, is_admin FROM users").fetchone()
    assert tuple(admin) == ("Dana Whitfield", "boss", 1)
    assert windows_calls == [("firewall", run.HTTP_PORT), ("autostart", True), "shortcut", "start"]
    out = capsys.readouterr().out
    assert "Created." in out and "Setup finished" in out


def test_running_again_skips_finished_steps_and_respects_no(fresh_app, windows_calls, monkeypatch, capsys):
    answers(monkeypatch, typed=["Dana", "boss", "", "", ""], secrets=["correct-horse", "correct-horse", ""])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    windows_calls.clear()
    capsys.readouterr()

    answers(monkeypatch, typed=["n", "n", "n"], secrets=[])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    out = capsys.readouterr().out
    assert "Already set up." in out and "An admin already exists" in out
    assert windows_calls == [("autostart", False), "start"]


def test_autostart_file_points_at_this_install(tmp_path):
    wintools.set_autostart(True, startup_dir=tmp_path)
    text = wintools.startup_file(tmp_path).read_text(encoding="utf-8")
    assert "pythonw.exe" in text and "run.py" in text and "--background" in text
    assert wintools.autostart_enabled(tmp_path)
    wintools.set_autostart(False, startup_dir=tmp_path)
    assert not wintools.autostart_enabled(tmp_path)


def test_firewall_rule_only_admits_local_network_addresses():
    cmd = wintools.firewall_command(8000)
    assert "-LocalPort 8000 " in cmd
    assert "-RemoteAddress 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" in cmd


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")
def test_desktop_shortcut_is_a_real_working_shortcut(tmp_path):
    assert wintools.create_desktop_shortcut(folder=tmp_path)
    link = tmp_path / "In-Out.lnk"
    target = wintools._powershell(
        f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath").stdout.strip()
    assert target == str(wintools.HERE / "Start In-Out.bat")


# ---------------------------------------------------------------- Linux

def test_linux_first_run_skips_the_firewall_when_none_is_on(fresh_app, linux_calls, monkeypatch, capsys):
    answers(monkeypatch,
            typed=["Dana", "boss", "", ""],  # name, username, then Enter = yes x2
            secrets=["correct-horse", "correct-horse", ""])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    assert linux_calls == [("autostart", True), "shortcut", "start"]
    out = capsys.readouterr().out
    assert "No firewall is turned on" in out and "Setup finished" in out


def test_linux_offers_to_open_a_firewall_that_is_on(fresh_app, linux_calls, monkeypatch):
    monkeypatch.setattr(linuxtools, "active_firewall", lambda: "ufw")
    answers(monkeypatch, typed=["Dana", "boss", "", "", ""], secrets=["correct-horse", "correct-horse", ""])
    with fresh_app.app_context():
        manage.cmd_setup(None)
    assert linux_calls == [("firewall", run.HTTP_PORT), ("autostart", True), "shortcut", "start"]


def test_linux_firewall_rules_only_admit_local_network_addresses():
    ufw = linuxtools.firewall_command(8000, "ufw")
    assert ufw.count("sudo ufw allow from ") == 3 and ufw.count(" to any port 8000 proto tcp") == 3
    firewalld = linuxtools.firewall_command(8000, "firewalld")
    assert firewalld.count('port port="8000" protocol="tcp" accept') == 3
    assert firewalld.endswith("sudo firewall-cmd --reload")
    for cmd in (ufw, firewalld):
        assert all(net in cmd for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def test_linux_sees_ufw_only_when_it_is_turned_on(tmp_path, monkeypatch):
    conf = tmp_path / "ufw.conf"
    monkeypatch.setattr(linuxtools, "UFW_CONF", conf)
    monkeypatch.setattr(linuxtools.shutil, "which", lambda name: None)  # no firewalld either
    assert linuxtools.active_firewall() is None  # ufw not installed
    conf.write_text("# comment\nENABLED=no\n")
    assert linuxtools.active_firewall() is None
    conf.write_text("ENABLED=yes\n")
    assert linuxtools.active_firewall() == "ufw"


def test_linux_autostart_entry_points_at_this_install(tmp_path):
    linuxtools.set_autostart(True, startup_dir=tmp_path)
    text = linuxtools.startup_file(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]\n")
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert "python" in exec_line and "run.py" in exec_line and exec_line.endswith('"--background"')
    assert linuxtools.autostart_enabled(tmp_path)
    linuxtools.set_autostart(False, startup_dir=tmp_path)
    assert not linuxtools.autostart_enabled(tmp_path)


def test_linux_icon_goes_in_the_app_menu_and_on_the_desktop(tmp_path):
    desktop, menu = tmp_path / "Desktop", tmp_path / "applications"
    desktop.mkdir()
    assert linuxtools.create_desktop_shortcut(folder=desktop, menu_dir=menu)
    for path in (desktop / "In-Out.desktop", menu / "In-Out.desktop"):
        text = path.read_text(encoding="utf-8")
        assert "start-inout.sh" in text and "Terminal=true" in text
        assert "[Desktop Action stop]" in text and "stop-inout.sh" in text
    icon = next(line for line in text.splitlines() if line.startswith("Icon="))
    assert Path(icon.removeprefix("Icon=")).is_file()


def test_linux_exec_lines_work_for_any_folder_name():
    assert linuxtools._exec("/home/a b/run.py", "--background") == '"/home/a b/run.py" "--background"'
    # Quotes, $, ` and \ are escaped for the quoted argument, then \ again for the file; % doubles.
    assert linuxtools._exec(r'/x/"$`\y', "100%") == r'"/x/\\"\\$\\`\\\\y" "100%%"'
