import ast
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StandardLibraryOnlyTests(unittest.TestCase):
    def test_package_imports_only_stdlib_and_itself(self):
        for source_path in sorted((ROOT / "vcf_operations").glob("*.py")):
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=str(source_path),
            )
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


if __name__ == "__main__":
    unittest.main()
