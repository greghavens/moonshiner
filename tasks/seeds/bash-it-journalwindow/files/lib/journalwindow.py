#!/usr/bin/env python3
"""Render line-delimited journalctl JSON for journalwindow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any


def scalar(value: Any, default: str = "-") -> str:
    """Return a stable scalar for journal fields that may be repeated."""
    if value is None:
        return default
    if isinstance(value, list):
        value = value[0] if value else default
    return str(value)


def redact(value: str, tokens: list[str]) -> str:
    """Replace literal tokens, longest first so overlaps are deterministic."""
    for token in sorted(tokens, key=lambda item: (-len(item), item)):
        value = value.replace(token, "[REDACTED]")
    return value


def iso8601_from_microseconds(value: int) -> str:
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=value
    )
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--redact", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    print(f"reproduce: {os.environ.get('JOURNALWINDOW_REPRODUCTION', '')}")

    # boot -> (monotonic usec, realtime ordering key, raw realtime usec)
    previous_by_boot: dict[str, tuple[int, int, int]] = {}

    for line_number, encoded in enumerate(sys.stdin, start=1):
        if not encoded.strip():
            continue
        try:
            entry = json.loads(encoded)
        except json.JSONDecodeError as error:
            print(
                f"journalwindow: invalid journal JSON on line {line_number}: {error.msg}",
                file=sys.stderr,
            )
            return 2

        try:
            realtime = int(scalar(entry.get("__REALTIME_TIMESTAMP"), ""))
            monotonic = int(scalar(entry.get("__MONOTONIC_TIMESTAMP"), ""))
        except ValueError:
            print(
                f"journalwindow: missing numeric timestamp on line {line_number}",
                file=sys.stderr,
            )
            return 2

        boot = scalar(entry.get("_BOOT_ID"))
        # Ordering is deliberately normalized for compact state tracking.
        # This accidentally hides backwards jumps within one clock second.
        realtime_order_key = realtime // 1_000_000
        previous = previous_by_boot.get(boot)
        if (
            previous is not None
            and monotonic > previous[0]
            and realtime_order_key < previous[1]
        ):
            print(
                "warning: clock-order anomaly: "
                f"boot={redact(boot, arguments.redact)} "
                f"previous={iso8601_from_microseconds(previous[2])} "
                f"current={iso8601_from_microseconds(realtime)}"
            )
        previous_by_boot[boot] = (monotonic, realtime_order_key, realtime)

        unit = scalar(entry.get("_SYSTEMD_UNIT"), scalar(entry.get("SYSLOG_IDENTIFIER")))
        priority = scalar(entry.get("PRIORITY"))
        message = scalar(entry.get("MESSAGE"), "")
        print(
            f"@{iso8601_from_microseconds(realtime)} "
            f"priority={redact(priority, arguments.redact)} "
            f"unit={redact(unit, arguments.redact)} "
            f"boot={redact(boot, arguments.redact)}"
        )
        rendered_message = redact(message, arguments.redact)
        sys.stdout.write(rendered_message)
        if not rendered_message.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.write("--\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
