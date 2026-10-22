"""The employee page: PIN pad when nobody is signed in, otherwise today's clock."""
import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from . import days, filters, timesheet
from .auth import employee_required
from .db import get_db, transaction

bp = Blueprint("clock", __name__)

NOTE_MAX = 500


@bp.route("/")
def dashboard():
    if g.employee is None:
        return render_template("pin.html")
    at = timesheet.now()
    day = days.find_day(g.employee.id, at.date(), at)
    segments = day.segments if day else []
    open_seg = next((s for s in segments if s.clock_out is None), None)
    return render_template(
        "dashboard.html",
        today=at.date(),
        server_now=at,
        state=timesheet.state(segments, day.submitted_at if day else None),
        segments=segments,
        worked_seconds=timesheet.worked_seconds(segments),
        total_seconds=timesheet.total_seconds(segments, at.date(), at),
        open_since=open_seg.clock_in if open_seg else None,
        submitted_at=day.submitted_at if day else None,
        note=day.note if day else "",
        note_max=NOTE_MAX,
        stale_days=days.stale_dates(g.employee.id, at.date()),
    )


@bp.route("/clock", methods=["POST"])
@employee_required
def punch():
    at = timesheet.now()
    db = get_db()
    try:
        with transaction(db):
            day = days.find_day(g.employee.id, at.date(), at)
            current = timesheet.state(day.segments if day else [], day.submitted_at if day else None)
            # "expect" is the state the page showed. If it differs, the page was stale (or
            # this is a double tap), so do nothing rather than guess what was meant.
            if request.form.get("expect") != current:
                flash("This page was out of date, so nothing changed. Here's where you are now.", "warning")
                return redirect(url_for("clock.dashboard"))
            if current == "submitted":
                flash("Today is already submitted. Ask an admin if something needs fixing.", "error")
                return redirect(url_for("clock.dashboard"))
            if current == "in":
                db.execute("UPDATE segments SET clock_out = ? WHERE workday_id = ? AND clock_out IS NULL",
                           (at.isoformat(), day.id))
                message = f"Clocked out at {filters.clock(at)}."
            else:
                if day is None:
                    day_id = db.execute("INSERT INTO workdays (user_id, work_date) VALUES (?, ?)",
                                        (g.employee.id, at.date().isoformat())).lastrowid
                else:
                    day_id = day.id
                db.execute("INSERT INTO segments (workday_id, clock_in) VALUES (?, ?)",
                           (day_id, at.isoformat()))
                message = f"Clocked in at {filters.clock(at)}."
    except sqlite3.IntegrityError:
        # The one-open-segment index caught a race between two taps.
        flash("That tap was already counted.", "warning")
        return redirect(url_for("clock.dashboard"))
    flash(message, "success")
    return redirect(url_for("clock.dashboard"))


@bp.route("/submit", methods=["POST"])
@employee_required
def submit():
    at = timesheet.now()
    note = request.form.get("note", "").strip()[:NOTE_MAX]
    with transaction():
        day = days.find_day(g.employee.id, at.date(), at)
        if day is None or timesheet.state(day.segments, day.submitted_at) != "out":
            flash("Clock out before you submit the day.", "error")
            return redirect(url_for("clock.dashboard"))
        get_db().execute("UPDATE workdays SET submitted_at = ?, note = ? WHERE id = ?",
                         (at.isoformat(), note, day.id))
    flash("Day submitted. Nice work.", "success")
    return redirect(url_for("clock.dashboard"))
