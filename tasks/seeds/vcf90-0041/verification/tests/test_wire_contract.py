#!/usr/bin/env python3
"""Wire-contract verification for the vcfsizer right-sizing tool.

The suite starts the loopback vCenter mock on 127.0.0.1, runs the tool against
it once, and then asserts the exact shape of every request the tool produced.
No live VMware endpoint is contacted.

Run with:  python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
MOCK = os.path.join(REPO_ROOT, "mock", "vcenter_mock.py")
CONTRACT = os.path.join(REPO_ROOT, "docs", "contract.json")
SEED = os.path.join(REPO_ROOT, "fixtures", "vcenter_state.json")
PLAN = os.path.join(REPO_ROOT, "fixtures", "rightsizing-plan.json")

SESSION_HEADER = "vmware-api-session-id"

EDGE, DB, CACHE, LEGACY = "vm-1041", "vm-1052", "vm-1063", "vm-1077"

# (method, path, operation_id, status) for every request, in order.
EXPECTED_SEQUENCE = [
    ("POST", "/api/session", "Cis.Session_create", 201),
    ("GET", "/api/vcenter/vm", "Vcenter.VM_list", 200),
    ("PATCH", "/api/vcenter/vm/%s/hardware/cpu" % EDGE, "Vcenter.Vm.Hardware.Cpu_update", 204),
    ("PATCH", "/api/vcenter/vm/%s/hardware/memory" % EDGE, "Vcenter.Vm.Hardware.Memory_update", 204),
    ("PATCH", "/api/vcenter/vm/%s/hardware/cpu" % DB, "Vcenter.Vm.Hardware.Cpu_update", 204),
    ("PATCH", "/api/vcenter/vm/%s/hardware/memory" % DB, "Vcenter.Vm.Hardware.Memory_update", 401),
    ("POST", "/api/session", "Cis.Session_create", 201),
    ("PATCH", "/api/vcenter/vm/%s/hardware/memory" % DB, "Vcenter.Vm.Hardware.Memory_update", 204),
    ("PATCH", "/api/vcenter/vm/%s/hardware/memory" % CACHE, "Vcenter.Vm.Hardware.Memory_update", 204),
    ("DELETE", "/api/session", "Cis.Session_delete", 204),
]

# Index in EXPECTED_SEQUENCE -> the exact JSON object that must be on the wire.
EXPECTED_BODIES = {
    2: {"count": 8, "cores_per_socket": 2},
    3: {"size_mib": 16384},
    4: {"count": 4},
    5: {"hot_add_enabled": False},
    7: {"hot_add_enabled": False},
    8: {"size_mib": 8192, "hot_add_enabled": True},
}

EXPECTED_REPORT = {
    "plan_id": "rs-2026-q3-edge",
    "session_creations": 2,
    "results": [
        {"vm_name": "edge-gw-01", "vm": EDGE, "applied": ["cpu", "memory"]},
        {"vm_name": "db-tier-02", "vm": DB, "applied": ["cpu", "memory"]},
        {"vm_name": "app-cache-03", "vm": CACHE, "applied": ["memory"]},
        {"vm_name": "legacy-print-01", "vm": LEGACY, "applied": []},
    ],
}

EXPECTED_MUTATIONS = [
    {
        "operation_id": "Vcenter.Vm.Hardware.Cpu_update",
        "vm": EDGE,
        "name": "edge-gw-01",
        "changed": {"count": 8, "cores_per_socket": 2},
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Memory_update",
        "vm": EDGE,
        "name": "edge-gw-01",
        "changed": {"size_mib": 16384},
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Cpu_update",
        "vm": DB,
        "name": "db-tier-02",
        "changed": {"count": 4},
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Memory_update",
        "vm": DB,
        "name": "db-tier-02",
        "changed": {"hot_add_enabled": False},
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Memory_update",
        "vm": CACHE,
        "name": "app-cache-03",
        "changed": {"size_mib": 8192, "hot_add_enabled": True},
    },
]


class WireContractTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        with open(SEED, encoding="utf-8") as handle:
            credentials = json.load(handle)["credentials"]
        cls.username = credentials["username"]
        cls.password = credentials["password"]

        cls.tmp = tempfile.TemporaryDirectory()
        workdir = cls.tmp.name
        cls.log_path = os.path.join(workdir, "requests.jsonl")
        cls.state_path = os.path.join(workdir, "state.json")
        cls.report_path = os.path.join(workdir, "report.json")
        port_file = os.path.join(workdir, "port")

        cls.mock = subprocess.Popen(
            [
                sys.executable,
                MOCK,
                "--contract", CONTRACT,
                "--seed", SEED,
                "--log", cls.log_path,
                "--state-out", cls.state_path,
                "--host", "127.0.0.1",
                "--port", "0",
                "--port-file", port_file,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        port = None
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls.mock.poll() is not None:
                raise AssertionError(
                    "the mock exited early: %s"
                    % cls.mock.stderr.read().decode("utf-8", "replace")
                )
            if os.path.exists(port_file):
                with open(port_file, encoding="utf-8") as handle:
                    text = handle.read().strip()
                if text:
                    port = int(text)
                    break
            time.sleep(0.05)
        if port is None:
            cls.mock.kill()
            raise AssertionError("the mock did not report a port within 30s")
        cls.base_url = "http://127.0.0.1:%d" % port

        env = dict(os.environ)
        env["VCENTER_PASSWORD"] = cls.password
        env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        try:
            cls.run_result = subprocess.run(
                [
                    sys.executable, "-m", "vcfsizer",
                    "--base-url", cls.base_url,
                    "--username", cls.username,
                    "--plan", PLAN,
                    "--report", cls.report_path,
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        finally:
            cls.mock.terminate()
            try:
                cls.mock.communicate(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover
                cls.mock.kill()
                cls.mock.communicate(timeout=10)

        cls.entries = []
        if os.path.exists(cls.log_path):
            with open(cls.log_path, encoding="utf-8") as handle:
                cls.entries = [json.loads(line) for line in handle if line.strip()]
        cls.state = {}
        if os.path.exists(cls.state_path):
            with open(cls.state_path, encoding="utf-8") as handle:
                cls.state = json.load(handle)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def entry(self, index):
        self.assertGreater(
            len(self.entries), index,
            "expected at least %d requests, the tool made %d"
            % (index + 1, len(self.entries)),
        )
        return self.entries[index]

    def tokens(self):
        issued = self.state.get("tokens_issued", [])
        self.assertGreaterEqual(
            len(issued), 2,
            "the mock issued %d session tokens; the run needs a refreshed token"
            % len(issued),
        )
        return issued

    def cli_failure_detail(self):
        return "stdout=%r stderr=%r" % (
            self.run_result.stdout.decode("utf-8", "replace"),
            self.run_result.stderr.decode("utf-8", "replace"),
        )

    # -- tests ------------------------------------------------------------

    def test_cli_exits_successfully(self):
        self.assertEqual(
            0, self.run_result.returncode,
            "`python3 -m vcfsizer` exited %d: %s"
            % (self.run_result.returncode, self.cli_failure_detail()),
        )

    def test_request_sequence_is_exact(self):
        actual = [
            (e["method"], e["path"], e["operation_id"], e["status"])
            for e in self.entries
        ]
        self.assertEqual(EXPECTED_SEQUENCE, actual)

    def test_only_contract_operations_are_called(self):
        unknown = [
            (e["method"], e["path"]) for e in self.entries if e["operation_id"] is None
        ]
        self.assertEqual([], unknown, "requests hit endpoints outside the contract")

    def test_no_unexpected_error_responses(self):
        failures = [
            (e["seq"], e["method"], e["path"], e["status"], e["response_body"])
            for e in self.entries
            if e["status"] >= 400 and e["seq"] != 6
        ]
        self.assertEqual(
            [], failures,
            "the only failing request may be the one that hits the expired token",
        )

    def test_session_create_uses_basic_auth_only(self):
        expected = "Basic " + base64.b64encode(
            ("%s:%s" % (self.username, self.password)).encode("utf-8")
        ).decode("ascii")
        for index in (0, 6):
            entry = self.entry(index)
            headers = entry["headers"]
            self.assertEqual(expected, headers.get("authorization"))
            self.assertNotIn(
                SESSION_HEADER, headers,
                "Cis.Session_create must not carry a session token",
            )
            self.assertIn(entry["body_raw"], (None, ""),
                          "Cis.Session_create defines no request body")
            self.assertEqual("", entry["raw_query"])

    def test_authenticated_calls_use_the_session_header(self):
        for index, (_, _, operation_id, _) in enumerate(EXPECTED_SEQUENCE):
            if operation_id == "Cis.Session_create":
                continue
            headers = self.entry(index)["headers"]
            self.assertIn(
                SESSION_HEADER, headers,
                "request %d must authenticate with the %s header"
                % (index + 1, SESSION_HEADER),
            )
            self.assertNotIn(
                "authorization", headers,
                "request %d must not resend HTTP Basic credentials" % (index + 1),
            )

    def test_vm_list_filter_serialization(self):
        entry = self.entry(1)
        self.assertEqual("GET", entry["method"])
        self.assertEqual(
            ["names"], sorted(entry["query"]),
            "the listing must filter on `names` alone; the raw query was %r"
            % entry["raw_query"],
        )
        self.assertEqual(
            ["app-cache-03", "db-tier-02", "edge-gw-01", "legacy-print-01"],
            sorted(entry["query"]["names"]),
            "each plan target must appear as its own `names` repetition "
            "(style: form, explode: true); the raw query was %r" % entry["raw_query"],
        )
        self.assertEqual(4, len(entry["query"]["names"]))
        self.assertNotIn(",", entry["raw_query"],
                         "array filters must not be comma-joined")
        self.assertIn(entry["body_raw"], (None, ""),
                      "Vcenter.VM_list defines no request body")

    def test_update_bodies_are_exact(self):
        for index, expected in EXPECTED_BODIES.items():
            entry = self.entry(index)
            self.assertEqual(
                expected, entry["body_json"],
                "request %d sent %r" % (index + 1, entry["body_raw"]),
            )
            media_type = (entry["headers"].get("content-type") or "").split(";")[0]
            self.assertEqual("application/json", media_type.strip())

    def test_unset_optional_properties_are_omitted(self):
        never_sent = {
            2: ["hot_add_enabled", "hot_remove_enabled"],
            3: ["hot_add_enabled"],
            4: ["cores_per_socket", "hot_add_enabled", "hot_remove_enabled"],
            5: ["size_mib"],
            7: ["size_mib"],
        }
        for index, absent in never_sent.items():
            entry = self.entry(index)
            self.assertIsInstance(
                entry["body_json"], dict,
                "request %d should carry a JSON update spec, it carried %r"
                % (index + 1, entry["body_raw"]),
            )
            for name in absent:
                self.assertNotIn(
                    name, entry["body_json"],
                    "%s is not being changed, so it must be omitted rather than "
                    "sent empty; request %d was %r"
                    % (name, index + 1, entry["body_raw"]),
                )
        for index in EXPECTED_BODIES:
            raw = self.entry(index)["body_raw"] or ""
            self.assertNotIn("null", raw,
                             "unset properties must be omitted, not sent as null")
            self.assertNotIn('""', raw,
                             "unset properties must be omitted, not sent as empty")
            self.assertNotIn("size_MiB", raw,
                             "the 9.0 memory update spec names the property size_mib")

    def test_token_is_refreshed_and_the_old_one_retired(self):
        first, second = self.tokens()[0], self.tokens()[1]
        self.assertEqual(
            2, len(self.tokens()),
            "the run must create exactly two sessions: the initial one and the "
            "replacement for the expired token",
        )
        presented = [
            e["headers"].get(SESSION_HEADER)
            for e in self.entries
            if e["operation_id"] != "Cis.Session_create"
        ]
        self.assertEqual(
            [first, first, first, first, first, second, second, second], presented
        )

    def test_expired_request_is_retried_identically(self):
        failed, retried = self.entry(5), self.entry(7)
        self.assertEqual(401, failed["status"])
        self.assertEqual(
            "UNAUTHENTICATED", (failed["response_body"] or {}).get("error_type")
        )
        for field in ("method", "path", "raw_query", "body_json"):
            self.assertEqual(
                failed[field], retried[field],
                "the retry must replay the same request, only with a fresh token",
            )
        self.assertNotEqual(
            failed["headers"].get(SESSION_HEADER),
            retried["headers"].get(SESSION_HEADER),
        )

    def test_final_session_is_deleted(self):
        entry = self.entry(len(EXPECTED_SEQUENCE) - 1)
        self.assertEqual(
            len(EXPECTED_SEQUENCE), len(self.entries),
            "logging out must be the last thing the run does",
        )
        self.assertEqual("Cis.Session_delete", entry["operation_id"])
        self.assertEqual(204, entry["status"])
        self.assertEqual(self.tokens()[1], entry["headers"].get(SESSION_HEADER))

    def test_each_change_is_applied_exactly_once(self):
        self.assertEqual(EXPECTED_MUTATIONS, self.state.get("mutations"))

    def test_final_inventory(self):
        by_id = {vm["vm"]: vm for vm in self.state.get("virtual_machines", [])}
        self.assertEqual(8, by_id[EDGE]["cpu"]["count"])
        self.assertEqual(2, by_id[EDGE]["cpu"]["cores_per_socket"])
        self.assertEqual(16384, by_id[EDGE]["memory"]["size_mib"])
        self.assertEqual(4, by_id[DB]["cpu"]["count"])
        self.assertEqual(2, by_id[DB]["cpu"]["cores_per_socket"],
                         "db-tier-02 has no cores-per-socket change in the plan")
        self.assertEqual(32768, by_id[DB]["memory"]["size_mib"],
                         "db-tier-02 has no memory-size change in the plan")
        self.assertFalse(by_id[DB]["memory"]["hot_add_enabled"])
        self.assertEqual(8192, by_id[CACHE]["memory"]["size_mib"])
        self.assertTrue(by_id[CACHE]["memory"]["hot_add_enabled"])
        self.assertEqual(8, by_id[CACHE]["cpu"]["count"],
                         "app-cache-03 has no CPU change in the plan")
        self.assertEqual(1, by_id[LEGACY]["cpu"]["count"],
                         "a no-op target must be resolved but left untouched")
        self.assertEqual(2048, by_id[LEGACY]["memory"]["size_mib"])

    def test_report_contents(self):
        self.assertTrue(
            os.path.exists(self.report_path),
            "no report was written: %s" % self.cli_failure_detail(),
        )
        with open(self.report_path, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(EXPECTED_REPORT, report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
