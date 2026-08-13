#!/usr/bin/env python3
"""Run one rollout case in a fresh interpreter and report the outcome as JSON.

Invoked by the protected verifier as::

    python3 -B .protected/run_case.py <case.json> <result.json>

The case file carries the runtime-generated base URL, credentials and plan.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: run_case.py <case.json> <result.json>", file=sys.stderr)
        return 2
    case = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result_path = Path(sys.argv[2])

    outcome: dict[str, object]
    try:
        import vcfon_tiers

        kwargs = {
            "page_size": case["page_size"],
            "timeout_seconds": case.get("timeout_seconds", 30.0),
        }
        if case.get("domain") is not None:
            kwargs["domain"] = case["domain"]
        report = vcfon_tiers.run_tier_rollout(
            case["base_url"],
            case["username"],
            case["password"],
            case["plan"],
            **kwargs,
        )
        outcome = {
            "status": "ok",
            "report": report,
            "reportKeyOrder": list(report) if isinstance(report, dict) else None,
        }
    except BaseException as error:  # noqa: BLE001 - the verifier judges the type
        module = type(error).__module__
        outcome = {
            "status": "error",
            "errorType": type(error).__name__,
            "errorModule": module,
            "errorMro": [
                base.__name__
                for base in type(error).__mro__
                if base is not object
            ],
            "errorMessage": str(error),
            "traceback": traceback.format_exc(),
        }
        try:
            import vcfon_tiers

            outcome["isTokenRefreshError"] = isinstance(
                error, vcfon_tiers.TokenRefreshError
            )
            outcome["isApiError"] = isinstance(error, vcfon_tiers.ApiError)
            outcome["isVcfOnError"] = isinstance(error, vcfon_tiers.VcfOnError)
            outcome["errorStatus"] = getattr(error, "status", None)
            outcome["errorCode"] = getattr(error, "code", None)
            outcome["apiMessage"] = getattr(error, "message", None)
        except BaseException:  # noqa: BLE001 - import itself may be what failed
            outcome["isTokenRefreshError"] = False
            outcome["isApiError"] = False
            outcome["isVcfOnError"] = False

    result_path.write_text(
        json.dumps(outcome, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
