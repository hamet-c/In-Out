"""Setup commands.

    python manage.py init-db        create instance/inout.db (once)
    python manage.py create-admin   add an admin who can sign in to the admin pages
    python manage.py make-certs     set up HTTPS (again after the server's IP changes)
    python manage.py setup          guided setup of all of the above, plus Windows extras
"""
import argparse
import getpass
import sys
from pathlib import Path

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


def _yes(question, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{hint}] ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")


def cmd_setup(_args):
    """The guided setup behind "Setup In-Out.bat". Safe to run again; finished steps are skipped."""
    import run
    import wintools

    step = iter(range(1, 10))
    print("\n=== In-Out setup ===")

    print(f"\n{next(step)}. Database")
    db_path = Path(current_app.config["DATABASE"])
    if db_path.exists() and db_path.stat().st_size > 0:
        print("   Already set up.")
    else:
        init_db()
        print("   Created.")

    print(f"\n{next(step)}. Admin account")
    if get_db().execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
        print("   An admin already exists. (Add another with: manage.py create-admin)")
    else:
        print("   This is who signs in to the admin pages.")
        cmd_create_admin(None)

    print(f"\n{next(step)}. Secure connection (HTTPS)")
    cmd_make_certs(argparse.Namespace(ip=[]))

    if sys.platform != "win32":
        print("\nThe remaining steps are for Windows. Start In-Out with: python run.py")
        return

    ca = certs.paths(current_app.instance_path).ca_cert
    print(f"\n{next(step)}. Trust the certificate on this computer")
    if wintools.ca_is_trusted(ca):
        print("   Already trusted.")
    elif _yes("   Trust it now, so this computer's browser shows a padlock? Windows will ask you to confirm."):
        print("   Done." if wintools.trust_ca(ca) else "   Not added. You can do it later from the setup page.")

    print(f"\n{next(step)}. Let phones and other computers connect (Windows Firewall)")
    if wintools.firewall_rule_exists():
        print(f"   A firewall rule named \"{wintools.FIREWALL_RULE}\" already exists.")
    elif _yes("   Add the rule now? Windows will ask for admin permission."):
        if wintools.add_firewall_rule(run.HTTP_PORT, run.HTTPS_PORT):
            print("   Added. Only devices on the local network can connect.")
        else:
            print("   Not added. To do it later, run this in an admin PowerShell:")
            print("   " + wintools.firewall_command(run.HTTP_PORT, run.HTTPS_PORT))

    print(f"\n{next(step)}. Start automatically")
    if _yes("   Start In-Out in the background whenever you log in to this computer?"):
        wintools.set_autostart(True)
        print("   On.")
    else:
        wintools.set_autostart(False)
        print("   Off.")

    print(f"\n{next(step)}. Desktop icon")
    if _yes("   Put an In-Out icon on the desktop?"):
        print("   Added." if wintools.create_desktop_shortcut() else "   Couldn't create it.")

    print(f"\n{next(step)}. Starting In-Out")
    if run.is_running():
        run.stop_background()  # restart so it uses the fresh certificate
    run.start_background()
    print("\nSetup finished. Use the In-Out icon (or \"Start In-Out.bat\") to open it any time.")


def main():
    parser = argparse.ArgumentParser(description="In-Out setup commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="guided setup (used by Setup In-Out.bat)").set_defaults(func=cmd_setup)
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
