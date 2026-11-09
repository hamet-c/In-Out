"""Windows integration for "Setup In-Out.bat": open the firewall, start with the computer
(for the Windows kiosk) or at login, and put an icon on the desktop.

Every change can be undone. The firewall rule and the scheduled task show up under their
"In-Out" names in Windows, the start-at-login entry is one file in the Startup folder, and
the icon is a normal shortcut.
"""
import base64
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIREWALL_RULE = "In-Out"
TASK_NAME = "In-Out"  # the startup task, see "start with the computer" below
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


# ---------------------------------------------------------------- start with the computer (for the Windows kiosk)
#
# Windows' kiosk signs in a kiosk account that runs only Edge, so nothing in that account can
# start In-Out. Instead a scheduled task starts it when the computer starts, before anyone
# signs in, and the kiosk's Edge opens http://127.0.0.1:8000/.

KIOSK_SETTINGS = "ms-settings:otherusers"


def current_user():
    """DOMAIN\\name of whoever runs the setup."""
    return f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"


def boot_task_script(user, name=TASK_NAME):
    """PowerShell that registers the task. It runs In-Out as user, never as SYSTEM: anyone can
    edit the In-Out folder's files, so running them as SYSTEM would let them take over the computer."""
    pythonw = HERE / ".venv" / "Scripts" / "pythonw.exe"
    arguments = f'"{HERE / "run.py"}" --service'
    return (
        f"$user = {_ps_quote(user)};"
        "$sid = (New-Object System.Security.Principal.NTAccount($user))"
        ".Translate([System.Security.Principal.SecurityIdentifier]).Value;"
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(pythonw)} "
        f"-Argument {_ps_quote(arguments)} -WorkingDirectory {_ps_quote(HERE)};"
        "$trigger = New-ScheduledTaskTrigger -AtStartup;"
        # S4U: runs whether or not they're signed in, without storing their Windows password.
        "$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited;"
        # No time limit (by default tasks are stopped after 3 days), and keep going on battery.
        "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -Priority 5;"
        "$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings "
        "-Description 'Starts In-Out when the computer starts, for the Windows kiosk. Made by Setup In-Out.bat.';"
        # Let them start and remove it without the admin prompt (Start/Update In-Out.bat, setup).
        "$task.RegistrationInfo.SecurityDescriptor = \"D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;FA;;;$sid)\";"
        f"Register-ScheduledTask -TaskName {_ps_quote(name)} -InputObject $task -Force | Out-Null"
    )


def boot_task_exists(name=TASK_NAME):
    return subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0


def add_boot_task(name=TASK_NAME):
    """Only admins may add tasks that run at startup, so Windows asks for admin permission."""
    _powershell(boot_task_script(current_user(), name), elevated=True)
    return boot_task_exists(name)


def remove_boot_task(name=TASK_NAME):
    subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True)
    if boot_task_exists(name):  # someone else's task, say: ask for admin permission
        _powershell(f"Unregister-ScheduledTask -TaskName {_ps_quote(name)} -Confirm:$false", elevated=True)
    return not boot_task_exists(name)


def run_boot_task(name=TASK_NAME):
    """Start In-Out through the task, so it keeps running after this person signs out."""
    return subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True).returncode == 0


def end_boot_task(name=TASK_NAME):
    """Stop the In-Out the task started. Windows won't let its owner end that process directly
    (taskkill says "access denied"), but Task Scheduler ends it for anyone the task allows."""
    return subprocess.run(["schtasks", "/End", "/TN", name], capture_output=True).returncode == 0


def windows_edition():
    import winreg  # Windows only, and this module is also imported by the tests elsewhere

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            return winreg.QueryValueEx(key, "EditionID")[0]
    except OSError:
        return ""


def kiosk_supported(edition=None):
    """Windows' kiosk needs Pro, Enterprise or Education. Home editions are called "Core"."""
    edition = windows_edition() if edition is None else edition
    return not edition.startswith("Core")


def open_kiosk_settings():
    os.startfile(KIOSK_SETTINGS)
