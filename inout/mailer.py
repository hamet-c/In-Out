"""Sends email through the account an admin sets up under Email settings on the Timesheets page.

For Gmail: server smtp.gmail.com, port 587, the Gmail address, and an app password made at
myaccount.google.com/apppasswords (it needs 2-Step Verification on). The normal Gmail password
doesn't work for this.

The settings live in the settings table with everything else, so the password is in
instance/inout.db and its backups. Anyone who can copy those files can read it."""
import smtplib
import ssl
from email.message import EmailMessage

from .db import get_db, setting

# What an admin types in, by settings key.
FIELDS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password")
TIMEOUT = 20  # seconds; the admin's page waits while it sends


class SendFailed(Exception):
    """Says what went wrong, in words for the admin."""


def settings():
    """The saved email settings, plus email_to: the address the last CSV went to. Empty strings if unset."""
    return {key: setting(key) or "" for key in FIELDS + ("email_to",)}


def is_set_up(saved=None):
    saved = saved or settings()
    return all(saved[key] for key in FIELDS)


def save(values):
    """values: some of FIELDS and email_to. The caller holds the transaction."""
    db = get_db()
    for key, value in values.items():
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def send(to, subject, body, filename, data):
    """Send `data` (bytes) as a CSV attachment called `filename`. Raises SendFailed."""
    saved = settings()
    if not is_set_up(saved):
        raise SendFailed("Email isn't set up yet. Fill in Email settings at the bottom of this page.")
    msg = EmailMessage()
    msg["From"] = saved["smtp_user"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(data, maintype="text", subtype="csv", filename=filename, params={"charset": "utf-8"})

    host, port = saved["smtp_host"], int(saved["smtp_port"])
    tls = ssl.create_default_context()
    try:
        # Port 465 is encrypted from the start. Any other port must switch to encryption
        # before the password is sent, or it doesn't get sent at all.
        server = (smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=tls) if port == 465 else
                  smtplib.SMTP(host, port, timeout=TIMEOUT))
        with server:
            if port != 465:
                server.starttls(context=tls)
            server.login(saved["smtp_user"], saved["smtp_password"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise SendFailed("The email server didn't accept the address and password. "
                         "For Gmail, use an app password, not your normal password.") from None
    except smtplib.SMTPNotSupportedError:
        raise SendFailed(f"{host} on port {port} can't encrypt the connection, so the email wasn't sent. "
                         "Check the port: Gmail uses 587.") from None
    except (smtplib.SMTPException, OSError) as e:
        raise SendFailed(f"Couldn't send the email ({e or type(e).__name__}). "
                         "Check the email settings and that this computer is online.") from None
