# In-Out

A simple time clock for your local network. Employees enter a 4-digit PIN or tap their card to clock in and out, and to submit their day with an optional note. Admins review hours, fix mistakes and export a CSV.

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
   - makes In-Out start when the computer starts, before anyone signs in (Windows asks for admin permission again). If you skip that, it offers to start In-Out when you log in instead,
   - walks you through turning on the Windows kiosk for the clock station (see **Clock station (Windows kiosk)** below),
   - puts an **In-Out** icon on the desktop,
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
- **Auto-start:** if you said yes to starting with the computer, Windows starts In-Out when the computer starts, before anyone signs in. It runs as your Windows account, with no window, and keeps running when you sign out. It's a task named "In-Out" in Task Scheduler. If you chose starting at login instead, In-Out starts when you log in and stops when you sign out. To turn either off, run the setup again and answer "n".
- **Log:** messages go to `instance\server.log`.

When it starts, it shows the address to use, such as `http://192.168.1.50:8000`.

### Clock station (Windows kiosk)
On the computer people clock in at, Windows' own kiosk shows only the clock, full screen, with no taskbar or desktop. If Edge is closed, Windows reopens it. The kiosk needs **Windows Pro, Enterprise or Education** (Home has no kiosk).

The kiosk signs in to a separate kiosk account that can only run Edge, so it can't start In-Out itself. That's why In-Out starts with the computer instead: by the time the kiosk opens Edge, In-Out is already running.

1. Run the setup and answer yes to starting In-Out with the computer.
2. At the **Windows kiosk** step, let it open Settings (**Accounts > Other users**), then:
   1. Next to **Set up a kiosk**, select **Get started**.
   2. Name the kiosk account, for example "In-Out clock".
   3. Choose **Microsoft Edge**, and the full-screen option (digital sign or interactive display), not the public browser.
   4. For the address, enter exactly `http://127.0.0.1:8000/`. That's this computer. The card reader only works there, not at its network address.
3. Restart. The kiosk account signs in by itself and opens the clock.

**To get back to your own account**, for example to run **Update In-Out.bat**, press Ctrl+Alt+Del, choose **Sign out**, and sign in as yourself. In-Out keeps running, and Update and Stop work as usual. Signing out hands the screen back to the kiosk.

**After switching, check the card reader**: the **Users** page should say "Card reader: … is ready". In-Out now runs without anyone signed in, and that hasn't been tried with the reader yet. If it says no reader is plugged in while one is, tell the developer. PINs work either way.

To stop using the kiosk, remove it in the same Settings page.

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

In-Out runs on Linux too. The icon and auto-start need a desktop such as GNOME, KDE, Cinnamon or Xfce. Shell scripts in the `linux` folder take the place of the .bat files:

| Windows | Linux |
|---|---|
| `Setup In-Out.bat` | `./linux/setup-inout.sh` |
| `Start In-Out.bat` | `./linux/start-inout.sh` |
| `Stop In-Out.bat` | `./linux/stop-inout.sh` |
| `Update In-Out.bat` | `./linux/update-inout.sh` |

1. **Install Python 3.10 or newer, its venv support, and git.** On Ubuntu, Debian or Mint: `sudo apt install python3 python3-venv git`. On Fedora: `sudo dnf install python3 git`.
2. **Download In-Out with git**, for example into your home folder:
   ```
   git clone https://github.com/hamet-c/In-Out.git ~/In-Out
   ```
3. **Run the setup** in a terminal: `cd ~/In-Out`, then `./linux/setup-inout.sh`. It asks the same questions as on Windows:
   - **Firewall:** if ufw or firewalld is turned on, it adds rules that let only local-network addresses reach port 8000, and asks for your password (sudo). Ubuntu's firewall is off unless someone turned it on, and then there's nothing to do. To add the rules by hand with ufw, run `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`, and the same with `10.0.0.0/8` and `172.16.0.0/12`.
   - **Auto-start** at login: adds `~/.config/autostart/In-Out.desktop`. Delete that file to turn it off.
   - **Icon:** puts In-Out in the app menu, and on the desktop if there is one. Right-click it for **Stop In-Out**. If the desktop calls the icon untrusted, right-click it and choose **Allow Launching**.

**The card reader isn't set up on Linux yet**, so PINs are the only way to sign in there. To try it (untested):
1. `sudo apt install pcscd libpcsclite-dev swig build-essential`, then `.venv/bin/pip install pyscard`.
2. For an ACR122U, stop Linux's own NFC driver from taking the reader away from pcscd: `echo 'blacklist pn533_usb' | sudo tee /etc/modprobe.d/inout-nfc.conf`, then restart the computer.
3. Restart In-Out. The **Users** page says whether it sees the reader.

Everything else works as on Windows, with `/` in paths and `.venv/bin/` in place of `.venv\Scripts\`. The data is in `instance/inout.db` and the log is `instance/server.log`. For an offline install, download the `vendor` folder on a Linux computer, because some components differ between Windows and Linux.

## Day to day

- **Employees** open the site, enter their PIN (or tap their card at the server computer), and tap **Clock In** or **Clock Out**. After clocking out, they can **Submit day** with an optional note, for example "Forgot to clock in, started at 8:00". Submitting locks the day. The screen signs itself out after 30 seconds without a tap, so a shared station is safe to walk away from.
- **New employees** tap "Request access", enter their name and choose a PIN. An admin approves them under **Users**.
- **Admins** sign in from "Admin sign in" on the PIN screen:
  - **Timesheets:** hours by date range and person, with notes, flagged problems and **Export CSV**. In the CSV, a day's total is on that day's first row only, so adding up the column gives the right answer.
  - **Email CSV** (next to Export CSV): sends the same CSV to an email address. It remembers the last address. Set it up once under **Email settings** at the bottom of the Timesheets page. For Gmail, enter server `smtp.gmail.com`, port `587`, your Gmail address, and an **app password** from myaccount.google.com/apppasswords. That needs 2-Step Verification turned on. Your normal Gmail password won't work. The password is saved in `instance/inout.db` and its backups, so keep those files private.
  - **Add a day** (under the Timesheets table): for a day someone never clocked in on. Pick the person and the date, then type in their times on the next page.
  - **Edit:** fix a day's times, add or delete entries, or reopen a submitted day. **Submit day** submits a day for someone who didn't. People can only submit today themselves, so this is how an earlier day stops showing as "Not submitted". Every change is recorded in the day's change log.
  - **Users:** approve or reject requests, add employees, disable people, set or change PINs, and link cards.
  - **More admins:** on **Users**, **Add an admin** creates a new one with their own username and password. A clock-in PIN is optional. **Make admin** on someone's row gives an employee admin access and keeps their PIN, card and hours. **Remove admin** takes it away again, and they stay on as an employee. You can't remove your own admin access or disable yourself, so there's always at least one admin.
  - **Passwords:** on **Users**, **Change password** on your own row asks for your current password and a new one. **Reset password** on another admin's row sets a new one for an admin who forgot theirs. If the only admin forgot theirs, run `.venv\Scripts\python manage.py reset-password` on the server computer.
  - **Change log:** everything admins have changed, to people and to days, with who did it and when. Newest first.

### Card reader
Employees can tap a card instead of typing their PIN. PINs keep working too.

1. **Plug the USB reader into the server computer.** An ACS ACR122U works, and so does any other "PC/SC" (smart card) USB reader. Windows sets it up by itself, with no driver to install. The **Users** page says "Card reader: … is ready" once In-Out sees it.
2. **Use 13.56 MHz NFC cards or key fobs**, such as NTAG215 or MIFARE. The reader beeps when it sees one. 125 kHz cards, often sold as "RFID" or "EM4100", don't work with it. Bank cards and phones show a different ID on every tap, so they can't be linked.
3. **Link each person's card:** on **Users**, press **Link card** next to their name, then tap their card on the reader. The page goes back to Users by itself. A card can belong to only one person, and linking a new card replaces their old one. **Remove card** unlinks a lost card.

**The next person's tap takes over the screen.** If someone forgets to press **Done**, the next person doesn't have to wait for the 30-second sign-out. They tap their card, and the first person is signed out. An unknown card signs the first person out too. Tapping your own card again does nothing.

**Only the server computer's own screen listens for cards,** when In-Out is opened with the In-Out icon (`http://127.0.0.1:8000`). Opened by its network address, such as `http://192.168.1.50:8000`, even on the server itself, the page shows only the PIN pad. Otherwise a laptop on the Wi-Fi sitting on the PIN screen would be signed in as whoever tapped their card at the station.

### Security notes
- PINs and card IDs are stored as keyed hashes, never as the digits themselves.
- Only one person is signed in per browser. Entering a PIN signs out any admin on that screen, so an admin who walks away can't leave the admin pages open to the next employee. Admin pages also sign out after 5 minutes without a tap or key press.
- Wrong PINs make that device wait. The first two cost nothing, so a typo or two never holds up the clock station. After that, each wrong PIN means a wait before the next try: 5 seconds, then 10, then 20, doubling up to 5 minutes. A wrong try stops counting after 15 minutes. A correct PIN doesn't reset the count. Wrong admin passwords count the same way. Unknown cards don't count, and cards still work during a wait. A card ID comes from the reader, not from the browser, so it can't be guessed by typing.
- Anyone holding a coworker's card can clock them in, and a card's ID can be copied by someone who gets it close to their own reader. Treat cards like keys. Timesheets show who clocked in when.
- Because employees choose their own PINs and PINs must be unique, "that PIN can't be used" reveals that someone else has it. These attempts count as wrong tries too. If that's a concern, have admins assign PINs instead.
- In-Out uses plain HTTP, so PINs and admin passwords cross the network unencrypted. Someone else on the same Wi-Fi with the right tools could capture them. Browsers label the site "Not secure" for this reason.

## Backups

All data is in `instance\inout.db`, including what's needed to check PINs. To back up, stop the server and copy that file somewhere safe. To restore, copy it back.

`instance\secret_key` only keeps people signed in; if it's lost, everyone simply signs in again.

## For developers

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

- **`inout/credentials.py`:** the credential-to-person lookup. PINs use `kind="pin"` and card IDs `kind="nfc"`, with the same functions and table.
- **`inout/nfc.py`:** a background thread (started by `run.py`) that watches the USB reader through pyscard and keeps the latest tap. Pages on the server's own screen ask `POST /card` (or `/admin/users/<id>/card` when linking) twice a second for a newer tap, and the card ID never reaches a browser. Tests tap cards with `reader.tapped(uid)`.
- **`inout/timesheet.py`:** the clock rules, as plain functions.
- **Templates:** each one lists the context it expects in a comment at its top.
