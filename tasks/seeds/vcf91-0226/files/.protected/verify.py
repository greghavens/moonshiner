#!/usr/bin/env python3
"""Protected verification entry point.

Runs the wire-and-contract suite entirely offline against a loopback mock.
No VMware endpoint is contacted.

    python3 -B .protected/verify.py

PROTECTED: do not modify.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


def main():
    sys.dont_write_bytecode = True
    for path in (HERE, os.path.join(PROJECT_ROOT, "src")):
        if path not in sys.path:
            sys.path.insert(0, path)

    import test_wire_contract

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_wire_contract)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
