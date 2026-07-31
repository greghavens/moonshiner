"""Ordered forwarder-pair rollout orchestration."""

from __future__ import annotations

from typing import Any

from .client import ApiResponse, ForwarderSpec, VcfLogClient


# rollout_forwarder_pair returns a JSON-safe dict with this shape:
# {
#   "outcome": "succeeded" | "failed" | "partial_failure",
#   "steps": [
#     {
#       "operationId": str,
#       "target": str | None,
#       "status": "succeeded" | "failed",
#       "httpStatus": int,
#       # resourceId is present when a successful response contains id.
#       "resourceId": str,
#       # error is present only on failure and preserves the response body.
#       "error": object,
#     }
#   ],
# }


def rollout_forwarder_pair(
    client: VcfLogClient,
    primary: ForwarderSpec,
    secondary: ForwarderSpec,
) -> dict[str, Any]:
    """Create and enable primary, then create and enable secondary."""

    raise NotImplementedError
