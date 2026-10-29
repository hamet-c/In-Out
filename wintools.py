"""Windows integration for "Setup In-Out.bat": open the firewall, start at login and
put an icon on the desktop.

Every change can be undone. The firewall rule shows up under its "In-Out" name in
Windows, the auto-start entry is one file in the Startup folder, and the icon is a
normal shortcut.
"""
import base64
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIREWALL_RULE = "In-Out"
PRIVATE_RANGES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

# Wording for the setup wizard (linuxtools has its own).
ADMIN_PROMPT = "Windows will ask for admin permission."
BY_HAND = "run this in an admin PowerShell:"
ICON_QUESTION = "Put an In-Out icon on the desktop?"


def _powershell(script, elevated=False):
    """Run a PowerShell script. elevated=True shows the Windows admin prompt for it."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    if elevated:
        outer = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                 f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'")
        encoded = base64.b64encode(outer.encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                          capture_output=True, text=True)


def _ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


# ---------------------------------------------------------------- firewall

def active_firewall():
    """The firewall in the way. Windows always has one."""
    return "Windows Firewall"


def firewall_rule_exists():
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def firewall_command(port):
    """The rule, as a command people can also run by hand. Only private (local network)
    addresses may connect, never the internet."""
    return (f"New-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -Direction Inbound -Protocol TCP "
            f"-LocalPort {port} -Action Allow -Profile Any "
            f"-RemoteAddress {PRIVATE_RANGES}")


def add_firewall_rule(port):
    _powershell(firewall_command(port), elevated=True)
    return firewall_rule_exists()


# ---------------------------------------------------------------- auto-start and desktop icon

def startup_file(startup_dir=None):
    folder = Path(startup_dir) if startup_dir else Path(
        os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    return folder / "In-Out.cmd"


def set_autostart(enabled, startup_dir=None):
    """Start In-Out in the background whenever this Windows user logs in."""
    path = startup_file(startup_dir)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    pythonw = HERE / ".venv" / "Scripts" / "pythonw.exe"
    path.write_text(
        "@echo off\r\n"
        "rem Starts In-Out at login. Delete this file to stop that.\r\n"
        f'start "" "{pythonw}" "{HERE / "run.py"}" --background\r\n',
        encoding="utf-8",
    )


def autostart_enabled(startup_dir=None):
    return startup_file(startup_dir).exists()


def create_desktop_shortcut(folder=None):
    """Put an "In-Out" shortcut on the desktop (or in folder) that runs Start In-Out.bat."""
    target = HERE / "Start In-Out.bat"
    where = _ps_quote(folder) if folder else "[Environment]::GetFolderPath('Desktop')"
    script = (
        f"$desktop = {where};"
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'In-Out.lnk'));"
        f"$s.TargetPath = {_ps_quote(target)};"
        f"$s.WorkingDirectory = {_ps_quote(HERE)};"
        "$s.WindowStyle = 7;"  # minimized: the start script's window barely flashes
        "$s.IconLocation = '%SystemRoot%\\System32\\timedate.cpl,0';"
        "$s.Description = 'Start In-Out and open the clock';"
        "$s.Save();"
        "Test-Path (Join-Path $desktop 'In-Out.lnk')"
    )
    return "True" in _powershell(script).stdout
