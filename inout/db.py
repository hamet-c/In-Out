"""SQLite access: one connection per request, explicit transactions."""
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from flask import current_app, g

from . import migrations


def get_db():
    if "db" not in g:
        # isolation_level=None: no implicit transactions; writes go through transaction().
        g.db = sqlite3.connect(current_app.config["DATABASE"], isolation_level=None, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db=None):
    """BEGIN IMMEDIATE takes the write lock up front, so read-then-write steps
    (like deciding whether a punch is "in" or "out") can't interleave."""
    db = db or get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def init_db():
    """Create the tables in an empty database. Refuses to touch an existing one."""
    path = Path(current_app.config["DATABASE"])
    if path.exists() and path.stat().st_size > 0:
        raise RuntimeError(f"{path} already exists; not overwriting it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript(Path(current_app.root_path, "schema.sql").read_text(encoding="utf-8"))
    db.execute("INSERT INTO settings (key, value) VALUES ('credential_key', ?)",
               (secrets.token_hex(32),))
    db.execute(f"PRAGMA user_version = {migrations.LATEST}")


def backup(keep=30):
    """Copy the database to instance/backups/ with a timestamp, keeping the newest `keep`.

    Uses SQLite's backup API, so the copy is consistent even if the server is running.
    """
    src = Path(current_app.config["DATABASE"])
    folder = src.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"inout-{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dest)) as d:
        s.backup(d)
    for old in sorted(folder.glob("inout-*.db"))[:-keep]:
        old.unlink()
    return dest


def setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
