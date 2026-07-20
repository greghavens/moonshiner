import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publish import _verify_remote_card, publication_files


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RemoteCardVerification(unittest.TestCase):
    def test_publication_files_exclude_local_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("traces.jsonl", "README.md", "moonshiner-dataset-banner.png",
                         "traces.jsonl.pre-1661"):
                (root / name).write_bytes(b"fixture")
            self.assertEqual({path.name for path in publication_files(root)},
                             {"traces.jsonl", "README.md",
                              "moonshiner-dataset-banner.png"})

    def test_accepts_exact_live_card(self):
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory) / "README.md"
            card.write_bytes(b"current card\n")
            with patch("publish.urllib.request.urlopen",
                       return_value=_Response(card.read_bytes())):
                _verify_remote_card("owner/dataset", card, "token")

    def test_rejects_stale_live_card(self):
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory) / "README.md"
            card.write_bytes(b"current card\n")
            with patch("publish.urllib.request.urlopen",
                       return_value=_Response(b"old card\n")):
                with self.assertRaisesRegex(RuntimeError, "failed remote verification"):
                    _verify_remote_card("owner/dataset", card, "token")


if __name__ == "__main__":
    unittest.main()
