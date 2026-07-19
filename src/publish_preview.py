"""Compatibility tombstone for the removed continuous preview publisher.

Publishing partially completed runs is intentionally unsupported. Use the
validated, explicit ``moonshiner publish --yes`` path instead.
"""
from __future__ import annotations


def publish_once() -> None:
    raise SystemExit("preview publishing is disabled; use `moonshiner publish --yes`")


def main(argv=None) -> int:
    publish_once()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
