"""The unsafe continuous publisher stays permanently disabled."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import publish_preview


class DisabledPreview(unittest.TestCase):
    def test_preview_publication_is_disabled(self):
        with self.assertRaisesRegex(SystemExit, "disabled"):
            publish_preview.publish_once()


if __name__ == "__main__":
    unittest.main()
