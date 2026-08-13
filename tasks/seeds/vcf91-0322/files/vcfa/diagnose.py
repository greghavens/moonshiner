"""Triage a failed VCF Automation deployment and write a diagnosis report.

Run it as::

    python3 -m vcfa.diagnose --base-url URL --tenant TENANT --api-token TOKEN \
        --deployment NAME --out diagnosis.json

The report written to ``--out`` is a JSON object whose keys are exactly ``REPORT_KEYS``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .client import VcfAutomationClient

#: The exact key set of the report written to ``--out``.
REPORT_KEYS = (
    "deployment_id",
    "deployment_name",
    "deployment_status",
    "failed_request_id",
    "failed_action_id",
    "failed_event_id",
    "failed_resource_name",
    "failed_resource_id",
    "root_cause_code",
    "root_cause_message",
    "root_cause_log_row",
    "classification",
    "dismissed_request_id",
    "resubmitted_request_id",
)


def triage(client: VcfAutomationClient, deployment_name: str) -> Dict[str, Any]:
    """Diagnose the named deployment's failure and return the report object.

    The returned mapping must carry exactly the keys in ``REPORT_KEYS``.
    """
    raise NotImplementedError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vcfa.diagnose", description=__doc__)
    parser.add_argument("--base-url", required=True, help="VCF Automation base URL.")
    parser.add_argument("--tenant", required=True, help="Tenant (organization) name.")
    parser.add_argument("--api-token", required=True, help="VCF Automation API token.")
    parser.add_argument("--deployment", required=True, help="Exact deployment name.")
    parser.add_argument("--out", required=True, help="Path to write the report to.")
    args = parser.parse_args(argv)

    client = VcfAutomationClient(
        base_url=args.base_url, tenant=args.tenant, api_token=args.api_token
    )
    report = triage(client, args.deployment)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
