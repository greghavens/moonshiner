from __future__ import annotations

import unittest

import test_journalwindow


class SubsecondClockRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = test_journalwindow.JournalWindowTest(
            "test_entries_from_different_boots_are_not_compared"
        )
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def test_subsecond_clock_reversal_is_reported(self) -> None:
        result = self.harness.run_program(
            [
                test_journalwindow.record(
                    1_700_000_002_900_001, 10, "before adjustment"
                ),
                test_journalwindow.record(
                    1_700_000_002_900_000, 20, "after adjustment"
                ),
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "warning: clock-order anomaly: boot=boot-a "
            "previous=2023-11-14T22:13:22.900001Z "
            "current=2023-11-14T22:13:22.900000Z\n",
            result.stdout,
        )

    def test_non_anomalous_timestamp_pairs_do_not_warn(self) -> None:
        result = self.harness.run_program(
            [
                test_journalwindow.record(1_700_000_002_100_000, 10, "initial"),
                test_journalwindow.record(1_700_000_002_100_001, 20, "forward"),
                test_journalwindow.record(1_700_000_002_100_001, 30, "equal"),
                test_journalwindow.record(
                    1_700_000_002_100_000, 25, "monotonic did not advance"
                ),
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("clock-order anomaly", result.stdout)
