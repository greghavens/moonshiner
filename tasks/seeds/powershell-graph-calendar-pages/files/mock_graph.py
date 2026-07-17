# Loopback fake of the Microsoft Graph v1.0 calendarView endpoint for the
# GraphCalendarExport acceptance harness. Binds 127.0.0.1 on an ephemeral
# port, writes the port to argv[1] (atomically), then serves until killed.
#
# Speaks the subset pinned in docs/contract.json: required UTC range
# parameters, Prefer: outlook.timezone handling, opaque $skiptoken paging
# (continuation queries must match the issued link byte-for-byte), and 429
# Retry-After throttling. Control endpoints:
#
#   GET  /__log__       -> [{method, path, query, auth, prefer}, ...]
#   POST /__reset__     -> clears log, throttling, failure mode, flip counters
#   POST /__throttle__  -> {"token": str, "times": int, "retry_after": int}
#   POST /__mode__      -> {"fail": bool}
#
# Event order inside every page flips on alternate serves so a client that
# does not sort its normalized output cannot produce stable JSON.
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

USER = "u-ravi"
TOKEN = "dummy-token-c91f44"  # dummy; never a real credential
START = "2026-06-01T00:00:00Z"
END = "2026-06-08T00:00:00Z"
CAL_PATH = f"/v1.0/users/{USER}/calendarView"
SKIP2 = "$skiptoken=cal-pg2-b2Zmc2V0%3D"
SKIP3 = "$skiptoken=cal-pg3-b2Zmc2V0%3D"
PREFER_RE = re.compile(r'^outlook\.timezone="(UTC|Pacific Standard Time)"$')

# Each event carries pre-baked start/end pairs for both supported zones.
EVENTS = {
    "e-kickoff": {
        "iCalUId": "040000008200E00074C5B7101A82E008KICKOFF01",
        "subject": "Fitout kickoff",
        "type": "singleInstance",
        "seriesMasterId": None,
        "isAllDay": False,
        "organizer": ("Mia Tran", "mia.tran@northline.example"),
        "UTC": ("2026-06-01T13:00:00", "2026-06-01T14:00:00"),
        "Pacific Standard Time": ("2026-06-01T06:00:00", "2026-06-01T07:00:00"),
    },
    "e-standup-0602": {
        "iCalUId": "040000008200E00074C5B7101A82E008STANDUP01",
        "subject": "Site standup",
        "type": "occurrence",
        "seriesMasterId": "sm-standup",
        "isAllDay": False,
        "organizer": ("Ravi Patel", "ravi.patel@northline.example"),
        "UTC": ("2026-06-02T14:30:00", "2026-06-02T14:45:00"),
        "Pacific Standard Time": ("2026-06-02T07:30:00", "2026-06-02T07:45:00"),
    },
    "e-standup-0604": {
        "iCalUId": "040000008200E00074C5B7101A82E008STANDUP01",
        "subject": "Site standup",
        "type": "occurrence",
        "seriesMasterId": "sm-standup",
        "isAllDay": False,
        "organizer": ("Ravi Patel", "ravi.patel@northline.example"),
        "UTC": ("2026-06-04T14:30:00", "2026-06-04T14:45:00"),
        "Pacific Standard Time": ("2026-06-04T07:30:00", "2026-06-04T07:45:00"),
    },
    "e-standup-0606": {
        "iCalUId": "040000008200E00074C5B7101A82E008STANDUP01",
        "subject": "Site standup",
        "type": "occurrence",
        "seriesMasterId": "sm-standup",
        "isAllDay": False,
        "organizer": ("Ravi Patel", "ravi.patel@northline.example"),
        "UTC": ("2026-06-06T14:30:00", "2026-06-06T14:45:00"),
        "Pacific Standard Time": ("2026-06-06T07:30:00", "2026-06-06T07:45:00"),
    },
    "e-allhands": {
        "iCalUId": "040000008200E00074C5B7101A82E008ALLHANDS1",
        "subject": "Northline all hands",
        "type": "singleInstance",
        "seriesMasterId": None,
        "isAllDay": False,
        "organizer": ("Comms", "comms@northline.example"),
        "UTC": ("2026-06-05T15:00:00", "2026-06-05T16:00:00"),
        "Pacific Standard Time": ("2026-06-05T08:00:00", "2026-06-05T09:00:00"),
    },
    "e-inventory": {
        "iCalUId": "040000008200E00074C5B7101A82E008INVENTRY1",
        "subject": "Yard inventory day",
        "type": "singleInstance",
        "seriesMasterId": None,
        "isAllDay": True,
        "organizer": ("Yard Ops", "ops@northline.example"),
        "UTC": ("2026-06-07T00:00:00", "2026-06-08T00:00:00"),
        "Pacific Standard Time": ("2026-06-06T17:00:00", "2026-06-07T17:00:00"),
    },
}

# e-standup-0604 is replayed at the start of page 3 on purpose: delta-style
# duplicates across page boundaries are documented Graph behavior.
PAGES = [
    ["e-kickoff", "e-standup-0602"],
    ["e-standup-0604", "e-allhands"],
    ["e-standup-0604", "e-standup-0606", "e-inventory"],
]

STATE = {
    "log": [],
    "throttle": None,       # {"token", "times", "retry_after"}
    "fail": False,
    "serve_counts": {},     # page index -> times served
}


def wire_event(event_id, zone):
    spec = EVENTS[event_id]
    start, end = spec[zone]
    name, address = spec["organizer"]
    return {
        "id": event_id,
        "iCalUId": spec["iCalUId"],
        "subject": spec["subject"],
        "type": spec["type"],
        "seriesMasterId": spec["seriesMasterId"],
        "isAllDay": spec["isAllDay"],
        "organizer": {"emailAddress": {"name": name, "address": address}},
        "start": {"dateTime": start + ".0000000", "timeZone": zone},
        "end": {"dateTime": end + ".0000000", "timeZone": zone},
    }


def error_body(code, message):
    return {"error": {"code": code, "message": message}}


def normalize_query(query):
    return (query.replace("%24select", "$select")
                 .replace("%24skiptoken", "$skiptoken")
                 .replace("%24", "$"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/__log__":
            self._send(200, STATE["log"])
            return
        if parsed.path != CAL_PATH:
            self._send(404, error_body("ResourceNotFound", "No such endpoint: " + parsed.path))
            return

        query = normalize_query(parsed.query)
        auth = self.headers.get("Authorization") or ""
        prefer = self.headers.get("Prefer") or ""
        STATE["log"].append({
            "method": "GET", "path": parsed.path, "query": query,
            "auth": auth, "prefer": prefer,
        })

        if auth != "Bearer " + TOKEN:
            self._send(401, error_body("InvalidAuthenticationToken", "Access token is empty or invalid."))
            return
        if STATE["fail"]:
            self._send(503, error_body("serviceNotAvailable", "The service is temporarily unavailable."))
            return

        throttle = STATE["throttle"]
        if throttle and throttle["times"] > 0 and throttle["token"] in query:
            throttle["times"] -= 1
            self._send(429, error_body("TooManyRequests", "Please retry again later."),
                       {"Retry-After": str(throttle["retry_after"])})
            return

        match = PREFER_RE.match(prefer)
        if not match:
            self._send(400, error_body("invalidRequest",
                       'Prefer must be outlook.timezone="UTC" or '
                       '"Pacific Standard Time" on every request; got: ' + prefer))
            return
        zone = match.group(1)

        if query.startswith("$skiptoken="):
            if query == SKIP2:
                page_index = 1
            elif query == SKIP3:
                page_index = 2
            else:
                self._send(400, error_body("BadRequest",
                           "Unrecognized skiptoken; nextLink URLs must be reused "
                           "verbatim. Got query: " + query))
                return
        else:
            params = parse_qs(query, keep_blank_values=True)
            if sorted(params) != ["endDateTime", "startDateTime"]:
                self._send(400, error_body("BadRequest",
                           "calendarView requires exactly startDateTime and "
                           "endDateTime. Got query: " + query))
                return
            if params["startDateTime"] != [START] or params["endDateTime"] != [END]:
                self._send(400, error_body("BadRequest",
                           "Unexpected range values; the harness asked for "
                           f"{START}..{END}. Got query: " + query))
                return
            page_index = 0

        count = STATE["serve_counts"].get(page_index, 0)
        STATE["serve_counts"][page_index] = count + 1
        ids = list(PAGES[page_index])
        if count % 2 == 1:
            ids.reverse()

        body = {
            "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#users('{USER}')/calendarView",
            "value": [wire_event(eid, zone) for eid in ids],
        }
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if page_index == 0:
            body["@odata.nextLink"] = base + CAL_PATH + "?" + SKIP2
        elif page_index == 1:
            body["@odata.nextLink"] = base + CAL_PATH + "?" + SKIP3
        self._send(200, body)

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_body()
        if parsed.path == "/__reset__":
            STATE["log"].clear()
            STATE["throttle"] = None
            STATE["fail"] = False
            STATE["serve_counts"].clear()
            self._send(200, {"ok": True})
            return
        if parsed.path == "/__throttle__":
            spec = json.loads(body)
            STATE["throttle"] = {
                "token": spec["token"],
                "times": int(spec["times"]),
                "retry_after": int(spec["retry_after"]),
            }
            self._send(200, {"ok": True})
            return
        if parsed.path == "/__mode__":
            spec = json.loads(body)
            STATE["fail"] = bool(spec.get("fail", False))
            self._send(200, {"ok": True})
            return
        self._send(404, error_body("ResourceNotFound", "No such control endpoint"))


def main():
    port_file = sys.argv[1]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    tmp = port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(server.server_address[1]))
    os.replace(tmp, port_file)
    server.serve_forever()


if __name__ == "__main__":
    main()
