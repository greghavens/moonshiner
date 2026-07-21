import json
import unittest
from pathlib import Path

from checkout.budget import RequestBudget
from checkout.errors import (
    CheckoutRejected,
    InventoryUnavailable,
    TransportTimeout,
)
from checkout.inventory import InventoryGateway
from checkout.service import CheckoutService


ROOT = Path(__file__).resolve().parent


class LogicalClock:
    def __init__(self):
        self._now_ms = 0

    def now_ms(self):
        return self._now_ms

    def advance(self, milliseconds):
        self._now_ms += milliseconds


class RecordingLogger:
    def __init__(self, clock):
        self.clock = clock
        self.events = []

    def emit(self, event, **fields):
        self.events.append({"at_ms": self.clock.now_ms(), "event": event, **fields})


class InventoryResponse:
    def __init__(
        self,
        clock,
        *,
        status,
        request_id,
        reservation_id=None,
        detail=None,
        detail_delay_ms=0,
        detail_times_out=False,
    ):
        self.clock = clock
        self.status = status
        self.request_id = request_id
        self.reservation_id = reservation_id
        self.detail = detail
        self.detail_delay_ms = detail_delay_ms
        self.detail_times_out = detail_times_out
        self.read_timeouts = []

    def read_body(self, *, timeout_ms):
        self.read_timeouts.append(timeout_ms)
        if self.detail_times_out or self.detail_delay_ms > timeout_ms:
            self.clock.advance(timeout_ms)
            raise TransportTimeout("read_error_body", timeout_ms)
        self.clock.advance(self.detail_delay_ms)
        return self.detail


class ScriptedInventoryTransport:
    def __init__(self, clock, response, *, header_delay_ms=42):
        self.clock = clock
        self.response = response
        self.header_delay_ms = header_delay_ms
        self.calls = []

    def reserve(self, checkout_id, lines, *, timeout_ms):
        self.calls.append(
            {
                "checkout_id": checkout_id,
                "lines": list(lines),
                "timeout_ms": timeout_ms,
            }
        )
        if self.header_delay_ms > timeout_ms:
            self.clock.advance(timeout_ms)
            raise TransportTimeout("reserve_headers", timeout_ms)
        self.clock.advance(self.header_delay_ms)
        return self.response


class RecordingPayment:
    def __init__(self, payment_id="pay-123"):
        self.payment_id = payment_id
        self.calls = []

    def charge(self, checkout_id, reservation_id, lines, *, budget):
        self.calls.append(
            {
                "checkout_id": checkout_id,
                "reservation_id": reservation_id,
                "lines": list(lines),
                "remaining_ms": budget.remaining_ms(),
            }
        )
        return self.payment_id


def build_failure(
    *,
    budget_ms=2000,
    status=503,
    request_id="inv-7f3",
    detail_delay_ms=0,
    detail_times_out=True,
):
    clock = LogicalClock()
    logger = RecordingLogger(clock)
    response = InventoryResponse(
        clock,
        status=status,
        request_id=request_id,
        detail="inventory primary unavailable",
        detail_delay_ms=detail_delay_ms,
        detail_times_out=detail_times_out,
    )
    transport = ScriptedInventoryTransport(clock, response)
    inventory = InventoryGateway(transport, logger)
    payment = RecordingPayment()
    service = CheckoutService(inventory, payment, logger, clock)
    return clock, logger, response, transport, payment, service, budget_ms


class IncidentEvidenceTests(unittest.TestCase):
    def test_logs_and_waterfall_locate_the_stall_after_failure_headers(self):
        with (ROOT / "incident" / "checkout_logs.jsonl").open(encoding="utf-8") as stream:
            logs = [json.loads(line) for line in stream if line.strip()]
        trace = json.loads((ROOT / "incident" / "trace_waterfall.json").read_text())

        self.assertEqual({event["trace_id"] for event in logs}, {trace["trace_id"]})
        response = next(event for event in logs if event["event"] == "inventory.response")
        timeout = next(event for event in logs if event["event"] == "checkout.timed_out")
        self.assertEqual((response["at_ms"], response["status"]), (42, 503))
        self.assertEqual(timeout["at_ms"], trace["request_budget_ms"])

        spans = {span["name"]: span for span in trace["spans"]}
        headers = spans["inventory.response_headers"]
        detail = spans["inventory.read_failure_detail"]
        self.assertEqual(headers["end_ms"], detail["start_ms"])
        self.assertEqual(headers["attributes"]["http.status_code"], 503)
        self.assertEqual(detail["end_ms"] - detail["start_ms"], 1958)
        self.assertEqual(detail["attributes"]["timeout_ms"], 1958)


class TimeoutOwnershipTests(unittest.TestCase):
    def test_failure_detail_timeout_is_bounded_and_inventory_failure_survives(self):
        clock, logger, response, transport, payment, service, budget_ms = build_failure()

        with self.assertRaises(CheckoutRejected) as raised:
            service.submit("co-8841", [{"sku": "A-17", "quantity": 2}], budget_ms=budget_ms)

        self.assertEqual(clock.now_ms(), 117)
        self.assertEqual(response.read_timeouts, [75])
        self.assertEqual(transport.calls[0]["timeout_ms"], 150)
        self.assertEqual(payment.calls, [])

        inventory_error = raised.exception.__cause__
        self.assertIsInstance(inventory_error, InventoryUnavailable)
        self.assertEqual(inventory_error.status, 503)
        self.assertEqual(inventory_error.request_id, "inv-7f3")
        self.assertIsNone(inventory_error.detail)
        diagnostic_error = inventory_error.__cause__
        self.assertIsInstance(diagnostic_error, TransportTimeout)
        self.assertEqual(diagnostic_error.operation, "read_error_body")
        self.assertEqual(diagnostic_error.timeout_ms, 75)

        events = [event["event"] for event in logger.events]
        self.assertEqual(
            events,
            [
                "checkout.started",
                "inventory.response",
                "inventory.failure_detail_timeout",
                "checkout.rejected",
            ],
        )
        evidence = logger.events[2]
        expected_evidence = {
            "at_ms": 117,
            "event": "inventory.failure_detail_timeout",
            "checkout_id": "co-8841",
            "status": 503,
            "request_id": "inv-7f3",
            "configured_timeout_ms": 75,
            "remaining_budget_ms": 1958,
            "effective_timeout_ms": 75,
            "timeout_owner": "inventory_failure_diagnostics",
            "error_type": "TransportTimeout",
            "operation": "read_error_body",
            "error": "read_error_body timed out after 75 ms",
        }
        for field, expected in expected_evidence.items():
            self.assertEqual(evidence[field], expected)
        rejection = logger.events[3]
        self.assertEqual(rejection["status"], 503)
        self.assertEqual(rejection["request_id"], "inv-7f3")
        self.assertEqual(rejection["error_type"], "InventoryUnavailable")

    def test_shorter_checkout_budget_constrains_failure_diagnostics(self):
        clock, logger, response, _, payment, service, _ = build_failure(
            budget_ms=60,
            status=429,
            request_id="inv-short-2",
        )

        with self.assertRaises(CheckoutRejected) as raised:
            service.submit("co-short", [{"sku": "B-2", "quantity": 1}], budget_ms=60)

        self.assertEqual(clock.now_ms(), 60)
        self.assertEqual(response.read_timeouts, [18])
        self.assertEqual(payment.calls, [])
        self.assertEqual(raised.exception.__cause__.status, 429)
        self.assertEqual(raised.exception.__cause__.request_id, "inv-short-2")
        evidence = next(
            event
            for event in logger.events
            if event["event"] == "inventory.failure_detail_timeout"
        )
        self.assertEqual(evidence["configured_timeout_ms"], 75)
        self.assertEqual(evidence["remaining_budget_ms"], 18)
        self.assertEqual(evidence["effective_timeout_ms"], 18)
        self.assertEqual(evidence["timeout_owner"], "checkout_budget")
        self.assertEqual(evidence["status"], 429)
        self.assertEqual(evidence["request_id"], "inv-short-2")

    def test_readable_failure_detail_is_retained(self):
        clock, logger, response, _, payment, service, _ = build_failure(
            status=500,
            request_id="inv-detail-9",
            detail_delay_ms=4,
            detail_times_out=False,
        )

        with self.assertRaises(CheckoutRejected) as raised:
            service.submit("co-detail", [{"sku": "C-9", "quantity": 1}])

        self.assertEqual(clock.now_ms(), 46)
        self.assertEqual(response.read_timeouts, [75])
        self.assertEqual(payment.calls, [])
        inventory_error = raised.exception.__cause__
        self.assertEqual(inventory_error.status, 500)
        self.assertEqual(inventory_error.request_id, "inv-detail-9")
        self.assertEqual(inventory_error.detail, "inventory primary unavailable")
        self.assertIn("inventory primary unavailable", str(inventory_error))
        self.assertNotIn(
            "inventory.failure_detail_timeout",
            [event["event"] for event in logger.events],
        )
        rejection = logger.events[-1]
        self.assertEqual(rejection["status"], 500)
        self.assertEqual(rejection["request_id"], "inv-detail-9")
        self.assertEqual(rejection["detail"], "inventory primary unavailable")

    def test_header_timeout_remains_separate_and_budget_constrained(self):
        clock = LogicalClock()
        logger = RecordingLogger(clock)
        response = InventoryResponse(clock, status=200, request_id="unused")
        transport = ScriptedInventoryTransport(clock, response, header_delay_ms=500)
        gateway = InventoryGateway(transport, logger)

        with self.assertRaises(TransportTimeout) as owned:
            gateway.reserve("co-header", [], RequestBudget(clock, 2000))
        self.assertEqual(owned.exception.operation, "reserve_headers")
        self.assertEqual(owned.exception.timeout_ms, 150)

        clock = LogicalClock()
        logger = RecordingLogger(clock)
        response = InventoryResponse(clock, status=200, request_id="unused")
        transport = ScriptedInventoryTransport(clock, response, header_delay_ms=500)
        gateway = InventoryGateway(transport, logger)
        with self.assertRaises(TransportTimeout) as parent:
            gateway.reserve("co-header-short", [], RequestBudget(clock, 90))
        self.assertEqual(parent.exception.timeout_ms, 90)

    def test_successful_checkout_still_reserves_then_charges(self):
        clock = LogicalClock()
        logger = RecordingLogger(clock)
        response = InventoryResponse(
            clock,
            status=200,
            request_id="inv-ok-1",
            reservation_id="res-44",
        )
        transport = ScriptedInventoryTransport(clock, response, header_delay_ms=20)
        inventory = InventoryGateway(transport, logger)
        payment = RecordingPayment("pay-91")
        service = CheckoutService(inventory, payment, logger, clock)

        result = service.submit("co-ok", [{"sku": "D-4", "quantity": 3}])

        self.assertEqual(
            result,
            {
                "checkout_id": "co-ok",
                "reservation_id": "res-44",
                "payment_id": "pay-91",
            },
        )
        self.assertEqual(response.read_timeouts, [])
        self.assertEqual(transport.calls[0]["timeout_ms"], 150)
        self.assertEqual(payment.calls[0]["reservation_id"], "res-44")
        self.assertEqual(payment.calls[0]["remaining_ms"], 1980)
        self.assertEqual(
            [event["event"] for event in logger.events],
            ["checkout.started", "inventory.response", "checkout.completed"],
        )


if __name__ == "__main__":
    unittest.main()
