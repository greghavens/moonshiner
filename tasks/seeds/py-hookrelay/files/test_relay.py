"""Tests for the outbound webhook relay.

The EXISTING BEHAVIOR block passes against the shipped relay.py and must
keep passing byte-for-byte. The blocks after it cover the routing upgrade
(per-subscriber event filters, field-mapping transforms, the per-delivery
attempt log, and dead-lettering) and fail until it is implemented.

Run: python3 test_relay.py
"""
import copy


class ScriptedTransport:
    """Callable transport double with a per-URL script of outcomes.

    Outcomes are True (accepted), False (declined), or an Exception
    instance (raised). URLs without a script always accept. Every call is
    recorded with a deep copy of its envelope.
    """

    def __init__(self):
        self.calls = []  # (url, envelope) in call order
        self.script = {}

    def plan(self, url, *outcomes):
        self.script.setdefault(url, []).extend(outcomes)

    def __call__(self, url, envelope):
        self.calls.append((url, copy.deepcopy(envelope)))
        queued = self.script.get(url)
        outcome = queued.pop(0) if queued else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def urls(self):
        return [url for url, _ in self.calls]


# ================================================================ EXISTING BEHAVIOR

def test_receive_stores_events_in_order():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    e1 = relay.receive("invoice.paid", {"invoice": "INV-1", "total": 120})
    e2 = relay.receive("order.created", {"order": "ORD-7"})
    assert (e1, e2) == ("evt-1", "evt-2"), (e1, e2)
    assert relay.events() == [
        {"id": "evt-1", "event": "invoice.paid", "payload": {"invoice": "INV-1", "total": 120}},
        {"id": "evt-2", "event": "order.created", "payload": {"order": "ORD-7"}},
    ], relay.events()
    assert transport.calls == [], "receive() must not forward anything by itself"


def test_dispatch_fans_out_to_every_subscriber_with_the_full_envelope():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("crm", "https://crm.internal/hooks")
    relay.add_subscriber("ledger", "https://ledger.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1", "total": 120})
    done = relay.dispatch()
    assert done == 2, done
    envelope = {"id": "evt-1", "event": "invoice.paid",
                "payload": {"invoice": "INV-1", "total": 120}}
    assert transport.calls == [
        ("https://crm.internal/hooks", envelope),
        ("https://ledger.internal/hooks", envelope),
    ], transport.calls
    statuses = [(d.subscriber_id, d.status) for d in relay.deliveries()]
    assert statuses == [("crm", "delivered"), ("ledger", "delivered")], statuses


def test_delivered_events_are_not_resent():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("crm", "https://crm.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    assert relay.dispatch() == 1
    assert relay.dispatch() == 0
    assert len(transport.calls) == 1, transport.calls


def test_failed_delivery_stays_pending_and_is_retried():
    from relay import Relay
    transport = ScriptedTransport()
    transport.plan("https://crm.internal/hooks", False, True)
    relay = Relay(transport)
    relay.add_subscriber("crm", "https://crm.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    assert relay.dispatch() == 0
    (delivery,) = relay.deliveries()
    assert delivery.status == "pending", delivery.status
    assert relay.dispatch() == 1
    assert delivery.status == "delivered", delivery.status
    assert len(transport.calls) == 2
    assert transport.calls[0] == transport.calls[1], "the retry must resend the same envelope"


def test_subscribers_added_late_still_get_stored_events():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    assert relay.dispatch() == 0
    assert transport.calls == []
    relay.add_subscriber("crm", "https://crm.internal/hooks")
    assert relay.dispatch() == 1
    assert transport.urls() == ["https://crm.internal/hooks"]


# ================================================================ EVENT-TYPE FILTERS

def test_subscriber_event_filters_limit_what_gets_delivered():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("billing", "https://billing.internal/hooks",
                         events=["invoice.paid", "invoice.voided"])
    relay.add_subscriber("firehose", "https://firehose.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    relay.receive("customer.created", {"customer": "C-9"})
    relay.dispatch()
    # billing gets only the invoice event; the unfiltered subscriber gets both
    assert transport.urls() == [
        "https://billing.internal/hooks",
        "https://firehose.internal/hooks",
        "https://firehose.internal/hooks",
    ], transport.urls()
    keys = sorted((d.event_id, d.subscriber_id) for d in relay.deliveries())
    assert keys == [("evt-1", "billing"), ("evt-1", "firehose"), ("evt-2", "firehose")], (
        "a non-matching event must not even create a delivery record: %s" % keys)


def test_filter_wildcards_match_prefixes():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("orders", "https://orders.internal/hooks", events=["order.*"])
    relay.add_subscriber("all", "https://all.internal/hooks", events=["*"])
    relay.receive("order.created", {"order": "ORD-1"})
    relay.receive("order.refund.requested", {"order": "ORD-1"})
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    relay.dispatch()
    per_sub = {}
    for delivery in relay.deliveries():
        per_sub.setdefault(delivery.subscriber_id, []).append(delivery.event)
    assert per_sub["orders"] == ["order.created", "order.refund.requested"], per_sub
    assert per_sub["all"] == ["order.created", "order.refund.requested", "invoice.paid"], per_sub
    # "order.*" must not match a bare "order" prefix without the dot
    relay.receive("orders.export", {"n": 1})
    relay.dispatch()
    assert per_sub["orders"] == ["order.created", "order.refund.requested"], (
        "orders.export must not match order.*")


# ================================================================ FIELD-MAPPING TRANSFORMS

def test_mapping_reshapes_the_payload_with_dotted_paths():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("crm", "https://crm.internal/hooks", mapping={
        "amount": "invoice.total",
        "customer_email": "invoice.customer.email",
        "missing": "invoice.nope.deep",
    })
    relay.receive("invoice.paid", {
        "invoice": {
            "total": 1200,
            "customer": {"email": "kim@example.com"},
            "memo": "Q3 retainer",
        },
    })
    relay.dispatch()
    (call,) = transport.calls
    assert call[1] == {
        "id": "evt-1",
        "event": "invoice.paid",
        "payload": {"amount": 1200, "customer_email": "kim@example.com"},
    }, "mapped payloads carry exactly the mapped fields; unresolvable paths are omitted: %r" % (call[1],)


def test_unmapped_subscribers_still_get_the_full_payload():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("raw", "https://raw.internal/hooks")
    relay.add_subscriber("slim", "https://slim.internal/hooks", mapping={"total": "total"})
    relay.receive("invoice.paid", {"total": 55, "memo": "keep me"})
    relay.dispatch()
    by_url = dict(transport.calls)
    assert by_url["https://raw.internal/hooks"]["payload"] == {"total": 55, "memo": "keep me"}
    assert by_url["https://slim.internal/hooks"]["payload"] == {"total": 55}


# ================================================================ ATTEMPT LOG + DEAD LETTER

def test_every_attempt_is_logged_with_its_outcome():
    from relay import Relay
    transport = ScriptedTransport()
    transport.plan("https://crm.internal/hooks",
                   RuntimeError("connect timeout"), False, True)
    relay = Relay(transport)
    relay.add_subscriber("crm", "https://crm.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    relay.dispatch()
    relay.dispatch()
    relay.dispatch()
    (delivery,) = relay.deliveries()
    assert delivery.status == "delivered"
    assert relay.attempts(delivery.id) == [
        {"n": 1, "ok": False, "error": "connect timeout"},
        {"n": 2, "ok": False, "error": None},
        {"n": 3, "ok": True, "error": None},
    ], relay.attempts(delivery.id)
    assert relay.attempts("dlv-does-not-exist") == []


def test_dead_letter_after_max_attempts_stops_the_retries():
    from relay import Relay
    transport = ScriptedTransport()
    transport.plan("https://flaky.internal/hooks", False, False, False)
    relay = Relay(transport, max_attempts=2)
    relay.add_subscriber("flaky", "https://flaky.internal/hooks")
    relay.add_subscriber("solid", "https://solid.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    relay.dispatch()
    relay.dispatch()
    flaky, solid = relay.deliveries()
    assert (flaky.subscriber_id, flaky.status) == ("flaky", "dead_letter"), flaky
    assert (solid.subscriber_id, solid.status) == ("solid", "delivered"), solid
    calls_so_far = len(transport.calls)
    relay.dispatch()
    assert len(transport.calls) == calls_so_far, "dead-lettered deliveries must never be retried"
    assert [d.id for d in relay.dead_letters()] == [flaky.id]
    assert len(relay.attempts(flaky.id)) == 2


def test_without_a_cap_failures_keep_retrying_forever():
    from relay import Relay
    transport = ScriptedTransport()
    transport.plan("https://flaky.internal/hooks", False, False, False, True)
    relay = Relay(transport)
    relay.add_subscriber("flaky", "https://flaky.internal/hooks")
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    for _ in range(3):
        relay.dispatch()
    (delivery,) = relay.deliveries()
    assert delivery.status == "pending", "no max_attempts means no dead-lettering"
    assert relay.dead_letters() == []
    relay.dispatch()
    assert delivery.status == "delivered"
    assert len(relay.attempts(delivery.id)) == 4


def test_filters_and_mapping_compose_on_one_subscriber():
    from relay import Relay
    transport = ScriptedTransport()
    relay = Relay(transport)
    relay.add_subscriber("digest", "https://digest.internal/hooks",
                         events=["order.*"], mapping={"ref": "order.ref"})
    relay.receive("order.created", {"order": {"ref": "ORD-77", "lines": [1, 2]}})
    relay.receive("invoice.paid", {"invoice": "INV-1"})
    relay.dispatch()
    (call,) = transport.calls
    assert call == ("https://digest.internal/hooks", {
        "id": "evt-1",
        "event": "order.created",
        "payload": {"ref": "ORD-77"},
    }), call


def main():
    # EXISTING BEHAVIOR — green against the shipped relay.py
    test_receive_stores_events_in_order()
    test_dispatch_fans_out_to_every_subscriber_with_the_full_envelope()
    test_delivered_events_are_not_resent()
    test_failed_delivery_stays_pending_and_is_retried()
    test_subscribers_added_late_still_get_stored_events()
    # ROUTING UPGRADE — red until the feature lands
    test_subscriber_event_filters_limit_what_gets_delivered()
    test_filter_wildcards_match_prefixes()
    test_mapping_reshapes_the_payload_with_dotted_paths()
    test_unmapped_subscribers_still_get_the_full_payload()
    test_every_attempt_is_logged_with_its_outcome()
    test_dead_letter_after_max_attempts_stops_the_retries()
    test_without_a_cap_failures_keep_retrying_forever()
    test_filters_and_mapping_compose_on_one_subscriber()
    print("ok")


if __name__ == "__main__":
    main()
