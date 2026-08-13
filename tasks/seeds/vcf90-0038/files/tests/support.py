"""PROTECTED FILE -- do not modify.

Shared helpers for the acceptance suite: locating the workspace, running the
protected PowerShell drivers, and loading the JSON they emit.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_DIR = os.path.join(ROOT, "tests", "driver")
MANIFEST = os.path.join(ROOT, "VcfEvcGuard", "VcfEvcGuard.psd1")
DOCS = os.path.join(ROOT, "docs")

# The tag of vmware/vcf-api-specs the contract must be derived from, and the
# commit that tag points at. The 9.1.0.0 revision of the same file is a
# different contract and is not acceptable here.
SPEC_TAG = "9.0.0.0"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_REPO = "vmware/vcf-api-specs"

OPERATION_IDS = {
    "createSession": "Cis.Session_create",
    "getEvcMode": "Vcenter.Cluster.EvcMode_get",
    "checkSetEvcMode": "Vcenter.Cluster.EvcMode_checkSet$Task",
    "setEvcMode": "Vcenter.Cluster.EvcMode_set$Task",
    "getTask": "Cis.Tasks_get",
}

CLUSTER = "domain-c9"

# The EVC mode the driver asks for in the scenarios that supply one.
TARGET_EVC_MODE = {
    "key": "intel-skylake",
    "masks": [
        {"key": "cpuid.AVX512F", "name": "cpuid.AVX512F", "value": "Val:0x00000001"},
    ],
}


def pwsh_executable():
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


def require_pwsh():
    executable = pwsh_executable()
    if executable is None:
        raise unittest.SkipTest("PowerShell (pwsh) is not on PATH")
    return executable


def run_driver(script, arguments, timeout=300):
    """Run a protected driver script and return the JSON report it wrote."""
    executable = require_pwsh()
    handle, out_path = tempfile.mkstemp(prefix="vcfevc-", suffix=".json")
    os.close(handle)
    try:
        command = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            os.path.join(DRIVER_DIR, script),
            "-OutFile",
            out_path,
        ]
        for key, value in arguments.items():
            command.extend(["-%s" % key, str(value)])

        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        try:
            with open(out_path, "r", encoding="utf-8-sig") as handle:
                report = json.load(handle)
        except (OSError, ValueError):
            raise AssertionError(
                "driver %s produced no report (exit %d)\n%s"
                % (
                    script,
                    completed.returncode,
                    completed.stdout.decode("utf-8", "replace"),
                )
            )
        report["_stdout"] = completed.stdout.decode("utf-8", "replace")
        return report
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def load_doc(name):
    path = os.path.join(DOCS, name)
    if not os.path.exists(path):
        raise AssertionError("docs/%s is missing -- it has to be written" % name)
    with open(path, "r", encoding="utf-8-sig") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise AssertionError("docs/%s is not valid JSON: %s" % (name, exc))


def walk_strings(node):
    """Yield every string found anywhere in a nested JSON structure."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            for item in walk_strings(value):
                yield item
    elif isinstance(node, (list, tuple)):
        for value in node:
            for item in walk_strings(value):
                yield item


def find_operation(contract, operation_id):
    """Return the sub-object of the contract that declares this operationId."""
    matches = []

    def visit(node):
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str) and value == operation_id:
                    matches.append(node)
                    break
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(contract)
    return matches[0] if matches else None


def ensure_module_written():
    if not os.path.exists(MANIFEST):
        raise AssertionError(
            "VcfEvcGuard/VcfEvcGuard.psd1 is missing -- the module manifest has to be written"
        )


def result_field(report, name):
    result = report.get("result")
    if not isinstance(result, dict):
        raise AssertionError(
            "driver returned no result object; error was:\n%s" % report.get("error")
        )
    if name not in result:
        raise AssertionError(
            "the object returned by Invoke-VcfEvcModeGuardedSet has no '%s' property; "
            "it has: %s" % (name, sorted(result))
        )
    return result[name]
