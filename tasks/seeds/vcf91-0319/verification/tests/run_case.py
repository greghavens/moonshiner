#!/usr/bin/env python3
"""Run one solution call in a fresh interpreter and capture its return value."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: run_case.py ARGS_JSON OUTPUT_JSON")
    args_path, output_path = Path(argv[1]), Path(argv[2])
    call = json.loads(args_path.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))

    payload: dict[str, object]
    try:
        from vcfa_change import apply_catalog_change

        result = apply_catalog_change(**call)
        payload = {"ok": True, "result": result}
        json.dumps(payload)
    except BaseException as error:  # noqa: BLE001 - report whatever went wrong
        payload = {
            "ok": False,
            "errorType": type(error).__name__,
            "traceback": traceback.format_exc(),
        }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
