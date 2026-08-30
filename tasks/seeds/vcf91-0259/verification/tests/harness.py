"""Test support: start the contract-pinned mock and read back its request log.

Part of the verifier. Do not modify.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MOCK = os.path.join(ROOT, "mock", "vcfops_mock.py")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
SOURCES = os.path.join(ROOT, "docs", "official_sources.json")

for _path in (SRC, os.path.join(ROOT, "mock")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# The verifier talks to 127.0.0.1 and nothing else. Drop any inherited proxy
# configuration so no request can be relayed off the loopback interface, and
# make the setting inheritable by the CLI subprocess.
for _var in (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
):
    os.environ.pop(_var, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"


def load_contract():
    with open(CONTRACT, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_sources():
    with open(SOURCES, "r", encoding="utf-8") as handle:
        return json.load(handle)


class MockServer:
    """A freshly started mock on 127.0.0.1 with an empty request log."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vcfops-mock-")
        self.log_path = os.path.join(self.tmpdir, "requests.jsonl")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", MOCK, "--log", self.log_path, "--contract", CONTRACT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.proc.stdout.readline()
        if not line.startswith("VCFOPS_MOCK_READY "):
            self.proc.kill()
            raise RuntimeError(
                "mock did not start: %r %r" % (line, self.proc.stderr.read())
            )
        info = json.loads(line[len("VCFOPS_MOCK_READY ") :])
        self.port = info["port"]
        self.base_url = info["baseUrl"]
        self.operations = info["operations"]

    # -- log access -------------------------------------------------------

    def records(self):
        """Every request the mock received, in arrival order."""
        if not os.path.exists(self.log_path):
            return []
        out = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        out.sort(key=lambda r: r["seq"])
        return out

    def records_for(self, operation_id):
        return [r for r in self.records() if r["operationId"] == operation_id]

    def only(self, operation_id):
        found = self.records_for(operation_id)
        if len(found) != 1:
            raise AssertionError(
                "expected exactly one %s request, got %d: %s"
                % (operation_id, len(found), json.dumps(found, indent=2))
            )
        return found[0]

    # -- lifecycle --------------------------------------------------------

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False


# -- fixture constants mirrored from mock/vcfops_mock.py ---------------------

USERNAME = "report-runner"
PASSWORD = "Fixture-Passw0rd!"
AUTH_SOURCE = "Local Users"

DEF_COMPLETES = "2f7a2f2a-0001-4a10-9f1a-9b0f0d5c1001"
DEF_FAILS = "2f7a2f2a-0002-4a10-9f1a-9b0f0d5c1002"
DEF_NEVER_FINISHES = "2f7a2f2a-0003-4a10-9f1a-9b0f0d5c1003"

RESOURCE_CLUSTER = "8b1d4a76-2c33-4a5e-9f27-6a4f2c0b7e11"
RESOURCE_DATASTORE = "3d9c7e21-5b48-4d19-8a63-1f7e5c9d0a22"

FAST_POLLING = {"poll_interval": 0.02, "poll_timeout": 10.0, "request_timeout": 10.0}


def content_type_of(record):
    return record["headers"].get("content-type", "").split(";")[0].strip()


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
