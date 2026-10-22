import ipaddress
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding

import run
from inout import certs


@pytest.fixture
def cert_paths(app):
    return certs.paths(app.instance_path)


@pytest.fixture
def app(app, tmp_path):
    app.instance_path = str(tmp_path / "instance")
    return app


def load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ca_is_limited_to_local_addresses(cert_paths):
    certs.ensure_ca(cert_paths)
    ca = load(cert_paths.ca_cert)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    permitted = nc.value.permitted_subtrees
    assert x509.IPAddress(ipaddress.ip_network("192.168.0.0/16")) in permitted
    assert x509.DNSName("localhost") in permitted
    assert len(permitted) == 5  # 3 private ranges, loopback, localhost; nothing else


def test_server_cert_is_signed_by_the_ca_and_covers_the_addresses(cert_paths):
    ips = certs.issue_server_cert(cert_paths, ["192.168.43.185"])
    assert ips == ["127.0.0.1", "192.168.43.185"]
    ca, cert = load(cert_paths.ca_cert), load(cert_paths.cert)
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(),
                           cert.signature_hash_algorithm)
    assert cert.issuer == ca.subject
    lifetime = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert lifetime <= timedelta(days=825)  # Apple's limit
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert certs.status(cert_paths).ips == ["127.0.0.1", "192.168.43.185"]


def test_reissuing_keeps_the_same_ca(cert_paths):
    certs.issue_server_cert(cert_paths, ["192.168.1.20"])
    ca_before = cert_paths.ca_cert.read_bytes()
    certs.issue_server_cert(cert_paths, ["10.0.0.7"])
    assert cert_paths.ca_cert.read_bytes() == ca_before
    assert "10.0.0.7" in certs.status(cert_paths).ips


def test_public_addresses_are_refused(cert_paths):
    with pytest.raises(ValueError):
        certs.issue_server_cert(cert_paths, ["8.8.8.8"])


def test_setup_page_without_https_explains_what_to_do(client):
    page = client.get("/setup").get_data(as_text=True)
    assert "make-certs" in page
    assert client.get("/setup/ca.crt").status_code == 404


def test_setup_page_offers_the_certificate_but_never_the_key(app, client, cert_paths):
    certs.issue_server_cert(cert_paths, ["192.168.43.185"])
    app.config["HTTPS_PORT"] = 8443
    page = client.get("/setup", base_url="http://192.168.43.185:8000").get_data(as_text=True)
    assert "Download certificate" in page and "https://192.168.43.185:8443/" in page
    r = client.get("/setup/ca.crt")
    assert r.status_code == 200 and r.mimetype == "application/x-x509-ca-cert"
    assert b"BEGIN CERTIFICATE" in r.data and b"PRIVATE KEY" not in r.data
    for path in ("/setup/ca.key", "/setup/../certs/ca.key", "/static/../certs/ca.key"):
        assert b"PRIVATE KEY" not in client.get(path).data


def test_plain_http_side_only_shows_setup(app, cert_paths):
    certs.issue_server_cert(cert_paths, ["192.168.43.185"])
    app.config["HTTPS_PORT"] = 8443
    app.wsgi_app = run.setup_only(app.wsgi_app)
    plain = app.test_client()
    for path in ("/", "/admin/", "/register"):
        page = plain.get(path).get_data(as_text=True)
        assert "Set up this device" in page and "Enter your PIN" not in page
    assert plain.post("/pin", data={"pin": "2468"}).status_code == 405
    assert plain.get("/setup/ca.crt").status_code == 200
    assert plain.get("/static/style.css").status_code == 200
