"""Admin pages: timesheets, CSV export, user approval, PINs and cards, admins and their passwords,
fixing a day, and the change log."""
import csv
import io
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace as NS

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import credentials, days, mailer, timesheet
from .auth import admin_required, card_reader, locked_out, wrong_try
from .db import get_db, transaction
from .filters import clock

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------- timesheets

def _range_from_args():
    today = timesheet.now().date()
    try:
        start = date.fromisoformat(request.args.get("start", ""))
    except ValueError:
        start = today - timedelta(days=13)
    try:
        end = date.fromisoformat(request.args.get("end", ""))
    except ValueError:
        end = today
    if end < start:
        start, end = end, start
    return start, end, request.args.get("user_id", type=int)


def _needs_attention(day):
    return day.status == "unsubmitted" or (day.is_open and day.status != "today")


@bp.route("/")
@admin_required
def timesheets():
    start, end, user_id = _range_from_args()
    rows = days.days_in_range(start, end, timesheet.now(), user_id)
    users = get_db().execute(
        "SELECT id, display_name FROM users WHERE status != 'pending' ORDER BY display_name").fetchall()
    return render_template(
        "admin/timesheets.html",
        start=start, end=end, user_id=user_id, users=users, rows=rows, today=timesheet.now().date(),
        total_seconds=sum(d.total_seconds for d in rows),
        attention=sum(1 for d in rows if _needs_attention(d)),
        email=_email_context(),
    )


def _email_context():
    """The saved email settings for the page, without the password itself."""
    saved = mailer.settings()
    return NS(ready=mailer.is_set_up(saved), host=saved["smtp_host"], port=saved["smtp_port"],
              user=saved["smtp_user"], has_password=bool(saved["smtp_password"]), last_to=saved["email_to"])


def _typed(text):
    """Text someone typed, for a CSV cell. A spreadsheet runs a cell that starts with = + - or @
    as a formula, so "-left early" would show an error and "=..." could do worse. A leading
    apostrophe makes it plain text."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(start, end, user_id):
    """(filename, text) of the timesheet CSV, for Export CSV and Email CSV alike."""
    rows = days.days_in_range(start, end, timesheet.now(), user_id)
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["employee", "date", "clock_in", "clock_out", "segment_hours",
                  "day_total_hours", "status", "note"])
    for d in sorted(rows, key=lambda d: (d.user.display_name.lower(), d.work_date)):
        for i, s in enumerate(d.segments):
            hours = f"{(s.clock_out - s.clock_in).total_seconds() / 3600:.2f}" if s.clock_out else ""
            # The day's total goes on the day's first row only, so adding up the column is right.
            day_total = f"{d.total_seconds / 3600:.2f}" if i == 0 else ""
            out.writerow([_typed(d.user.display_name), d.work_date.isoformat(), s.clock_in.strftime("%H:%M"),
                          s.clock_out.strftime("%H:%M") if s.clock_out else "", hours,
                          day_total, d.status, _typed(d.note)])
    # BOM so Excel opens accented names correctly.
    return f"timesheets_{start.isoformat()}_{end.isoformat()}.csv", "﻿" + buf.getvalue()


@bp.route("/export.csv")
@admin_required
def export_csv():
    name, text = _csv(*_range_from_args())
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})


def _back_to_timesheets():
    """To the Timesheets page showing the same dates and person the form was posted from."""
    return redirect(url_for("admin.timesheets", **{k: v for k, v in request.args.items()
                                                   if k in ("start", "end", "user_id")}))


# One address: no spaces, commas or angle brackets, an @ and a dot after it.
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


@bp.route("/email-csv", methods=["POST"])
@admin_required
def email_csv():
    """Email the same CSV Export CSV downloads. The range comes in the URL, like Export CSV's."""
    start, end, user_id = _range_from_args()
    to = request.form.get("to", "").strip()
    if not _EMAIL.fullmatch(to) or len(to) > 254:
        flash("Enter one email address to send the CSV to.", "error")
        return _back_to_timesheets()
    name, text = _csv(start, end, user_id)
    person = get_db().execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    who = person["display_name"] if person else "everyone"
    span = start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"
    try:
        mailer.send(to, f"In-Out timesheets {span}",
                    f"Timesheets for {who}, {span}, are attached as a CSV file.\n\n"
                    f"Sent from In-Out by {g.admin.display_name}.\n",
                    name, text.encode("utf-8"))
    except mailer.SendFailed as e:
        flash(str(e), "error")
        return _back_to_timesheets()
    with transaction():
        mailer.save({"email_to": to})  # filled in for next time
        days.log(g.admin.id, "email_csv", f"Emailed the timesheets for {who}, {span}, to {to}", timesheet.now())
    flash(f"Sent the CSV to {to}.", "success")
    return _back_to_timesheets()


@bp.route("/email-settings", methods=["POST"])
@admin_required
def email_settings():
    """The account Email CSV sends from. An empty password keeps the saved one."""
    host = request.form.get("smtp_host", "").strip()
    port = request.form.get("smtp_port", type=int)
    user = request.form.get("smtp_user", "").strip()
    password = request.form.get("smtp_password", "")
    problem = ("Enter the email server, for example smtp.gmail.com." if not host or any(c.isspace() for c in host)
               else "The port is a number, usually 587." if port is None or not 0 < port < 65536
               else "Enter the email address to send from." if not _EMAIL.fullmatch(user)
               else "Enter the password for that email address." if not password and not mailer.settings()["smtp_password"]
               else None)
    if problem:
        flash(problem, "error")
        return _back_to_timesheets()
    values = {"smtp_host": host, "smtp_port": str(port), "smtp_user": user}
    if password:
        values["smtp_password"] = password
    with transaction():
        mailer.save(values)
        days.log(g.admin.id, "email_settings", f"Changed the email settings: sends from {user} through {host}",
                 timesheet.now())
    flash("Saved the email settings. Try Email CSV to check them.", "success")
    return _back_to_timesheets()


# ---------------------------------------------------------------- users

@bp.route("/users")
@admin_required
def users():
    rows = get_db().execute(
        "SELECT u.id, u.display_name, u.username, u.is_admin, u.status, u.created_at, "
        "EXISTS (SELECT 1 FROM credentials c WHERE c.user_id = u.id AND c.kind = 'pin') AS has_pin, "
        "EXISTS (SELECT 1 FROM credentials c WHERE c.user_id = u.id AND c.kind = 'nfc') AS has_card "
        "FROM users u ORDER BY u.status != 'active', lower(u.display_name)"
    ).fetchall()
    people = [NS(**{**dict(r), "created_at": datetime.fromisoformat(r["created_at"])}) for r in rows]
    return render_template(
        "admin/users.html",
        pending=[p for p in people if p.status == "pending"],
        users=[p for p in people if p.status != "pending"],
        card_reader=card_reader(),
    )


@bp.route("/users/new", methods=["POST"])
@admin_required
def create_user():
    name = " ".join(request.form.get("display_name", "").split())[:80]
    pin = request.form.get("pin", "").strip()
    problem = "Please enter a name." if not name else credentials.pin_problem(pin)
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin.users"))
    at = timesheet.now()
    try:
        with transaction():
            user_id = get_db().execute(
                "INSERT INTO users (display_name, status, created_at) VALUES (?, 'active', ?)",
                (name, at.isoformat())).lastrowid
            credentials.set_secret(user_id, "pin", pin, at)
            days.log(g.admin.id, "create_user", f"Added {name}", at)
    except credentials.CredentialTaken:
        flash("Someone else already has that PIN. Pick a different one.", "error")
        return redirect(url_for("admin.users"))
    flash(f"Added {name}. They can clock in with their PIN now.", "success")
    return redirect(url_for("admin.users"))


ADMIN_PASSWORD_MIN = 8  # the same as manage.py create-admin


def _admin_form():
    """The username (as admins type it to sign in) and password from a Make admin or Add an admin form."""
    return request.form.get("username", "").strip().lower(), request.form.get("password", "")


def _password_problem(password):
    if len(password) < ADMIN_PASSWORD_MIN:
        return f"The password needs at least {ADMIN_PASSWORD_MIN} characters."
    if password != request.form.get("confirm", ""):
        return "The two passwords didn't match."
    return None


def _admin_form_problem(db, username, password, for_user_id=None):
    if not username or len(username) > 40 or any(ch.isspace() for ch in username):
        return "Pick a username of up to 40 characters, with no spaces."
    if db.execute("SELECT 1 FROM users WHERE username = ? AND id IS NOT ?", (username, for_user_id)).fetchone():
        return f"The username {username} is taken. Pick another."
    return _password_problem(password)


@bp.route("/users/new-admin", methods=["POST"])
@admin_required
def create_admin():
    """A new person who signs in to the admin pages. The PIN is optional, for admins who clock in too."""
    db = get_db()
    name = " ".join(request.form.get("display_name", "").split())[:80]
    pin = request.form.get("pin", "").strip()
    username, password = _admin_form()
    problem = ("Please enter a name." if not name else
               _admin_form_problem(db, username, password) or (credentials.pin_problem(pin) if pin else None))
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin.users"))
    at = timesheet.now()
    try:
        with transaction(db):
            user_id = db.execute(
                "INSERT INTO users (display_name, username, password_hash, is_admin, status, created_at) "
                "VALUES (?, ?, ?, 1, 'active', ?)",
                (name, username, generate_password_hash(password), at.isoformat())).lastrowid
            if pin:
                credentials.set_secret(user_id, "pin", pin, at)
            days.log(g.admin.id, "create_admin", f"Added {name} as an admin ({username})", at)
    except credentials.CredentialTaken:
        flash("Someone else already has that PIN. Pick a different one, or leave it empty.", "error")
        return redirect(url_for("admin.users"))
    except sqlite3.IntegrityError:  # the same username saved a moment ago from another screen
        flash(f"The username {username} is taken. Pick another.", "error")
        return redirect(url_for("admin.users"))
    flash(f"Added {name} as an admin. They sign in with the username {username}.", "success")
    return redirect(url_for("admin.users"))


_ACTIONS = {
    "approve": "Approved {name}.",
    "reject": "Rejected {name}'s request.",
    "disable": "Disabled {name}.",
    "enable": "Re-enabled {name}.",
    "set_pin": "Set a new PIN for {name}.",
    "remove_card": "Removed {name}'s card.",
    "make_admin": "Made {name} an admin.",
    "remove_admin": "{name} is no longer an admin.",
    "set_password": "Changed {name}'s password.",
}


@bp.route("/users/<int:user_id>", methods=["POST"])
@admin_required
def user_update(user_id):
    action = request.form.get("action")
    if action not in _ACTIONS:
        abort(400)
    db = get_db()
    user = db.execute("SELECT id, display_name, status, is_admin, password_hash FROM users WHERE id = ?",
                      (user_id,)).fetchone()
    if user is None:
        abort(404)
    # Never your own: then whoever is signed in stays an admin, so there's always at least one.
    if user_id == g.admin.id and action in ("disable", "remove_admin"):
        flash("You can't disable your own account." if action == "disable" else
              "You can't remove your own admin access. Another admin can.", "error")
        return redirect(url_for("admin.users"))

    at = timesheet.now()
    name = user["display_name"]
    try:
        with transaction(db):
            if action == "approve" and user["status"] == "pending":
                db.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
            elif action == "reject" and user["status"] == "pending":
                db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            elif action in ("disable", "enable") and user["status"] != "pending":
                db.execute("UPDATE users SET status = ? WHERE id = ?",
                           ("disabled" if action == "disable" else "active", user_id))
            elif action == "set_pin":
                pin = request.form.get("pin", "").strip()
                problem = credentials.pin_problem(pin)
                if problem:
                    flash(problem, "error")
                    return redirect(url_for("admin.users"))
                credentials.set_secret(user_id, "pin", pin, at)
            elif action == "remove_card":
                credentials.remove_secret(user_id, "nfc")
            elif action == "make_admin" and user["status"] == "active" and not user["is_admin"]:
                username, password = _admin_form()
                problem = _admin_form_problem(db, username, password, for_user_id=user_id)
                if problem:
                    flash(problem, "error")
                    return redirect(url_for("admin.users"))
                db.execute("UPDATE users SET is_admin = 1, username = ?, password_hash = ? WHERE id = ?",
                           (username, generate_password_hash(password), user_id))
            elif action == "remove_admin" and user["is_admin"]:
                # They stay on as an employee, PIN and card included. Their username is freed,
                # and an admin session they have open ends on its next page.
                db.execute("UPDATE users SET is_admin = 0, username = NULL, password_hash = NULL WHERE id = ?",
                           (user_id,))
            elif action == "set_password" and user["is_admin"]:
                # Any admin can set another admin's, for one who forgot theirs. Changing your own
                # asks for the current one first, and wrong tries count like wrong sign-ins, so
                # a screen left signed in can't be used to guess it.
                password = request.form.get("password", "")
                problem = _password_problem(password)
                if problem is None and user_id == g.admin.id:
                    if locked_out():
                        return redirect(url_for("admin.users"))
                    if not check_password_hash(user["password_hash"], request.form.get("current", "")):
                        wrong_try()
                        problem = "Your current password didn't match."
                if problem:
                    flash(problem, "error")
                    return redirect(url_for("admin.users"))
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(password), user_id))
            else:
                abort(400)
            days.log(g.admin.id, action, _ACTIONS[action].format(name=name), at)
    except credentials.CredentialTaken:
        flash("Someone else already has that PIN. Pick a different one.", "error")
        return redirect(url_for("admin.users"))
    except sqlite3.IntegrityError:  # the same username saved a moment ago from another screen
        flash("That username is taken. Pick another.", "error")
        return redirect(url_for("admin.users"))
    message = _ACTIONS[action].format(name=name)
    if action == "make_admin":
        message += f" They sign in with the username {username}."
    elif action == "set_password":
        message = ("Changed your password." if user_id == g.admin.id else
                   f"{message} Tell them the new one privately.")
    flash(message, "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/card", methods=["GET", "POST"])
@admin_required
def link_card(user_id):
    """GET shows "tap the card now". The page then asks with POST twice a second (see app.js):
    204 until a card is tapped on the server's reader, then link it and say where to go."""
    db = get_db()
    user = db.execute("SELECT id, display_name FROM users WHERE id = ? AND status != 'pending'",
                      (user_id,)).fetchone()
    if user is None:
        abort(404)
    reader = card_reader()
    if request.method == "GET":
        return render_template("admin/link_card.html", user=user, card_reader=reader)

    tap = reader.tap_after(request.form.get("after", 0, type=int))
    if tap is None:
        return "", 204
    at = timesheet.now()
    name = user["display_name"]
    try:
        with transaction(db):
            credentials.set_secret(user_id, "nfc", tap.uid, at)
            days.log(g.admin.id, "link_card", f"Linked a card to {name}", at)
    except credentials.CredentialTaken:
        owner = db.execute("SELECT display_name FROM users WHERE id = ?",
                           (credentials.find_user_id("nfc", tap.uid),)).fetchone()
        flash(f"That card is already linked to {owner['display_name'] if owner else 'someone else'}. "
              "Remove it from them first, or use a different card.", "error")
        return {"next": url_for("admin.link_card", user_id=user_id)}
    flash(f"Linked the card to {name}. They can tap it to clock in now.", "success")
    return {"next": url_for("admin.users")}


# ---------------------------------------------------------------- one day

def _day_or_404(day_id):
    day = days.get_day(day_id, timesheet.now())
    if day is None:
        abort(404)
    return day


def _span(a, b):
    return f"{clock(a)}–{clock(b) if b else 'open'}"


@bp.route("/day/new", methods=["POST"])
@admin_required
def day_new():
    """Open someone's day so its times can be typed in, for a day they never clocked in on.
    The day page does the rest. If the day exists already, this just goes to it."""
    db = get_db()
    user = db.execute("SELECT id, display_name FROM users WHERE id = ? AND status != 'pending'",
                      (request.form.get("user_id", type=int),)).fetchone()
    try:
        work_date = date.fromisoformat(request.form.get("work_date", ""))
    except ValueError:
        work_date = None
    at = timesheet.now()
    problem = ("Choose a person and a date." if user is None or work_date is None else
               "That date hasn't come yet." if work_date > at.date() else None)
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin.timesheets"))
    with transaction(db):
        row = db.execute("SELECT id, EXISTS (SELECT 1 FROM segments s WHERE s.workday_id = w.id) AS has_times "
                         "FROM workdays w WHERE user_id = ? AND work_date = ?",
                         (user["id"], work_date.isoformat())).fetchone()
        if row is None:
            day_id = db.execute("INSERT INTO workdays (user_id, work_date) VALUES (?, ?)",
                                (user["id"], work_date.isoformat())).lastrowid
            days.log(g.admin.id, "add_day", "Added the day", at, workday_id=day_id)
        else:
            day_id = row["id"]
    if row is not None and row["has_times"]:
        flash(f"{user['display_name']} already has times on that day. Change them here.", "warning")
    else:
        flash("Type in the times below and press Add.", "success")
    return redirect(url_for("admin.day", day_id=day_id))


@bp.route("/day/<int:day_id>")
@admin_required
def day(day_id):
    return render_template("admin/day.html", day=_day_or_404(day_id), audit=days.audit_for_day(day_id))


@bp.route("/day/<int:day_id>/segment", methods=["POST"])
@admin_required
def segment_save(day_id):
    day = _day_or_404(day_id)
    back = redirect(url_for("admin.day", day_id=day_id))
    try:
        start = datetime.combine(day.work_date, time.fromisoformat(request.form.get("clock_in", "")))
        raw_end = request.form.get("clock_out", "")
        end = datetime.combine(day.work_date, time.fromisoformat(raw_end)) if raw_end else None
    except ValueError:
        flash("Times need to look like 08:30.", "error")
        return back
    if end and end <= start:
        flash("Clock-out has to be after clock-in.", "error")
        return back

    seg_id = request.form.get("segment_id", type=int)
    existing = next((s for s in day.segments if s.id == seg_id), None)
    if seg_id and existing is None:
        abort(404)
    others = [s for s in day.segments if s.id != seg_id]
    if timesheet.overlaps(start, end, others):
        flash("That overlaps another time on this day.", "error")
        return back

    at = timesheet.now()
    db = get_db()
    try:
        with transaction(db):
            if existing:
                db.execute("UPDATE segments SET clock_in = ?, clock_out = ? WHERE id = ?",
                           (start.isoformat(), end.isoformat() if end else None, seg_id))
                detail = f"Changed {_span(existing.clock_in, existing.clock_out)} to {_span(start, end)}"
            else:
                db.execute("INSERT INTO segments (workday_id, clock_in, clock_out) VALUES (?, ?, ?)",
                           (day_id, start.isoformat(), end.isoformat() if end else None))
                detail = f"Added {_span(start, end)}"
            days.log(g.admin.id, "edit_segment", detail, at, workday_id=day_id)
    except sqlite3.IntegrityError:
        flash("Only one time on a day can be left open.", "error")
        return back
    flash("Saved.", "success")
    return back


@bp.route("/day/<int:day_id>/segment/<int:segment_id>/delete", methods=["POST"])
@admin_required
def segment_delete(day_id, segment_id):
    day = _day_or_404(day_id)
    seg = next((s for s in day.segments if s.id == segment_id), None)
    if seg is None:
        abort(404)
    with transaction():
        get_db().execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        days.log(g.admin.id, "delete_segment", f"Deleted {_span(seg.clock_in, seg.clock_out)}",
                 timesheet.now(), workday_id=day_id)
    flash("Deleted.", "success")
    return redirect(url_for("admin.day", day_id=day_id))


@bp.route("/day/<int:day_id>/reopen", methods=["POST"])
@admin_required
def day_reopen(day_id):
    day = _day_or_404(day_id)
    if day.submitted_at:
        with transaction():
            get_db().execute("UPDATE workdays SET submitted_at = NULL WHERE id = ?", (day_id,))
            days.log(g.admin.id, "reopen", "Reopened the day", timesheet.now(), workday_id=day_id)
        flash(f"Reopened. {day.user.display_name} can clock in and submit again.", "success")
    return redirect(url_for("admin.day", day_id=day_id))


@bp.route("/day/<int:day_id>/submit", methods=["POST"])
@admin_required
def day_submit(day_id):
    """Submit a day for someone. People can only submit today themselves, so this is how an
    earlier day (one they forgot to submit, or one an admin typed in) stops being flagged."""
    day = _day_or_404(day_id)
    back = redirect(url_for("admin.day", day_id=day_id))
    if day.submitted_at:
        return back
    if not day.segments or day.is_open:
        flash("Fill in the missing clock-out first." if day.is_open else "Add the day's times first.", "error")
        return back
    at = timesheet.now()
    with transaction():
        get_db().execute("UPDATE workdays SET submitted_at = ? WHERE id = ?", (at.isoformat(), day_id))
        days.log(g.admin.id, "submit", "Submitted the day", at, workday_id=day_id)
    flash(f"Submitted. {day.user.display_name}'s day is locked.", "success")
    return back


# ---------------------------------------------------------------- change log

LOG_PAGE = 100


@bp.route("/changes")
@admin_required
def change_log():
    """Everything admins have changed, newest first, LOG_PAGE at a time. "before" is the id
    the previous page ended on."""
    before = request.args.get("before", type=int)
    entries = days.audit_page(LOG_PAGE + 1, before)  # one extra tells whether older ones exist
    older = entries[LOG_PAGE - 1].id if len(entries) > LOG_PAGE else None
    return render_template("admin/log.html", entries=entries[:LOG_PAGE], older=older, first_page=before is None)
