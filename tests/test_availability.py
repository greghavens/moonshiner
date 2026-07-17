"""Usage-limit backoff: reset-time parsing and marker lifecycle. Model-free."""
import pathlib
import sys
import unittest
from datetime import datetime

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from runtimes import availability as av  # noqa: E402

FIXED_NOW = datetime(2030, 1, 1, 9, 0).astimezone()
LIMIT_MSG = "you've hit your usage limit. try again at Jan 2nd, 2030 5:00 PM."


class ParseRetryAt(unittest.TestCase):
    def test_parses_usage_limit_reset(self):
        got = av.parse_retry_at(LIMIT_MSG, FIXED_NOW)
        self.assertIsNotNone(got)
        self.assertEqual((got.year, got.month, got.day, got.hour),
                         (2030, 1, 2, 17))

    def test_non_limit_message_is_ignored(self):
        self.assertIsNone(av.parse_retry_at("connection reset by peer", FIXED_NOW))

    def test_limit_without_reset_time_is_none(self):
        self.assertIsNone(av.parse_retry_at("you've hit your usage limit", FIXED_NOW))


class MarkerLifecycle(unittest.TestCase):
    RUNTIME = "unit-test-fake"

    def tearDown(self):
        av._marker(self.RUNTIME).unlink(missing_ok=True)

    def test_future_block_is_active_and_fails_closed(self):
        self.assertIsNotNone(
            av.record_block(self.RUNTIME, LIMIT_MSG, "test", FIXED_NOW))
        self.assertIsNotNone(av.active_block(self.RUNTIME, FIXED_NOW))
        with self.assertRaises(av.ModelUnavailable):
            av.require_available(self.RUNTIME, FIXED_NOW)

    def test_expired_block_clears_and_allows(self):
        av.record_block(self.RUNTIME, LIMIT_MSG, "test", FIXED_NOW)
        later = datetime(2030, 1, 3, 9, 0).astimezone()
        self.assertIsNone(av.active_block(self.RUNTIME, later))
        self.assertFalse(av._marker(self.RUNTIME).exists())
        av.require_available(self.RUNTIME, later)  # must not raise

    def test_non_limit_message_records_nothing(self):
        self.assertIsNone(
            av.record_block(self.RUNTIME, "random error", "test", FIXED_NOW))
        self.assertFalse(av._marker(self.RUNTIME).exists())


if __name__ == "__main__":
    unittest.main()
