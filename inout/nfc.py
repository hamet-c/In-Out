"""The USB card reader plugged into the server computer (an ACR122U, or any PC/SC reader).

A background thread watches the reader and remembers the latest card tapped on it. Pages on
this computer's own screen ask the server for a tap newer than the last one they saw (see
app.js), so the card ID itself never travels through a browser.
"""
import threading
import time
from collections import namedtuple

try:
    from smartcard.Exceptions import SmartcardException
    from smartcard.System import readers
    MISSING = None
except Exception as e:  # pyscard isn't installed, or on Linux, pcsc-lite isn't
    MISSING = e

Tap = namedtuple("Tap", "seq uid at")

# A command for the reader rather than the card: "tell me this card's ID". It works the
# same for every card type the reader handles.
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]
# A tap older than this is ignored, so a page that wakes up late can't sign in someone
# who has already walked away.
FRESH_SECONDS = 5
POLL_SECONDS = 0.2
NO_READER_POLL_SECONDS = 2


class CardReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None
        self.name = None  # the reader's name while one is plugged in
        self.problem = "The card reader isn't running."  # None once a reader is ready

    @property
    def ready(self):
        return self.problem is None

    @property
    def seq(self):
        """Number of the latest tap, 0 before the first. Pages start waiting from here."""
        with self._lock:
            return self._latest.seq if self._latest else 0

    def tapped(self, uid):
        """Record a tap. Called by the watcher thread (and by tests)."""
        with self._lock:
            seq = self._latest.seq + 1 if self._latest else 1
            self._latest = Tap(seq, uid, time.monotonic())

    def tap_after(self, seq):
        """The latest tap if it's newer than seq and still fresh, otherwise None."""
        with self._lock:
            tap = self._latest
        if tap is None or tap.seq <= seq or time.monotonic() - tap.at > FRESH_SECONDS:
            return None
        return tap

    def start(self):
        threading.Thread(target=self._watch, name="card-reader", daemon=True).start()

    def _set(self, name, problem):
        if (name, problem) != (self.name, self.problem):
            print(f"Card reader: {name} is ready." if name else f"Card reader: {problem}", flush=True)
        self.name, self.problem = name, problem

    def _watch(self):
        if MISSING:
            self._set(None, f"Card reader support isn't installed ({MISSING}). PINs still work.")
            return
        last = None  # the card on the reader right now, so leaving it there counts as one tap
        while True:
            try:
                found = readers()
            except Exception:  # Windows stops its smart card service while no reader is plugged in
                found = []
            if not found:
                self._set(None, "No card reader is plugged into this computer.")
                last = None
                time.sleep(NO_READER_POLL_SECONDS)
                continue
            self._set(str(found[0]), None)
            try:
                uid = _read(found[0])
            except Exception as e:  # never let the thread die; try again in a moment
                print(f"Card reader: {e!r}", flush=True)
                uid = None
                time.sleep(NO_READER_POLL_SECONDS)
            if uid and uid != last:
                self.tapped(uid)
            last = uid
            time.sleep(POLL_SECONDS)


def _read(reader):
    """The ID of the card on the reader as hex, or None if there's no card."""
    conn = reader.createConnection()
    try:
        conn.connect()
        data, sw1, sw2 = conn.transmit(GET_UID)
        return bytes(data).hex().upper() if (sw1, sw2) == (0x90, 0x00) and data else None
    except SmartcardException:  # no card, or it was pulled away mid-read
        return None
    finally:
        try:
            conn.disconnect()
        except SmartcardException:
            pass
        conn.release()
