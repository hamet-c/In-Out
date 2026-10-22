# In-Out

A simple time clock for your local network. Employees enter a 4-digit PIN to clock in and out, and to submit their day with an optional note. Admins review hours, fix mistakes and export a CSV.

Everything runs on **one computer at your workplace**. Nothing is sent to the internet; the data lives in a single file on that computer.

## Setting up the server computer

1. **Install Python 3.10 or newer** from python.org. In the installer, tick **"Add python.exe to PATH"**.
2. **Copy this folder** to the server computer, somewhere like `C:\In-Out`. Leave out `.venv` and `instance`, and don't use a OneDrive folder, because syncing can corrupt the database.
3. **Double-click `Setup In-Out.bat`.** It walks you through each step:
   - installs In-Out's components,
   - creates the database and your admin account (name, username, password, and an optional clock-in PIN),
   - sets up secure connections (HTTPS),
   - trusts the certificate on this computer (Windows asks you to confirm),
   - adds the firewall rule so phones can connect (Windows asks for admin permission),
   - turns on auto-start at login and puts an **In-Out** icon on the desktop,
   - starts In-Out.

You can run the setup again at any time. Finished steps are skipped, and it refreshes the certificate if the computer's address has changed.

**No internet on that computer?** On a computer that has internet, run `pip download -r requirements.txt -d vendor` and copy the `vendor` folder in with the rest. The setup installs from it automatically.

## Running it

- **In-Out icon on the desktop** (or `Start In-Out.bat`): starts In-Out in the background if it isn't running yet, and opens the clock in the browser. There's no window to close by accident.
- **`Stop In-Out.bat`**: stops it.
- **Auto-start:** if you turned it on, In-Out starts by itself whenever you log in to this computer. To turn it off, run the setup again and answer "n", or delete `In-Out.cmd` from the Startup folder (Win+R, then `shell:startup`).
- **Log:** messages go to `instance\server.log`.

When it starts, it shows the addresses to use, such as `https://192.168.1.50:8443` for daily use and `http://192.168.1.50:8000` for setting up a new device.

Auto-start runs when this computer's user logs in, not before. If the server computer reboots, someone needs to log in, or set up automatic sign-in for that account.

### So other devices can reach it
1. **Firewall:** the setup adds a rule named "In-Out" that allows only local-network addresses. To add it by hand, run this in an admin PowerShell:
   `New-NetFirewallRule -DisplayName 'In-Out' -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Any -RemoteAddress 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`
2. **Fixed address:** in your router's settings, reserve this computer's IP address (often called a "DHCP reservation"). On a school or company network, ask IT. That keeps the address, and everyone's bookmarks, from changing.
3. **The Wi-Fi must let devices reach each other.** Guest and campus networks (such as eduroam) usually block this ("client isolation"), and then phones can't reach the server at all. Use the main staff network, or ask IT to allow devices to reach this computer on ports 8000 and 8443.

To allow only this computer (a single shared station), set `INOUT_HOST=127.0.0.1` before running. `INOUT_PORT` (default 8000) and `INOUT_HTTPS_PORT` (default 8443) change the ports.

### Doing it by hand instead
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py init-db
.venv\Scripts\python manage.py create-admin
.venv\Scripts\python manage.py make-certs
.venv\Scripts\python run.py
```
`run.py` on its own runs in that window (Ctrl+C stops it). `run.py --background`, `--stop` and `--open` are what the .bat files use.

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
- Only one person is signed in per browser. Entering a PIN signs out any admin on that screen, so an admin who walks away can't leave the admin pages open to the next employee. Admin pages also sign out after 5 minutes without a tap or key press.
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
