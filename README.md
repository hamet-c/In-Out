# In-Out

A simple time clock for your local network. Employees enter a 4-digit PIN to clock in and out, and to submit their day with an optional note. Admins review hours, fix mistakes and export a CSV.

Everything runs on **one computer at your workplace**. Nothing is sent to the internet; the data lives in a single file on that computer.

## Setting up the server computer

You need Python 3.12 or newer on the computer that will run In-Out.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py init-db
.venv\Scripts\python manage.py create-admin
.venv\Scripts\python manage.py make-certs
```

`create-admin` asks for a name, a username and a password, which are used for the admin pages. It also asks for an optional clock-in PIN, if the admin clocks in too.

`make-certs` sets up HTTPS, so there are no "Not secure" warnings (see **Secure connections** below).

**No internet on that computer?** On a computer that has internet, run `pip download -r requirements.txt -d vendor` and copy the `vendor` folder over. Then install with `.venv\Scripts\pip install --no-index -f vendor -r requirements.txt`.

## Running it

```
.venv\Scripts\python run.py
```

It prints the addresses to use, such as `https://192.168.1.50:8443` for daily use and `http://192.168.1.50:8000` for setting up a new device. Keep this window open while people need to clock in.

### So other devices can reach it
1. **Windows Firewall:** the first time you run it, Windows asks whether to allow Python on networks. Choose **Private networks**. If you missed the prompt, run this once in an admin PowerShell:
   `New-NetFirewallRule -DisplayName "In-Out" -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Private`
2. **Fixed address:** in your router's settings, reserve this computer's IP address (often called a "DHCP reservation"). That keeps the address, and everyone's bookmarks, from changing.
3. **Start automatically (optional):** in Task Scheduler, create a task that runs `.venv\Scripts\python.exe run.py` at log-on, with "Start in" set to this folder.

4. **The Wi-Fi must let devices reach each other.** Guest and campus networks (such as eduroam) usually block this ("client isolation"), and then phones can't reach the server at all. Use the main staff network.

To allow only this computer (a single shared station), set `INOUT_HOST=127.0.0.1` before running. `INOUT_PORT` (default 8000) and `INOUT_HTTPS_PORT` (default 8443) change the ports.

## Secure connections (HTTPS)

`make-certs` creates In-Out's own small certificate authority on this computer, and a certificate for this computer's network addresses signed by it. Nothing goes online.

- **Each device trusts it once.** On the phone or computer, open `http://<server address>:8000`. That plain address only ever shows the setup page, with a download button and step-by-step instructions for iPhone, Android, Windows and Mac. From then on, use `https://<server address>:8443`.
- **It can't be misused for real websites.** The certificate authority is restricted to local network addresses (10.x, 172.16–31.x, 192.168.x, 127.x and `localhost`), so even a stolen copy couldn't impersonate sites like a bank.
- **If the server's IP address changes**, `run.py` warns you. Run `make-certs` again and restart. Devices don't need to do anything, because the authority they trust stays the same. Use `make-certs --ip <address>` to add an address that isn't active right now.
- **Every 800 days or so** the server certificate expires (Apple's limit). `run.py` warns 30 days ahead; run `make-certs` again.

## Day to day

- **Employees** open the site, enter their PIN, and tap **Clock In** or **Clock Out**. After clocking out, they can **Submit day** with an optional note, for example "Forgot to clock in, started at 8:00". Submitting locks the day. The screen signs itself out after 30 seconds without a tap, so a shared station is safe to walk away from.
- **New employees** tap "Request access", enter their name and choose a PIN. An admin approves them under **Users**.
- **Admins** sign in from "Admin sign in" on the PIN screen:
  - **Timesheets:** hours by date range and person, with notes, flagged problems and **Export CSV**.
  - **Edit:** fix a day's times, add or delete entries, or reopen a submitted day. Every change is recorded in the day's change log.
  - **Users:** approve or reject requests, add employees, disable people, and set or change PINs.

### Security notes
- PINs are stored as keyed hashes, never as the digits themselves.
- After 5 wrong PINs within 15 minutes, that device is locked out for 5 minutes. A correct PIN doesn't reset the count.
- Because employees choose their own PINs and PINs must be unique, "that PIN can't be used" reveals that someone else has it. These attempts count toward the lockout above. If that's a concern, have admins assign PINs instead.
- With `make-certs` done, PINs and passwords travel encrypted (HTTPS). The plain-HTTP port only serves the setup page and the public certificate.

## Backups

All data is in `instance\inout.db`, including what's needed to check PINs. To back up, stop the server and copy that file somewhere safe. To restore, copy it back.

Also back up `instance\certs\`. If you lose `ca.key`, every device has to repeat the setup steps with a new certificate. Keep that folder private: it should never be shared or emailed. `instance\secret_key` only keeps people signed in; if it's lost, everyone simply signs in again.

## For developers

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

- **`inout/credentials.py`:** the credential-to-person lookup. PINs use `kind="pin"`, and a future NFC card reader uses `kind="nfc"` with the same functions and table (see `PLAN.md`).
- **`inout/timesheet.py`:** the clock rules, as plain functions.
- **Templates:** each one lists the context it expects in a comment at its top.
