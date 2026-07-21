import unittest
from unittest.mock import patch

from eventual_assertion.core import assert_eventually


class EventualAssertionBehaviorTests(unittest.TestCase):
    def test_returns_as_soon_as_assertion_passes(self):
        calls = []

        def passing_assertion():
            calls.append("called")

        assert_eventually(passing_assertion, timeout=0.25, interval=0.01)

        self.assertEqual(calls, ["called"])

    def test_timeout_reraises_most_recent_assertion_failure(self):
        attempts = []

        def failing_assertion():
            attempts.append(len(attempts) + 1)
            raise AssertionError(f"failure from attempt {attempts[-1]}")

        with (
            patch(
                "eventual_assertion.core.monotonic",
                side_effect=[10.0, 10.01, 10.06],
            ),
            patch("eventual_assertion.core.sleep") as mocked_sleep,
        ):
            with self.assertRaisesRegex(
                AssertionError,
                "failure from attempt 2",
            ):
                assert_eventually(
                    failing_assertion,
                    timeout=0.05,
                    interval=0.01,
                )

        self.assertEqual(attempts, [1, 2])
        mocked_sleep.assert_called_once_with(0.01)


if __name__ == "__main__":
    unittest.main()
