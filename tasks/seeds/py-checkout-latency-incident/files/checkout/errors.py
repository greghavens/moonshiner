"""Typed failures crossing checkout dependency boundaries."""


class TransportTimeout(TimeoutError):
    def __init__(self, operation, timeout_ms):
        self.operation = operation
        self.timeout_ms = timeout_ms
        super().__init__(f"{operation} timed out after {timeout_ms} ms")


class InventoryUnavailable(RuntimeError):
    def __init__(self, status, request_id, detail=None):
        self.status = status
        self.request_id = request_id
        self.detail = detail
        message = f"inventory returned {status} (request {request_id})"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class CheckoutRejected(RuntimeError):
    def __init__(self, checkout_id, reason):
        self.checkout_id = checkout_id
        self.reason = reason
        super().__init__(f"checkout {checkout_id} rejected: {reason}")


class CheckoutTimedOut(TimeoutError):
    def __init__(self, checkout_id):
        self.checkout_id = checkout_id
        super().__init__(f"checkout {checkout_id} timed out")

