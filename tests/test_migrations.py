import sqlite3
from pathlib import Path

import pytest

import run
from inout import migrations
from inout.db import backup, get_db

BASELINE_SQL = Path(migrations.__file__).with_name("schema_baseline.sql").read_text(encoding="utf-8")


def structure(db):
    """Tables, columns and indexes, in a form that ignores how the SQL was written."""
    out = {}
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        cols = [tuple(r)[1:] for r in db.execute(f"PRAGMA table_info({t})")]
        idx = sorted((r[1], r[2], r[4], tuple(c[2] for c in db.execute(f"PRAGMA index_info({r[1]})")))
                     for r in db.execute(f"PRAGMA index_list({t})"))
        out[t] = (cols, idx)
    return out


def baseline_db(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(BASELINE_SQL)
    return db


def test_new_database_is_stamped_with_the_latest_version(app):
    with app.app_context():
        assert migrations.version(get_db()) == migrations.LATEST
        migrations.check(get_db())  # no error


def test_upgrading_the_baseline_matches_a_fresh_install(app, tmp_path):
    old = baseline_db(tmp_path / "old.db")
    migrations.upgrade(old)
    with app.app_context():
        assert structure(old) == structure(get_db())
    assert migrations.version(old) == migrations.LATEST


def test_database_from_before_versioning_counts_as_the_baseline(tmp_path):
    db = baseline_db(tmp_path / "legacy.db")  # user_version was never set
    assert migrations.version(db) == 0 and migrations.effective_version(db) == migrations.BASELINE
    migrations.upgrade(db)
    assert migrations.version(db) == migrations.LATEST


def test_a_new_step_is_applied_once_and_keeps_the_data(tmp_path, monkeypatch):
    db = baseline_db(tmp_path / "v1.db")
    db.execute("INSERT INTO users (display_name, status, created_at) VALUES ('Alice', 'active', 'x')")
    monkeypatch.setattr(migrations, "MIGRATIONS",
                        {2: ("Add phone", ["ALTER TABLE users ADD COLUMN phone TEXT"])})
    monkeypatch.setattr(migrations, "LATEST", 2)

    with pytest.raises(migrations.VersionMismatch, match="needs 2"):
        migrations.check(db)
    assert migrations.upgrade(db) == ["2: Add phone"]
    assert migrations.upgrade(db) == []
    assert db.execute("SELECT display_name, phone FROM users").fetchall() == [("Alice", None)]
    migrations.check(db)


def test_a_failing_step_leaves_the_database_untouched(tmp_path, monkeypatch):
    db = baseline_db(tmp_path / "v1.db")
    db.execute("PRAGMA user_version = 1")
    monkeypatch.setattr(migrations, "MIGRATIONS", {2: ("Broken", [
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "THIS IS NOT SQL",
    ])})
    monkeypatch.setattr(migrations, "LATEST", 2)
    with pytest.raises(sqlite3.OperationalError):
        migrations.upgrade(db)
    assert migrations.version(db) == 1
    assert "phone" not in [r[1] for r in db.execute("PRAGMA table_info(users)")]


def test_older_code_refuses_a_newer_database(app):
    with app.app_context():
        get_db().execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
        with pytest.raises(migrations.VersionMismatch, match="newer than this copy"):
            migrations.check(get_db())
        with pytest.raises(migrations.VersionMismatch):
            migrations.upgrade(get_db())


def test_server_will_not_start_on_a_mismatched_database(app):
    with app.app_context():
        get_db().execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    with pytest.raises(SystemExit, match="newer than this copy"):
        run.check_database(app)


def test_backup_is_a_full_copy_and_old_ones_are_pruned(app, make_user, tmp_path):
    make_user("Alice", pin="2468")
    with app.app_context():
        first = backup()
        copy = sqlite3.connect(first)
        assert copy.execute("SELECT display_name FROM users").fetchall() == [("Alice",)]
        copy.close()
        folder = first.parent
        for i in range(5):
            (folder / f"inout-20000101-00000{i}.db").write_bytes(b"old")
        backup(keep=3)
        assert len(list(folder.glob("inout-*.db"))) == 3
