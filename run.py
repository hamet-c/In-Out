"""Start In-Out for the local network.

    python run.py                 run in this window (Ctrl+C stops it)
    python run.py --background    run with no window; log in instance/server.log
    python run.py --stop          stop the background server
    python run.py --open          open In-Out in this computer's browser

"Start In-Out.bat" and "Stop In-Out.bat" use these.

Once HTTPS is set up (python manage.py make-certs):
    https://<this computer>:8443   In-Out itself            (INOUT_HTTPS_PORT)
    http://<this computer>:8000    device setup page only   (INOUT_PORT)
Before that, In-Out runs on plain http://<this computer>:8000.

INOUT_HOST=127.0.0.1 allows only this computer to connect.
"""
import argparse
import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cheroot import wsgi
from cheroot.ssl.builtin import BuiltinSSLAdapter

from inout import certs, create_app, migrations
from inout.db import get_db

HERE = Path(__file__).resolve().parent
HOST = os.environ.get("INOUT_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("INOUT_PORT", "8000"))
HTTPS_PORT = int(os.environ.get("INOUT_HTTPS_PORT", "8443"))
LOG_MAX_BYTES = 1_000_000


def setup_only(app):
    """The plain-HTTP side once HTTPS is on. Every address shows the device setup page,
    so the only thing ever sent unencrypted is the public certificate."""
    def wsgi_app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path != "/setup/ca.crt" and not path.startswith("/static/"):
            environ = {**environ, "PATH_INFO": "/setup", "QUERY_STRING": ""}
        return app(environ, start_response)
    return wsgi_app


def build_servers(app, host, http_port, https_port):
    """Returns (servers, cert_status). cert_status is None when running plain HTTP."""
    p = certs.paths(app.instance_path)
    info = certs.status(p)
    if info is None:
        return [wsgi.Server((host, http_port), app, numthreads=8)], None
    app.config.update(HTTPS_PORT=https_port, SESSION_COOKIE_SECURE=True)
    secure = wsgi.Server((host, https_port), app, numthreads=8)
    secure.ssl_adapter = BuiltinSSLAdapter(str(p.cert), str(p.key))
    plain = wsgi.Server((host, http_port), setup_only(app), numthreads=2)
    return [secure, plain], info


def serve_forever(servers):
    for s in servers:
        s.prepare()  # binds the port now, so "port in use" fails here with a clear error
    threads = [threading.Thread(target=s.serve, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        while all(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.stop()


def describe(info, host):
    """What to tell people: addresses and any certificate warnings."""
    lan = [ip for ip in certs.local_ips() if ip != "127.0.0.1"] if host == "0.0.0.0" else []
    if info is None:
        lines = [f"In-Out is running (plain HTTP). On this computer: http://127.0.0.1:{HTTP_PORT}"]
        lines += [f"  From phones and other computers: http://{ip}:{HTTP_PORT}" for ip in lan]
        lines.append("Tip: run `python manage.py make-certs` to remove the 'Not secure' warnings.")
        return lines
    lines = [f"In-Out is running. On this computer: https://localhost:{HTTPS_PORT}"]
    for ip in lan:
        lines.append(f"  From phones and other computers: https://{ip}:{HTTPS_PORT}")
        lines.append(f"  First time on a device? Open http://{ip}:{HTTP_PORT} to set it up.")
    missing = [ip for ip in lan if ip not in info.ips]
    if missing:
        lines.append(f"WARNING: the certificate doesn't cover {', '.join(missing)}. Devices will see a "
                     "warning there. Run `python manage.py make-certs` (phones need no changes) and restart.")
    if info.expires - datetime.now(timezone.utc) < timedelta(days=30):
        lines.append(f"WARNING: the certificate expires {info.expires:%Y-%m-%d}. "
                     "Run `python manage.py make-certs` and restart.")
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
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout.lower()
    return "python" in out


def start_background():
    if is_running():
        print("In-Out is already running.")
        return 0
    if not Path(create_app().config["DATABASE"]).exists():
        print("No database yet. Run \"Setup In-Out.bat\" first.")
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
    subprocess.Popen([str(exe), str(HERE / "run.py"), "--log", str(log), "--pidfile", str(_pidfile())],
                     cwd=HERE, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.5)
        if is_running():
            print("\n".join(describe(certs.status(certs.paths(_instance())), HOST)))
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
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    pidfile.unlink(missing_ok=True)
    for _ in range(20):
        if not is_running():
            print("In-Out stopped.")
            return 0
        time.sleep(0.25)
    print("In-Out didn't stop. Restart the computer or end it in Task Manager.")
    return 1


def open_browser():
    has_https = certs.status(certs.paths(_instance())) is not None
    url = f"https://localhost:{HTTPS_PORT}/" if has_https else f"http://127.0.0.1:{HTTP_PORT}/"
    webbrowser.open(url)
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
    servers, info = build_servers(app, HOST, HTTP_PORT, HTTPS_PORT)
    if pidfile:
        Path(pidfile).write_text(str(os.getpid()))
        atexit.register(lambda: Path(pidfile).unlink(missing_ok=True))
    print("\n".join(describe(info, HOST)))
    if not log:
        print("Press Ctrl+C to stop.")
    serve_forever(servers)


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
