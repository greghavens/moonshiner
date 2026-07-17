"""Label stamp rendering for outbound parcels at the depot."""

import datetime

STAMP_FMT = "%Y-%m-%d %H:%M UTC"


def stamp_line(parcel_id, service):
    """Render the printed stamp line for one parcel label."""
    now = datetime.datetime.utcnow()
    return "%s [%s] printed %s" % (parcel_id, service, now.strftime(STAMP_FMT))


def pickup_window(days):
    """Return (first_day, last_day) of the pickup window as ISO dates."""
    start = datetime.datetime.utcnow()
    end = start + datetime.timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
