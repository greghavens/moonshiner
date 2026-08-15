from __future__ import annotations

import json

from travel_planner import TravelPlanningSession


INITIAL = {
    "kind": "request",
    "request_hold": True,
    "trip": {
        "destination": "Lisbon",
        "travelers": ["Maya", "Jon"],
        "dates": {"start": "2026-10-08", "end": "2026-10-12"},
        "budget_usd": 2600,
        "lodging": {
            "area": "Alfama",
            "style": "small locally owned hotel",
            "rooms": 1,
        },
        "dietary": ["vegetarian-friendly"],
        "pace": "unhurried",
        "day_start": "09:30",
        "transport": "public transit and walking",
        "activity_days": {
            "Belém": 2,
            "Sintra day trip": 3,
            "fado performance": 2,
        },
        "avoid": ["rental car", "red-eye activities"],
    },
}

CLARIFICATION = {
    "kind": "clarification",
    "answers": {
        "arrival": "Lisbon airport at 15:00 on 2026-10-08",
        "departure": "Lisbon airport at 12:30 on 2026-10-12",
        "lodging": {"bed": "one queen"},
    },
}

CORRECTION = {
    "kind": "correction",
    "changes": {
        "lodging": {"area": "Baixa"},
        "activity_days": {"Sintra day trip": 4},
    },
}

FINAL = {
    "kind": "final",
    "confirmed": True,
    "action": "place_reservation_hold",
    "deliver": ["daily_plan", "lodging", "budget", "constraints"],
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_four_turn_delivery() -> None:
    session = TravelPlanningSession()

    first = session.handle(INITIAL)
    require(first["status"] in {"planning", "needs_clarification"}, "initial request was not accepted")
    require(session.reservation_requests == [], "a hold was recorded from the initial request")

    second = session.handle(CLARIFICATION)
    require(second["status"] == "planning", "clarification did not continue planning")
    require(session.reservation_requests == [], "a hold was recorded after clarification")

    third = session.handle(CORRECTION)
    require(third["status"] in {"planning", "revised"}, "correction did not revise the plan")
    require(session.reservation_requests == [], "a hold was recorded before explicit confirmation")

    state = session.state
    require(state["destination"] == "Lisbon", "correction discarded the destination")
    require(state["travelers"] == ["Maya", "Jon"], "correction discarded travelers")
    require(state["dates"] == {"start": "2026-10-08", "end": "2026-10-12"}, "dates were not retained")
    require(state["budget_usd"] == 2600, "budget was not retained")
    require(state["arrival"].startswith("Lisbon airport at 15:00"), "clarified arrival was not retained")
    require(state["departure"].startswith("Lisbon airport at 12:30"), "clarified departure was not retained")
    require(
        state["lodging"]
        == {
            "area": "Baixa",
            "style": "small locally owned hotel",
            "rooms": 1,
            "bed": "one queen",
        },
        "narrow lodging correction replaced unrelated lodging details",
    )
    require(
        state["activity_days"]
        == {"Belém": 2, "Sintra day trip": 4, "fado performance": 2},
        "narrow schedule correction replaced unrelated activities",
    )

    result = session.handle(FINAL)
    require(result["status"] == "confirmed", "final response is not marked confirmed")
    require(len(session.reservation_requests) == 1, "confirmation must record exactly one hold")
    require(result["action"] == session.reservation_requests[0], "reported action differs from recorded hold")
    require(result["action"]["lodging_area"] == "Baixa", "hold used stale lodging details")

    itinerary = result.get("itinerary")
    require(isinstance(itinerary, dict), "final response describes work instead of returning an itinerary")
    require(set(("days", "lodging", "budget", "constraints")) <= set(itinerary), "final deliverable is incomplete")

    days = itinerary["days"]
    require(len(days) == 5, "one daily entry is required for every trip date")
    require(
        [day["date"] for day in days]
        == ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"],
        "daily entries are not complete and chronological",
    )
    for day in days:
        require(day["start"] >= "09:30", f"day {day['day']} begins before the requested time")
        require(all(day.get(part) for part in ("morning", "afternoon", "evening", "transport", "meals")), f"day {day['day']} lacks useful detail")

    day_two = text(days[1])
    day_three = text(days[2])
    day_four = text(days[3])
    require("Belém" in day_two and "fado" in day_two, "retained day-two activities are missing")
    require("Sintra" not in day_three and "Sintra" in day_four, "newest Sintra correction was not applied narrowly")
    require("vegetarian" in text(days).lower(), "daily plan lacks dietary guidance")
    require("public transit" in text(days).lower(), "daily plan lacks transit guidance")

    require(
        itinerary["lodging"]
        == {
            "area": "Baixa",
            "style": "small locally owned hotel",
            "rooms": 1,
            "bed": "one queen",
        },
        "final lodging brief is stale or incomplete",
    )
    budget = itinerary["budget"]
    component_total = sum(value for key, value in budget.items() if key != "total")
    require(budget["total"] == 2600 and component_total == 2600, "budget does not reconcile to the retained limit")

    constraints = itinerary["constraints"]
    require(constraints["budget_usd"] == 2600, "constraint recap lost the budget")
    require(constraints["pace"] == "unhurried", "constraint recap lost the pace")
    require(constraints["day_start"] == "09:30", "constraint recap lost the start time")
    require(constraints["transport"] == "public transit and walking", "constraint recap lost transit")
    require(constraints["dietary"] == ["vegetarian-friendly"], "constraint recap lost dietary needs")
    require(constraints["avoid"] == ["rental car", "red-eye activities"], "constraint recap lost exclusions")


def test_non_boolean_confirmation_has_no_consequence() -> None:
    session = TravelPlanningSession()
    session.handle(INITIAL)
    session.handle({**FINAL, "confirmed": "yes"})
    require(session.reservation_requests == [], "non-boolean confirmation triggered a hold")


def main() -> None:
    test_four_turn_delivery()
    test_non_boolean_confirmation_has_no_consequence()
    print("ok: corrections retained, confirmation gated, complete itinerary delivered")


if __name__ == "__main__":
    main()
