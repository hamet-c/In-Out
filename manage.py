"""Setup commands.

    python manage.py init-db        create instance/inout.db (once)
    python manage.py create-admin   add an admin who can sign in to the admin pages
    python manage.py setup          guided setup of both of the above, plus Windows or Linux extras
    python manage.py migrate        upgrade the database after new code arrives
    python manage.py backup         copy the database to instance/backups/
    python manage.py update         pull the latest version from GitHub and apply it
"""
import argparse
import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from flask import current_app
from werkzeug.security import generate_password_hash

from inout import create_app, credentials, migrations, timesheet
from inout.db import backup, get_db, init_db, transaction


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


def cmd_migrate(_args):
    db = get_db()
    applied = migrations.upgrade(db)
    if applied:
        print("Upgraded the database:")
        for line in applied:
            print(f"  {line}")
    else:
        print(f"The database is up to date (version {migrations.version(db)}).")


def cmd_backup(_args):
    print(f"Backed up the database to {backup()}")


# ---------------------------------------------------------------- update from GitHub

ROOT = Path(__file__).resolve().parent


def _git(*args, capture=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=capture, text=True)


def _step(cmd):
    """Run a later step as a fresh process, so it uses the newly pulled code."""
    return subprocess.run(cmd, cwd=ROOT).returncode


def cmd_update(_args):
    """Behind "Update In-Out.bat": stop, back up, pull, install, upgrade the database, restart."""
    import run

    if shutil.which("git") is None or not (ROOT / ".git").exists():
        sys.exit("This folder wasn't installed with git, so it can't update itself.\n"
                 "See \"Updating\" in README.md.")
    edited = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if edited:
        sys.exit("Files in this folder were changed by hand, so updating could overwrite them:\n"
                 f"{edited}\nUndo those changes (git checkout -- .) or ask whoever made them.")

    print("Checking GitHub for a new version...")
    if _git("fetch", "--quiet", capture=False).returncode != 0:
        sys.exit("Couldn't reach GitHub. Check the internet connection and try again.")
    if _git("rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
        sys.exit("This copy isn't linked to a GitHub branch. See \"Updating\" in README.md.")
    incoming = _git("log", "--oneline", "HEAD..@{u}").stdout.strip()
    if not incoming:
        print("Already up to date.")
        return
    print("New changes:\n" + "\n".join(f"  {line}" for line in incoming.splitlines()))

    if run.is_running():
        print("\nStopping In-Out...")
        if run.stop_background() != 0:
            sys.exit("Couldn't stop In-Out, so nothing was changed.")

    saved = backup()
    print(f"Backed up the database to {saved}")
    old = _git("rev-parse", "--short", "HEAD").stdout.strip()

    restore_help = (
        "\nTo go back to the version that worked:\n"
        f"  1. Copy {saved} over {current_app.config['DATABASE']}\n"
        f"  2. In this folder run: git reset --hard {old}\n"
        f"  3. Run {run.START_SCRIPT}")

    if _git("pull", "--ff-only", "--quiet", capture=False).returncode != 0:
        print("Couldn't apply the update. Nothing was changed; restarting the current version.")
        _step([sys.executable, "run.py", "--background"])
        sys.exit(1)

    print("Installing components...")
    if _step([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
              "-r", "requirements.txt"]) != 0:
        sys.exit("Installing components failed." + restore_help)

    if _step([sys.executable, "manage.py", "migrate"]) != 0:
        sys.exit("Upgrading the database failed. It was left as it was." + restore_help)

    print("Starting In-Out...")
    if _step([sys.executable, "run.py", "--background"]) != 0:
        sys.exit("The new version didn't start." + restore_help)
    print(f"\nUpdated from {old} to {_git('rev-parse', '--short', 'HEAD').stdout.strip()}.")


def _yes(question, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{hint}] ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")


def cmd_setup(_args):
    """The guided setup behind "Setup In-Out.bat" and setup-inout.sh. Safe to run again;
    finished steps are skipped."""
    import run

    step = iter(range(1, 10))
    print("\n=== In-Out setup ===")

    print(f"\n{next(step)}. Database")
    db_path = Path(current_app.config["DATABASE"])
    if db_path.exists() and db_path.stat().st_size > 0:
        applied = migrations.upgrade(get_db())
        print("   Already set up." + (f" Upgraded: {'; '.join(applied)}" if applied else ""))
    else:
        init_db()
        print("   Created.")

    print(f"\n{next(step)}. Admin account")
    if get_db().execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
        print("   An admin already exists. (Add another with: manage.py create-admin)")
    else:
        print("   This is who signs in to the admin pages.")
        cmd_create_admin(None)

    if sys.platform == "win32":
        import wintools as tools
    elif sys.platform.startswith("linux"):
        import linuxtools as tools
    else:
        print("\nThe remaining steps are for Windows and Linux. Start In-Out with: python run.py")
        return

    print(f"\n{next(step)}. Let phones and other computers connect (firewall)")
    firewall = tools.active_firewall()
    if firewall is None:
        print("   No firewall is turned on, so there's nothing to open.")
    elif tools.firewall_rule_exists():
        print(f"   A firewall rule named \"{tools.FIREWALL_RULE}\" already exists.")
    elif _yes(f"   Add a rule to {firewall} now? {tools.ADMIN_PROMPT}"):
        if tools.add_firewall_rule(run.HTTP_PORT):
            print("   Added. Only devices on the local network can connect.")
        else:
            print(f"   Not added. To do it later, {tools.BY_HAND}")
            print("   " + tools.firewall_command(run.HTTP_PORT))

    print(f"\n{next(step)}. Start automatically")
    if _yes("   Start In-Out in the background whenever you log in to this computer?"):
        tools.set_autostart(True)
        print("   On.")
    else:
        tools.set_autostart(False)
        print("   Off.")

    print(f"\n{next(step)}. In-Out icon")
    if _yes(f"   {tools.ICON_QUESTION}"):
        print("   Added." if tools.create_desktop_shortcut() else "   Couldn't create it.")

    print(f"\n{next(step)}. Starting In-Out")
    run.start_background()
    print(f"\nSetup finished. Use the In-Out icon (or {run.START_SCRIPT}) to open it any time.")


def main():
    parser = argparse.ArgumentParser(description="In-Out setup commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="guided setup (used by Setup In-Out.bat and setup-inout.sh)").set_defaults(func=cmd_setup)
    sub.add_parser("init-db", help="create the database").set_defaults(func=cmd_init_db)
    sub.add_parser("migrate", help="upgrade the database").set_defaults(func=cmd_migrate)
    sub.add_parser("backup", help="back up the database").set_defaults(func=cmd_backup)
    sub.add_parser("update", help="update from GitHub (used by Update In-Out.bat)").set_defaults(func=cmd_update)
    sub.add_parser("create-admin", help="add an admin account").set_defaults(func=cmd_create_admin)
    args = parser.parse_args()
    with create_app().app_context():
        try:
            args.func(args)
        except RuntimeError as e:
            sys.exit(str(e))


if __name__ == "__main__":
    main()
