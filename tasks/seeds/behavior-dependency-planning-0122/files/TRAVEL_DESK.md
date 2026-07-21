# Local Travel Desk

`travelctl.py` is the command-line application for this sandbox. Run it from
the workspace root with Python 3. Each successful command prints a JSON result.

## Read operations

```text
python3 travelctl.py search --query TEXT --location CITY
python3 travelctl.py get RECORD_ID
```

Search matches names case-insensitively within the requested location. Search
and get are safe to run concurrently in background shell jobs. Each read takes
a short, fixed amount of time so independently launched operations overlap
observably in the application's audit trail.

## Write operations

```text
python3 travelctl.py update RECORD_ID --status STATUS
python3 travelctl.py notify RECORD_ID --message TEXT
```

The application owns `.travel/records.json`, `.travel/notifications.json`, and
`.travel/audit.jsonl`. Do not edit those files directly. Every Travel Desk
operation is audited, including unsuccessful attempts.
