"""The package must stay standard-library only."""

import ast
import sys
import unittest
from pathlib import Path

from vcfops_triage import client as client_module

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "vcfops_triage"


class StandardLibraryOnlyTests(unittest.TestCase):
    def test_package_imports_only_stdlib_and_itself(self):
        for source_path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = [(node.module or "").split(".", 1)[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(
                        root,
                        sys.stdlib_module_names,
                        "{} imports non-stdlib module {!r}".format(source_path, root),
                    )


class TransportTests(unittest.TestCase):
    def test_requests_are_issued_through_the_module_level_urlopen(self):
        self.assertTrue(hasattr(client_module, "urlopen"))
        self.assertEqual(client_module.BASE_PATH, "/suite-api")


if __name__ == "__main__":
    unittest.main()
