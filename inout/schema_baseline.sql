-- FROZEN: the database structure when versioning began (version 1). Never edit this file.
-- Existing installs are upgraded from here by inout/migrations.py; tests check that the
-- result matches schema.sql.

PRAGMA foreign_keys = ON;

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,
  username      TEXT UNIQUE,              -- admins only: used with the password to sign in to admin pages
  password_hash TEXT,                     -- admins only
  is_admin      INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL CHECK (status IN ('pending', 'active', 'disabled')),
  created_at    TEXT NOT NULL
);

-- Anything that identifies a person at the clock: a PIN now, an NFC card later.
-- Lookup is credential -> user. secret_hash is HMAC-SHA256(credential_key, "kind:secret"),
-- so the raw PIN or card ID is never stored, but an exact match is still one index lookup.
CREATE TABLE credentials (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN ('pin', 'nfc')),
  secret_hash TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (kind, secret_hash),
  UNIQUE (user_id, kind)
);

CREATE TABLE workdays (
  id           INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  work_date    TEXT NOT NULL,             -- YYYY-MM-DD, the server's local date
  submitted_at TEXT,
  note         TEXT NOT NULL DEFAULT '',  -- optional message the employee leaves when submitting
  UNIQUE (user_id, work_date)
);

-- One row per in/out pair. clock_out is NULL while the person is clocked in.
CREATE TABLE segments (
  id         INTEGER PRIMARY KEY,
  workday_id INTEGER NOT NULL REFERENCES workdays(id) ON DELETE CASCADE,
  clock_in   TEXT NOT NULL,
  clock_out  TEXT
);
CREATE INDEX segments_by_workday ON segments (workday_id);
-- A double tap can't open two segments at once.
CREATE UNIQUE INDEX one_open_segment ON segments (workday_id) WHERE clock_out IS NULL;

CREATE TABLE audit_log (
  id         INTEGER PRIMARY KEY,
  actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
  workday_id INTEGER REFERENCES workdays(id) ON DELETE CASCADE,
  action     TEXT NOT NULL,
  detail     TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Server-wide values that must travel with the data, such as the credential key.
-- Keeping it here means backing up this one file keeps every PIN working.
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
