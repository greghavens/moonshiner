"""Summaries of webserver access logs for the ops dashboard.

Log lines are tab-separated:  <ip> <method> <path> <status> <duration_ms>
Blank lines and lines starting with '#' are ignored, as are lines that do
not have exactly five fields.
"""

SLOW_MS = 500.0


def parse_entries(lines):
    """Yield one dict per well-formed log line."""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        ip, method, path, status, ms = parts
        yield {
            "ip": ip,
            "method": method,
            "path": path,
            "status": int(status),
            "ms": float(ms),
        }


def summarize(lines):
    """Build the report dict the dashboard renders for one log window."""
    entries = parse_entries(lines)

    total = 0
    for entry in entries:
        total += 1

    by_status = {}
    for entry in entries:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1

    slow_paths = sorted({e["path"] for e in entries if e["ms"] >= SLOW_MS})

    server_errors = sum(n for status, n in by_status.items() if status >= 500)
    return {
        "total": total,
        "by_status": by_status,
        "slow_paths": slow_paths,
        "error_rate": (server_errors / total) if total else 0.0,
    }
