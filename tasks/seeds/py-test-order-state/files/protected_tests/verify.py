"""Run the direct regression and the pinned full suite in one process."""

from __future__ import annotations

import sys
import unittest

from . import seeded_runner


def main() -> int:
    direct = unittest.defaultTestLoader.loadTestsFromName(
        "protected_tests.test_default_registry"
    )
    direct_result = unittest.TextTestRunner(verbosity=2).run(direct)
    if not direct_result.wasSuccessful():
        return 1
    return seeded_runner.main(["--seed", "10101"])


if __name__ == "__main__":
    sys.exit(main())
