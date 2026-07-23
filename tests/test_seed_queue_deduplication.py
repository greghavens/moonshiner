"""The one seed queue must deduplicate work across coordinator processes."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_queue  # noqa: E402


class SeedQueueDeduplication(unittest.TestCase):
    def test_competing_workers_author_an_id_exactly_once(self):
        authored = set()
        calls = []
        guard = threading.Lock()

        def run(command, cwd):
            with guard:
                calls.append(command)
            time.sleep(0.05)
            authored.add("seed-0001")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(seed_queue, "CLAIMS", pathlib.Path(directory)), \
             mock.patch.object(seed_queue, "authored_ids",
                               side_effect=lambda: set(authored)), \
             mock.patch.object(seed_queue, "load_seeds", return_value=[]), \
             mock.patch.object(seed_queue, "_moonshiner", return_value="moonshiner"), \
             mock.patch.object(seed_queue.subprocess, "run", side_effect=run):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _: seed_queue.author_one(
                        "seed-0001", {"seed-0001": "brief"}), range(2)))

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [("seed-0001", 0), ("seed-0001", 0)])


if __name__ == "__main__":
    unittest.main()
