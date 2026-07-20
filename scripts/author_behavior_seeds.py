#!/usr/bin/env python3
"""Author the checked-in 1,000-seed non-code external function-calling evaluations curriculum.

The output is deterministic. Existing identifiers are immutable: generation
accepts an identical file and refuses to overwrite a differing one.
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tasks" / "behavior-seeds"
WORLDS_PATH = ROOT / "tasks" / "behavior-worlds.json"

DOMAINS = [
    ("calendar", "meeting", ["Planning Review", "Budget Sync", "Vendor Briefing", "Hiring Panel"], ["Denver", "Chicago", "Boston", "Seattle"]),
    ("travel", "trip", ["Lisbon visit", "Kyoto visit", "Montreal visit", "Oslo visit"], ["Lisbon", "Kyoto", "Montreal", "Oslo"]),
    ("hospitality", "reservation", ["Juniper Table", "Harbor Room", "Maple Hall", "Orchid Suite"], ["Denver", "Portland", "Austin", "Raleigh"]),
    ("messaging", "message", ["Quarterly update", "Schedule change", "Welcome note", "Renewal reminder"], ["North Team", "South Team", "Vendors", "Volunteers"]),
    ("support", "case", ["Damaged parcel", "Duplicate charge", "Missing accessory", "Late delivery"], ["Acme", "Beacon", "Cedar", "Delta"]),
    ("commerce", "order", ["Pantry order", "Office order", "Gift order", "Supply order"], ["Denver", "Boise", "Phoenix", "Tulsa"]),
    ("inventory", "item", ["Blue folders", "AA batteries", "Packing tape", "Printer paper"], ["Warehouse A", "Warehouse B", "Warehouse C", "Warehouse D"]),
    ("shipping", "shipment", ["Parcel Aurora", "Parcel Birch", "Parcel Copper", "Parcel Drift"], ["Denver", "Dallas", "Atlanta", "Portland"]),
    ("crm", "account", ["Arbor Foods", "Bright Dental", "Canyon Labs", "Dovetail Arts"], ["West", "Central", "East", "North"]),
    ("projects", "task", ["Venue shortlist", "Policy review", "Newsletter draft", "Supplier audit"], ["Atlas", "Beacon", "Clover", "Drift"]),
    ("recruiting", "candidate", ["Avery Chen", "Blair Diaz", "Casey Evans", "Devon Flores"], ["Design", "Operations", "Sales", "Research"]),
    ("education", "course", ["Modern History", "Organic Chemistry", "Public Speaking", "Microeconomics"], ["North Campus", "South Campus", "Online", "Downtown"]),
    ("library", "title", ["River Almanac", "Quiet Geometry", "Stone Orchard", "Winter Atlas"], ["Central", "East", "North", "West"]),
    ("expenses", "expense", ["Hotel receipt", "Train fare", "Team lunch", "Printing invoice"], ["Denver", "Chicago", "Boston", "Seattle"]),
    ("health-admin", "appointment", ["Annual visit", "Vision exam", "Dental cleaning", "Lab visit"], ["Aspen Clinic", "Birch Clinic", "Cedar Clinic", "Dale Clinic"]),
    ("insurance", "claim", ["Windshield claim", "Baggage claim", "Water claim", "Theft claim"], ["North Office", "South Office", "East Office", "West Office"]),
    ("facilities", "request", ["Lighting repair", "Room setup", "Badge access", "Temperature check"], ["Building A", "Building B", "Building C", "Building D"]),
    ("telecom", "subscription", ["Mobile plan", "Fiber plan", "Tablet plan", "Hotspot plan"], ["Personal", "Family", "Studio", "Field Team"]),
    ("public-services", "application", ["Parking permit", "Library card", "Pet license", "Facility permit"], ["Denver", "Boulder", "Aurora", "Lakewood"]),
    ("fleet", "vehicle", ["Van 12", "Truck 18", "Sedan 24", "Shuttle 30"], ["Depot A", "Depot B", "Depot C", "Depot D"]),
]


def tool(domain: str, action: str) -> str:
    return f"{domain}_{action}"


def world_def(domain: str, resource: str) -> dict:
    def schema(action, description, properties, required):
        return {"name": tool(domain, action), "description": description,
                "parameters": {"type": "object", "properties": properties,
                               "required": required, "additionalProperties": False}}
    string = {"type": "string"}
    return {
        "resource": resource,
        "tools": [
            schema("search", f"Search {resource} records without changing them.",
                   {"query": string, "location": string}, ["query"]),
            schema("get", f"Retrieve one {resource} by its stable identifier.",
                   {"id": string}, ["id"]),
            schema("list", f"List {resource} records matching structured filters.",
                   {"location": string, "status": string, "date": string}, []),
            schema("profile", "Retrieve the current user's saved operational preferences.",
                   {}, []),
            schema("availability", f"Check availability for one {resource} and date.",
                   {"name": string, "date": string, "location": string}, ["name", "date"]),
            schema("create", f"Create a {resource}. This changes persistent state.",
                   {"name": string, "date": string, "location": string, "quantity": {"type": "integer", "minimum": 1}}, ["name"]),
            schema("update", f"Update an existing {resource}. This changes persistent state.",
                   {"id": string, "date": string, "status": string, "location": string}, ["id"]),
            schema("cancel", f"Cancel an existing {resource}. This changes persistent state.",
                   {"id": string, "reason": string}, ["id"]),
            schema("notify", f"Send a notification about a {resource}. This cannot be unsent.",
                   {"recipient": string, "subject": string, "body": string}, ["recipient", "body"]),
        ],
    }


WORLDS = {domain: world_def(domain, resource) for domain, resource, _, _ in DOMAINS}
WORLDS.update({
    "research": {"resource": "source", "tools": [
        {"name": "web_search", "description": "Search indexed sources.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
        {"name": "fetch_url", "description": "Retrieve the contents of a URL returned by search.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}},
    ]},
    "memory": {"resource": "memory", "tools": [
        {"name": "memory_search", "description": "Search persistent memory; use before relying on facts absent from the conversation.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
        {"name": "memory_add", "description": "Add a new persistent memory.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"], "additionalProperties": False}},
        {"name": "memory_update", "description": "Replace a superseded persistent memory.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"], "additionalProperties": False}},
        {"name": "memory_remove", "description": "Remove one memory only when explicitly requested or invalidated.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False}},
        {"name": "memory_retrieve_summary", "description": "Retrieve the current recursive summary.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
        {"name": "memory_update_summary", "description": "Consolidate durable new information into the recursive summary.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}},
    ]},
})

CATEGORY_COUNTS = [
    ("tool-selection", 80), ("multiple-functions", 70),
    ("parallel-same", 80), ("parallel-mixed", 80),
    ("dependency-planning", 90), ("multi-turn-state", 110),
    ("missing-parameter", 60), ("missing-function", 30),
    ("long-context-composite", 50), ("relevance-abstention", 70),
    ("error-recovery", 60), ("web-research", 100),
    ("persistent-memory", 100), ("format-sensitivity", 20),
]

INSTRUCTION_OVERLAYS = [
    ("Do not make any changes; report findings only.", "if:negative-constraint"),
    ("Do not send notifications or messages.", "if:negative-constraint"),
    ("If more than one match exists, stop and ask me which one.", "if:conditional"),
    ("Keep the final answer to three bullets and include the record IDs.", "if:exact-format"),
    ("Sort the reported results alphabetically and omit unavailable entries.", "if:ordering"),
    ("Use only information returned by the tools; say when something is unknown.", "if:grounded-response"),
    ("Prepare the action, but do not commit a change until I explicitly confirm.", "action:confirmation-required"),
    ("Do exactly the requested operation and do not update related records.", "if:scope-boundary"),
]


def calls(*items):
    return [{"tool": name, "arguments": args} for name, args in items]


def expected(decision="tool_calls", stages=None, forbidden=None, constraints=None, clarification=None, assertions=None):
    value = {"decision": decision, "stages": stages or [],
             "forbidden_tools": forbidden or [],
             "response_constraints": constraints or ["Do not claim any result not established by a tool response."]}
    if clarification:
        value["clarification"] = clarification
    if assertions:
        value["state_assertions"] = assertions
    return value


def base(category: str, number: int, domain_index: int) -> tuple[dict, tuple]:
    domain, resource, names, locations = DOMAINS[domain_index % len(DOMAINS)]
    # Each recipe operates on a distinct realistic fixture entity. This keeps
    # prompts and backend states genuinely separate instead of creating a
    # paraphrase-only corpus over the same four records.
    names = [f"{name} {number + 1:03d}" for name in names]
    n = number % 4
    seed_id = f"behavior-{category}-{number + 1:04d}"
    return ({"schema_version": 1, "id": seed_id, "kind": "tool_behavior",
             "category": category, "world": domain, "follow_up_turns": [],
             "initial_state": {"records": [
                 {"id": f"{domain[:3]}-{number + 101}", "name": names[n], "location": locations[n], "status": "active"},
                 {"id": f"{domain[:3]}-{number + 501}", "name": names[(n + 1) % 4], "location": locations[(n + 1) % 4], "status": "pending"},
             ]}, "tool_results": {}, "failure_injections": []},
            (domain, resource, names, locations, n))


def add_overlay(seed: dict, serial: int, allow_mutation=True):
    text, tag = INSTRUCTION_OVERLAYS[serial % len(INSTRUCTION_OVERLAYS)]
    if not allow_mutation and (tag == "action:confirmation-required"
                               or (tag == "if:negative-constraint"
                                   and text.startswith("Do not make"))):
        text, tag = INSTRUCTION_OVERLAYS[5]
    seed["prompt"] += " " + text
    seed["training_tags"].append(tag)
    if tag == "if:negative-constraint" and "Do not make" in text:
        mutations = [t for t in seed["available_tools"] if t.endswith(("_create", "_update", "_cancel", "_notify"))]
        seed["expected"]["forbidden_tools"] = sorted(set(seed["expected"]["forbidden_tools"] + mutations))
    seed["training_tags"] = sorted(set(seed["training_tags"]))


def make_seed(category: str, number: int) -> dict:
    seed, ctx = base(category, number, number)
    domain, resource, names, locations, n = ctx
    search, get, listing = tool(domain, "search"), tool(domain, "get"), tool(domain, "list")
    avail, create, update = tool(domain, "availability"), tool(domain, "create"), tool(domain, "update")
    cancel, notify = tool(domain, "cancel"), tool(domain, "notify")
    all_tools = [search, get, listing, avail, create, update, cancel, notify]
    record_id = f"{domain[:3]}-{number + 101}"
    date = f"2026-{8 + number % 4:02d}-{10 + number % 18:02d}"
    name, location = names[n], locations[n]

    if category == "tool-selection":
        mode = number % 4
        if mode == 0:
            prompt = f"Retrieve {name} using record ID {record_id} and tell me its current status."
            stages = [{"parallel": False, "calls": calls((get, {"id": record_id})), "purpose": "Retrieve the uniquely identified record."}]
        elif mode == 1:
            prompt = f"Search for the {resource} named {name} in {location}; I do not know its record ID."
            stages = [{"parallel": False, "calls": calls((search, {"query": name, "location": location})), "purpose": "Resolve a natural-language name without guessing an ID."}]
        elif mode == 2:
            prompt = f"List active {resource} records in {location}; do not perform a fuzzy name search."
            stages = [{"parallel": False, "calls": calls((listing, {"location": location, "status": "active"})), "purpose": "Use structured filtering rather than fuzzy search."}]
        else:
            prompt = f"Check whether {name} is available in {location} on {date}. This is only an availability check."
            stages = [{"parallel": False, "calls": calls((avail, {"name": name, "date": date, "location": location})), "purpose": "Check availability without creating anything."}]
        seed.update(prompt=prompt, available_tools=all_tools,
                    expected=expected(stages=stages, forbidden=[create, update, cancel, notify]),
                    training_tags=["tool:single-call", "tool:argument-grounding", "tool:distractor-selection"])

    elif category == "multiple-functions":
        prompt = f"I need the current details for {name} in {location}. Find the matching {resource}; do not list every record, check future availability, or change it."
        seed.update(prompt=prompt, available_tools=all_tools,
                    expected=expected(stages=[{"parallel": False, "calls": calls((search, {"query": name, "location": location})), "purpose": "Choose search among several semantically adjacent functions."}], forbidden=[listing, avail, create, update, cancel, notify]),
                    training_tags=["tool:function-selection", "tool:distractor-selection", "if:scope-boundary"])

    elif category == "parallel-same":
        chosen = [names[(n + j) % 4] for j in range(4)]
        prompt = f"Check {resource} availability on {date} for {', '.join(chosen)}. These checks are independent; return one compact comparison and do not create anything."
        stage_calls = calls(*[(avail, {"name": x, "date": date, "location": locations[(n+j)%4]}) for j, x in enumerate(chosen)])
        seed.update(prompt=prompt, available_tools=[avail, create, search],
                    expected=expected(stages=[{"parallel": True, "calls": stage_calls, "purpose": "Issue all independent availability checks in one assistant action."}], forbidden=[create]),
                    training_tags=["execution:parallel", "planning:independent-fanout", "if:negative-constraint"])

    elif category == "parallel-mixed":
        prompt = f"In one pass, retrieve {record_id}, check {name}'s availability for {date}, and list active {resource} records in {location}. Do not change or notify anything."
        stage_calls = calls((get, {"id": record_id}), (avail, {"name": name, "date": date, "location": location}), (listing, {"location": location, "status": "active"}))
        seed.update(prompt=prompt, available_tools=all_tools,
                    expected=expected(stages=[{"parallel": True, "calls": stage_calls, "purpose": "Issue heterogeneous independent reads in one assistant action."}], forbidden=[create, update, cancel, notify]),
                    training_tags=["execution:parallel-mixed", "planning:independent-fanout", "if:negative-constraint"])

    elif category == "dependency-planning":
        other = names[(n + 1) % 4]
        prompt = f"Find {name} and {other} independently. After their IDs are returned, retrieve both complete records and compare their status. Do not modify either one."
        seed["tool_results"] = {"stage_1": [{"query": name, "id": record_id}, {"query": other, "id": f"{domain[:3]}-{number + 501}"}]}
        seed.update(prompt=prompt, available_tools=[search, get, update, cancel],
                    expected=expected(stages=[
                        {"parallel": True, "calls": calls((search, {"query": name, "location": location}), (search, {"query": other, "location": locations[(n+1)%4]})), "purpose": "Resolve independent names concurrently."},
                        {"parallel": True, "calls": calls((get, {"id": record_id}), (get, {"id": f"{domain[:3]}-{number + 501}"})), "purpose": "Use returned IDs only after resolution, then retrieve independently."}], forbidden=[update, cancel]),
                    training_tags=["planning:dependency-dag", "execution:parallel", "planning:multi-step", "if:negative-constraint"])

    elif category == "multi-turn-state":
        prompt = f"Find {name} in {location} and show me its current date. Do not change it yet."
        seed["follow_up_turns"] = [
            {"after_stage": 1, "content": f"Move that {resource} to {date}, but still do not notify anyone."},
            {"after_stage": 2, "content": f"Correction: use 2026-12-{10 + number % 18:02d} instead. The later date replaces my prior instruction."},
        ]
        seed.update(prompt=prompt, available_tools=[search, get, update, notify],
                    expected=expected(stages=[
                        {"parallel": False, "calls": calls((search, {"query": name, "location": location})), "purpose": "Resolve the referenced record."},
                        {"parallel": False, "calls": calls((update, {"id": record_id, "date": date})), "purpose": "Apply the first explicit update."},
                        {"parallel": False, "calls": calls((update, {"id": record_id, "date": f"2026-12-{10 + number % 18:02d}"})), "purpose": "Apply the user's correction to the same entity."}], forbidden=[notify], assertions=[f"{record_id}.date == 2026-12-{10 + number % 18:02d}"]),
                    training_tags=["conversation:multi-turn", "state:entity-continuity", "if:correction", "if:negative-constraint"])

    elif category == "missing-parameter":
        inferable = number % 2 == 1
        if inferable:
            seed["initial_state"]["profile"] = {"preferred_location": location}
            prompt = f"List my active {resource} records in my preferred location."
            profile = tool(domain, "profile")
            stages = [
                {"parallel": False, "calls": calls((profile, {})), "purpose": "Retrieve the saved location rather than guessing or asking redundantly."},
                {"parallel": False, "calls": calls((listing, {"location": location, "status": "active"})), "purpose": "Use the retrieved profile fact."},
            ]
            exp = expected(stages=stages, forbidden=[create, update, cancel])
            tags = ["clarification:missing-parameter", "clarification:inferable", "state:profile-grounding"]
        else:
            prompt = f"Create a new {resource} named {name} on {date}."
            exp = expected(decision="clarify", clarification=f"Ask which location to use for the new {resource}.", forbidden=[create], constraints=["Ask one concise question for the missing required location."])
            tags = ["clarification:missing-parameter", "clarification:required", "tool:no-invented-arguments"]
        seed.update(prompt=prompt, available_tools=[listing, create, search, tool(domain, "profile")], expected=exp, training_tags=tags)

    elif category == "missing-function":
        prompt = f"Cancel {name}, record ID {record_id}, because the event is no longer needed."
        seed["follow_up_turns"] = [{"after_stage": 0, "content": "You now have a cancellation function. Please continue without repeating completed work.", "add_tools": [cancel]}]
        seed.update(prompt=prompt, available_tools=[get, search],
                    expected=expected(decision="missing_function", stages=[{"parallel": False, "calls": calls((cancel, {"id": record_id, "reason": "event is no longer needed"})), "purpose": "Call the newly introduced function only after it becomes available."}], forbidden=[], constraints=["First state that no available function can cancel the record.", "Resume when the cancellation tool is introduced."]),
                    training_tags=["tool:missing-function", "conversation:multi-turn", "tool:no-hallucinated-function"])

    elif category == "long-context-composite":
        distractors = [{"id": f"{domain[:3]}-{number + 700 + j}", "name": name if j % 7 == 0 else names[j % 4], "location": locations[j % 4], "status": "closed" if j % 3 else "pending"} for j in range(60)]
        seed["initial_state"]["records"].extend(distractors)
        prompt = f"Among the records, find the active {resource} named {name} in {location}. Retrieve its full details. Ignore same-name records in other locations and do not change anything."
        seed["tool_results"] = {"search_match": {"id": record_id}}
        seed.update(prompt=prompt, available_tools=all_tools,
                    expected=expected(stages=[{"parallel": False, "calls": calls((search, {"query": name, "location": location})), "purpose": "Disambiguate using all supplied constraints."}, {"parallel": False, "calls": calls((get, {"id": record_id})), "purpose": "Retrieve the resolved record rather than guessing."}], forbidden=[create, update, cancel, notify]),
                    training_tags=["context:long", "planning:composite", "state:distractor-resistance", "if:negative-constraint"])

    elif category == "relevance-abstention":
        relevant = number % 3 == 0
        if relevant:
            prompt = f"What is the current status of {name}, record ID {record_id}? Use the record system rather than relying on memory."
            exp = expected(stages=[{"parallel": False, "calls": calls((get, {"id": record_id})), "purpose": "Use the relevant retrieval tool."}], forbidden=[create, update, cancel])
            tags = ["tool:relevance", "tool:required-use"]
        else:
            questions = ["Explain the difference between a fact and an opinion.", "Rewrite this sentence more politely: Send it today.", "What are three ways to take clearer meeting notes?"]
            prompt = questions[number % len(questions)]
            exp = expected(decision="respond_without_tools", forbidden=all_tools, constraints=["Answer directly without invoking any supplied tool."])
            tags = ["tool:irrelevance", "tool:abstention"]
        seed.update(prompt=prompt, available_tools=all_tools, expected=exp, training_tags=tags)

    elif category == "error-recovery":
        maybe_committed = number % 2 == 0
        if maybe_committed:
            prompt = f"Cancel {name}, ID {record_id}. If the cancellation times out, verify its status before considering a retry so it is not canceled twice."
            seed["failure_injections"] = [{"tool": cancel, "occurrence": 1, "error": "timeout", "may_have_committed": True}]
            seed["tool_results"] = {"post_timeout_get": {"id": record_id, "status": "cancelled"}}
            stages = [{"parallel": False, "calls": calls((cancel, {"id": record_id, "reason": "user requested cancellation"})), "purpose": "Attempt the requested mutation once."}, {"parallel": False, "calls": calls((get, {"id": record_id})), "purpose": "Resolve uncertain commit status before retrying."}]
            forbidden = []
            tags = ["tool:error-recovery", "tool:idempotency", "state:uncertain-commit"]
        else:
            other = names[(n + 1) % 4]
            prompt = f"Check availability for {name} and {other} on {date}. One independent check may fail; preserve the successful result and retry only the failed check."
            seed["failure_injections"] = [{"tool": avail, "occurrence": 2, "error": "temporary_unavailable", "may_have_committed": False}]
            stages = [{"parallel": True, "calls": calls((avail, {"name": name, "date": date, "location": location}), (avail, {"name": other, "date": date, "location": locations[(n+1)%4]})), "purpose": "Run independent checks together."}, {"parallel": False, "calls": calls((avail, {"name": other, "date": date, "location": locations[(n+1)%4]})), "purpose": "Retry only the failed branch."}]
            forbidden = [create]
            tags = ["tool:error-recovery", "tool:partial-failure", "execution:parallel"]
        seed.update(prompt=prompt, available_tools=[avail, get, cancel, create], expected=expected(stages=stages, forbidden=forbidden), training_tags=tags)

    elif category == "web-research":
        municipalities = ["Northbridge", "Lakehurst", "Pinehaven", "Westmere", "Clearford",
                          "Ashbourne", "Redwick", "Stonehaven", "Fairmont", "Glenhaven",
                          "Brookfield", "Eastmere", "Highland", "Riverton", "Oakridge",
                          "Crestview", "Mapleford", "Silverton", "Meadowbrook", "Cedarvale",
                          "Foxhaven", "Willowdale", "Brighton", "Rosemont", "Elmstead",
                          "Amberley", "Briarford", "Coralwick", "Dunmere", "Everbrook",
                          "Fernridge", "Goldhaven", "Heatherby", "Ironwood", "Juniper Bay",
                          "Kingsmere", "Larkspur", "Mossford"]
        institution = ["Aurora civic pavilion", "Birch maritime museum",
                       "Copper trail foundation", "Drift public archive"][n]
        entity = f"the {institution} in {municipalities[number // 4]}"
        organization = ["Aurora Civic Trust", "Birch Maritime Society",
                        "Copper Trail Conservancy", "Drift Archive Authority"][n]
        attribute = ["opening year", "architect", "current director", "original location"][number % 4]
        prompt = f"Research {entity}'s {attribute}. First identify the authoritative organization, then locate and fetch a primary source that establishes the answer. Do not answer from memory, and distinguish sourced fact from inference."
        q1, q2 = f"{entity} official organization", f"{organization} {attribute} primary source"
        seed.update(world="research", prompt=prompt,
                    available_tools=["web_search", "fetch_url"], initial_state={"web_index_version": f"fixture-{number+1:04d}"},
                    tool_results={"search_1": {"organization": organization, "urls": [f"https://fixtures.example/org/{number}"]}, "search_2": {"urls": [f"https://fixtures.example/source/{number}"]}},
                    expected=expected(stages=[{"parallel": False, "calls": calls(("web_search", {"query": q1})), "purpose": "Resolve the authoritative organization."}, {"parallel": False, "calls": calls(("web_search", {"query": q2})), "purpose": "Find a primary source for the requested attribute."}, {"parallel": False, "calls": calls(("fetch_url", {"url": f"https://fixtures.example/source/{number}"})), "purpose": "Read the source rather than trusting a snippet."}], forbidden=[], constraints=["Cite the retrieved primary source.", "Separate retrieved facts from inference.", "Do not use unsupported parametric knowledge."]),
                    training_tags=["web:multihop", "web:primary-source", "if:grounded-response"])
        if number % 5 == 0:
            seed["failure_injections"] = [{"tool": "fetch_url", "occurrence": 1, "error": ["http_403", "http_429", "http_503", "timeout"][n], "may_have_committed": False}]
            seed["tool_results"]["alternate_search"] = {"urls": [f"https://fixtures.example/alternate/{number}"]}
            seed["expected"]["stages"].extend([
                {"parallel": False, "calls": calls(("web_search", {"query": f"{organization} {attribute} alternate official source"})), "purpose": "Search for an alternate primary source after retrieval failure."},
                {"parallel": False, "calls": calls(("fetch_url", {"url": f"https://fixtures.example/alternate/{number}"})), "purpose": "Retrieve the authoritative alternative instead of repeating the failed URL."},
            ])
            seed["training_tags"].append("web:retrieval-recovery")

    elif category == "persistent-memory":
        mode = number % 5
        key = ["travel.seating", "accessibility.requirement", "support.preferred_contact", "education.goal"][n]
        value = ["aisle seat", "step-free access", "email", "complete the certificate by spring"][n]
        seed.update(world="memory", available_tools=["memory_search", "memory_add", "memory_update", "memory_remove", "memory_retrieve_summary", "memory_update_summary"], initial_state={"memory": {key: "window seat" if mode == 2 else value}}, tool_results={})
        if mode == 0:
            seed["prompt"] = f"Please remember that my {key.replace('.', ' ')} is {value}; this is a lasting preference."
            seed["expected"] = expected(stages=[{"parallel": False, "calls": calls(("memory_add", {"key": key, "value": value})), "purpose": "Store a durable user preference."}], forbidden=["memory_remove"])
            tags = ["memory:key-value", "memory:durable-fact"]
        elif mode == 1:
            seed["prompt"] = f"What is my {key.replace('.', ' ')}? It is not present in this conversation, so retrieve it."
            seed["expected"] = expected(stages=[{"parallel": False, "calls": calls(("memory_search", {"query": key.replace('.', ' ')})), "purpose": "Explicitly retrieve absent conversation context."}], forbidden=["memory_add", "memory_update", "memory_remove"])
            tags = ["memory:semantic-search", "memory:explicit-retrieval"]
        elif mode == 2:
            seed["prompt"] = f"Update my {key.replace('.', ' ')} from window seat to aisle seat. Preserve unrelated memories."
            seed["expected"] = expected(stages=[{"parallel": False, "calls": calls(("memory_update", {"key": key, "value": "aisle seat"})), "purpose": "Replace only the superseded fact."}], forbidden=["memory_remove"], assertions=[f"memory[{key}] == aisle seat", "unrelated memories remain unchanged"])
            tags = ["memory:key-value", "memory:consolidation", "if:scope-boundary"]
        elif mode == 3:
            seed["prompt"] = "Retrieve my ongoing planning summary, add that the venue decision is postponed until September, and retain every unrelated decision."
            seed["tool_results"] = {"summary": "Budget approved. Catering undecided. Venue review scheduled for July."}
            seed["expected"] = expected(stages=[{"parallel": False, "calls": calls(("memory_retrieve_summary", {})), "purpose": "Read the existing summary before modifying it."}, {"parallel": False, "calls": calls(("memory_update_summary", {"summary": "Budget approved. Catering undecided. Venue decision postponed until September."})), "purpose": "Consolidate the changed fact while preserving unrelated decisions."}], forbidden=["memory_remove"])
            tags = ["memory:summary", "memory:consolidation"]
        else:
            seed["prompt"] = f"For this one request, use {value}. Do not save it as a lasting preference."
            seed["expected"] = expected(decision="respond_without_tools", forbidden=["memory_add", "memory_update", "memory_remove", "memory_update_summary"], constraints=["Acknowledge the one-time instruction without writing memory."])
            tags = ["memory:key-value", "memory:incidental-detail", "tool:abstention"]
        seed["training_tags"] = tags

    elif category == "format-sensitivity":
        formats = [
            "Tool documentation is expressed in XML, but return the tool call as strict JSON with no commentary.",
            "The surrounding prompt uses Markdown headings; return only the native function call.",
            "Return a Python-style function call and no prose.",
            "The tool-call wrapper is <tool_call>; preserve that wrapper exactly.",
            "The request is terse and informal, but argument names and types remain exact.",
        ]
        prompt = f"{formats[number % len(formats)]} Retrieve record {record_id}."
        seed.update(prompt=prompt, available_tools=[get, search, update],
                    expected=expected(stages=[{"parallel": False, "calls": calls((get, {"id": record_id})), "purpose": "Select and format the exact retrieval call."}], forbidden=[update], constraints=[formats[number % len(formats)]]),
                    training_tags=["format:strict", "tool:argument-grounding", "if:exact-format"])
    else:
        raise AssertionError(category)

    if category not in {"relevance-abstention", "missing-parameter", "missing-function", "persistent-memory", "format-sensitivity"}:
        add_overlay(seed, number, allow_mutation=not any(x.endswith(("_create", "_update", "_cancel")) for stage in seed["expected"]["stages"] for x in [call["tool"] for call in stage["calls"]]))
    return seed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true",
                        help="Refresh generated files during corpus development")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    worlds_payload = json.dumps({
        "schema_version": 1,
        "execution_contract": {
            "mode": "deterministic_simulation",
            "external_side_effects": False,
            "live_network": False,
            "real_credentials": False
        },
        "worlds": WORLDS
    }, indent=2, sort_keys=True) + "\n"
    if WORLDS_PATH.exists() and WORLDS_PATH.read_text() != worlds_payload and not args.refresh:
        raise SystemExit(f"refusing to replace differing world registry: {WORLDS_PATH}")
    WORLDS_PATH.write_text(worlds_payload)

    expected_ids = set()
    for category, count in CATEGORY_COUNTS:
        for number in range(count):
            seed = make_seed(category, number)
            expected_ids.add(seed["id"])
            path = OUT / f"{seed['id']}.json"
            payload = json.dumps(seed, indent=2, sort_keys=True) + "\n"
            if path.exists() and path.read_text() != payload and not args.refresh:
                raise SystemExit(f"refusing to replace immutable differing seed: {path}")
            path.write_text(payload)

    existing = {p.stem for p in OUT.glob("behavior-*.json")}
    unexpected = existing - expected_ids
    if unexpected:
        raise SystemExit(f"unexpected generated seed files (not deleting): {sorted(unexpected)[:5]}")
    print(f"authored {len(expected_ids)} immutable behavior seeds across {len(CATEGORY_COUNTS)} categories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
