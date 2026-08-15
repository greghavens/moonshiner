"""Stateful travel-planning conversation used by the local handoff service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class TravelPlanningSession:
    """Accumulates a trip request and returns a response for each user turn."""

    def __init__(self) -> None:
        self._trip: dict[str, Any] = {}
        self.reservation_requests: list[dict[str, Any]] = []

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._trip)

    def _merge(self, updates: dict[str, Any]) -> None:
        self._trip.update(deepcopy(updates))

    def _record_hold(self) -> dict[str, Any]:
        hold = {
            "type": "reservation_hold",
            "destination": self._trip.get("destination"),
            "dates": deepcopy(self._trip.get("dates")),
            "lodging_area": self._trip.get("lodging", {}).get("area"),
        }
        self.reservation_requests.append(hold)
        return hold

    def handle(self, turn: dict[str, Any]) -> dict[str, Any]:
        kind = turn.get("kind")

        if kind == "request":
            self._trip = deepcopy(turn.get("trip", {}))
        elif kind == "clarification":
            self._merge(turn.get("answers", {}))
        elif kind == "correction":
            self._trip = deepcopy(turn.get("changes", {}))

        action = None
        if turn.get("request_hold") or turn.get("action") == "place_reservation_hold":
            action = self._record_hold()

        if kind == "final":
            return {
                "status": "draft",
                "message": "I would prepare a complete itinerary after reviewing the updates.",
                "action": action,
            }

        return {"status": "planning", "trip": self.state, "action": action}
