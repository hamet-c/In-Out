"""Admin pages: timesheets, CSV export, user approval and PINs, fixing a day."""
import csv
import io
import sqlite3
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace as NS

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from . import credentials, days, timesheet
from .auth import admin_required
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
        start=start, end=end, user_id=user_id, users=users, rows=rows,
        total_seconds=sum(d.total_seconds for d in rows),
        attention=sum(1 for d in rows if _needs_attention(d)),
    )


@bp.route("/export.csv")
@admin_required
def export_csv():
    start, end, user_id = _range_from_args()
    rows = days.days_in_range(start, end, timesheet.now(), user_id)
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["employee", "date", "clock_in", "clock_out", "segment_hours",
                  "day_total_hours", "status", "note"])
    for d in sorted(rows, key=lambda d: (d.user.display_name.lower(), d.work_date)):
        for s in d.segments:
            hours = f"{(s.clock_out - s.clock_in).total_seconds() / 3600:.2f}" if s.clock_out else ""
            out.writerow([d.user.display_name, d.work_date.isoformat(), s.clock_in.strftime("%H:%M"),
                          s.clock_out.strftime("%H:%M") if s.clock_out else "", hours,
                          f"{d.total_seconds / 3600:.2f}", d.status, d.note])
    name = f"timesheets_{start.isoformat()}_{end.isoformat()}.csv"
    # BOM so Excel opens accented names correctly.
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


# ---------------------------------------------------------------- users

@bp.route("/users")
@admin_required
def users():
    rows = get_db().execute(
        "SELECT u.id, u.display_name, u.username, u.is_admin, u.status, u.created_at, "
        "EXISTS (SELECT 1 FROM credentials c WHERE c.user_id = u.id AND c.kind = 'pin') AS has_pin "
        "FROM users u ORDER BY u.status != 'active', lower(u.display_name)"
    ).fetchall()
    people = [NS(**{**dict(r), "created_at": datetime.fromisoformat(r["created_at"])}) for r in rows]
    return render_template(
        "admin/users.html",
        pending=[p for p in people if p.status == "pending"],
        users=[p for p in people if p.status != "pending"],
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


_ACTIONS = {
    "approve": "Approved {name}.",
    "reject": "Rejected {name}'s request.",
    "disable": "Disabled {name}.",
    "enable": "Re-enabled {name}.",
    "set_pin": "Set a new PIN for {name}.",
}


@bp.route("/users/<int:user_id>", methods=["POST"])
@admin_required
def user_update(user_id):
    action = request.form.get("action")
    if action not in _ACTIONS:
        abort(400)
    db = get_db()
    user = db.execute("SELECT id, display_name, status FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if user_id == g.admin.id and action == "disable":
        flash("You can't disable your own account.", "error")
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
            else:
                abort(400)
            days.log(g.admin.id, action, _ACTIONS[action].format(name=name), at)
    except credentials.CredentialTaken:
        flash("Someone else already has that PIN. Pick a different one.", "error")
        return redirect(url_for("admin.users"))
    flash(_ACTIONS[action].format(name=name), "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------- one day

def _day_or_404(day_id):
    day = days.get_day(day_id, timesheet.now())
    if day is None:
        abort(404)
    return day


def _span(a, b):
    return f"{clock(a)}–{clock(b) if b else 'open'}"


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
