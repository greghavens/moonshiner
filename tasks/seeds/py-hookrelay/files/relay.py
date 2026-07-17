"""Outbound webhook relay for the notifications service.

Producers push events in with receive(); partner endpoints register with
add_subscriber(); dispatch() forwards every stored event to every
subscriber through the injected transport and remembers what has been
delivered, retrying anything still owed on the next run.

The transport is any callable ``(url, envelope_dict) -> truthy on
success``. Network specifics (and the scripted double in the tests) live
with the caller; the relay itself never opens a socket.
"""


class Delivery:
    """One (event, subscriber) forwarding obligation."""

    def __init__(self, delivery_id, event_id, event_type, subscriber_id):
        self.id = delivery_id
        self.event_id = event_id
        self.event = event_type
        self.subscriber_id = subscriber_id
        self.status = "pending"

    def __repr__(self):
        return "Delivery(%s %s -> %s: %s)" % (
            self.id, self.event_id, self.subscriber_id, self.status)


class Relay:
    def __init__(self, transport):
        self._transport = transport
        self._subscribers = {}  # subscriber_id -> {"url": ...}, insertion ordered
        self._events = []       # {"id", "event", "payload"} in arrival order
        self._deliveries = []
        self._by_key = {}       # (event_id, subscriber_id) -> Delivery
        self._event_seq = 0
        self._delivery_seq = 0

    def add_subscriber(self, subscriber_id, url):
        self._subscribers[subscriber_id] = {"url": url}

    def receive(self, event_type, payload):
        """Store an inbound event; forwarding happens on dispatch()."""
        self._event_seq += 1
        event_id = "evt-%d" % self._event_seq
        self._events.append({"id": event_id, "event": event_type, "payload": payload})
        return event_id

    def events(self):
        return list(self._events)

    def deliveries(self):
        return list(self._deliveries)

    def dispatch(self):
        """Forward everything still owed. Returns how many completed now."""
        self._create_missing()
        delivered = 0
        for delivery in self._deliveries:
            if delivery.status != "pending":
                continue
            if self._send(delivery):
                delivery.status = "delivered"
                delivered += 1
        return delivered

    def _create_missing(self):
        for event in self._events:
            for subscriber_id in self._subscribers:
                key = (event["id"], subscriber_id)
                if key in self._by_key:
                    continue
                self._delivery_seq += 1
                delivery = Delivery("dlv-%d" % self._delivery_seq,
                                    event["id"], event["event"], subscriber_id)
                self._by_key[key] = delivery
                self._deliveries.append(delivery)

    def _send(self, delivery):
        event = self._event(delivery.event_id)
        envelope = {"id": event["id"], "event": event["event"],
                    "payload": event["payload"]}
        url = self._subscribers[delivery.subscriber_id]["url"]
        try:
            return bool(self._transport(url, envelope))
        except Exception:
            return False

    def _event(self, event_id):
        for event in self._events:
            if event["id"] == event_id:
                return event
        raise KeyError(event_id)
