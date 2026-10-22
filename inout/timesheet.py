"""Clock rules as pure functions. Segments are objects with clock_in / clock_out datetimes."""
from datetime import datetime


def now():
    """The server's clock, the only time source. Tests replace this."""
    return datetime.now().replace(microsecond=0)


def state(segments, submitted_at):
    """'idle' (no punches), 'in' (open segment), 'out' (clocked out), or 'submitted'."""
    if submitted_at:
        return "submitted"
    if not segments:
        return "idle"
    return "in" if segments[-1].clock_out is None else "out"


def worked_seconds(segments):
    """Total of the closed segments."""
    return int(sum((s.clock_out - s.clock_in).total_seconds() for s in segments if s.clock_out))


def total_seconds(segments, work_date, at):
    """Closed segments, plus the open one if work_date is today.

    An open segment on a past day is a forgotten clock-out. Counting it up to now
    would add days of fake hours, so it counts for nothing until an admin fixes it.
    """
    total = worked_seconds(segments)
    if work_date == at.date():
        total += int(sum((at - s.clock_in).total_seconds() for s in segments if s.clock_out is None))
    return total


def day_status(work_date, submitted_at, today):
    if submitted_at:
        return "submitted"
    return "today" if work_date == today else "unsubmitted"


def overlaps(start, end, others):
    """Whether [start, end) overlaps any other segment. An open end counts as running forever."""
    for o in others:
        o_end = o.clock_out or datetime.max
        if start < o_end and o.clock_in < (end or datetime.max):
            return True
    return False
