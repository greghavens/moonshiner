"""Usage-limit detection. Live-only: nothing about a limit is ever persisted."""
import contextlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from runtimes import availability as av  # noqa: E402


@contextlib.contextmanager
def _isolated_runs():
    """Point the module at a scratch runs/ directory.

    ``common.RUNS`` resolves to the real project state, and purging markers is
    a destructive operation — a test must never reach live pipeline state.
    """
    with tempfile.TemporaryDirectory() as name:
        runs = pathlib.Path(name)
        with mock.patch.object(av, "RUNS", runs):
            yield runs

LIMIT_MSG = ("You've hit your usage limit. Visit https://example.invalid/usage "
             "to purchase more credits or try again at Jan 2nd, 2030 5:00 PM.")


class DetectUsageLimit(unittest.TestCase):
    def test_detects_usage_limit_notice(self):
        self.assertTrue(av.is_usage_limit(LIMIT_MSG))

    def test_ignores_unrelated_errors(self):
        self.assertFalse(av.is_usage_limit("connection reset by peer"))
        self.assertFalse(av.is_usage_limit(""))
        self.assertFalse(av.is_usage_limit(None))

    def test_find_returns_first_matching_message(self):
        self.assertEqual(av.find_usage_limit("", None, LIMIT_MSG), LIMIT_MSG.strip())
        self.assertIsNone(av.find_usage_limit("boom", None))


class NeverPersisted(unittest.TestCase):
    """A quoted reset time must never become durable state.

    Providers move reset times and credits can be purchased mid-run, so a
    persisted block outlives the condition it describes and blocks a product
    that is no longer limited.
    """

    def test_module_exposes_no_marker_or_block_api(self):
        for removed in ("_marker", "record_block", "active_block",
                        "require_available", "parse_retry_at",
                        "record_from_messages"):
            self.assertFalse(hasattr(av, removed),
                             f"{removed} reintroduces persisted usage limits")

    def test_detection_writes_nothing_under_runs(self):
        with _isolated_runs() as runs:
            av.find_usage_limit(LIMIT_MSG)
            self.assertEqual(list(runs.iterdir()), [])

    def test_purge_removes_markers_left_by_earlier_releases(self):
        with _isolated_runs() as runs:
            marker = runs / "model-unavailable-codex.json"
            marker.write_text(json.dumps({"retry_at": "2030-01-02T17:00:00"}))
            keep = runs / "trace-run-notes.json"
            keep.write_text("{}")
            self.assertEqual(av.purge_legacy_markers(), [marker])
            self.assertFalse(marker.exists())
            self.assertTrue(keep.exists(), "purge must not touch unrelated state")


if __name__ == "__main__":
    unittest.main()
