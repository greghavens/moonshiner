"""Invoice batches queued for the nightly billing export."""

import json


class TransientError(Exception):
    """The export endpoint hiccuped; the submission may be retried."""


class InvoiceBatch:
    """A group of invoices exported together, plus manual adjustments."""

    def __init__(self, batch_id, invoices=None, adjustments=[]):
        self.batch_id = batch_id
        self.invoices = list(invoices or [])
        self.adjustments = adjustments

    def add_adjustment(self, code, amount_cents):
        self.adjustments.append({"code": code, "amount_cents": amount_cents})

    def total_cents(self):
        invoiced = sum(i["amount_cents"] for i in self.invoices)
        adjusted = sum(a["amount_cents"] for a in self.adjustments)
        return invoiced + adjusted

    def to_payload(self):
        """The dict the export service expects."""
        return {
            "batch_id": self.batch_id,
            "invoices": self.invoices,
            "adjustments": self.adjustments,
            "total_cents": self.total_cents(),
        }

    def serialize(self):
        """Stable JSON for the wire and for de-duplication hashes."""
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


def submit_with_retry(batch, transport, max_attempts=3, attempt_log=[]):
    """POST a serialized batch, retrying transient failures.

    Each try appends (batch_id, attempt_number) to attempt_log; the response
    of the first successful try is returned.
    """
    while len(attempt_log) < max_attempts:
        attempt_log.append((batch.batch_id, len(attempt_log) + 1))
        try:
            return transport(batch.serialize())
        except TransientError as exc:
            last_error = exc
    raise last_error
