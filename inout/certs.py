"""Local HTTPS without the internet.

In-Out makes its own small certificate authority (CA). Each device trusts the CA once,
and the CA signs the server's certificate. Re-issuing the server certificate (for example
when the IP changes) needs no change on the devices.

The CA is name-constrained to private network addresses and "localhost". Even if its key
leaked, it could not vouch for internet sites, only for local-network addresses.
"""
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]
CA_DAYS = 3650
# Apple devices reject server certificates valid for more than 825 days.
SERVER_DAYS = 800


def paths(instance_path):
    d = Path(instance_path, "certs")
    return NS(dir=d, ca_cert=d / "ca.crt", ca_key=d / "ca.key",
              cert=d / "server.crt", key=d / "server.key")


def is_private(ip):
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in PRIVATE_NETS)


def local_ips():
    """This computer's private IPv4 addresses, plus 127.0.0.1."""
    found = {"127.0.0.1"}
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
    return sorted((ip for ip in found if is_private(ip)), key=ipaddress.ip_address)


def _now():
    return datetime.now(timezone.utc)


def _write_key(key, path):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _load(p):
    cert = x509.load_pem_x509_certificate(p.ca_cert.read_bytes())
    key = serialization.load_pem_private_key(p.ca_key.read_bytes(), password=None)
    return cert, key


def ensure_ca(p):
    """Load the CA, or create it the first time. Returns (cert, key, created)."""
    if p.ca_cert.exists() and p.ca_key.exists():
        return (*_load(p), False)
    p.dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"In-Out Local CA ({socket.gethostname()})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "In-Out"),
    ])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.NameConstraints(
            permitted_subtrees=[x509.IPAddress(net) for net in PRIVATE_NETS] + [x509.DNSName("localhost")],
            excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key, p.ca_key)
    p.ca_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key, True


def issue_server_cert(p, ips):
    """(Re)issue the server certificate for these IP addresses, signed by the CA."""
    ips = sorted(set(ips) | {"127.0.0.1"}, key=ipaddress.ip_address)
    bad = [ip for ip in ips if not is_private(ip)]
    if bad:
        raise ValueError(f"Only local network addresses can be used, not {', '.join(bad)}")
    ca_cert, ca_key, _ = ensure_ca(p)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"In-Out server {ips[-1]}")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips] + [x509.DNSName("localhost")]),
            critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True,
                                     content_commitment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(key, p.key)
    p.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return ips


def status(p):
    """None if HTTPS isn't set up, else what the server certificate covers and when it ends."""
    if not (p.cert.exists() and p.key.exists() and p.ca_cert.exists()):
        return None
    cert = x509.load_pem_x509_certificate(p.cert.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return NS(ips=[str(ip) for ip in san.get_values_for_type(x509.IPAddress)],
              expires=cert.not_valid_after_utc)
