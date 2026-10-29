# In-Out

A simple time clock for your local network. Employees enter a 4-digit PIN to clock in and out, and to submit their day with an optional note. Admins review hours, fix mistakes and export a CSV.

Everything runs on **one computer at your workplace**. Nothing is sent to the internet; the data lives in a single file on that computer.

## Setting up the server computer

These steps are for Windows. For a Linux computer, see **On Linux** below.

1. **Install Python 3.10 or newer** from python.org. In the installer, tick **"Add python.exe to PATH"**.
2. **Install Git for Windows** from git-scm.com (the default options are fine).
3. **Download In-Out with git.** Open PowerShell and run:
   ```
   git clone https://github.com/hamet-c/In-Out.git C:\In-Out
   ```
   Use a plain folder like `C:\In-Out`, not a OneDrive folder, because syncing can corrupt the database. Installing with git (rather than copying files) is what lets **Update In-Out.bat** work later.
4. **Double-click `Setup In-Out.bat`** in that folder. It walks you through each step:
   - installs In-Out's components,
   - creates the database and your admin account (name, username, password, and an optional clock-in PIN),
   - adds the firewall rule so phones can connect (Windows asks for admin permission),
   - turns on auto-start at login and puts an **In-Out** icon on the desktop,
   - starts In-Out.

You can run the setup again at any time. Finished steps are skipped.

**No internet on that computer?** On a computer that has internet, run `pip download -r requirements.txt -d vendor` and copy the `vendor` folder in with the rest. The setup installs from it automatically.

## Updating

New versions are pushed to GitHub from the development laptop. To install one on the server, **double-click `Update In-Out.bat`**. It:

1. checks GitHub, and stops right there with "Already up to date" if nothing is new,
2. lists what changed, then stops In-Out,
3. backs up the database to `instance\backups\` (the newest 30 are kept),
4. downloads the new version (`git pull`),
5. installs any new components and upgrades the database if needed,
6. starts In-Out again.

Your data (`instance` folder: database and settings) is never touched by updates. Devices don't need to be set up again.

**If something goes wrong**, the update stops and prints the exact steps to go back: copy the backup over the database, return the code to the previous version with `git reset --hard <version>`, and start In-Out. The server also refuses to start if the database and code versions don't match, instead of running with mismatched data.

**It refuses to update if files on the server were edited by hand**, so an update can never silently overwrite someone's change. Make changes on the laptop and push them instead.

**Private repository:** if the GitHub repository is private, the first update opens a GitHub sign-in window. Sign in with an account that can read the repository, and Windows remembers it. A read-only access token works too, and is safer than a personal login on a shared computer.

### Releasing a new version (on the laptop)
1. Make the change and run the tests: `.venv\Scripts\python -m pytest`
2. If the change alters the database structure, add an upgrade step (see the top of `inout/migrations.py`).
3. Commit and `git push`. Then run **Update In-Out.bat** on the server.

## Running it

- **In-Out icon on the desktop** (or `Start In-Out.bat`): starts In-Out in the background if it isn't running yet, and opens the clock in the browser. There's no window to close by accident.
- **`Stop In-Out.bat`**: stops it.
- **`Update In-Out.bat`**: installs the newest version from GitHub (see **Updating**).
- **Auto-start:** if you turned it on, In-Out starts by itself whenever you log in to this computer. To turn it off, run the setup again and answer "n", or delete `In-Out.cmd` from the Startup folder (Win+R, then `shell:startup`).
- **Log:** messages go to `instance\server.log`.

When it starts, it shows the address to use, such as `http://192.168.1.50:8000`.

Auto-start runs when this computer's user logs in, not before. If the server computer reboots, someone needs to log in, or set up automatic sign-in for that account.

### So other devices can reach it
1. **Firewall:** the setup adds a rule named "In-Out" that allows only local-network addresses. To add it by hand, run this in an admin PowerShell:
   `New-NetFirewallRule -DisplayName 'In-Out' -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any -RemoteAddress 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`
2. **Fixed address:** in your router's settings, reserve this computer's IP address (often called a "DHCP reservation"). On a school or company network, ask IT. That keeps the address, and everyone's bookmarks, from changing.
3. **The Wi-Fi must let devices reach each other.** Guest and campus networks (such as eduroam) usually block this ("client isolation"), and then phones can't reach the server at all. Use the main staff network, or ask IT to allow devices to reach this computer on port 8000.

To allow only this computer (a single shared station), set `INOUT_HOST=127.0.0.1` before running. `INOUT_PORT` (default 8000) changes the port.

### Doing it by hand instead
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py init-db
.venv\Scripts\python manage.py create-admin
.venv\Scripts\python run.py
```
`run.py` on its own runs in that window (Ctrl+C stops it). `run.py --background`, `--stop` and `--open` are what the .bat files use.

## On Linux

In-Out runs on Linux too. The icon and auto-start need a desktop such as GNOME, KDE, Cinnamon or Xfce. Shell scripts take the place of the .bat files:

| Windows | Linux |
|---|---|
| `Setup In-Out.bat` | `./setup-inout.sh` |
| `Start In-Out.bat` | `./start-inout.sh` |
| `Stop In-Out.bat` | `./stop-inout.sh` |
| `Update In-Out.bat` | `./update-inout.sh` |

1. **Install Python 3.10 or newer, its venv support, and git.** On Ubuntu, Debian or Mint: `sudo apt install python3 python3-venv git`. On Fedora: `sudo dnf install python3 git`.
2. **Download In-Out with git**, for example into your home folder:
   ```
   git clone https://github.com/hamet-c/In-Out.git ~/In-Out
   ```
3. **Run the setup** in a terminal: `cd ~/In-Out`, then `./setup-inout.sh`. It asks the same questions as on Windows:
   - **Firewall:** if ufw or firewalld is turned on, it adds rules that let only local-network addresses reach port 8000, and asks for your password (sudo). Ubuntu's firewall is off unless someone turned it on, and then there's nothing to do. To add the rules by hand with ufw, run `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`, and the same with `10.0.0.0/8` and `172.16.0.0/12`.
   - **Auto-start** at login: adds `~/.config/autostart/In-Out.desktop`. Delete that file to turn it off.
   - **Icon:** puts In-Out in the app menu, and on the desktop if there is one. Right-click it for **Stop In-Out**. If the desktop calls the icon untrusted, right-click it and choose **Allow Launching**.

Everything else works as on Windows, with `/` in paths and `.venv/bin/` in place of `.venv\Scripts\`. The data is in `instance/inout.db` and the log is `instance/server.log`. For an offline install, download the `vendor` folder on a Linux computer, because some components differ between Windows and Linux.

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
- In-Out uses plain HTTP, so PINs and admin passwords cross the network unencrypted. Someone else on the same Wi-Fi with the right tools could capture them. Browsers label the site "Not secure" for this reason.

## Backups

All data is in `instance\inout.db`, including what's needed to check PINs. To back up, stop the server and copy that file somewhere safe. To restore, copy it back.

`instance\secret_key` only keeps people signed in; if it's lost, everyone simply signs in again.

## For developers

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

- **`inout/credentials.py`:** the credential-to-person lookup. PINs use `kind="pin"`, and a future NFC card reader uses `kind="nfc"` with the same functions and table (see `PLAN.md`).
- **`inout/timesheet.py`:** the clock rules, as plain functions.
- **Templates:** each one lists the context it expects in a comment at its top.
