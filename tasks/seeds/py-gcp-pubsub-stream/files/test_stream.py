"""Acceptance tests for the pubstream subscriber package.

Everything runs through the pinned google-cloud-pubsub client library's own
injectable subscriber surface: real FlowControl values, real subscriber
Message objects wired to a local request queue (so ack/nack/modack use the
library's genuine dispatch semantics), and a fake SubscriberClient /
StreamingPullFuture pair mirroring the real subscribe()/cancel()/result()
contract. No network, no real project, no credentials, no sleeps.
"""

import queue

import pytest
from google.cloud.pubsub_v1.subscriber._protocol import requests as sub_requests
from google.cloud.pubsub_v1.subscriber.message import Message
from google.cloud.pubsub_v1.types import FlowControl
from google.pubsub_v1 import PubsubMessage

from pubstream import LeaseHandle, StreamRunner, StreamSettings, StreamWorker

PROJECT = "ingest-lab"
SUBSCRIPTION = "frame-events"


def make_message(request_queue, *, data=b"payload", message_id="m-1",
                 attributes=None, ack_id="ack-1", delivery_attempt=0):
    pb = PubsubMessage(data=data, message_id=message_id,
                       attributes=attributes or {})._pb
    return Message(pb, ack_id, delivery_attempt, request_queue)


def drain(request_queue):
    items = []
    while not request_queue.empty():
        items.append(request_queue.get_nowait())
    return items


class FakeStreamingPullFuture:
    """Mirrors the StreamingPullFuture shutdown contract: the documented
    clean stop is cancel() first, then result() to wait it out."""

    def __init__(self):
        self.calls = []
        self.cancelled = False

    def cancel(self):
        self.calls.append(("cancel",))
        self.cancelled = True

    def result(self, timeout=None):
        if not self.cancelled:
            raise AssertionError(
                "result() before cancel() would block forever on a live stream")
        self.calls.append(("result", timeout))
        return None


class FakeSubscriber:
    """Records subscribe() exactly as the real SubscriberClient accepts it."""

    def __init__(self):
        self.subscriptions = []
        self.futures = []

    def subscription_path(self, project, name):
        return f"projects/{project}/subscriptions/{name}"

    def subscribe(self, subscription, callback, flow_control=(), scheduler=None,
                  use_legacy_flow_control=False, await_callbacks_on_shutdown=False):
        self.subscriptions.append({
            "subscription": subscription,
            "callback": callback,
            "flow_control": flow_control,
            "scheduler": scheduler,
            "use_legacy_flow_control": use_legacy_flow_control,
            "await_callbacks_on_shutdown": await_callbacks_on_shutdown,
        })
        future = FakeStreamingPullFuture()
        self.futures.append(future)
        return future


# ---- flow control settings ----

def test_flow_control_defaults_mirror_documented_values():
    fc = StreamSettings().flow_control()
    assert isinstance(fc, FlowControl)
    assert fc.max_messages == 1000
    assert fc.max_bytes == 104857600
    assert fc.max_lease_duration == 3600
    assert fc.min_duration_per_lease_extension == 0
    assert fc.max_duration_per_lease_extension == 0


def test_flow_control_custom_values_pass_through():
    settings = StreamSettings(
        max_messages=64,
        max_bytes=16 * 1024 * 1024,
        max_lease_duration=900,
        min_duration_per_lease_extension=30,
        max_duration_per_lease_extension=120,
    )
    fc = settings.flow_control()
    assert fc == FlowControl(
        max_bytes=16 * 1024 * 1024,
        max_messages=64,
        max_lease_duration=900,
        min_duration_per_lease_extension=30,
        max_duration_per_lease_extension=120,
    )


def test_flow_control_validation_rejects_bad_values():
    with pytest.raises(ValueError):
        StreamSettings(max_messages=0)
    with pytest.raises(ValueError):
        StreamSettings(max_bytes=0)
    with pytest.raises(ValueError):
        StreamSettings(max_duration_per_lease_extension=5)
    with pytest.raises(ValueError):
        StreamSettings(min_duration_per_lease_extension=700)
    with pytest.raises(ValueError):
        StreamSettings(min_duration_per_lease_extension=120,
                       max_duration_per_lease_extension=60)


# ---- worker ack/nack behavior ----

def test_successful_handler_acks_only_after_completion():
    rq = queue.Queue()
    seen = []

    def handler(data, attributes, lease):
        seen.append((data, attributes))
        lease.extend(120)

    worker = StreamWorker(handler)
    worker(make_message(rq, data=b"frame-0042", message_id="m-7",
                        attributes={"source": "cam-7"}, ack_id="ack-7"))

    assert seen == [(b"frame-0042", {"source": "cam-7"})]
    items = drain(rq)
    assert [type(item).__name__ for item in items] == ["ModAckRequest", "AckRequest"]
    modack, ack = items
    assert isinstance(modack, sub_requests.ModAckRequest)
    assert modack.ack_id == "ack-7"
    assert modack.seconds == 120
    assert isinstance(ack, sub_requests.AckRequest)
    assert ack.ack_id == "ack-7"
    assert ack.message_id == "m-7"
    assert worker.acked == ["m-7"]
    assert worker.nacked == []
    assert worker.failures == []


def test_failing_handler_nacks_and_records_failure():
    rq = queue.Queue()
    boom = RuntimeError("decoder blew up")

    def handler(data, attributes, lease):
        raise boom

    worker = StreamWorker(handler)
    worker(make_message(rq, message_id="m-9", ack_id="ack-9"))

    items = drain(rq)
    assert len(items) == 1
    nack = items[0]
    assert isinstance(nack, sub_requests.NackRequest)
    assert nack.ack_id == "ack-9"
    assert not any(isinstance(item, sub_requests.AckRequest) for item in items)
    assert worker.acked == []
    assert worker.nacked == ["m-9"]
    assert len(worker.failures) == 1
    assert worker.failures[0][0] == "m-9"
    assert worker.failures[0][1] is boom


def test_mixed_batch_acks_and_nacks_independently():
    rq = queue.Queue()

    def handler(data, attributes, lease):
        if attributes.get("poison") == "yes":
            raise ValueError("bad frame")

    worker = StreamWorker(handler)
    worker(make_message(rq, message_id="m-1", ack_id="ack-1"))
    worker(make_message(rq, message_id="m-2", ack_id="ack-2",
                        attributes={"poison": "yes"}))
    worker(make_message(rq, message_id="m-3", ack_id="ack-3"))

    items = drain(rq)
    acks = [i for i in items if isinstance(i, sub_requests.AckRequest)]
    nacks = [i for i in items if isinstance(i, sub_requests.NackRequest)]
    assert sorted(a.ack_id for a in acks) == ["ack-1", "ack-3"]
    assert [n.ack_id for n in nacks] == ["ack-2"]
    assert worker.acked == ["m-1", "m-3"]
    assert worker.nacked == ["m-2"]


# ---- lease extension ----

def test_lease_extension_goes_through_modack():
    rq = queue.Queue()
    message = make_message(rq, ack_id="ack-5")
    lease = LeaseHandle(message)
    lease.extend(60)
    lease.extend(600)
    items = drain(rq)
    assert [type(item).__name__ for item in items] == ["ModAckRequest", "ModAckRequest"]
    assert [item.seconds for item in items] == [60, 600]
    assert all(item.ack_id == "ack-5" for item in items)
    assert lease.extensions == [60, 600]


def test_lease_extension_bounds_follow_the_documented_deadline_range():
    rq = queue.Queue()
    lease = LeaseHandle(make_message(rq))
    # 600 is the documented modifyAckDeadline maximum; below 10 the client
    # docs advise against (and 0 would behave like a nack).
    with pytest.raises(ValueError):
        lease.extend(9)
    with pytest.raises(ValueError):
        lease.extend(601)
    with pytest.raises(ValueError):
        lease.extend(0)
    assert drain(rq) == []
    assert lease.extensions == []


# ---- runner lifecycle ----

def test_runner_subscribes_with_configured_flow_control():
    fake = FakeSubscriber()
    settings = StreamSettings(max_messages=25, max_bytes=5 * 1024 * 1024,
                              max_lease_duration=300)
    worker = StreamWorker(lambda data, attributes, lease: None)
    runner = StreamRunner(fake, PROJECT, SUBSCRIPTION, settings, worker)
    future = runner.start()

    assert len(fake.subscriptions) == 1
    sub = fake.subscriptions[0]
    assert sub["subscription"] == "projects/ingest-lab/subscriptions/frame-events"
    assert sub["callback"] is worker
    assert isinstance(sub["flow_control"], FlowControl)
    assert sub["flow_control"].max_messages == 25
    assert sub["flow_control"].max_bytes == 5 * 1024 * 1024
    assert sub["flow_control"].max_lease_duration == 300
    assert sub["await_callbacks_on_shutdown"] is False
    assert future is fake.futures[0]
    assert runner.is_running


def test_runner_passes_await_callbacks_on_shutdown():
    fake = FakeSubscriber()
    runner = StreamRunner(fake, PROJECT, SUBSCRIPTION, StreamSettings(),
                          StreamWorker(lambda data, attributes, lease: None),
                          await_callbacks_on_shutdown=True)
    runner.start()
    assert fake.subscriptions[0]["await_callbacks_on_shutdown"] is True


def test_runner_rejects_double_start_and_stop_without_start():
    fake = FakeSubscriber()
    runner = StreamRunner(fake, PROJECT, SUBSCRIPTION, StreamSettings(),
                          StreamWorker(lambda data, attributes, lease: None))
    with pytest.raises(RuntimeError):
        runner.stop()
    runner.start()
    with pytest.raises(RuntimeError):
        runner.start()
    assert len(fake.subscriptions) == 1


def test_runner_stop_cancels_then_waits():
    fake = FakeSubscriber()
    runner = StreamRunner(fake, PROJECT, SUBSCRIPTION, StreamSettings(),
                          StreamWorker(lambda data, attributes, lease: None))
    runner.start()
    runner.stop(timeout=5.0)
    future = fake.futures[0]
    assert future.calls == [("cancel",), ("result", 5.0)]
    assert not runner.is_running
    with pytest.raises(RuntimeError):
        runner.stop()


def test_end_to_end_dispatch_through_runner():
    fake = FakeSubscriber()
    rq = queue.Queue()

    def handler(data, attributes, lease):
        if data == b"bad":
            raise RuntimeError("cannot process")
        lease.extend(45)

    worker = StreamWorker(handler)
    runner = StreamRunner(fake, PROJECT, SUBSCRIPTION, StreamSettings(), worker)
    runner.start()

    callback = fake.subscriptions[0]["callback"]
    callback(make_message(rq, data=b"good", message_id="m-20", ack_id="ack-20"))
    callback(make_message(rq, data=b"bad", message_id="m-21", ack_id="ack-21"))

    runner.stop(timeout=2.0)

    items = drain(rq)
    kinds = [type(item).__name__ for item in items]
    assert kinds == ["ModAckRequest", "AckRequest", "NackRequest"]
    assert items[1].ack_id == "ack-20"
    assert items[2].ack_id == "ack-21"
    assert worker.acked == ["m-20"]
    assert worker.nacked == ["m-21"]
    assert fake.futures[0].calls == [("cancel",), ("result", 2.0)]
