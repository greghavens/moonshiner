"""``python3 -m vcfops_alerts`` -- print the alert collection as JSON.

    python3 -m vcfops_alerts --base-url URL --username U --password P \
        [--auth-source S] [--page-size N] [--resource-id ID ...] \
        [--alert-id ID ...]

The document on stdout is ``json.dumps(alerts, indent=2, sort_keys=True)``
followed by a newline, and is byte-identical from one run to the next.
"""

import sys


def build_parser():
    """The argument parser for the command."""
    raise NotImplementedError


def main(argv=None):
    """Return a process exit status: 0 on success, non-zero on failure."""
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
