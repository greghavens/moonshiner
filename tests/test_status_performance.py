"""Status counts the published corpus without parsing every training row."""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import moonshiner  # noqa: E402


class PublishedStatusCounts(unittest.TestCase):
    def test_seed_counts_separate_catalog_presence_from_trace_readiness(self):
        counts = moonshiner._seed_status_counts(
            planned={"ready", "replace", "new", "retired"},
            catalogued={"ready", "replace"},
            ready={"ready"},
            retired={"retired"},
        )
        self.assertEqual(counts, {
            "planned": 4,
            "catalogued": 2,
            "authored": 1,
            "requires_reauthoring": 1,
            "retired": 1,
            "waiting_first_authorship": 1,
            "waiting_total": 2,
        })

    def test_counts_rows_in_binary_blocks_without_json_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = pathlib.Path(directory) / "traces.jsonl"
            dataset.write_bytes(
                b'{"task":"one","messages":[{"content":"large"}]}\n'
                b'{"task":"one","messages":[{"content":"context"}]}\n'
                b'{"task":"two","messages":[{"content":"answer"}]}\n')

            with mock.patch.object(
                    moonshiner.json, "loads",
                    side_effect=AssertionError("status parsed a dataset row")):
                trajectories, rows = moonshiner._published_counts(dataset, 2)

            self.assertEqual((trajectories, rows), (2, 3))

    def test_missing_published_file_still_reports_acknowledged_trajectories(self):
        missing = pathlib.Path("/definitely/missing/moonshiner-traces.jsonl")
        self.assertEqual(moonshiner._published_counts(missing, 7), (7, 0))


if __name__ == "__main__":
    unittest.main()
