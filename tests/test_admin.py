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
    # The day's total is on its first row only, so adding up that column doesn't count it twice.
    assert lines[2].startswith("Alice,2026-10-20,12:30,16:30,4.00,,today")


def test_csv_keeps_typed_text_from_running_as_a_spreadsheet_formula(admin_client, alice_day):
    with admin_client.application.app_context():
        from inout.db import get_db
        get_db().execute("UPDATE workdays SET note = '=1+1 left early'")
        get_db().execute("UPDATE users SET display_name = '+Alice' WHERE display_name = 'Alice'")
    text = admin_client.get("/admin/export.csv?start=2026-10-20&end=2026-10-20").get_data(as_text=True)
    assert text.splitlines()[1] == "'+Alice,2026-10-20,08:00,12:00,4.00,4.50,today,'=1+1 left early"


class FakeSMTP:
    """Stands in for smtplib.SMTP and SMTP_SSL. Records what each connection did in `sent`."""
    sent = []
    fail = None  # an exception for login() to raise

    def __init__(self, host, port, timeout=None, context=None):
        self.record = {"host": host, "port": port, "ssl": context is not None, "starttls": False}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.record["starttls"] = True

    def login(self, user, password):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        self.record["login"] = (user, password)

    def send_message(self, msg):
        self.record["msg"] = msg
        FakeSMTP.sent.append(self.record)


@pytest.fixture
def smtp(monkeypatch):
    import smtplib
    FakeSMTP.sent, FakeSMTP.fail = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


GMAIL = {"smtp_host": "smtp.gmail.com", "smtp_port": "587", "smtp_user": "office@example.com",
         "smtp_password": "abcd efgh ijkl mnop"}


def test_email_csv_needs_settings_first(admin_client, alice_day, smtp):
    page = admin_client.get("/admin/?start=2026-10-20&end=2026-10-20").get_data(as_text=True)
    assert "Email isn't set up yet" in page
    page = admin_client.post("/admin/email-csv?start=2026-10-20&end=2026-10-20", data={"to": "me@example.com"},
                             follow_redirects=True).get_data(as_text=True)
    assert "Email isn't set up yet" in page
    assert smtp.sent == []


def test_email_csv_sends_the_same_csv_as_export(admin_client, alice_day, smtp, query):
    admin_client.post("/admin/email-settings", data=GMAIL)
    page = admin_client.get("/admin/").get_data(as_text=True)
    assert "abcd efgh" not in page  # the saved password never goes back to the browser
    assert "Sends from office@example.com" in page

    r = admin_client.post(f"/admin/email-csv?start=2026-10-20&end=2026-10-20&user_id={alice_day}",
                          data={"to": "boss@example.com"}, follow_redirects=True)
    assert "Sent the CSV to boss@example.com" in r.get_data(as_text=True)
    # It went back to the same view it was sent from.
    assert r.request.args.get("user_id") == str(alice_day)

    [sent] = smtp.sent
    assert (sent["host"], sent["port"], sent["starttls"]) == ("smtp.gmail.com", 587, True)
    assert sent["login"] == ("office@example.com", "abcd efgh ijkl mnop")
    msg = sent["msg"]
    assert (msg["From"], msg["To"]) == ("office@example.com", "boss@example.com")
    assert msg["Subject"] == "In-Out timesheets 2026-10-20"
    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "timesheets_2026-10-20_2026-10-20.csv"
    exported = admin_client.get(f"/admin/export.csv?start=2026-10-20&end=2026-10-20&user_id={alice_day}")
    assert attachment.get_content().encode("utf-8") == exported.get_data()

    # Remembered for next time, and in the change log.
    assert 'value="boss@example.com"' in admin_client.get("/admin/").get_data(as_text=True)
    assert [r["action"] for r in query("SELECT action FROM audit_log ORDER BY id")] == ["email_settings", "email_csv"]


def test_email_settings_keep_the_password_when_left_blank(admin_client, smtp, query):
    admin_client.post("/admin/email-settings", data=GMAIL)
    admin_client.post("/admin/email-settings", data={**GMAIL, "smtp_port": "465", "smtp_password": ""})
    saved = {r["key"]: r["value"] for r in query("SELECT key, value FROM settings WHERE key LIKE 'smtp_%'")}
    assert saved == {**GMAIL, "smtp_port": "465"}
    admin_client.post("/admin/email-csv", data={"to": "me@example.com"})
    assert smtp.sent[0]["ssl"] and not smtp.sent[0]["starttls"]  # 465 is encrypted from the start


def test_email_settings_and_address_are_checked(admin_client, smtp, query):
    page = admin_client.post("/admin/email-settings", data={**GMAIL, "smtp_password": ""},
                             follow_redirects=True).get_data(as_text=True)
    assert "Enter the password" in page
    page = admin_client.post("/admin/email-settings", data={**GMAIL, "smtp_port": "abc"},
                             follow_redirects=True).get_data(as_text=True)
    assert "The port is a number" in page
    assert query("SELECT key FROM settings WHERE key LIKE 'smtp_%'") == []

    admin_client.post("/admin/email-settings", data=GMAIL)
    for bad in ["", "not an address", "a@example.com, b@example.com", "a@example.com\nBcc: x@example.com"]:
        page = admin_client.post("/admin/email-csv", data={"to": bad}, follow_redirects=True).get_data(as_text=True)
        assert "Enter one email address" in page
    assert smtp.sent == []


def test_email_csv_explains_a_wrong_password(admin_client, smtp):
    import smtplib
    admin_client.post("/admin/email-settings", data=GMAIL)
    smtp.fail = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    page = admin_client.post("/admin/email-csv", data={"to": "me@example.com"},
                             follow_redirects=True).get_data(as_text=True)
    assert "use an app password" in page
    assert smtp.sent == []


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


def test_admin_adds_a_day_someone_never_clocked_in_on_and_submits_it(admin_client, app, make_user, clock, query):
    alice = make_user("Alice", pin="2468")
    clock.set("2026-10-22T09:00")  # two days later: Alice forgot to clock in at all on the 20th
    sign_in_admin(admin_client)
    assert "Add a day" in admin_client.get("/admin/").get_data(as_text=True)
    r = admin_client.post("/admin/day/new", data={"user_id": alice, "work_date": "2026-10-20"})
    day_id = query("SELECT id FROM workdays")[0]["id"]
    assert r.headers["Location"] == f"/admin/day/{day_id}"
    admin_client.post(f"/admin/day/{day_id}/segment", data={"clock_in": "08:00", "clock_out": "16:00"})

    page = admin_client.get("/admin/?start=2026-10-20&end=2026-10-20").get_data(as_text=True)
    assert "8h 00m" in page and "Not submitted" in page
    station = app.test_client()
    assert "Not submitted: Tue Oct 20" in sign_in_pin(station, "2468").get_data(as_text=True)

    # Alice can't submit an earlier day herself, so the admin does, and it stops being flagged.
    page = admin_client.post(f"/admin/day/{day_id}/submit", follow_redirects=True).get_data(as_text=True)
    assert "day is locked" in page and "Added the day" in page and "Submitted the day" in page
    assert query("SELECT submitted_at FROM workdays")[0]["submitted_at"] == "2026-10-22T09:00:00"
    assert "Not submitted" not in station.get("/").get_data(as_text=True)
    assert "Not submitted" not in admin_client.get("/admin/?start=2026-10-20&end=2026-10-20").get_data(as_text=True)


def test_adding_a_day_that_exists_opens_it_and_impossible_ones_are_refused(admin_client, alice_day, query):
    day_id = query("SELECT id FROM workdays")[0]["id"]
    r = admin_client.post("/admin/day/new", data={"user_id": alice_day, "work_date": "2026-10-20"})
    assert r.headers["Location"] == f"/admin/day/{day_id}"
    for form in ({"user_id": alice_day, "work_date": "2026-10-21"},  # tomorrow
                 {"user_id": 999, "work_date": "2026-10-19"},  # nobody
                 {"user_id": alice_day, "work_date": "soon"}):
        assert admin_client.post("/admin/day/new", data=form).headers["Location"] == "/admin/"
    assert len(query("SELECT id FROM workdays")) == 1


def test_a_day_with_a_missing_clock_out_cannot_be_submitted(admin_client, alice_day, query):
    day_id = query("SELECT id FROM workdays")[0]["id"]
    assert f"/admin/day/{day_id}/submit" not in admin_client.get(f"/admin/day/{day_id}").get_data(as_text=True)
    page = admin_client.post(f"/admin/day/{day_id}/submit", follow_redirects=True).get_data(as_text=True)
    assert "missing clock-out" in page
    assert query("SELECT submitted_at FROM workdays")[0]["submitted_at"] is None


# ---------------------------------------------------------------- change log

def test_change_log_lists_changes_to_people_and_to_days_newest_first(admin_client, alice_day, query):
    # Following the redirects shows the "Added Bob" and "Saved" messages there, not on the log page.
    admin_client.post("/admin/users/new", data={"display_name": "Bob", "pin": "3579"}, follow_redirects=True)
    day_id = query("SELECT id FROM workdays")[0]["id"]
    open_seg = query("SELECT id FROM segments WHERE clock_out IS NULL")[0]["id"]
    admin_client.post(f"/admin/day/{day_id}/segment", follow_redirects=True,
                      data={"segment_id": open_seg, "clock_in": "12:30", "clock_out": "16:30"})
    page = admin_client.get("/admin/changes").get_data(as_text=True)
    assert "Added Bob" in page and "<td class=\"nowrap\">Dana</td>" in page
    assert f'<a href="/admin/day/{day_id}">Alice, Tue Oct 20</a>' in page
    assert page.index("Changed 12:30 PM–open to 12:30 PM–4:30 PM") < page.index("Added Bob")
    assert "Older changes" not in page and "Newest changes" not in page


def test_change_log_shows_a_hundred_at_a_time(admin_client, app, clock, query):
    from inout import days
    from inout.db import transaction
    dana = query("SELECT id FROM users WHERE username = 'boss'")[0]["id"]
    with app.app_context(), transaction():
        for i in range(1, 106):
            days.log(dana, "test", f"Change number {i}.", clock())
    page = admin_client.get("/admin/changes").get_data(as_text=True)
    assert "Change number 105." in page and "Change number 6." in page and "Change number 5." not in page
    assert 'href="/admin/changes?before=6"' in page
    older = admin_client.get("/admin/changes?before=6").get_data(as_text=True)
    assert "Change number 5." in older and "Change number 1." in older and "Change number 6." not in older
    assert "Older changes" not in older and "Newest changes" in older


# ---------------------------------------------------------------- more than one admin

def _admin_form(username="sam", password="sam-password", confirm=None, **extra):
    return {"username": username, "password": password,
            "confirm": password if confirm is None else confirm, **extra}


def test_admin_adds_another_admin_who_can_sign_in(admin_client, client, query):
    r = admin_client.post("/admin/users/new-admin", data=_admin_form("Sam ", display_name="Sam Ortiz"),
                          follow_redirects=True)
    assert "Added Sam Ortiz as an admin. They sign in with the username sam." in r.get_data(as_text=True)
    assert query("SELECT is_admin, status FROM users WHERE username = 'sam'") == [{"is_admin": 1, "status": "active"}]
    assert query("SELECT COUNT(*) AS n FROM credentials")[0]["n"] == 0  # no PIN asked for, none set

    sam = client.application.test_client()
    assert sign_in_admin(sam, "sam", "sam-password").headers["Location"].endswith("/admin/")
    assert "Added Sam Ortiz as an admin (sam)" in [r["detail"] for r in query("SELECT detail FROM audit_log")]


def test_a_new_admin_can_also_get_a_pin(admin_client, client, query):
    admin_client.post("/admin/users/new-admin", data=_admin_form(display_name="Sam", pin="1357"))
    assert "Hi, Sam" in sign_in_pin(client.application.test_client(), "1357").get_data(as_text=True)


@pytest.mark.parametrize("form, problem", [
    (_admin_form("boss", display_name="Sam"), "The username boss is taken"),
    (_admin_form("sam o", display_name="Sam"), "no spaces"),
    (_admin_form(password="short", display_name="Sam"), "at least 8 characters"),
    (_admin_form(confirm="sam-passw0rd", display_name="Sam"), "passwords didn"),  # the page escapes the '
    (_admin_form(display_name="  "), "Please enter a name"),
    (_admin_form(display_name="Sam", pin="12"), "4 digits"),
])
def test_adding_an_admin_is_checked(admin_client, query, form, problem):
    r = admin_client.post("/admin/users/new-admin", data=form, follow_redirects=True)
    assert problem in r.get_data(as_text=True)
    assert query("SELECT display_name FROM users") == [{"display_name": "Dana"}]


def test_making_an_employee_an_admin_keeps_their_pin(admin_client, client, make_user, query):
    alice = make_user("Alice", pin="2468")
    r = admin_client.post(f"/admin/users/{alice}", data=_admin_form("alice", action="make_admin"),
                          follow_redirects=True)
    assert "Made Alice an admin. They sign in with the username alice." in r.get_data(as_text=True)
    assert sign_in_admin(client.application.test_client(), "alice", "sam-password").status_code == 302
    assert query("SELECT is_admin FROM users WHERE id = ?", alice) == [{"is_admin": 1}]
    assert "Hi, Alice" in sign_in_pin(client.application.test_client(), "2468").get_data(as_text=True)


def test_make_admin_refuses_a_taken_username(admin_client, make_user, query):
    alice = make_user("Alice", pin="2468")
    r = admin_client.post(f"/admin/users/{alice}", data=_admin_form("boss", action="make_admin"),
                          follow_redirects=True)
    assert "The username boss is taken" in r.get_data(as_text=True)
    assert query("SELECT is_admin, username FROM users WHERE id = ?", alice) == [{"is_admin": 0, "username": None}]


def test_removing_an_admin_keeps_them_as_an_employee_and_signs_them_out(admin_client, app, make_user, query):
    sam = make_user("Sam", pin="1357", admin="sam")
    sam_screen = app.test_client()
    sign_in_admin(sam_screen, "sam")
    assert sam_screen.get("/admin/users").status_code == 200

    r = admin_client.post(f"/admin/users/{sam}", data={"action": "remove_admin"}, follow_redirects=True)
    assert "Sam is no longer an admin." in r.get_data(as_text=True)
    assert query("SELECT is_admin, username, password_hash, status FROM users WHERE id = ?", sam) == [
        {"is_admin": 0, "username": None, "password_hash": None, "status": "active"}]
    assert sam_screen.get("/admin/users").status_code == 302  # their open admin page is closed
    assert sign_in_admin(app.test_client(), "sam").status_code == 200  # the sign-in page again, not in
    assert "Hi, Sam" in sign_in_pin(app.test_client(), "1357").get_data(as_text=True)


def test_an_admin_cannot_remove_their_own_admin_access(admin_client, query):
    dana = query("SELECT id FROM users WHERE username = 'boss'")[0]["id"]
    r = admin_client.post(f"/admin/users/{dana}", data={"action": "remove_admin"}, follow_redirects=True)
    assert "remove your own admin access" in r.get_data(as_text=True)
    assert query("SELECT is_admin FROM users WHERE id = ?", dana) == [{"is_admin": 1}]


def test_users_page_offers_the_admin_buttons(admin_client, make_user):
    make_user("Alice", pin="2468")
    make_user("Sam", admin="sam")
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert page.count('value="make_admin"') == 1  # Alice; not Sam or Dana, who are admins already
    assert page.count('value="remove_admin"') == 1  # Sam; never yourself
    assert "Add an admin" in page
    # Your own password asks for the current one; another admin's doesn't.
    assert page.count('value="set_password"') == 2 and page.count('name="current"') == 1
    assert "Change password" in page and "Reset password" in page


# ---------------------------------------------------------------- admin passwords

def _new_password(password="new-password", **extra):
    return {"action": "set_password", "password": password, "confirm": password, **extra}


def test_admin_changes_their_own_password_with_the_current_one(admin_client, app, query):
    dana = query("SELECT id FROM users WHERE username = 'boss'")[0]["id"]
    r = admin_client.post(f"/admin/users/{dana}", data=_new_password(current="not-it"), follow_redirects=True)
    assert "current password didn" in r.get_data(as_text=True)
    assert sign_in_admin(app.test_client()).status_code == 302  # the old one still works

    r = admin_client.post(f"/admin/users/{dana}", data=_new_password(current="adminpass"), follow_redirects=True)
    assert "Changed your password." in r.get_data(as_text=True)
    assert sign_in_admin(app.test_client()).status_code == 200  # the sign-in page again, not in
    assert sign_in_admin(app.test_client(), password="new-password").status_code == 302


def test_guessing_your_current_password_has_to_wait_like_wrong_sign_ins(admin_client, app, query):
    dana = query("SELECT id FROM users WHERE username = 'boss'")[0]["id"]
    for _ in range(3):
        admin_client.post(f"/admin/users/{dana}", data=_new_password(current="a guess"))
    r = admin_client.post(f"/admin/users/{dana}", data=_new_password(current="adminpass"), follow_redirects=True)
    assert "Too many wrong tries" in r.get_data(as_text=True)
    with app.app_context():
        from werkzeug.security import check_password_hash
        assert check_password_hash(query("SELECT password_hash FROM users WHERE id = ?", dana)[0]["password_hash"],
                                   "adminpass")


def test_admin_resets_another_admins_forgotten_password(admin_client, app, make_user, query):
    sam = make_user("Sam", admin="sam")
    r = admin_client.post(f"/admin/users/{sam}", data=_new_password("sams-new-one"), follow_redirects=True)
    assert "Changed Sam&#39;s password. Tell them the new one privately." in r.get_data(as_text=True)
    assert sign_in_admin(app.test_client(), "sam", "sams-new-one").status_code == 302
    assert "Changed Sam's password." in [r["detail"] for r in query("SELECT detail FROM audit_log")]


def test_password_changes_are_checked(admin_client, make_user, query):
    alice = make_user("Alice", pin="2468")
    sam = make_user("Sam", admin="sam")
    before = query("SELECT password_hash FROM users WHERE id = ?", sam)
    assert admin_client.post(f"/admin/users/{alice}", data=_new_password()).status_code == 400  # not an admin
    for form, problem in ((_new_password("short"), "at least 8 characters"),
                          (_new_password(confirm="new-passw0rd"), "passwords didn")):
        r = admin_client.post(f"/admin/users/{sam}", data=form, follow_redirects=True)
        assert problem in r.get_data(as_text=True)
    assert query("SELECT password_hash FROM users WHERE id = ?", sam) == before
    assert query("SELECT detail FROM audit_log") == []
