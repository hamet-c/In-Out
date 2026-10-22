"""Who is at the screen.

Employees sign in with their PIN (later also an NFC card) and are signed out after a short
idle time, since the same screen may be shared. Admins sign in separately with a username
and password.

Only one person is signed in per browser: a PIN sign-in ends any admin session there, and
an admin sign-in ends any employee session. Otherwise an admin who walked away from a
shared screen would leave the admin pages open to the next person who enters a PIN.
"""
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
    """Locks a device (IP address) out after too many wrong PINs or passwords.

    A successful sign-in does NOT clear the count. Otherwise someone who knows one valid
    PIN could interleave it with guesses and never get locked out.
    """

    def __init__(self, max_failures=5, window=timedelta(minutes=15), lockout=timedelta(minutes=5)):
        self.max_failures, self.window, self.lockout = max_failures, window, lockout
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
            if len(q) < self.max_failures:
                return 0
            return max(0, int((q[-1] + self.lockout - at).total_seconds()))

    def fail(self, key, at):
        with self._lock:
            self._prune(key, at).append(at)


def _limiter():
    return current_app.extensions["inout_limiter"]


def _locked_out():
    """Flash and return True if this device is locked out right now."""
    left = _limiter().seconds_left(request.remote_addr, timesheet.now())
    if left:
        minutes = (left + 59) // 60
        flash(f"Too many wrong tries. Wait {minutes} minute{'s' if minutes != 1 else ''} and try again.", "error")
    return bool(left)


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


# ---------------------------------------------------------------- employee: PIN

def sign_in_with(kind, secret):
    """Shared by the PIN pad now and an NFC reader later. Returns True on success."""
    if _locked_out():
        return False
    user_id = credentials.find_user_id(kind, secret)
    row = _load_user(user_id) if user_id else None
    if row is None:
        _limiter().fail(request.remote_addr, timesheet.now())
        flash("That PIN didn't match anyone. Try again." if kind == "pin" else "Card not recognized.", "error")
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
    if problem is None and _locked_out():
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
        _limiter().fail(request.remote_addr, at)
        flash("That PIN can't be used. Please pick a different one.", "error")
        return render_template("register.html", form=form), 400

    flash("Request sent. You can clock in once an admin approves your account.", "success")
    return redirect(url_for("clock.dashboard"))


# ---------------------------------------------------------------- admin: username + password

@bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST" and not _locked_out():
        username = request.form.get("username", "").strip().lower()
        row = get_db().execute(
            "SELECT id, password_hash, status, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
        ok = (row is not None and row["is_admin"] and row["password_hash"]
              and check_password_hash(row["password_hash"], request.form.get("password", "")))
        if not ok:
            _limiter().fail(request.remote_addr, timesheet.now())
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

