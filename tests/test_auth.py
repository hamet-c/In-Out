from datetime import timedelta

import pytest

from inout import credentials
from .conftest import sign_in_admin, sign_in_pin


def test_pin_rules():
    assert credentials.pin_problem("2468") is None
    assert credentials.pin_problem("246") is not None
    assert credentials.pin_problem("24a8") is not None
    assert credentials.pin_problem("1234") is not None  # too easy
    assert credentials.pin_problem("7777") is not None
    assert credentials.pin_problem("2468", "2469") is not None


def test_pin_is_stored_hashed_and_unique(app, make_user, query):
    make_user("Alice", pin="2468")
    row = query("SELECT secret_hash FROM credentials")[0]
    assert "2468" not in row["secret_hash"] and len(row["secret_hash"]) == 64
    with pytest.raises(credentials.CredentialTaken):
        make_user("Bob", pin="2468")


def test_correct_pin_shows_that_persons_clock(client, make_user):
    make_user("Alice", pin="2468")
    page = sign_in_pin(client, "2468").get_data(as_text=True)
    assert "Hi, Alice" in page and "Clock In" in page


def test_wrong_pin_stays_on_pin_pad(client, make_user):
    make_user("Alice", pin="2468")
    page = sign_in_pin(client, "1357").get_data(as_text=True)
    assert "didn&#39;t match anyone" in page and "Enter your PIN" in page


@pytest.mark.parametrize("status, message", [("pending", "waiting for an admin"), ("disabled", "disabled")])
def test_pending_and_disabled_people_cannot_clock_in(client, make_user, status, message):
    make_user("Alice", pin="2468", status=status)
    page = sign_in_pin(client, "2468").get_data(as_text=True)
    assert message in page and "Hi, Alice" not in page


def test_lockout_after_five_wrong_pins_and_a_right_pin_does_not_reset_it(client, make_user, clock):
    make_user("Alice", pin="2468")
    for _ in range(4):
        sign_in_pin(client, "1357")
    assert "Hi, Alice" in sign_in_pin(client, "2468").get_data(as_text=True)
    client.post("/done")
    sign_in_pin(client, "1357")  # fifth wrong try
    page = sign_in_pin(client, "2468").get_data(as_text=True)
    assert "Too many wrong tries" in page and "Hi, Alice" not in page
    clock.value += timedelta(minutes=6)
    assert "Hi, Alice" in sign_in_pin(client, "2468").get_data(as_text=True)


def test_employee_is_signed_out_after_idling(client, make_user, clock):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    clock.value += timedelta(minutes=11)
    assert "Enter your PIN" in client.get("/").get_data(as_text=True)


def test_done_signs_out(client, make_user):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    page = client.post("/done", follow_redirects=True).get_data(as_text=True)
    assert "Enter your PIN" in page


def test_register_creates_a_pending_person_with_their_pin(client, query):
    r = client.post("/register", data={"display_name": "  Cara   Osei ", "pin": "5824", "confirm": "5824"})
    assert r.status_code == 302
    assert query("SELECT display_name, status FROM users") == [{"display_name": "Cara Osei", "status": "pending"}]
    assert "waiting for an admin" in sign_in_pin(client, "5824").get_data(as_text=True)


def test_register_with_a_taken_pin_counts_as_a_wrong_guess(client, make_user, query):
    make_user("Alice", pin="2468")
    for i in range(5):
        r = client.post("/register", data={"display_name": f"X{i}", "pin": "2468", "confirm": "2468"})
        assert r.status_code == 400
    assert len(query("SELECT id FROM users")) == 1
    assert "Too many wrong tries" in sign_in_pin(client, "2468").get_data(as_text=True)


def test_admin_pages_need_the_admin_password(client, make_user):
    make_user("Dana", pin="9157", admin="boss")
    assert client.get("/admin/").status_code == 302
    sign_in_pin(client, "9157")  # a PIN alone never opens admin pages
    assert client.get("/admin/").status_code == 302
    assert sign_in_admin(client, password="wrong").status_code == 200
    assert sign_in_admin(client).status_code == 302
    assert client.get("/admin/").status_code == 200


def test_admin_login_only_redirects_to_local_pages(client, make_user):
    make_user("Dana", admin="boss")
    r = client.post("/admin/login?next=//evil.example/x", data={"username": "boss", "password": "adminpass"})
    assert r.headers["Location"] == "/admin/"


def test_csrf_token_is_required_when_enabled(app, client, make_user):
    app.config["CSRF_CHECK"] = True
    make_user("Alice", pin="2468")
    assert client.post("/pin", data={"pin": "2468"}).status_code == 400
    client.get("/")
    with client.session_transaction() as s:
        token = s["csrf"]
    assert client.post("/pin", data={"pin": "2468", "csrf_token": token}).status_code == 302


def test_background_sign_out_is_accepted(app, client, make_user):
    # The clock page's idle timer posts the Done form with fetch(new FormData(form)),
    # which arrives as multipart. It must pass CSRF and land on the PIN pad.
    app.config["CSRF_CHECK"] = True
    make_user("Alice", pin="2468")
    client.get("/")
    with client.session_transaction() as s:
        token = s["csrf"]
    client.post("/pin", data={"pin": "2468", "csrf_token": token})
    r = client.post("/done", data={"csrf_token": token}, content_type="multipart/form-data",
                    follow_redirects=True)
    assert r.status_code == 200 and "Enter your PIN" in r.get_data(as_text=True)


def test_entering_a_pin_signs_the_admin_out(client, make_user):
    # An admin leaves the shared screen signed in; the next person enters their own PIN.
    make_user("Dana", admin="boss")
    make_user("Alice", pin="2468")
    sign_in_admin(client)
    assert client.get("/admin/").status_code == 200
    page = sign_in_pin(client, "2468").get_data(as_text=True)
    assert "Hi, Alice" in page and "Signed out of the admin account" in page
    assert "Timesheets" not in page
    assert client.get("/admin/").status_code == 302  # back to the admin sign-in


def test_signing_in_as_admin_signs_the_employee_out(client, make_user):
    make_user("Dana", admin="boss")
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    sign_in_admin(client)
    assert "Enter your PIN" in client.get("/").get_data(as_text=True)


def test_wrong_pin_does_not_end_the_admin_session(client, make_user):
    make_user("Dana", admin="boss")
    sign_in_admin(client)
    sign_in_pin(client, "1357")
    assert client.get("/admin/").status_code == 200


def test_admin_is_signed_out_after_idling(client, make_user, clock):
    make_user("Dana", admin="boss")
    sign_in_admin(client)
    page = client.get("/admin/").get_data(as_text=True)
    assert 'data-idle-signout="300"' in page and "data-signout-form" in page
    clock.value += timedelta(minutes=11)
    assert client.get("/admin/").status_code == 302
