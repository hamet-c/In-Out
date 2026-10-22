"""Windows integration for "Setup In-Out.bat": trust the certificate on this computer,
open the firewall, start at login and put an icon on the desktop.

Every change can be undone. The firewall rule and the certificate show up under their
"In-Out" names in Windows, the auto-start entry is one file in the Startup folder, and
the icon is a normal shortcut.
"""
import base64
import os
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

HERE = Path(__file__).resolve().parent
FIREWALL_RULE = "In-Out"
PRIVATE_RANGES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


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


# ---------------------------------------------------------------- certificate trust

def _thumbprint(ca_cert_path):
    cert = x509.load_pem_x509_certificate(Path(ca_cert_path).read_bytes())
    return cert.fingerprint(hashes.SHA1()).hex().upper()


def ca_is_trusted(ca_cert_path):
    """Whether this Windows user already trusts the In-Out certificate authority."""
    r = subprocess.run(["certutil", "-user", "-store", "Root", _thumbprint(ca_cert_path)],
                       capture_output=True, text=True)
    return r.returncode == 0


def trust_ca(ca_cert_path):
    """Add the CA to this user's trusted roots. Windows asks the user to confirm."""
    subprocess.run(["certutil", "-user", "-addstore", "Root", str(ca_cert_path)],
                   capture_output=True, text=True)
    return ca_is_trusted(ca_cert_path)


# ---------------------------------------------------------------- firewall

def firewall_rule_exists():
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def firewall_command(http_port, https_port):
    """The rule, as a command people can also run by hand. Only private (local network)
    addresses may connect, never the internet."""
    return (f"New-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -Direction Inbound -Protocol TCP "
            f"-LocalPort {http_port},{https_port} -Action Allow -Profile Any "
            f"-RemoteAddress {PRIVATE_RANGES}")


def add_firewall_rule(http_port, https_port):
    _powershell(firewall_command(http_port, https_port), elevated=True)
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
