"""Setup commands.

    python manage.py init-db        create instance/inout.db (once)
    python manage.py create-admin   add an admin who can sign in to the admin pages
    python manage.py make-certs     set up HTTPS (again after the server's IP changes)
"""
import argparse
import getpass
import sys

from flask import current_app
from werkzeug.security import generate_password_hash

from inout import certs, create_app, credentials, timesheet
from inout.db import get_db, init_db, transaction


def cmd_init_db(_args):
    init_db()
    print("Created the database.")


def _hidden(prompt, getch=None):
    """Read a secret, printing * for each character typed. Backspace erases, Enter ends.

    Needs the Windows console (msvcrt). Anywhere else it falls back to getpass,
    which hides typing completely.
    """
    if getch is None:
        try:
            import msvcrt
        except ImportError:
            return getpass.getpass(prompt)
        if not sys.stdin.isatty():
            return getpass.getpass(prompt)
        getch = msvcrt.getwch
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        ch = getch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            return "".join(chars)
        if ch == "\x03":  # Ctrl+C
            sys.stdout.write("\n")
            raise KeyboardInterrupt
        if ch == "\b":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
        elif ch in ("\x00", "\xe0"):  # arrow and function keys arrive as two codes
            getch()
            continue
        elif ch.isprintable():
            chars.append(ch)
            sys.stdout.write("*")
        sys.stdout.flush()


def _ask(prompt, check):
    while True:
        value = input(prompt).strip()
        problem = check(value)
        if not problem:
            return value
        print(f"  {problem}")


def cmd_create_admin(_args):
    db = get_db()
    name = _ask("Full name: ", lambda v: None if v else "Required.")
    username = _ask("Username (for the admin sign-in): ", lambda v: (
        "Required." if not v else
        "Already taken." if db.execute("SELECT 1 FROM users WHERE username = ?", (v.lower(),)).fetchone()
        else None)).lower()
    while True:
        password = _hidden("Password, 8+ characters: ")
        if len(password) < 8:
            print("  Too short.")
        elif password != _hidden("Password again: "):
            print("  Didn't match.")
        else:
            break
    pin = _hidden("Clock-in PIN, 4 digits (press Enter to skip): ").strip()
    while pin and credentials.pin_problem(pin):
        print(f"  {credentials.pin_problem(pin)}")
        pin = _hidden("Clock-in PIN (or Enter to skip): ").strip()

    at = timesheet.now()
    try:
        with transaction(db):
            user_id = db.execute(
                "INSERT INTO users (display_name, username, password_hash, is_admin, status, created_at) "
                "VALUES (?, ?, ?, 1, 'active', ?)",
                (name, username, generate_password_hash(password), at.isoformat())).lastrowid
            if pin:
                credentials.set_secret(user_id, "pin", pin, at)
    except credentials.CredentialTaken:
        sys.exit("Someone else already has that PIN. Nothing was saved; run this again.")
    print(f"Admin {username} created.")


def cmd_make_certs(args):
    p = certs.paths(current_app.instance_path)
    _, _, created = certs.ensure_ca(p)
    try:
        ips = certs.issue_server_cert(p, certs.local_ips() + args.ip)
    except ValueError as e:
        sys.exit(str(e))
    if created:
        print("Created the In-Out certificate authority. Each device needs to trust it once.")
    else:
        print("Reused the existing certificate authority. Devices that already trust it need no changes.")
    print("Server certificate covers: " + ", ".join(ips))
    print("Restart run.py to use it. Keep instance/certs/ca.key private and backed up.")


def main():
    parser = argparse.ArgumentParser(description="In-Out setup commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the database").set_defaults(func=cmd_init_db)
    sub.add_parser("create-admin", help="add an admin account").set_defaults(func=cmd_create_admin)
    certs_cmd = sub.add_parser("make-certs", help="set up HTTPS for this computer's addresses")
    certs_cmd.add_argument("--ip", action="append", default=[], metavar="ADDRESS",
                           help="also include this address (repeatable), e.g. the server's reserved IP")
    certs_cmd.set_defaults(func=cmd_make_certs)
    args = parser.parse_args()
    with create_app().app_context():
        try:
            args.func(args)
        except RuntimeError as e:
            sys.exit(str(e))


if __name__ == "__main__":
    main()
