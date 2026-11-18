"""Who is at the screen.

Employees sign in with their PIN or by tapping their card, and are signed out after a short
idle time, since the same screen may be shared. Admins sign in separately with a username
and password.

Only one person is signed in per browser: a PIN sign-in ends any admin session there, and
an admin sign-in ends any employee session. Otherwise an admin who walked away from a
shared screen would leave the admin pages open to the next person who enters a PIN.
"""
import math
import secrets
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from types import SimpleNamespace as NS

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

from . import credentials, timesheet
from .db import get_db, transaction

bp = Blueprint("auth", __name__)

# Pages sign themselves out when idle (see app.js): the clock page after 30 seconds,
# admin pages after ADMIN_PAGE_IDLE_SECONDS. These are the server-side backstops in case
# that script never runs.
EMPLOYEE_IDLE = timedelta(minutes=10)
ADMIN_IDLE = timedelta(minutes=10)
ADMIN_PAGE_IDLE_SECONDS = 5 * 60


# ---------------------------------------------------------------- wrong-guess lockout

class AttemptLimiter:
    """Makes a device (IP address) wait after wrong PINs or passwords.

    The first `free` wrong tries cost nothing. Each one after that means a wait before the
    next try: first_pause, then double that, and so on up to max_pause. Guessing gets slow
    quickly, but a few people's typos never block a shared clock station for long. A wrong
    try stops counting once it's older than `window`.

    A successful sign-in does NOT clear the count. Otherwise someone who knows one valid
    PIN could interleave it with guesses and never have to wait.
    """

    def __init__(self, free=2, first_pause=timedelta(seconds=5), max_pause=timedelta(minutes=5),
                 window=timedelta(minutes=15)):
        self.free, self.first_pause, self.max_pause, self.window = free, first_pause, max_pause, window
        self._fails = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key, at):
        q = self._fails[key]
        while q and at - q[0] > self.window:
            q.popleft()
        return q

    def seconds_left(self, key, at):
        with self._lock:
            q = self._prune(key, at)
            extra = len(q) - self.free
            if extra <= 0:
                return 0
            pause = min(self.first_pause * 2 ** min(extra - 1, 20), self.max_pause)
            return max(0, math.ceil((q[-1] + pause - at).total_seconds()))

    def fail(self, key, at):
        with self._lock:
            self._prune(key, at).append(at)


def _limiter():
    return current_app.extensions["inout_limiter"]


def _wait(seconds):
    """45 -> '45 seconds', 61 -> '2 minutes'."""
    n, unit = (seconds, "second") if seconds < 60 else ((seconds + 59) // 60, "minute")
    return f"{n} {unit}{'s' if n != 1 else ''}"


def locked_out():
    """Flash and return True if this device has to wait before its next try."""
    left = _limiter().seconds_left(request.remote_addr, timesheet.now())
    if left:
        flash(f"Too many wrong tries. Wait {_wait(left)} and try again.", "error")
    return bool(left)


def wrong_try():
    """Count a wrong PIN or password against this device. Returns the seconds it now has to wait."""
    at = timesheet.now()
    _limiter().fail(request.remote_addr, at)
    return _limiter().seconds_left(request.remote_addr, at)


# ---------------------------------------------------------------- CSRF

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if request.method != "POST" or not current_app.config.get("CSRF_CHECK", True):
        return
    sent = request.form.get("csrf_token", "")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400)


# ---------------------------------------------------------------- loading who is signed in

def _load_user(user_id):
    return get_db().execute(
        "SELECT id, display_name, username, is_admin, status FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def _signed_in(id_key, seen_key, idle, require_admin=False):
    user_id = session.get(id_key)
    if user_id is None:
        return None
    at = timesheet.now()
    seen = session.get(seen_key)
    row = _load_user(user_id)
    expired = seen is None or at - datetime.fromisoformat(seen) > idle
    if expired or row is None or row["status"] != "active" or (require_admin and not row["is_admin"]):
        session.pop(id_key, None)
        session.pop(seen_key, None)
        return None
    session[seen_key] = at.isoformat()
    return NS(id=row["id"], display_name=row["display_name"], username=row["username"],
              is_admin=bool(row["is_admin"]))


def load_people():
    if request.endpoint == "static":
        return
    g.employee = _signed_in("employee_id", "employee_seen", EMPLOYEE_IDLE)
    g.admin = _signed_in("admin_id", "admin_seen", ADMIN_IDLE, require_admin=True)


def employee_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.employee is None:
            flash("You were signed out. Enter your PIN again.", "warning")
            return redirect(url_for("clock.dashboard"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.admin is None:
            return redirect(url_for("auth.admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- employee: PIN or card

def sign_in_with(kind, secret):
    """Shared by the PIN pad and the card reader. Returns True on success.

    Only PINs count as wrong tries. A card ID comes from the reader on the server, never
    from the browser, so it can't be guessed by typing, and someone tapping a bank card by
    mistake shouldn't make the station wait.
    """
    guessable = kind == "pin"
    if guessable and locked_out():
        return False
    user_id = credentials.find_user_id(kind, secret)
    row = _load_user(user_id) if user_id else None
    if row is None:
        if guessable:
            left = wrong_try()
            flash("That PIN didn't match anyone. "
                  + (f"Wait {_wait(left)}, then try again." if left else "Try again."), "error")
        else:
            flash("That card isn't linked to anyone. Ask an admin to link it.", "error")
        return False
    if row["status"] == "pending":
        flash("Your account is waiting for an admin to approve it.", "warning")
        return False
    if row["status"] == "disabled":
        flash("Your account is disabled. Talk to your manager.", "error")
        return False
    if session.get("admin_id") is not None:
        _sign_out_admin()
        flash("Signed out of the admin account.", "warning")
    session["employee_id"] = row["id"]
    session["employee_seen"] = timesheet.now().isoformat()
    return True


def _sign_out_employee():
    session.pop("employee_id", None)
    session.pop("employee_seen", None)


def _sign_out_admin():
    session.pop("admin_id", None)
    session.pop("admin_seen", None)


@bp.route("/pin", methods=["POST"])
def pin():
    sign_in_with("pin", request.form.get("pin", "").strip())
    return redirect(url_for("clock.dashboard"))


def card_reader():
    return current_app.extensions["inout_cards"]


def station_reader():
    """The card reader, if this browser runs on the server computer itself, otherwise None.

    The reader is plugged into the server, so only the screen next to it may use its taps.
    Otherwise any laptop on the Wi-Fi showing the PIN pad would be signed in as whoever
    tapped at the station.
    """
    return card_reader() if request.remote_addr in ("127.0.0.1", "::1") else None


@bp.route("/card", methods=["POST"])
def card():
    """Asked twice a second by the PIN pad and the clock page (see app.js): 204 until a new
    card is tapped, then sign in with it and tell the page where to go.

    A tap means someone is at the station, so whoever forgot to press Done is signed out
    first, even if the new card turns out not to be linked.
    """
    reader = station_reader()
    if reader is None:
        abort(403)
    tap = reader.tap_after(request.form.get("after", 0, type=int))
    if tap is None:
        return "", 204
    if g.employee and credentials.find_user_id("nfc", tap.uid) == g.employee.id:
        # Their own card again, or the reader saw one tap twice. Leave their page alone.
        return "", 204
    _sign_out_employee()
    sign_in_with("nfc", tap.uid)
    return {"next": url_for("clock.dashboard")}


@bp.route("/done", methods=["POST"])
def done():
    _sign_out_employee()
    return redirect(url_for("clock.dashboard"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    form = request.form
    name = " ".join(form.get("display_name", "").split())[:80]
    pin_value = form.get("pin", "").strip()
    if not name:
        problem = "Please enter your name."
    else:
        problem = credentials.pin_problem(pin_value, form.get("confirm", "").strip())
    if problem is None and locked_out():
        return render_template("register.html", form=form), 429
    if problem:
        flash(problem, "error")
        return render_template("register.html", form=form), 400

    at = timesheet.now()
    try:
        with transaction():
            cur = get_db().execute(
                "INSERT INTO users (display_name, status, created_at) VALUES (?, 'pending', ?)",
                (name, at.isoformat()))
            credentials.set_secret(cur.lastrowid, "pin", pin_value, at)
    except credentials.CredentialTaken:
        # Saying "taken" reveals that this PIN belongs to someone, so these count as wrong guesses.
        wrong_try()
        flash("That PIN can't be used. Please pick a different one.", "error")
        return render_template("register.html", form=form), 400

    flash("Request sent. You can clock in once an admin approves your account.", "success")
    return redirect(url_for("clock.dashboard"))


# ---------------------------------------------------------------- admin: username + password

@bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST" and not locked_out():
        username = request.form.get("username", "").strip().lower()
        row = get_db().execute(
            "SELECT id, password_hash, status, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
        ok = (row is not None and row["is_admin"] and row["password_hash"]
              and check_password_hash(row["password_hash"], request.form.get("password", "")))
        if not ok:
            wrong_try()
            flash("Wrong username or password.", "error")
        elif row["status"] != "active":
            flash("This admin account is disabled.", "error")
        else:
            _sign_out_employee()
            session["admin_id"] = row["id"]
            session["admin_seen"] = timesheet.now().isoformat()
            target = request.args.get("next", "")
            # Only follow local paths, never another site.
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("admin.timesheets")
            return redirect(target)
        return render_template("admin/login.html", username=username)
    return render_template("admin/login.html")


@bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    _sign_out_admin()
    return redirect(url_for("clock.dashboard"))


def pending_count():
    return get_db().execute("SELECT COUNT(*) FROM users WHERE status = 'pending'").fetchone()[0]

