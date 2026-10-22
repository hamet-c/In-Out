from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from inout import create_app, credentials, timesheet
from inout.db import get_db, init_db, transaction


class FakeClock:
    """Stands in for timesheet.now() so tests control the time."""

    def __init__(self):
        self.value = datetime(2026, 10, 20, 8, 0)

    def set(self, text):
        self.value = datetime.fromisoformat(text)

    def __call__(self):
        return self.value


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(timesheet, "now", fake)
    return fake


@pytest.fixture
def app(tmp_path, clock):
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "CSRF_CHECK": False,
                      "DATABASE": str(tmp_path / "test.db")})
    with app.app_context():
        init_db()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app, clock):
    """make_user("Alice", pin="2468") -> user id. Admins also get username + password."""

    def make(name, pin=None, status="active", admin=None):
        with app.app_context():
            with transaction():
                user_id = get_db().execute(
                    "INSERT INTO users (display_name, username, password_hash, is_admin, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, admin, generate_password_hash("adminpass") if admin else None,
                     int(bool(admin)), status, clock().isoformat())).lastrowid
                if pin:
                    credentials.set_secret(user_id, "pin", pin, clock())
        return user_id

    return make


@pytest.fixture
def query(app):
    def run(sql, *args):
        with app.app_context():
            return [dict(r) for r in get_db().execute(sql, args).fetchall()]
    return run


def sign_in_pin(client, pin):
    return client.post("/pin", data={"pin": pin}, follow_redirects=True)


def sign_in_admin(client, username="boss", password="adminpass"):
    return client.post("/admin/login", data={"username": username, "password": password})
