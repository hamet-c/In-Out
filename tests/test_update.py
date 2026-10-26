"""The update command, run against real git repositories in a temp folder:
a "GitHub" (bare repo), the developer's laptop, and the server."""
import shutil
import subprocess

import pytest

import manage
import run

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    for var, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(var, value)
    github, laptop, server = tmp_path / "github.git", tmp_path / "laptop", tmp_path / "server"
    git(tmp_path, "init", "--bare", "-b", "main", str(github))
    git(tmp_path, "clone", str(github), str(laptop))
    (laptop / "app.txt").write_text("version 1\n")
    git(laptop, "add", ".")
    git(laptop, "commit", "-m", "first version")
    git(laptop, "push", "-u", "origin", "main")
    git(tmp_path, "clone", str(github), str(server))
    monkeypatch.setattr(manage, "ROOT", server)
    return laptop, server


@pytest.fixture
def steps(monkeypatch):
    """Record the later steps (pip, migrate, start) instead of running them."""
    calls = []
    monkeypatch.setattr(manage, "_step", lambda cmd: calls.append(cmd[1:]) or 0)
    monkeypatch.setattr(run, "is_running", lambda: False)
    return calls


def test_nothing_new_changes_nothing(app, repos, steps, capsys):
    with app.app_context():
        manage.cmd_update(None)
    assert "Already up to date" in capsys.readouterr().out
    assert steps == []


def test_new_version_is_pulled_backed_up_installed_migrated_and_started(app, repos, steps, capsys):
    laptop, server = repos
    (laptop / "app.txt").write_text("version 2\n")
    git(laptop, "commit", "-am", "second version")
    git(laptop, "push")

    with app.app_context():
        manage.cmd_update(None)
        backups = list((manage.Path(app.config["DATABASE"]).parent / "backups").glob("inout-*.db"))

    assert (server / "app.txt").read_text() == "version 2\n"
    assert len(backups) == 1
    assert [c[:2] for c in steps] == [["-m", "pip"], ["manage.py", "migrate"], ["run.py", "--background"]]
    out = capsys.readouterr().out
    assert "second version" in out and "Updated from" in out


def test_hand_edited_server_files_block_the_update(app, repos, steps):
    laptop, server = repos
    (server / "app.txt").write_text("someone edited this on the server\n")
    with app.app_context(), pytest.raises(SystemExit, match="changed by hand"):
        manage.cmd_update(None)
    assert steps == []


def test_failed_database_upgrade_explains_how_to_go_back(app, repos, monkeypatch):
    laptop, server = repos
    (laptop / "app.txt").write_text("version 2\n")
    git(laptop, "commit", "-am", "second version")
    git(laptop, "push")
    monkeypatch.setattr(run, "is_running", lambda: False)
    monkeypatch.setattr(manage, "_step", lambda cmd: 1 if "migrate" in cmd else 0)
    with app.app_context(), pytest.raises(SystemExit) as e:
        manage.cmd_update(None)
    assert "Upgrading the database failed" in str(e.value) and "git reset --hard" in str(e.value)
