"""Inventory integration and the timeouts it owns."""

from .errors import InventoryUnavailable


class InventoryGateway:
    def __init__(
        self,
        transport,
        logger,
        *,
        request_timeout_ms=150,
        failure_detail_timeout_ms=75,
    ):
        self._transport = transport
        self._logger = logger
        self._request_timeout_ms = request_timeout_ms
        self._failure_detail_timeout_ms = failure_detail_timeout_ms

    def reserve(self, checkout_id, lines, budget):
        """Reserve all *lines*, raising a typed failure for non-success replies."""
        header_timeout_ms = min(
            self._request_timeout_ms,
            budget.remaining_ms(),
        )
        response = self._transport.reserve(
            checkout_id,
            lines,
            timeout_ms=header_timeout_ms,
        )
        self._logger.emit(
            "inventory.response",
            checkout_id=checkout_id,
            status=response.status,
            request_id=response.request_id,
        )

        if response.status == 200:
            return response.reservation_id

        # Failure response bodies are optional diagnostics. The response status
        # and request ID above are already authoritative.
        detail = response.read_body(timeout_ms=budget.remaining_ms())
        raise InventoryUnavailable(response.status, response.request_id, detail)

