"""Deterministic entry point for the protected acceptance suite."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
