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


class CreditExhaustion(unittest.TestCase):
    """A provider that cannot afford the request is out of quota.

    OpenRouter answers 402 without reaching the model. Read as an ordinary
    failure it costs one of the seed's limited attempts, on every seed in the
    queue, for a condition that clears the moment credits are added.
    """

    def test_openrouter_402_is_a_usage_limit(self):
        message = ('402: {"message":"This request requires more credits, or '
                   'fewer max_tokens. You requested up to 122247 tokens, but '
                   'can only afford 84288.","code":402}')
        self.assertTrue(av.is_usage_limit(message))
        self.assertEqual(message, av.find_usage_limit(None, message))

    def test_zenmux_402_is_a_usage_limit(self):
        """Verbatim payload that stopped the seed queue on 2026-08-16.

        Unrecognised, this does not merely go unreported. The author session
        dies mid-turn, so no task.json is ever written, and the queue reports
        "author did not create task.json" -- a phantom authoring bug -- then
        exits INFRASTRUCTURE, which the supervisor will not restart. A quota
        that refills by itself then stays down until a human intervenes.
        """
        message = str({"name": "APIError", "data": {
            "message": "You have reached your subscription quota limit. Please "
                       "wait for automatic quota refresh in the rolling time "
                       "window, upgrade to a higher plan, or use a "
                       "Pay-As-You-Go API Key for unlimited access.",
            "statusCode": 402, "isRetryable": False,
            "responseBody": '{"error":{"code":"402","type":"quote_exceeded"}}'}})
        self.assertTrue(av.is_usage_limit(message))
        self.assertEqual(message, av.find_usage_limit(None, message))

    def test_the_provider_spelling_the_error_code_correctly_still_matches(self):
        """``quote_exceeded`` is ZenMux's own typo; assume it gets fixed."""
        self.assertTrue(av.is_usage_limit(
            '{"error":{"code":"402","type":"quota_exceeded"}}'))

    def test_an_ordinary_failure_is_not_a_usage_limit(self):
        self.assertFalse(av.is_usage_limit("connection reset by peer"))
        self.assertFalse(av.is_usage_limit(None))

    def test_pi_reports_a_quota_block_from_the_turn_error(self):
        """The reason arrives on the turn, not on a result event."""
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "runtimes" / "pi.py").read_text()
        block = source[source.index("def _parse_stream_meta"):
                       source.index("def _model_attested")]
        self.assertIn("errorMessage", block)
        self.assertIn("unavailable=availability.find_usage_limit",
                      source)


class AQuotaBlockCostsNoAttempt(unittest.TestCase):
    """A request the provider refused to serve is not an attempt.

    Attempts are a scarce, metered resource. A 402 never reaches the model,
    so recording one against the seed spends something for nothing — and a
    corpus-wide outage would exhaust every seed it touched.
    """

    def test_the_unavailable_check_precedes_any_record_of_the_attempt(self):
        source = (_ROOT / "src" / "generate_traces.py").read_text()
        body = source[source.index("def trace_task"):]
        raised = body.index("raise ModelUnavailable")
        for recorded in ("_write_meta(", "_deferral("):
            self.assertLess(raised, body.index(recorded),
                            f"{recorded} must not run before the quota check")

    def test_the_run_stops_rather_than_marking_the_seed(self):
        source = (_ROOT / "src" / "trace_pipeline.py").read_text()
        handler = source[source.index("except ModelUnavailable as blocked:"):]
        handler = handler[:handler.index("except BaseException")]
        self.assertIn("stop_claiming.set()", handler)
        self.assertNotIn("finish_attempt", handler)
        self.assertNotIn("set_job", handler)
