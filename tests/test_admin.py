import pytest

from .conftest import sign_in_admin, sign_in_pin


@pytest.fixture
def admin_client(client, make_user):
    make_user("Dana", admin="boss")
    sign_in_admin(client)
    return client


@pytest.fixture
def alice_day(app, client, make_user, clock):
    """Alice worked 8:00-12:00 and 12:30-(still open) on Oct 20. Returns the workday id."""
    alice = make_user("Alice", pin="2468")
    c = app.test_client()
    sign_in_pin(c, "2468")
    for t, expect in [("08:00", "idle"), ("12:00", "in"), ("12:30", "out")]:
        clock.set(f"2026-10-20T{t}")
        sign_in_pin(c, "2468")
        c.post("/clock", data={"expect": expect})
    clock.set("2026-10-20T13:00")
    sign_in_admin(client)  # hours passed, so the admin session timed out too
    return alice


def test_approve_and_reject_requests(admin_client, client, query):
    client.post("/register", data={"display_name": "Cara", "pin": "5824", "confirm": "5824"})
    client.post("/register", data={"display_name": "Eli", "pin": "6935", "confirm": "6935"})
    cara, eli = [r["id"] for r in query("SELECT id FROM users WHERE status = 'pending' ORDER BY id")]
    admin_client.post(f"/admin/users/{cara}", data={"action": "approve"})
    admin_client.post(f"/admin/users/{eli}", data={"action": "reject"})
    assert query("SELECT display_name, status FROM users WHERE id IN (?, ?)", cara, eli) == [
        {"display_name": "Cara", "status": "active"}]
    # Rejecting frees the PIN for someone else.
    assert query("SELECT COUNT(*) AS n FROM credentials")[0]["n"] == 1


def test_admin_adds_employee_and_changes_pin(admin_client, client, query):
    admin_client.post("/admin/users/new", data={"display_name": "Bob", "pin": "3579"})
    bob = query("SELECT id, status FROM users WHERE display_name = 'Bob'")[0]
    assert bob["status"] == "active"
    r = admin_client.post(f"/admin/users/{bob['id']}", data={"action": "set_pin", "pin": "8642"},
                          follow_redirects=True)
    assert "Set a new PIN for Bob" in r.get_data(as_text=True)
    other = client.application.test_client()
    assert "Hi, Bob" in sign_in_pin(other, "8642").get_data(as_text=True)


def test_admin_cannot_give_someone_a_taken_pin(admin_client, make_user, query):
    make_user("Alice", pin="2468")
    r = admin_client.post("/admin/users/new", data={"display_name": "Bob", "pin": "2468"},
                          follow_redirects=True)
    assert "already has that PIN" in r.get_data(as_text=True)
    assert query("SELECT id FROM users WHERE display_name = 'Bob'") == []


def test_disabling_someone_signs_them_out(admin_client, app, make_user):
    alice = make_user("Alice", pin="2468")
    station = app.test_client()
    sign_in_pin(station, "2468")
    admin_client.post(f"/admin/users/{alice}", data={"action": "disable"})
    assert "Enter your PIN" in station.get("/").get_data(as_text=True)


def test_timesheets_show_notes_and_flag_open_days(admin_client, alice_day, clock):
    clock.set("2026-10-22T09:00")  # two days later, Alice never clocked out
    sign_in_admin(admin_client)
    page = admin_client.get("/admin/?start=2026-10-01&end=2026-10-31").get_data(as_text=True)
    assert "Alice" in page and "Still clocked in" in page and "4h 00m" in page
    assert "1 needs attention" in page


def test_csv_export(admin_client, alice_day, clock, query):
    day_id = query("SELECT id FROM workdays")[0]["id"]
    admin_client.post(f"/admin/day/{day_id}/segment",
                      data={"segment_id": query("SELECT id FROM segments WHERE clock_out IS NULL")[0]["id"],
                            "clock_in": "12:30", "clock_out": "16:30"})
    with admin_client.application.app_context():
        from inout.db import get_db
        get_db().execute("UPDATE workdays SET note = 'Dentist, left early'")
    text = admin_client.get("/admin/export.csv?start=2026-10-20&end=2026-10-20").get_data(as_text=True)
    lines = text.lstrip("﻿").splitlines()
    assert lines[0] == "employee,date,clock_in,clock_out,segment_hours,day_total_hours,status,note"
    assert lines[1] == "Alice,2026-10-20,08:00,12:00,4.00,8.00,today,\"Dentist, left early\""
    assert lines[2].startswith("Alice,2026-10-20,12:30,16:30,4.00,8.00")


def test_editing_a_day_is_validated_and_logged(admin_client, alice_day, query):
    day_id = query("SELECT id FROM workdays")[0]["id"]
    open_seg = query("SELECT id FROM segments WHERE clock_out IS NULL")[0]["id"]
    url = f"/admin/day/{day_id}/segment"

    page = admin_client.post(url, data={"segment_id": open_seg, "clock_in": "11:00", "clock_out": "15:00"},
                             follow_redirects=True).get_data(as_text=True)
    assert "overlaps another time" in page
    page = admin_client.post(url, data={"segment_id": open_seg, "clock_in": "14:00", "clock_out": "13:00"},
                             follow_redirects=True).get_data(as_text=True)
    assert "has to be after" in page

    admin_client.post(url, data={"segment_id": open_seg, "clock_in": "12:30", "clock_out": "16:30"})
    admin_client.post(url, data={"clock_in": "17:00", "clock_out": "18:00"})
    first = query("SELECT id FROM segments ORDER BY clock_in")[0]["id"]
    admin_client.post(f"/admin/day/{day_id}/segment/{first}/delete")

    page = admin_client.get(f"/admin/day/{day_id}").get_data(as_text=True)
    assert "Changed 12:30 PM–open to 12:30 PM–4:30 PM" in page
    assert "Added 5:00 PM–6:00 PM" in page and "Deleted 8:00 AM–12:00 PM" in page
    assert len(query("SELECT id FROM segments")) == 2


def test_reopen_lets_the_employee_clock_in_again(admin_client, app, make_user, clock, query):
    make_user("Alice", pin="2468")
    c = app.test_client()
    sign_in_pin(c, "2468")
    c.post("/clock", data={"expect": "idle"})
    clock.set("2026-10-20T12:00")
    sign_in_pin(c, "2468")
    c.post("/clock", data={"expect": "in"})
    c.post("/submit")
    sign_in_admin(admin_client)
    day_id = query("SELECT id FROM workdays")[0]["id"]
    admin_client.post(f"/admin/day/{day_id}/reopen")
    assert query("SELECT submitted_at FROM workdays")[0]["submitted_at"] is None
    assert "Clock In" in c.get("/").get_data(as_text=True)
    assert "Reopened the day" in admin_client.get(f"/admin/day/{day_id}").get_data(as_text=True)
