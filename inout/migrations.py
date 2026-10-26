"""Database upgrades between versions of In-Out.

The database file records its structure version (PRAGMA user_version). schema.sql is
always the latest structure and is used for new installs. Existing installs are brought
up to date by applying, in order, every step in MIGRATIONS newer than their version.

To change the database structure:
  1. Add a step at the end of MIGRATIONS with the next number. Write it to upgrade an
     existing database, for example "ALTER TABLE users ADD COLUMN ...".
  2. Make the same change in schema.sql, so new installs get it too.
  tests/test_migrations.py checks that both paths end in the same structure.

Each step runs in its own transaction together with its version bump, so a failed step
leaves the database exactly as it was.
"""

BASELINE = 1  # the structure in schema_baseline.sql

# version: (what it does, [SQL statements] or a function taking the connection)
MIGRATIONS = {
    # 2: ("Add a phone number to users", ["ALTER TABLE users ADD COLUMN phone TEXT"]),
}

LATEST = max(MIGRATIONS, default=BASELINE)


class VersionMismatch(RuntimeError):
    pass


def version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


def _has_tables(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone() is not None


def effective_version(db):
    # Databases created before versioning existed have version 0 but the baseline tables.
    v = version(db)
    return BASELINE if v == 0 and _has_tables(db) else v


def check(db):
    """Raise VersionMismatch unless the database matches this code."""
    v = effective_version(db)
    if v < LATEST:
        raise VersionMismatch(
            f"The database is at version {v} and this version of In-Out needs {LATEST}. "
            "Run \"Update In-Out.bat\" (or: python manage.py migrate).")
    if v > LATEST:
        raise VersionMismatch(
            f"The database (version {v}) is newer than this copy of In-Out (version {LATEST}). "
            "Update the code instead of running an older version.")


def upgrade(db):
    """Apply every pending step. Returns the descriptions of the steps applied."""
    v = effective_version(db)
    if v == 0:
        raise VersionMismatch("This database is empty. Run: python manage.py init-db")
    if v > LATEST:
        check(db)
    if version(db) == 0:
        db.execute(f"PRAGMA user_version = {BASELINE}")
    applied = []
    for step in sorted(s for s in MIGRATIONS if s > v):
        description, change = MIGRATIONS[step]
        db.execute("BEGIN IMMEDIATE")
        try:
            if callable(change):
                change(db)
            else:
                for statement in change:
                    db.execute(statement)
            db.execute(f"PRAGMA user_version = {int(step)}")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
        applied.append(f"{step}: {description}")
    return applied
