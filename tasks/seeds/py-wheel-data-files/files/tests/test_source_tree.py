from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from letterpress import render_welcome  # noqa: E402


class SourceTreeTests(unittest.TestCase):
    def test_template_exists_in_checkout(self) -> None:
        self.assertTrue(
            (PROJECT_ROOT / "src" / "letterpress" / "templates" / "welcome.txt").is_file()
        )

    def test_render_welcome(self) -> None:
        self.assertEqual(
            render_welcome("Ada"),
            "Hello, Ada!\nWelcome to Letterpress.\n",
        )


if __name__ == "__main__":
    unittest.main()
