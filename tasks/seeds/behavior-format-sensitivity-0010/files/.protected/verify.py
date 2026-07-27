#!/usr/bin/env python3
"""Deterministic protected verifier for the typed stable-ID task lookup."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / ".projects" / "tasks.json"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "project-task-audit.jsonl"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "00c70b11e70f49617fc3b71510dc1844f6f82e8cafb2feda055c42e12d37985e"

EXPECTED_HASHES = {
    ".gitignore": "f54091a8718b02b018241e6bf7fb4ac43b4e67f6abf7a0ed44531d4ab32ef38f",
    ".projects/tasks.json": "a9d8e2104f7b705171c406b3643a961f259789ea268eac40c515ae3a00a8258a",
    ".protected/audit.key": "7bb4c69713288062ef77d89e0a04bc0b1630fe3205b217f24d38d2d747129988",
    ".protected/reference_setup.py": "ee88d2a9c09a236340342abdff3e4f1ef6713f7814358aeb6119df234e3e2151",
    "README.md": "6be5a1331a04124597a6ea7f4ea426149778b6ed5b5a18bc1b7e7ea3d0496e72",
    "project-task": "6de2d62c24c70590a5fae3e51e24579d1f061b8c9e8aec72377a6c554d7e4263",
}
EXPECTED_RESULT_FIELDS = [
    "assignee",
    "description",
    "due_on",
    "id",
    "lifecycle",
    "location",
    "name",
    "priority",
    "project",
    "status",
]
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".projects",
    ".protected",
    ".reference_solution",
    "README.md",
    "project-task",
}
ALLOWED_PROJECTS = {"tasks.json"}
ALLOWED_PROTECTED = {"audit.key", "reference_setup.py", "runtime", "verify.py"}
ALLOWED_PROTECTED_RUNTIME = {"project-task-audit.jsonl"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected project-task input changed: {relative}")


def unexpected_names(directory: Path, allowed: set[str]) -> list[str]:
    if not directory.is_dir():
        fail(f"required protected directory is missing: {directory.relative_to(ROOT)}")
    return sorted(path.name for path in directory.iterdir() if path.name not in allowed)


def verify_scope() -> None:
    unexpected_root = unexpected_names(ROOT, ALLOWED_ROOT_ENTRIES)
    if unexpected_root:
        fail("unexpected workspace artifact: " + ", ".join(unexpected_root))

    unexpected_projects = unexpected_names(ROOT / ".projects", ALLOWED_PROJECTS)
    if unexpected_projects:
        fail("unexpected project-task artifact: " + ", ".join(unexpected_projects))

    unexpected_protected = unexpected_names(ROOT / ".protected", ALLOWED_PROTECTED)
    if unexpected_protected:
        fail("unexpected protected artifact: " + ", ".join(unexpected_protected))

    unexpected_runtime = unexpected_names(
        ROOT / ".protected" / "runtime", ALLOWED_PROTECTED_RUNTIME
    )
    if unexpected_runtime:
        fail("unexpected runtime artifact: " + ", ".join(unexpected_runtime))


def expected_task_and_output_digest() -> tuple[dict, str]:
    try:
        document = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"project-task registry is unreadable: {exc}")
    tasks = document.get("tasks") if isinstance(document, dict) else None
    if not isinstance(tasks, list):
        fail("project-task registry has no task collection")
    matches = [
        record
        for record in tasks
        if isinstance(record, dict)
        and record.get("id") == "pro-110"
        and record.get("lifecycle") == "current"
    ]
    if len(matches) != 1:
        fail("protected registry does not contain one requested current task")
    record = matches[0]
    if sorted(record) != EXPECTED_RESULT_FIELDS:
        fail("protected requested task is not a complete record")
    digest = hashlib.sha256(canonical({"task": record})).hexdigest()
    return record, digest


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine project-task execution evidence was produced")
    events: list[dict] = []
    key = AUDIT_KEY_PATH.read_bytes()
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        event = dict(signed_event)
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by project-task")
        events.append(event)
    return events


def verify_execution(events: list[dict], expected_output_digest: str) -> None:
    if len(events) != 1:
        fail("exactly one project-task execution is required")
    event = events[0]
    if event.get("operation") != "get":
        fail("the sole project-task execution was not the full-record operation")
    if event.get("success") is not True:
        fail("the required project-task operation did not succeed")
    if event.get("stable_id") != "pro-110":
        fail("the requested stable ID was not passed unchanged")
    if event.get("argument_type") != "str":
        fail("the stable ID was not handled as a string")
    if event.get("result_count") != 1 or event.get("result_id") != "pro-110":
        fail("the complete requested task was not retrieved")
    if event.get("result_fields") != EXPECTED_RESULT_FIELDS:
        fail("the operation did not return the full task")
    if event.get("output_digest") != expected_output_digest:
        fail("the executable did not emit the complete protected task result")

    tasks_digest = file_sha256(TASKS_PATH)
    if (
        event.get("task_data_digest_before") != tasks_digest
        or event.get("task_data_digest_after") != tasks_digest
    ):
        fail("project-task registry state changed during the lookup-only request")

    for field in (
        "start_ns",
        "end_ns",
        "process_pid",
        "process_group_id",
        "session_id",
        "parent_pid",
    ):
        if not isinstance(event.get(field), int):
            fail(f"genuine process evidence is missing field: {field}")
    if event["start_ns"] >= event["end_ns"]:
        fail("project-task process evidence has an invalid execution interval")
    for field in ("process_start_ticks", "parent_start_ticks"):
        if (
            not isinstance(event.get(field), str)
            or event.get(field) == "unavailable"
        ):
            fail(f"genuine process identity evidence is incomplete: {field}")

    reference_run = event.get("reference_run")
    if not isinstance(reference_run, bool):
        fail("execution provenance is missing")
    if reference_run and (
        not MARKER.is_file() or file_sha256(MARKER) != MARKER_DIGEST
    ):
        fail("stale reference execution evidence is not a valid task solution")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    _, expected_output_digest = expected_task_and_output_digest()
    verify_execution(load_events(), expected_output_digest)
    print(
        "PASS: one genuine project-task lookup returned the full task with the "
        "literal string stable ID and left all protected state unchanged"
    )


if __name__ == "__main__":
    main()
