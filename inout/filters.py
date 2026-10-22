"""Jinja filters used by the templates. Call register(app) once at startup."""


def hm(seconds):
    """3725 -> '1h 02m'."""
    minutes = int(seconds or 0) // 60
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def clock(dt):
    """datetime -> '8:02 AM'. None -> '--:--'."""
    if dt is None:
        return "--:--"
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def hhmm(dt):
    """datetime -> '08:02', the value format of <input type="time">."""
    return dt.strftime("%H:%M") if dt else ""


def longdate(d):
    """date -> 'Tuesday, October 20'."""
    return f"{d:%A, %B} {d.day}"


def shortdate(d):
    """date -> 'Tue Oct 20'."""
    return f"{d:%a %b} {d.day}"


def register(app):
    app.jinja_env.filters.update(
        hm=hm, clock=clock, hhmm=hhmm, longdate=longdate, shortdate=shortdate
    )
