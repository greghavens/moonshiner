import json
import unittest
from pathlib import Path

from request_metrics import Exemplar, RequestMetrics, RequestObservation


ROUTE = "/v1/users/{user_id}/orders"


def observation(
    *,
    target: str,
    route_template: str | None = ROUTE,
    method: str = "GET",
    status_code: int = 200,
    duration: float = 0.025,
    trace_id: str | None = None,
) -> RequestObservation:
    return RequestObservation(
        method=method,
        target=target,
        route_template=route_template,
        status_code=status_code,
        duration_seconds=duration,
        trace_id=trace_id,
    )


class IncidentEvidenceTests(unittest.TestCase):
    def test_snapshot_is_raw_request_cardinality_fanout(self) -> None:
        snapshot = Path(__file__).parents[1] / "incident" / "metric_samples.jsonl"
        samples = [json.loads(line) for line in snapshot.read_text().splitlines()]

        self.assertEqual(len(samples), 6)
        self.assertEqual({row["labels"]["method"] for row in samples}, {"GET"})
        self.assertEqual(
            {row["labels"]["status_class"] for row in samples}, {"2xx"}
        )
        self.assertEqual(
            len({row["labels"]["request"] for row in samples}), len(samples)
        )
        self.assertTrue(
            all("?cursor=" in row["labels"]["request"] for row in samples)
        )


class RequestMetricsTests(unittest.TestCase):
    def test_incident_requests_aggregate_under_the_route_template(self) -> None:
        metrics = RequestMetrics([ROUTE])
        metrics.observe(
            observation(
                target="/v1/users/user-104/orders?cursor=order-9001",
                duration=0.041,
            )
        )
        metrics.observe(
            observation(
                target="/v1/users/user-219/orders?cursor=order-9002",
                duration=0.038,
            )
        )
        metrics.observe(
            observation(
                target="/v1/users/user-337/orders?cursor=order-9003",
                duration=0.052,
            )
        )

        samples = metrics.collect()

        self.assertEqual(len(samples), 1)
        self.assertEqual(
            dict(samples[0].labels),
            {"method": "GET", "route": ROUTE, "status_class": "2xx"},
        )
        self.assertEqual(samples[0].count, 3)
        self.assertAlmostEqual(samples[0].total, 0.131)

    def test_missing_and_unconfigured_routes_share_the_unmatched_series(self) -> None:
        metrics = RequestMetrics([ROUTE])
        metrics.observe(
            observation(target="/health/private?id=one", route_template=None)
        )
        metrics.observe(
            observation(
                target="/plugins/customer-defined/value-two",
                route_template="/plugins/{customer_pattern}",
            )
        )

        samples = metrics.collect()

        self.assertEqual(len(samples), 1)
        self.assertEqual(
            dict(samples[0].labels),
            {"method": "GET", "route": "unmatched", "status_class": "2xx"},
        )
        self.assertNotIn("one", repr(samples[0].labels))
        self.assertNotIn("value-two", repr(samples[0].labels))

    def test_sampled_trace_remains_an_exemplar_not_a_label(self) -> None:
        metrics = RequestMetrics([ROUTE])
        metrics.observe(
            observation(
                target="/v1/users/user-104/orders?cursor=first",
                duration=0.075,
                trace_id="trace-slow-request",
            )
        )
        metrics.observe(
            observation(
                target="/v1/users/user-219/orders?cursor=second",
                duration=0.020,
                trace_id=None,
            )
        )

        samples = metrics.collect()

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].count, 2)
        self.assertAlmostEqual(samples[0].total, 0.095)
        self.assertEqual(
            samples[0].exemplar,
            Exemplar(trace_id="trace-slow-request", value=0.075),
        )
        self.assertNotIn("trace-slow-request", dict(samples[0].labels).values())

    def test_latest_sampled_observation_updates_the_exemplar(self) -> None:
        metrics = RequestMetrics([ROUTE])
        metrics.observe(
            observation(target="/v1/users/a/orders", trace_id="trace-a", duration=0.1)
        )
        metrics.observe(
            observation(target="/v1/users/b/orders", trace_id="trace-b", duration=0.2)
        )

        sample = metrics.collect()[0]

        self.assertEqual(sample.exemplar, Exemplar(trace_id="trace-b", value=0.2))

    def test_method_and_status_fallbacks_remain_bounded(self) -> None:
        metrics = RequestMetrics([ROUTE])
        metrics.observe(
            observation(
                target="/v1/users/a/orders",
                method="get",
                status_code=404,
            )
        )
        metrics.observe(
            observation(
                target="/v1/users/b/orders",
                method="BREW-user-123",
                status_code=799,
            )
        )

        labels = {tuple(sample.labels) for sample in metrics.collect()}

        self.assertEqual(
            labels,
            {
                (("method", "GET"), ("route", ROUTE), ("status_class", "4xx")),
                (
                    ("method", "OTHER"),
                    ("route", ROUTE),
                    ("status_class", "unknown"),
                ),
            },
        )

    def test_negative_duration_is_rejected_without_creating_a_series(self) -> None:
        metrics = RequestMetrics([ROUTE])

        with self.assertRaisesRegex(ValueError, "duration_seconds"):
            metrics.observe(
                observation(target="/v1/users/a/orders", duration=-0.001)
            )

        self.assertEqual(metrics.collect(), ())


if __name__ == "__main__":
    unittest.main()
