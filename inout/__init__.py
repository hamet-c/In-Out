"""In-Out: a local job time clock."""
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template

from . import admin, auth, clock, filters
from .auth import AttemptLimiter
from .db import close_db


def _secret_key(instance_path):
    """Signs session cookies. Made once and kept in instance/; losing it only signs everyone out."""
    path = Path(instance_path, "secret_key")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        DATABASE=str(Path(app.instance_path, "inout.db")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key(app.instance_path)

    app.extensions["inout_limiter"] = AttemptLimiter()
    app.teardown_appcontext(close_db)
    app.before_request(auth.check_csrf)
    app.before_request(auth.load_people)

    filters.register(app)
    app.jinja_env.globals["csrf_token"] = auth.csrf_token

    @app.context_processor
    def people():
        admin_user = g.get("admin")
        return {
            "current_employee": g.get("employee"),
            "current_admin": admin_user,
            "pending_count": auth.pending_count() if admin_user else 0,
            "admin_idle_seconds": auth.ADMIN_PAGE_IDLE_SECONDS,
        }

    messages = {400: "That request didn't look right. Go back and try again.",
                403: "You don't have access to that page.",
                404: "There's nothing at this address."}

    def error_page(e):
        return render_template("error.html", code=e.code, message=messages[e.code]), e.code

    for code in messages:
        app.register_error_handler(code, error_page)

    app.register_blueprint(auth.bp)
    app.register_blueprint(clock.bp)
    app.register_blueprint(admin.bp)
    return app
