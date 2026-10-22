"""Credential -> user lookup. A PIN today; an NFC card ID later uses the same functions
with kind="nfc", so the clock and sign-in code never need to know which one was used.
"""
import hashlib
import hmac
import re
import sqlite3

from .db import get_db, setting

KINDS = ("pin", "nfc")
PIN_LENGTH = 4
_PIN_RE = re.compile(rf"\d{{{PIN_LENGTH}}}")
# Easy to guess, and so likely to be picked by several people.
_WEAK_PINS = {d * PIN_LENGTH for d in "0123456789"} | {"1234", "4321", "0123", "3210", "1212", "2580"}


class CredentialTaken(Exception):
    """Another user already has this PIN or card."""


def _digest(kind, secret):
    if kind not in KINDS:
        raise ValueError(kind)
    key = setting("credential_key").encode()
    return hmac.new(key, f"{kind}:{secret}".encode(), hashlib.sha256).hexdigest()


def pin_problem(pin, confirm=None):
    """Why a new PIN can't be used, or None if it's fine."""
    if not _PIN_RE.fullmatch(pin or ""):
        return f"Your PIN has to be exactly {PIN_LENGTH} digits."
    if pin in _WEAK_PINS:
        return "That PIN is too easy to guess. Pick another."
    if confirm is not None and pin != confirm:
        return "The two PINs don't match."
    return None


def find_user_id(kind, secret):
    row = get_db().execute(
        "SELECT user_id FROM credentials WHERE kind = ? AND secret_hash = ?",
        (kind, _digest(kind, secret)),
    ).fetchone()
    return row["user_id"] if row else None


def set_secret(user_id, kind, secret, at):
    """Give user_id this PIN or card, replacing any previous one of the same kind.
    Call inside a transaction. Raises CredentialTaken if someone else has it."""
    digest = _digest(kind, secret)
    db = get_db()
    owner = db.execute("SELECT user_id FROM credentials WHERE kind = ? AND secret_hash = ?",
                       (kind, digest)).fetchone()
    if owner and owner["user_id"] != user_id:
        raise CredentialTaken()
    try:
        db.execute(
            "INSERT INTO credentials (user_id, kind, secret_hash, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, kind) DO UPDATE SET secret_hash = excluded.secret_hash, "
            "created_at = excluded.created_at",
            (user_id, kind, digest, at.isoformat()),
        )
    except sqlite3.IntegrityError as e:
        raise CredentialTaken() from e


def has_secret(user_id, kind):
    return get_db().execute("SELECT 1 FROM credentials WHERE user_id = ? AND kind = ?",
                            (user_id, kind)).fetchone() is not None
