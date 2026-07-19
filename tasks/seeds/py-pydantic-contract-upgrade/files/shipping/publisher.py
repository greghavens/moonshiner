import json

from orders.payload import OrderPayload


class OrderPublisher:
    def __init__(self, transport):
        self._transport = transport

    def publish(self, payload: dict[str, object]) -> bytes:
        order = OrderPayload.model_validate(payload)
        body = json.dumps(
            order.dict(by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._transport(body)
        return body
