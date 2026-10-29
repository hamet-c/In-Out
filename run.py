"""Start In-Out for the local network.

    python run.py                 run in this window (Ctrl+C stops it)
    python run.py --background    run with no window; log in instance/server.log
    python run.py --stop          stop the background server
    python run.py --open          open In-Out in this computer's browser

"Start In-Out.bat" and "Stop In-Out.bat" use these (start-inout.sh and stop-inout.sh on Linux).

In-Out runs on http://<this computer>:8000 (INOUT_PORT).
INOUT_HOST=127.0.0.1 allows only this computer to connect.
"""
import argparse
import atexit
import ipaddress
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from cheroot import wsgi

from inout import create_app, migrations
from inout.db import get_db

HERE = Path(__file__).resolve().parent
HOST = os.environ.get("INOUT_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("INOUT_PORT", "8000"))
LOG_MAX_BYTES = 1_000_000
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
# What people run on this computer, for messages that tell them what to do.
SETUP_SCRIPT = '"Setup In-Out.bat"' if sys.platform == "win32" else "./setup-inout.sh"
START_SCRIPT = '"Start In-Out.bat"' if sys.platform == "win32" else "./start-inout.sh"


def serve_forever(server):
    server.prepare()  # binds the port now, so "port in use" fails here with a clear error
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


def local_ips():
    """This computer's local-network IPv4 addresses, the ones phones and other computers use."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        # Connecting a UDP socket sends nothing; it only picks the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    private = (ip for ip in found if any(ipaddress.ip_address(ip) in net for net in PRIVATE_NETS))
    return sorted(private, key=ipaddress.ip_address)


def describe(host):
    """What to tell people: the addresses to use."""
    lines = [f"In-Out is running. On this computer: http://127.0.0.1:{HTTP_PORT}"]
    if host == "0.0.0.0":
        lines += [f"  From phones and other computers: http://{ip}:{HTTP_PORT}" for ip in local_ips()]
    return lines


# ---------------------------------------------------------------- background running

def _instance():
    return Path(create_app().instance_path)


def _pidfile():
    return _instance() / "server.pid"


def _logfile():
    return _instance() / "server.log"


def is_running():
    """True if something answers on In-Out's port on this computer."""
    try:
        with socket.create_connection(("127.0.0.1", HTTP_PORT), timeout=1):
            return True
    except OSError:
        return False


def _is_python_process(pid):
    """Guards --stop against a stale pid file whose number now belongs to another program."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
    else:
        try:
            out = Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
        except OSError:  # no such process
            return False
    return "python" in out.lower()


def start_background():
    if is_running():
        print("In-Out is already running.")
        return 0
    if not Path(create_app().config["DATABASE"]).exists():
        print(f"No database yet. Run {SETUP_SCRIPT} first.")
        return 1
    log = _logfile()
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
        log.replace(log.with_suffix(".log.old"))
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    # On Linux, a session of its own means closing the terminal that started it doesn't stop it.
    subprocess.Popen([str(exe), str(HERE / "run.py"), "--log", str(log), "--pidfile", str(_pidfile())],
                     cwd=HERE, creationflags=flags, close_fds=True, start_new_session=sys.platform != "win32",
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.5)
        if is_running():
            print("\n".join(describe(HOST)))
            return 0
    print(f"In-Out didn't start. The end of {log}:")
    print("".join(log.read_text(encoding="utf-8", errors="replace").splitlines(True)[-15:]))
    return 1


def stop_background():
    pidfile = _pidfile()
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    if pid is None or not _is_python_process(pid):
        pidfile.unlink(missing_ok=True)
        if is_running():
            print("In-Out is running in a window. Close that window (or press Ctrl+C in it) to stop it.")
            return 1
        print("In-Out isn't running.")
        return 0
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:  # it ended on its own just now
            pass
    pidfile.unlink(missing_ok=True)
    for _ in range(20):
        if not is_running():
            print("In-Out stopped.")
            return 0
        time.sleep(0.25)
    if sys.platform == "win32":
        print("In-Out didn't stop. Restart the computer or end it in Task Manager.")
    else:
        print(f"In-Out didn't stop. End it with: kill -9 {pid}")
    return 1


def open_browser():
    webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}/")
    return 0


# ---------------------------------------------------------------- main

def check_database(app):
    """Refuse to run code and data from different versions of In-Out."""
    with app.app_context():
        try:
            migrations.check(get_db())
        except migrations.VersionMismatch as e:
            raise SystemExit(str(e))


def serve(log=None, pidfile=None):
    if log:
        # pythonw has no console, so everything goes to the log file instead.
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
        print(f"\n--- started {datetime.now():%Y-%m-%d %H:%M:%S} ---")
    app = create_app()
    if not Path(app.config["DATABASE"]).exists():
        raise SystemExit("No database yet. Run: python manage.py init-db")
    check_database(app)
    server = wsgi.Server((HOST, HTTP_PORT), app, numthreads=8)
    if pidfile:
        Path(pidfile).write_text(str(os.getpid()))
        atexit.register(lambda: Path(pidfile).unlink(missing_ok=True))
    print("\n".join(describe(HOST)))
    if not log:
        print("Press Ctrl+C to stop.")
    serve_forever(server)


def main():
    parser = argparse.ArgumentParser(description="Run In-Out")
    parser.add_argument("--background", action="store_true", help="run with no window")
    parser.add_argument("--stop", action="store_true", help="stop the background server")
    parser.add_argument("--open", action="store_true", help="open In-Out in the browser")
    parser.add_argument("--log", help=argparse.SUPPRESS)
    parser.add_argument("--pidfile", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.stop:
        sys.exit(stop_background())
    if args.background:
        code = start_background()
        if code == 0 and args.open:
            open_browser()
        sys.exit(code)
    if args.open:
        sys.exit(open_browser())
    serve(args.log, args.pidfile)


if __name__ == "__main__":
    main()
