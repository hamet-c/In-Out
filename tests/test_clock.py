from .conftest import sign_in_pin


def punch(client, expect):
    return client.post("/clock", data={"expect": expect}, follow_redirects=True).get_data(as_text=True)


def later(client, clock, when):
    """Move the clock forward. Real visits start with the PIN, and a long gap
    signs the employee out anyway, so sign in again."""
    clock.set(when)
    sign_in_pin(client, "2468")


def test_full_day_with_lunch_and_a_note(client, make_user, clock, query):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")

    later(client, clock, "2026-10-20T08:00")
    assert "Clocked in at 8:00 AM" in punch(client, "idle")
    later(client, clock, "2026-10-20T12:00")
    page = punch(client, "in")
    assert "Clocked out at 12:00 PM" in page and "Submit day" in page
    later(client, clock, "2026-10-20T12:30")
    punch(client, "out")
    later(client, clock, "2026-10-20T17:00")
    page = punch(client, "in")
    assert "8h 30m" in page

    page = client.post("/submit", data={"note": "  Forgot to clock in, started at 7:45.  "},
                       follow_redirects=True).get_data(as_text=True)
    assert "This day is locked" in page and "Forgot to clock in, started at 7:45." in page
    assert "Clock In" not in page

    day = query("SELECT submitted_at, note FROM workdays")[0]
    assert day == {"submitted_at": "2026-10-20T17:00:00", "note": "Forgot to clock in, started at 7:45."}
    assert len(query("SELECT id FROM segments WHERE clock_out IS NOT NULL")) == 2


def test_stale_page_or_double_tap_changes_nothing(client, make_user, query):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    punch(client, "idle")
    page = punch(client, "idle")  # second tap from the same old page
    assert "out of date" in page
    assert len(query("SELECT id FROM segments")) == 1


def test_submitted_day_is_locked(client, make_user, clock, query):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    punch(client, "idle")
    later(client, clock, "2026-10-20T09:00")
    punch(client, "in")
    client.post("/submit")
    assert "already submitted" in punch(client, "submitted")
    assert len(query("SELECT id FROM segments")) == 1


def test_cannot_submit_while_clocked_in(client, make_user, query):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    punch(client, "idle")
    page = client.post("/submit", follow_redirects=True).get_data(as_text=True)
    assert "Clock out before you submit" in page
    assert query("SELECT submitted_at FROM workdays")[0]["submitted_at"] is None


def test_note_is_capped_and_escaped(client, make_user, clock, query):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    punch(client, "idle")
    later(client, clock, "2026-10-20T09:00")
    punch(client, "in")
    page = client.post("/submit", data={"note": "<script>x</script>" + "a" * 600},
                       follow_redirects=True).get_data(as_text=True)
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page
    assert len(query("SELECT note FROM workdays")[0]["note"]) == 500


def test_unsubmitted_earlier_day_is_flagged_and_today_starts_fresh(client, make_user, clock):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    punch(client, "idle")  # Oct 20, never clocks out or submits
    clock.set("2026-10-21T08:00")
    sign_in_pin(client, "2468")
    page = client.get("/").get_data(as_text=True)
    assert "Not submitted: Tue Oct 20" in page and "Clock In" in page


def test_actions_need_a_signed_in_employee(client, make_user, query):
    make_user("Alice", pin="2468")
    page = punch(client, "idle")
    assert "Enter your PIN" in page
    assert query("SELECT id FROM segments") == []
