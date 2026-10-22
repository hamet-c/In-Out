from datetime import date, datetime
from types import SimpleNamespace as NS

from inout import timesheet


def seg(start, end=None):
    day = "2026-10-20T"
    return NS(clock_in=datetime.fromisoformat(day + start),
              clock_out=datetime.fromisoformat(day + end) if end else None)


def test_state_follows_the_punches():
    assert timesheet.state([], None) == "idle"
    assert timesheet.state([seg("08:00")], None) == "in"
    assert timesheet.state([seg("08:00", "12:00")], None) == "out"
    assert timesheet.state([seg("08:00", "12:00")], datetime(2026, 10, 20, 17)) == "submitted"


def test_totals_count_closed_segments_and_todays_open_one():
    segs = [seg("08:00", "12:00"), seg("12:30")]
    assert timesheet.worked_seconds(segs) == 4 * 3600
    at = datetime(2026, 10, 20, 13, 0)
    assert timesheet.total_seconds(segs, date(2026, 10, 20), at) == 4 * 3600 + 30 * 60


def test_forgotten_clock_out_on_a_past_day_adds_nothing():
    segs = [seg("08:00", "12:00"), seg("12:30")]
    three_days_later = datetime(2026, 10, 23, 9, 0)
    assert timesheet.total_seconds(segs, date(2026, 10, 20), three_days_later) == 4 * 3600


def test_day_status():
    today = date(2026, 10, 20)
    assert timesheet.day_status(today, None, today) == "today"
    assert timesheet.day_status(date(2026, 10, 19), None, today) == "unsubmitted"
    assert timesheet.day_status(date(2026, 10, 19), datetime(2026, 10, 19, 17), today) == "submitted"


def test_overlaps():
    others = [seg("08:00", "12:00")]
    t = lambda s: datetime.fromisoformat("2026-10-20T" + s)
    assert timesheet.overlaps(t("11:00"), t("13:00"), others)
    assert not timesheet.overlaps(t("12:00"), t("16:00"), others)  # touching is fine
    assert timesheet.overlaps(t("13:00"), None, [seg("14:00", "15:00")])  # open runs on
    assert timesheet.overlaps(t("15:00"), t("16:00"), [seg("14:00")])
