"""Start In-Out for the local network.

    python run.py

Once HTTPS is set up (python manage.py make-certs):
    https://<this computer>:8443   In-Out itself            (INOUT_HTTPS_PORT)
    http://<this computer>:8000    device setup page only   (INOUT_PORT)
Before that, In-Out runs on plain http://<this computer>:8000.

INOUT_HOST=127.0.0.1 allows only this computer to connect.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cheroot import wsgi
from cheroot.ssl.builtin import BuiltinSSLAdapter

from inout import certs, create_app


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


def main():
    app = create_app()
    if not Path(app.config["DATABASE"]).exists():
        raise SystemExit("No database yet. Run: python manage.py init-db")
    host = os.environ.get("INOUT_HOST", "0.0.0.0")
    http_port = int(os.environ.get("INOUT_PORT", "8000"))
    https_port = int(os.environ.get("INOUT_HTTPS_PORT", "8443"))
    servers, info = build_servers(app, host, http_port, https_port)

    lan = [ip for ip in certs.local_ips() if ip != "127.0.0.1"]
    shown = lan if host == "0.0.0.0" else []
    if info is None:
        print(f"In-Out is running (plain HTTP). On this computer: http://127.0.0.1:{http_port}")
        for ip in shown:
            print(f"  From phones and other computers: http://{ip}:{http_port}")
        print("Tip: run `python manage.py make-certs` to remove the 'Not secure' warnings.")
    else:
        print(f"In-Out is running. On this computer: https://127.0.0.1:{https_port}")
        for ip in shown:
            print(f"  From phones and other computers: https://{ip}:{https_port}")
            print(f"  First time on a device? Open http://{ip}:{http_port} to set it up.")
        missing = [ip for ip in shown if ip not in info.ips]
        if missing:
            print(f"WARNING: the certificate doesn't cover {', '.join(missing)}. Devices will see a "
                  "warning there. Run `python manage.py make-certs` (phones need no changes) and restart.")
        if info.expires - datetime.now(timezone.utc) < timedelta(days=30):
            print(f"WARNING: the certificate expires {info.expires:%Y-%m-%d}. "
                  "Run `python manage.py make-certs` and restart.")
    print("Press Ctrl+C to stop.")
    serve_forever(servers)


if __name__ == "__main__":
    main()
