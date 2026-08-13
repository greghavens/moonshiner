"""CLI entry point: sweep VCF Operations alerts and write a JSON report.

    python3 -m vcfops_alerts --base-url http://127.0.0.1:PORT/suite-api \
        --username svc-ops --password '...' --page-size 3 \
        --active-only --criticality CRITICAL --criticality IMMEDIATE \
        --output report.json
"""

import argparse
import json
import sys

from .client import VcfOperationsClient
from .collect import DEFAULT_DETAIL_LEVELS, sweep_alerts


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vcfops_alerts")
    ap.add_argument("--base-url", required=True, help="e.g. https://ops.example.com/suite-api")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--auth-source", default=None, help="optional; omitted when not given")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--active-only", action="store_true", default=False)
    ap.add_argument("--criticality", action="append", default=[])
    ap.add_argument("--alert-status", action="append", default=[])
    ap.add_argument("--alert-name", default=None)
    ap.add_argument("--resource-kind", default=None)
    ap.add_argument(
        "--detail-level",
        action="append",
        default=[],
        help="alertLevel values worth a getAlert call (default: CRITICAL, IMMEDIATE)",
    )
    ap.add_argument("--output", required=True)
    return ap.parse_args(argv)


def filters_from(args):
    """Only the filters the caller actually asked for."""
    filters = {}
    if args.active_only:
        filters["active_only"] = True
    if args.criticality:
        filters["criticality"] = list(args.criticality)
    if args.alert_status:
        filters["alert_status"] = list(args.alert_status)
    if args.alert_name:
        filters["alert_name"] = args.alert_name
    if args.resource_kind:
        filters["resource_kind"] = args.resource_kind
    return filters


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    client = VcfOperationsClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        auth_source=args.auth_source,
    )
    client.acquire_token()
    result = sweep_alerts(
        client,
        page_size=args.page_size,
        filters=filters_from(args),
        detail_levels=tuple(args.detail_level) or DEFAULT_DETAIL_LEVELS,
    )
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result.as_report(client), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
