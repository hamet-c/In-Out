import pytest

from inout import nfc
from .conftest import sign_in_admin, sign_in_pin

CARD = "1D0EDCDB091080"
LAN = {"REMOTE_ADDR": "192.168.1.20"}


@pytest.fixture
def reader(app):
    """The app's card reader, never started: tests tap cards with reader.tapped(uid)."""
    return app.extensions["inout_cards"]


@pytest.fixture
def monotonic(monkeypatch):
    """Controls the clock the reader uses to decide whether a tap is still fresh."""
    now = [1000.0]
    monkeypatch.setattr(nfc.time, "monotonic", lambda: now[0])
    return now


def wait_for_card(client, after=0, path="/card"):
    """What app.js does twice a second: 204 until a card is tapped, then where to go."""
    return client.post(path, data={"after": after})


# ---------------------------------------------------------------- the reader itself

def test_reader_hands_out_only_new_fresh_taps(monotonic):
    reader = nfc.CardReader()
    assert reader.seq == 0 and reader.tap_after(0) is None
    reader.tapped(CARD)
    assert reader.tap_after(0).uid == CARD
    assert reader.tap_after(1) is None  # a page drawn after this tap doesn't use it
    monotonic[0] += nfc.FRESH_SECONDS + 1
    assert reader.tap_after(0) is None  # nobody is standing there any more
    reader.tapped("AABBCCDD")
    assert reader.seq == 2 and reader.tap_after(1).uid == "AABBCCDD"


def test_reader_reports_why_it_is_not_ready():
    reader = nfc.CardReader()
    assert not reader.ready and "isn't running" in reader.problem


# ---------------------------------------------------------------- station sign-in

def test_tapping_a_linked_card_signs_in(client, make_user, reader):
    make_user("Alice", card=CARD)
    assert wait_for_card(client).status_code == 204
    reader.tapped(CARD)
    r = wait_for_card(client)
    assert r.get_json() == {"next": "/"}
    assert "Hi, Alice" in client.get("/").get_data(as_text=True)


def test_pin_pad_waits_for_taps_newer_than_itself(client, make_user, reader):
    make_user("Alice", card=CARD)
    reader.tapped(CARD)  # tapped before the page was drawn
    page = client.get("/").get_data(as_text=True)
    assert 'name="after" value="1"' in page
    assert wait_for_card(client, after=1).status_code == 204


def test_pin_pad_says_tap_only_when_the_reader_is_ready(client, reader):
    assert "Tap your card" not in client.get("/").get_data(as_text=True)
    reader.name, reader.problem = "ACS ACR122U PICC Interface 0", None
    assert "Tap your card or enter your PIN" in client.get("/").get_data(as_text=True)


def test_other_computers_cannot_use_the_reader(app, make_user, reader):
    # The reader is on the server. A laptop on the Wi-Fi must not be signed in as
    # whoever taps at the station.
    make_user("Alice", card=CARD)
    make_user("Bob", pin="3579")
    laptop = app.test_client()
    assert "data-card-form" not in laptop.get("/", environ_base=LAN).get_data(as_text=True)
    reader.tapped(CARD)
    assert laptop.post("/card", data={"after": 0}, environ_base=LAN).status_code == 403
    assert "Hi, Alice" not in laptop.get("/", environ_base=LAN).get_data(as_text=True)
    laptop.post("/pin", data={"pin": "3579"}, environ_base=LAN)
    page = laptop.get("/", environ_base=LAN).get_data(as_text=True)
    assert "Hi, Bob" in page and "data-card-form" not in page


def test_unknown_cards_say_so_and_never_lock_the_station(client, make_user, reader):
    make_user("Alice", pin="2468")
    for i in range(6):
        reader.tapped(f"BANKCARD{i}")
        wait_for_card(client, after=i)
    page = client.get("/").get_data(as_text=True)
    assert "isn&#39;t linked to anyone" in page
    assert "Hi, Alice" in sign_in_pin(client, "2468").get_data(as_text=True)


def test_cards_still_work_after_wrong_pins_lock_the_pin_pad(client, make_user, reader):
    make_user("Alice", card=CARD)
    for _ in range(5):
        sign_in_pin(client, "1357")
    assert "Too many wrong tries" in sign_in_pin(client, "1357").get_data(as_text=True)
    reader.tapped(CARD)
    wait_for_card(client)
    assert "Hi, Alice" in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("status, message", [("pending", "waiting for an admin"), ("disabled", "disabled")])
def test_pending_and_disabled_people_cannot_tap_in(client, make_user, reader, status, message):
    make_user("Alice", card=CARD, status=status)
    reader.tapped(CARD)
    wait_for_card(client)
    page = client.get("/").get_data(as_text=True)
    assert message in page and "Hi, Alice" not in page


@pytest.mark.parametrize("first_by_card", [True, False])
def test_next_persons_tap_takes_over_from_someone_who_forgot_done(client, make_user, reader, first_by_card):
    make_user("Alice", pin="2468", card="AAAA0001")
    make_user("Bob", card="BBBB0002")
    if first_by_card:
        reader.tapped("AAAA0001")
        wait_for_card(client)
    else:
        sign_in_pin(client, "2468")
    page = client.get("/").get_data(as_text=True)
    assert "Hi, Alice" in page and "data-card-form" in page  # her page listens for the next tap
    after = reader.seq
    reader.tapped("BBBB0002")
    assert wait_for_card(client, after=after).get_json() == {"next": "/"}
    assert "Hi, Bob" in client.get("/").get_data(as_text=True)


def test_own_card_again_leaves_the_page_alone(client, make_user, reader):
    # Also what happens when the reader reports one quick tap twice.
    make_user("Alice", card=CARD)
    reader.tapped(CARD)
    wait_for_card(client)
    reader.tapped(CARD)
    assert wait_for_card(client, after=1).status_code == 204
    assert "Hi, Alice" in client.get("/").get_data(as_text=True)


def test_unknown_card_still_signs_out_whoever_was_there(client, make_user, reader):
    make_user("Alice", pin="2468")
    sign_in_pin(client, "2468")
    reader.tapped("FFFF0000")
    wait_for_card(client)
    page = client.get("/").get_data(as_text=True)
    assert "Hi, Alice" not in page and "isn&#39;t linked to anyone" in page


def test_tapping_a_card_signs_the_admin_out(client, make_user, reader):
    make_user("Dana", admin="boss")
    make_user("Alice", card=CARD)
    sign_in_admin(client)
    reader.tapped(CARD)
    wait_for_card(client)
    assert "Hi, Alice" in client.get("/").get_data(as_text=True)
    assert client.get("/admin/").status_code == 302


# ---------------------------------------------------------------- admin: linking cards

@pytest.fixture
def admin_client(client, make_user):
    make_user("Dana", admin="boss")
    sign_in_admin(client)
    return client


def link_path(user_id):
    return f"/admin/users/{user_id}/card"


def test_admin_links_a_card_by_tapping_it(admin_client, app, make_user, reader, query):
    bob = make_user("Bob", pin="3579")
    page = admin_client.get(link_path(bob)).get_data(as_text=True)
    assert "Link a card to Bob" in page
    assert wait_for_card(admin_client, path=link_path(bob)).status_code == 204
    reader.tapped(CARD)
    assert wait_for_card(admin_client, path=link_path(bob)).get_json() == {"next": "/admin/users"}
    users = admin_client.get("/admin/users").get_data(as_text=True)
    assert "Linked the card to Bob" in users and "Replace card" in users
    assert query("SELECT detail FROM audit_log WHERE action = 'link_card'") == [{"detail": "Linked a card to Bob"}]

    station = app.test_client()
    reader.tapped(CARD)
    wait_for_card(station, after=1)
    assert "Hi, Bob" in station.get("/").get_data(as_text=True)


def test_linking_a_new_card_replaces_the_old_one(admin_client, make_user, reader, query):
    bob = make_user("Bob", card=CARD)
    reader.tapped("AABBCCDD")
    wait_for_card(admin_client, path=link_path(bob))
    assert len(query("SELECT id FROM credentials WHERE kind = 'nfc'")) == 1
    reader.tapped(CARD)
    wait_for_card(admin_client)  # signing in with the old card no longer works
    assert "isn&#39;t linked" in admin_client.get("/").get_data(as_text=True)


def test_a_card_is_never_taken_from_someone_else(admin_client, make_user, reader, query):
    make_user("Alice", card=CARD)
    bob = make_user("Bob", pin="3579")
    reader.tapped(CARD)
    r = wait_for_card(admin_client, path=link_path(bob))
    assert r.get_json() == {"next": link_path(bob)}
    page = admin_client.get(link_path(bob)).get_data(as_text=True)
    assert "already linked to Alice" in page
    assert query("SELECT u.display_name FROM credentials c JOIN users u ON u.id = c.user_id "
                 "WHERE c.kind = 'nfc'") == [{"display_name": "Alice"}]


def test_admin_removes_a_card(admin_client, make_user, reader, query):
    bob = make_user("Bob", pin="3579", card=CARD)
    r = admin_client.post(f"/admin/users/{bob}", data={"action": "remove_card"}, follow_redirects=True)
    assert "Removed Bob&#39;s card" in r.get_data(as_text=True)
    assert query("SELECT kind FROM credentials WHERE user_id = ?", bob) == [{"kind": "pin"}]


def test_linking_cards_needs_an_admin(client, app, make_user, reader):
    bob = make_user("Bob", pin="3579")
    assert client.get(link_path(bob)).status_code == 302
    reader.tapped(CARD)
    assert wait_for_card(client, path=link_path(bob)).status_code == 302
    sign_in_pin(client, "3579")  # an employee isn't enough either
    assert client.get(link_path(bob)).status_code == 302


def test_cannot_link_a_card_to_a_pending_request(admin_client, make_user):
    cara = make_user("Cara", pin="5824", status="pending")
    assert admin_client.get(link_path(cara)).status_code == 404
