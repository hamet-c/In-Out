"""Device setup: download the In-Out certificate and trust it once, so the site opens
over HTTPS with no "Not secure" warnings."""
from flask import Blueprint, abort, current_app, render_template, request, send_file

from . import certs

bp = Blueprint("setup", __name__, url_prefix="/setup")


def _https_url():
    port = current_app.config.get("HTTPS_PORT")
    if not port:
        return None
    host = request.host.rsplit(":", 1)[0] if not request.host.endswith("]") else request.host
    return f"https://{host}{'' if port == 443 else f':{port}'}/"


@bp.route("")
def index():
    https_url = _https_url()
    return render_template("setup.html", https_url=https_url, is_secure=request.is_secure,
                           ready=https_url is not None
                           and certs.status(certs.paths(current_app.instance_path)) is not None)


@bp.route("/ca.crt")
def ca_cert():
    path = certs.paths(current_app.instance_path).ca_cert
    if not path.exists():
        abort(404)
    # Only the public certificate is ever served. ca.key never leaves the server.
    return send_file(path, mimetype="application/x-x509-ca-cert", as_attachment=True,
                     download_name="In-Out-CA.crt", max_age=0)
