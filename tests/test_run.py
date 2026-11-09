import os
import sys
from types import SimpleNamespace as NS

import pytest

import run
import wintools


def test_stop_only_ends_python_processes():
    """--stop checks that the pid file's number still belongs to Python (tasklist on Windows, /proc on Linux)."""
    assert run._is_python_process(os.getpid())
    assert not run._is_python_process(99999999)  # no such process


@pytest.fixture
def starting(monkeypatch, tmp_path):
    """start_background() on a Windows computer with a database, recording how it starts the server."""
    db = tmp_path / "inout.db"
    db.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(run, "create_app", lambda: NS(config={"DATABASE": str(db)}, instance_path=str(tmp_path)))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    steps, up = [], iter([False, True])  # not running yet; answers on the first check after starting
    monkeypatch.setattr(run, "is_running", lambda: next(up))
    monkeypatch.setattr(run.subprocess, "Popen", lambda cmd, **kw: steps.append(("process", cmd[1:])))
    monkeypatch.setattr(wintools, "boot_task_exists", lambda: False)
    monkeypatch.setattr(wintools, "run_boot_task", lambda: steps.append("task") or True)
    return steps


def test_with_the_startup_task_in_out_starts_through_it(starting, monkeypatch):
    """So it keeps running when the admin signs out and the kiosk takes the screen back."""
    monkeypatch.setattr(wintools, "boot_task_exists", lambda: True)
    assert run.start_background() == 0
    assert starting == ["task"]


def test_without_the_task_it_starts_a_windowless_process(starting):
    assert run.start_background() == 0
    assert starting == [("process", [str(run.HERE / "run.py"), "--service"])]


def test_if_the_task_wont_start_it_still_starts_and_says_so(starting, monkeypatch, capsys):
    monkeypatch.setattr(wintools, "boot_task_exists", lambda: True)
    monkeypatch.setattr(wintools, "run_boot_task", lambda: starting.append("task") or False)
    assert run.start_background() == 0
    assert starting == ["task", ("process", [str(run.HERE / "run.py"), "--service"])]
    assert "runs only until you sign out" in capsys.readouterr().out


def test_service_logs_to_the_instance_folder(monkeypatch, tmp_path):
    served = []
    monkeypatch.setattr(run, "create_app", lambda: NS(instance_path=str(tmp_path)))
    monkeypatch.setattr(run, "serve", lambda log=None, pidfile=None: served.append((log, pidfile)))
    monkeypatch.setattr(sys, "argv", ["run.py", "--service"])
    run.main()
    assert served == [(str(tmp_path / "server.log"), str(tmp_path / "server.pid"))]


def test_stop_ends_the_startup_task_rather_than_the_process(monkeypatch, tmp_path):
    """Windows refuses taskkill on the task's In-Out, even for its owner; Task Scheduler may end it."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(run, "create_app", lambda: NS(instance_path=str(tmp_path)))
    (tmp_path / "server.pid").write_text("4242")
    running = iter([True, True, False])  # up; still up right after /End; then gone
    monkeypatch.setattr(run, "is_running", lambda: next(running))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(wintools, "boot_task_exists", lambda: True)
    ended = []
    monkeypatch.setattr(wintools, "end_boot_task", lambda: ended.append(True) or True)
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **kw: pytest.fail("used taskkill"))
    assert run.stop_background() == 0
    assert ended == [True]
    assert not (tmp_path / "server.pid").exists()
