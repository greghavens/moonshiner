"""Run test modules in a reproducibly shuffled order."""

from __future__ import annotations

import argparse
import random
import sys
import unittest


TEST_MODULES = (
    "protected_tests.test_default_registry",
    "protected_tests.test_plugin_override",
)


def module_order(seed: int) -> list[str]:
    modules = list(TEST_MODULES)
    random.Random(seed).shuffle(modules)
    return modules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)

    modules = module_order(args.seed)
    print(f"test-order seed: {args.seed}", flush=True)
    print(f"module order: {', '.join(modules)}", flush=True)

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
