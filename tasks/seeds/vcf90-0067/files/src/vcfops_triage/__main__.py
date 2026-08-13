"""Command line entry point: ``python -m vcfops_triage``.

See README.md for the argument list, the exit codes and the behaviour expected of
the triage run, including how a token that expires mid-batch has to be handled.
Nothing here is implemented yet.
"""

import sys


def main(argv=None):
    raise NotImplementedError(
        "vcfops_triage is not implemented yet: see README.md and docs/contract.json"
    )


if __name__ == "__main__":
    sys.exit(main())
