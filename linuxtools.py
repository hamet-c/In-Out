"""Linux integration for setup-inout.sh: open the firewall, start at login and put an
icon in the app menu and on the desktop.

Every change can be undone. The ufw rules carry an "In-Out" comment, the auto-start
entry is one file in ~/.config/autostart, and the icons are normal .desktop files.
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIREWALL_RULE = "In-Out"
PRIVATE_RANGES = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
UFW_CONF = Path("/etc/ufw/ufw.conf")

# Wording for the setup wizard (wintools has its own).
ADMIN_PROMPT = "It will ask for your password (sudo)."
BY_HAND = "run this in a terminal:"
ICON_QUESTION = "Add an In-Out icon to the app menu and the desktop?"


def _exec(*args):
    """An Exec= value that runs args, quoted as the Desktop Entry spec asks, so any folder name works."""
    quoted = []
    for arg in map(str, args):
        for ch in '\\"`$':
            arg = arg.replace(ch, "\\" + ch)
        quoted.append(f'"{arg}"')
    # Exec is also a string value, which escapes backslashes once more, and % starts a field code.
    return " ".join(quoted).replace("\\", "\\\\").replace("%", "%%")


def _xdg_dir(variable, default):
    return Path(os.environ.get(variable) or Path.home() / default)


# ---------------------------------------------------------------- firewall

def active_firewall():
    """"ufw" or "firewalld" if one is turned on. None means nothing blocks In-Out."""
    try:
        if any(line.strip() == "ENABLED=yes" for line in UFW_CONF.read_text().splitlines()):
            return "ufw"
    except OSError:  # ufw isn't installed
        pass
    if shutil.which("firewall-cmd"):
        if subprocess.run(["firewall-cmd", "--state"], capture_output=True).returncode == 0:
            return "firewalld"
    return None


def firewall_rule_exists():
    """Reading the rules needs root, so this can't tell. Adding them again is harmless."""
    return False


def _firewall_commands(port, firewall):
    if firewall == "firewalld":
        rules = [f'rule family="ipv4" source address="{net}" port port="{port}" protocol="tcp" accept'
                 for net in PRIVATE_RANGES]
        return ([["sudo", "firewall-cmd", "--permanent", f"--add-rich-rule={rule}"] for rule in rules]
                + [["sudo", "firewall-cmd", "--reload"]])
    return [["sudo", "ufw", "allow", "from", net, "to", "any", "port", str(port), "proto", "tcp",
             "comment", FIREWALL_RULE] for net in PRIVATE_RANGES]


def firewall_command(port, firewall=None):
    """The rules, as commands people can also run by hand. Only private (local network)
    addresses may connect, never the internet."""
    commands = _firewall_commands(port, firewall or active_firewall() or "ufw")
    return " && ".join(shlex.join(c) for c in commands)


def add_firewall_rule(port):
    """sudo asks for the password in this terminal."""
    commands = _firewall_commands(port, active_firewall() or "ufw")
    try:
        return all(subprocess.run(c).returncode == 0 for c in commands)
    except OSError:  # no sudo
        return False


# ---------------------------------------------------------------- auto-start and icons

def startup_file(startup_dir=None):
    folder = Path(startup_dir) if startup_dir else _xdg_dir("XDG_CONFIG_HOME", ".config") / "autostart"
    return folder / "In-Out.desktop"


def set_autostart(enabled, startup_dir=None):
    """Start In-Out in the background whenever this user logs in to the desktop."""
    path = startup_file(startup_dir)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=In-Out\n"
        "Comment=Starts In-Out at login. Delete this file to stop that.\n"
        f"Exec={_exec(HERE / '.venv' / 'bin' / 'python', HERE / 'run.py', '--background')}\n"
        "Terminal=false\n"
        "NoDisplay=true\n",
        encoding="utf-8",
    )


def autostart_enabled(startup_dir=None):
    return startup_file(startup_dir).exists()


def _launcher():
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=In-Out\n"
        "Comment=Start In-Out and open the clock\n"
        f"Exec={_exec('bash', HERE / 'start-inout.sh')}\n"
        f"Icon={HERE / 'inout' / 'static' / 'favicon.svg'}\n"
        # The start script's window closes by itself, or stays open to show what went wrong.
        "Terminal=true\n"
        "Categories=Office;\n"
        "Actions=stop;\n"
        "\n"
        "[Desktop Action stop]\n"
        "Name=Stop In-Out\n"
        f"Exec={_exec('bash', HERE / 'stop-inout.sh')}\n"
    )


def _desktop_dir():
    """The desktop folder, which may have another name (in other languages, say). None if there's none."""
    try:
        found = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
    except OSError:
        found = ""
    folder = Path(found) if found else Path.home() / "Desktop"
    # xdg-user-dir answers with the home folder itself when there's no desktop folder.
    return folder if folder.is_dir() and folder != Path.home() else None


def create_desktop_shortcut(folder=None, menu_dir=None):
    """Put an "In-Out" icon that runs start-inout.sh in the app menu, and on the desktop
    (or in folder) if there is one. Right-clicking it also offers "Stop In-Out"."""
    menu = Path(menu_dir) if menu_dir else _xdg_dir("XDG_DATA_HOME", ".local/share") / "applications"
    desktop = Path(folder) if folder else _desktop_dir()
    paths = [menu / "In-Out.desktop"] + ([desktop / "In-Out.desktop"] if desktop else [])
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_launcher(), encoding="utf-8")
        path.chmod(0o755)  # desktops only run launchers marked executable
    if desktop:
        try:  # GNOME also wants the desktop copy marked trusted; elsewhere this does nothing
            subprocess.run(["gio", "set", str(paths[-1]), "metadata::trusted", "true"], capture_output=True)
        except OSError:
            pass
    return all(p.exists() for p in paths)
