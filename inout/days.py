"""Load workdays with their segments as plain objects for the views and templates."""
from datetime import date, datetime
from types import SimpleNamespace as NS

from . import timesheet
from .db import get_db


def _dt(value):
    return datetime.fromisoformat(value) if value else None


def _segments_by_day(day_ids):
    out = {i: [] for i in day_ids}
    if not day_ids:
        return out
    marks = ",".join("?" * len(day_ids))
    rows = get_db().execute(
        f"SELECT id, workday_id, clock_in, clock_out FROM segments "
        f"WHERE workday_id IN ({marks}) ORDER BY clock_in, id",
        list(day_ids),
    )
    for r in rows:
        out[r["workday_id"]].append(NS(id=r["id"], clock_in=_dt(r["clock_in"]), clock_out=_dt(r["clock_out"])))
    return out


def _build(rows, at):
    segs = _segments_by_day([r["id"] for r in rows])
    days = []
    for r in rows:
        work_date = date.fromisoformat(r["work_date"])
        submitted_at = _dt(r["submitted_at"])
        segments = segs[r["id"]]
        days.append(NS(
            id=r["id"],
            user=NS(id=r["user_id"], display_name=r["display_name"]),
            work_date=work_date,
            submitted_at=submitted_at,
            note=r["note"],
            segments=segments,
            total_seconds=timesheet.total_seconds(segments, work_date, at),
            status=timesheet.day_status(work_date, submitted_at, at.date()),
            is_open=any(s.clock_out is None for s in segments),
        ))
    return days


_SELECT = ("SELECT w.id, w.user_id, w.work_date, w.submitted_at, w.note, u.display_name "
           "FROM workdays w JOIN users u ON u.id = w.user_id ")


def get_day(day_id, at):
    rows = get_db().execute(_SELECT + "WHERE w.id = ?", (day_id,)).fetchall()
    return _build(rows, at)[0] if rows else None


def find_day(user_id, work_date, at):
    rows = get_db().execute(_SELECT + "WHERE w.user_id = ? AND w.work_date = ?",
                            (user_id, work_date.isoformat())).fetchall()
    return _build(rows, at)[0] if rows else None


def days_in_range(start, end, at, user_id=None):
    """Days with at least one segment, newest first, then by name."""
    sql = _SELECT + ("WHERE w.work_date BETWEEN ? AND ? "
                     "AND EXISTS (SELECT 1 FROM segments s WHERE s.workday_id = w.id) ")
    args = [start.isoformat(), end.isoformat()]
    if user_id is not None:
        sql += "AND w.user_id = ? "
        args.append(user_id)
    sql += "ORDER BY w.work_date DESC, u.display_name"
    return _build(get_db().execute(sql, args).fetchall(), at)


def stale_dates(user_id, today):
    """Earlier days with punches that were never submitted."""
    rows = get_db().execute(
        "SELECT work_date FROM workdays w WHERE user_id = ? AND work_date < ? AND submitted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM segments s WHERE s.workday_id = w.id) ORDER BY work_date",
        (user_id, today.isoformat()),
    )
    return [date.fromisoformat(r["work_date"]) for r in rows]


def audit_for_day(day_id):
    rows = get_db().execute(
        "SELECT a.created_at, a.detail, COALESCE(u.display_name, 'Deleted user') AS actor "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id "
        "WHERE a.workday_id = ? ORDER BY a.id DESC",
        (day_id,),
    )
    return [NS(created_at=_dt(r["created_at"]), detail=r["detail"], actor=NS(display_name=r["actor"]))
            for r in rows]


def log(actor_id, action, detail, at, workday_id=None):
    get_db().execute(
        "INSERT INTO audit_log (actor_id, workday_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor_id, workday_id, action, detail, at.isoformat()),
    )
