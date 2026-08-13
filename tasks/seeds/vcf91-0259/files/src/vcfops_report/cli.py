"""Command line entry point: ``python3 -m vcfops_report``.

Stub. Implement ``build_parser`` and ``main`` so that the command below runs the
full workflow and writes the report to disk::

    python3 -m vcfops_report \\
        --base-url http://127.0.0.1:8080/suite-api \\
        --username report-runner --password '...' \\
        --report-definition-id <uuid> --resource-id <uuid> \\
        --format CSV --output report.csv

Required options: --base-url, --username, --password, --report-definition-id,
--resource-id, --output.
Optional: --auth-source, --format (one of the contract's formatValues; omitted
means the caller did not choose one), --name, --description, --subject
(repeatable), --publish/--no-publish, --poll-interval, --poll-timeout,
--request-timeout.

On success: write the report bytes to --output, print exactly one JSON object
to stdout and exit 0::

    {"reportId": "...", "status": "COMPLETED", "polls": 3,
     "outputPath": "report.csv", "bytes": 61}

On failure: write nothing to --output, print exactly one JSON object to stderr
and exit 1::

    {"error": "ReportGenerationFailed", "message": "..."}

where "error" is the exception's class name.

The token must be released before the process exits, on the success path and on
the failure path alike.
"""

from __future__ import annotations

import argparse  # noqa: F401
import json  # noqa: F401
import sys  # noqa: F401

from .client import VcfOperationsClient  # noqa: F401
from .errors import VcfOperationsError  # noqa: F401


def build_parser():
    """Return the argparse.ArgumentParser for the command described above."""
    raise NotImplementedError


def main(argv=None):
    """Run the CLI. Returns the process exit status."""
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
