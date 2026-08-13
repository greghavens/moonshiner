#!/usr/bin/env python3
"""Deterministic verification for the SDDC Manager 9.0 network pool client.

Runs the Go suite under the race detector and checks that every required test
actually executed and passed, then re-checks the contract provenance documents
independently of the Go code.

Nothing here reaches the network. The Go module has no dependencies and GOPROXY
is switched off, and the tests talk only to a loopback double on 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FILES = [
    "go.mod",
    "docs/contract.json",
    "docs/official_sources.json",
    "netpool/client.go",
    "netpool/client_test.go",
    "netpool/mock/mock.go",
]

REQUIRED_TESTS = [
    "TestContractPinsTheDeclaredSpecRevision",
    "TestCreateRequestWireShape",
    "TestListRequestWireShape",
    "TestEnsureNetworkPoolIsRetrySafe",
    "TestEnsureNetworkPoolChecksBeforeMutating",
    "TestConcurrentEnsureDoesNotDuplicate",
    "TestOnlyContractOperationsAreUsed",
]

# Minimum number of subtests each table-driven test must have run, so a suite
# cannot pass by having been reduced to a single case.
REQUIRED_SUBTESTS = {
    "TestCreateRequestWireShape": 3,
    "TestEnsureNetworkPoolIsRetrySafe": 8,
}

SPEC = {
    "tag": "9.0.0.0",
    "commit": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
    "specPath": "specifications/sddc-manager/sddc-manager-openapi.json",
    "operationIds": ["createNetworkPool", "getNetworkPool"],
}

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def go_env() -> dict:
    env = dict(os.environ)
    # Keep the toolchain off the network and independent of the caller's HOME
    # and compiler-cache configuration.
    env["GOFLAGS"] = "-mod=mod"
    env["GOPROXY"] = "off"
    env["GOTOOLCHAIN"] = "local"
    env["GOCACHE"] = os.path.join(ROOT, ".gocache")
    env["CCACHE_DISABLE"] = "1"
    env["CGO_ENABLED"] = "1"
    return env


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=go_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def check_files() -> None:
    for relative in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(ROOT, relative)):
            fail(f"required file is missing: {relative}")


def load(relative: str):
    try:
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        fail(f"cannot read {relative}: {exc}")
        return None


def check_provenance() -> None:
    contract = load("docs/contract.json")
    sources = load("docs/official_sources.json")
    if contract is None or sources is None:
        return

    source = contract.get("source", {})
    for key in ("tag", "commit", "specPath"):
        if source.get(key) != SPEC[key]:
            fail(f"docs/contract.json source.{key} = {source.get(key)!r}, want {SPEC[key]!r}")

    named = sorted(op.get("operationId") for op in contract.get("operations", []))
    if named != SPEC["operationIds"]:
        fail(f"docs/contract.json names operations {named}, want {SPEC['operationIds']}")

    entries = sources.get("sources") or []
    if len(entries) != 1:
        fail(f"docs/official_sources.json lists {len(entries)} sources, want exactly 1")
        return
    entry = entries[0]
    for key in ("tag", "commit", "specPath"):
        if entry.get(key) != SPEC[key]:
            fail(f"docs/official_sources.json {key} = {entry.get(key)!r}, want {SPEC[key]!r}")

    recorded = sorted(op.get("operationId") for op in entry.get("operationIds", []))
    if recorded != SPEC["operationIds"]:
        fail(f"docs/official_sources.json records operationIds {recorded}, want {SPEC['operationIds']}")

    # The 9.1.0.0 revision of the same file relaxes Network.required. Guard the
    # contract against having been derived from it.
    guard = contract.get("requestEncoding", {}).get("revisionGuard", {})
    want_required = ["gateway", "mask", "mtu", "subnet", "type", "vlanId"]
    if sorted(guard.get("networkRequiredAt9_0") or []) != want_required:
        fail(
            "docs/contract.json records Network.required at 9.0.0.0 as "
            f"{guard.get('networkRequiredAt9_0')!r}, want {want_required}"
        )


def check_go_suite() -> None:
    build = run(["go", "build", "./..."], timeout=300)
    if build.returncode != 0:
        fail("go build failed:\n" + build.stdout.strip())
        return

    vet = run(["go", "vet", "./..."], timeout=300)
    if vet.returncode != 0:
        fail("go vet failed:\n" + vet.stdout.strip())

    test = run(["go", "test", "-race", "-count=1", "-v", "./..."], timeout=420)
    output = test.stdout
    print(output)

    passed = set(re.findall(r"^\s*--- PASS: (\S+)", output, re.MULTILINE))
    failed = sorted(set(re.findall(r"^\s*--- FAIL: (\S+)", output, re.MULTILINE)))
    for name in failed:
        fail(f"test failed: {name}")

    for name in REQUIRED_TESTS:
        if name not in passed:
            fail(f"required test did not run and pass: {name}")

    for name, minimum in REQUIRED_SUBTESTS.items():
        ran = sum(1 for entry in passed if entry.startswith(name + "/"))
        if ran < minimum:
            fail(f"{name} ran {ran} subtests, want at least {minimum}")

    if test.returncode != 0 and not failures:
        fail("go test -race exited non-zero:\n" + output.strip()[-4000:])


def main() -> int:
    check_files()
    if not failures:
        check_provenance()
        check_go_suite()

    if failures:
        print("\nVERIFICATION FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1

    print("\nVERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired as expired:
        print(f"\nVERIFICATION FAILED\n  - timed out running {expired.cmd}")
        sys.exit(1)
