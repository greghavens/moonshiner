#!/usr/bin/env python3
"""Deterministic acceptance check for AutomationChangeClient.

Starts the loopback contract fixture on an ephemeral port, runs the protected
TestMain against it, then grades the fixture's own request log. Request
identifiers and detail strings are generated per run so they cannot be
hardcoded in the client.
"""
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESOURCE_PATH = "/deployment/api/resources/resource-42/requests"
RESOURCE_ID = "resource-42"

# The non-terminal cycle mirrors mock_vcfa.NONTERMINAL_CYCLE. Poll depths, the
# cycle offset and the failing step's terminal status are drawn per run, so no
# fixed poll count or terminal status string can be hardcoded in the client: a
# client that recognises only SUCCESSFUL never terminates the last step.
# Grading stays deterministic because verify.py hands the very same draw to the
# fixture and to the harness expectations.
NONTERMINAL_CYCLE = ["PENDING", "INITIALIZATION", "CHECKING_APPROVAL", "INPROGRESS", "COMPLETION"]
SUCCESS_TERMINAL = "SUCCESSFUL"
FINAL_TERMINALS = ["FAILED", "APPROVAL_REJECTED", "ABORTED"]
ACTIONS = [
    ("resize-cpu", {"cpuCount": 8}),
    ("resize-memory", {"memoryInMB": 32768}),
    ("extend-lease", {"expirationDate": "2026-12-31T23:59:59Z"}),
]
REASON = "quarterly capacity change"


def fail(message, detail=None):
    print(f"VERIFY_FAIL: {message}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    raise SystemExit(1)


def compile_sources(build_dir):
    command = [
        "javac",
        "--release",
        "17",
        "-d",
        str(build_dir),
        str(ROOT / "AutomationChangeClient.java"),
        str(ROOT / "TestMain.java"),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if result.returncode != 0:
        fail("Java compilation failed", result.stdout + result.stderr)


def read_events(log_path):
    try:
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        fail("could not read the loopback fixture request log", str(error))


def draw_plan_shape(rng):
    """Draw the per-run poll depths, cycle offsets and terminal statuses."""
    counts = [rng.randint(1, 7) for _ in ACTIONS]
    starts = [rng.randrange(len(NONTERMINAL_CYCLE)) for _ in ACTIONS]
    terminals = [SUCCESS_TERMINAL for _ in ACTIONS[:-1]]
    terminals.append(rng.choice(FINAL_TERMINALS))
    return counts, starts, terminals


def plan_for(nonce, detail_nonce, counts, starts, terminals):
    """Mirror of the fixture plan; used to build expectations and the trace."""
    steps = []
    for index, (action_id, inputs) in enumerate(ACTIONS):
        statuses = [
            NONTERMINAL_CYCLE[(starts[index] + step) % len(NONTERMINAL_CYCLE)]
            for step in range(counts[index])
        ]
        statuses.append(terminals[index])
        steps.append({
            "actionId": action_id,
            "inputs": inputs,
            "requestId": f"req-{nonce}-{index + 1}",
            "statuses": statuses,
            "terminalStatus": terminals[index],
            "terminalDetails": f"{action_id} reached {terminals[index]} [{detail_nonce}]",
        })
    return steps


def expected_trace(steps):
    trace = []
    for step in steps:
        trace.append(("POST", RESOURCE_PATH, "CREATED"))
        for status in step["statuses"]:
            trace.append(("GET", f"/deployment/api/requests/{step['requestId']}", status))
    return trace


def verify_trace(events, steps):
    expected = expected_trace(steps)
    actual = [(e.get("method"), e.get("path"), e.get("servedStatus")) for e in events]
    if actual != expected:
        fail(
            "request trace did not poll each request to its terminal state before "
            "reporting the step and submitting the next one",
            "expected=" + json.dumps(expected) + "\nactual=" + json.dumps(actual),
        )

    expected_bodies = [
        {"actionId": step["actionId"], "inputs": step["inputs"], "reason": REASON}
        for step in steps
    ]
    bodies = [e.get("body") for e in events if e.get("method") == "POST"]
    if bodies != expected_bodies:
        fail(
            "submitted action bodies do not match the supplied plan",
            "expected=" + json.dumps(expected_bodies) + "\nactual=" + json.dumps(bodies),
        )
    if any(e.get("responseStatus") != 200 for e in events):
        fail(
            "the client made a request the contract fixture rejected",
            json.dumps([e for e in events if e.get("responseStatus") != 200]),
        )
    if any(e.get("operation") not in {"Submit Resource Action Request", "Get Request"} for e in events):
        fail("the client called an operation outside docs/contract.json")


def main():
    with tempfile.TemporaryDirectory(prefix="vcfa-client-verify-") as temporary:
        temp = Path(temporary)
        build = temp / "classes"
        build.mkdir()
        log_path = temp / "requests.jsonl"
        compile_sources(build)

        rng = random.SystemRandom()
        nonce = f"{os.getpid():x}{os.urandom(4).hex()}"
        detail_nonce = "d" + os.urandom(6).hex()
        token = "tok-" + os.urandom(8).hex()
        counts, starts, terminals = draw_plan_shape(rng)
        steps = plan_for(nonce, detail_nonce, counts, starts, terminals)

        expectations = temp / "expected_steps.tsv"
        expectations.write_text(
            "".join(
                "\t".join([
                    step["actionId"],
                    step["requestId"],
                    step["terminalStatus"],
                    step["terminalDetails"],
                ]) + "\n"
                for step in steps
            ),
            encoding="utf-8",
        )

        mock = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "mock_vcfa.py"),
                "--log",
                str(log_path),
                "--token",
                token,
                "--resource-id",
                RESOURCE_ID,
                "--nonce",
                nonce,
                "--detail-nonce",
                detail_nonce,
                "--nonterminal-counts",
                ",".join(str(count) for count in counts),
                "--cycle-starts",
                ",".join(str(start) for start in starts),
                "--terminal-statuses",
                ",".join(terminals),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            base_url = mock.stdout.readline().strip()
            if not base_url.startswith("http://127.0.0.1:"):
                stderr = mock.stderr.read() if mock.poll() is not None else ""
                fail("loopback contract fixture did not start", stderr)

            result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(build),
                    "TestMain",
                    base_url,
                    token,
                    RESOURCE_ID,
                    str(expectations),
                ],
                text=True,
                capture_output=True,
                timeout=60,
            )
            events = read_events(log_path)
            verify_trace(events, steps)
            if result.returncode != 0:
                fail("TestMain rejected the client result", result.stdout + result.stderr)
            if result.stdout.strip() != "TEST_MAIN_OK":
                fail("unexpected TestMain output", result.stdout + result.stderr)
        except subprocess.TimeoutExpired:
            fail("client did not finish before the deterministic test timeout")
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=5)

    print("VERIFY_OK")


if __name__ == "__main__":
    main()
