"""Allow ``python3 -m vcfops_report``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
