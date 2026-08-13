"""Verification for the drain-safe SDDC Manager credential broker.

Everything here runs against the loopback stand-in in tests/mock_sddc.py. No
VMware endpoint is contacted.

Protected fixture. Do not edit.
"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _HERE)

import mock_sddc  # noqa: E402
from mock_sddc import MockSddcManager  # noqa: E402

from vcfcred import (  # noqa: E402
    Contract,
    CredentialBroker,
    RotationFailed,
    TargetCredential,
    build_token_spec,
    build_update_spec,
)

JOIN_TIMEOUT = 10.0
OBSERVATION_TIMEOUT = 2.0


def make_target():
    """The broker is configured with what the operator knows.

    resource_id and password are deliberately left unset: this is a ROTATE, so
    SDDC Manager picks the replacement password, and the operator identifies the
    resource by name.
    """
    return TargetCredential(
        credential_id=None,
        username=mock_sddc.SERVICE_USERNAME,
        resource_type=mock_sddc.RESOURCE_TYPE,
        resource_name=mock_sddc.RESOURCE_NAME,
        resource_id=None,
        account_type=mock_sddc.ACCOUNT_TYPE,
        credential_type=mock_sddc.CREDENTIAL_TYPE,
    )


def wait_for_gate_closed(broker, timeout=OBSERVATION_TIMEOUT):
    """Observe the broker's gate state without delaying rotation progress."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with broker._condition:
            if not broker._gate_open:
                return True
        time.sleep(0.001)
    return False


class BrokerTestCase(unittest.TestCase):
    fail_rotation = False

    def setUp(self):
        self.mock = MockSddcManager(fail_rotation=self.fail_rotation)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.broker = CredentialBroker(
            base_url=self.mock.base_url,
            admin_username=mock_sddc.ADMIN_USERNAME,
            admin_password=mock_sddc.ADMIN_PASSWORD,
            target=make_target(),
            initial_password=mock_sddc.INITIAL_PASSWORD,
        )

    def service_token_calls(self):
        """createToken entries made with the managed secret, not the admin one."""
        return [
            entry
            for entry in self.mock.log.by_operation("createToken")
            if entry["presented_secret"] != "admin"
        ]


class TestRequestWireShape(BrokerTestCase):
    def test_update_spec_omits_unset_optional_fields(self):
        self.broker.rotate()

        patches = self.mock.log.by_operation("updateOrRotatePasswords")
        self.assertEqual(len(patches), 1, "expected exactly one rotation submission")
        entry = patches[0]

        self.assertEqual(entry["method"], "PATCH")
        self.assertEqual(entry["path"], "/v1/credentials")
        self.assertEqual(entry["raw_query"], "", "updateOrRotatePasswords takes no query parameters")
        self.assertEqual(entry["headers"].get("content-type"), "application/json")
        self.assertEqual(entry["headers"].get("authorization"), "Bearer <token>")
        self.assertEqual(entry["status"], 202)

        expected = {
            "operationType": "ROTATE",
            "elements": [
                {
                    "resourceName": mock_sddc.RESOURCE_NAME,
                    "resourceType": mock_sddc.RESOURCE_TYPE,
                    "credentials": [
                        {
                            "credentialType": mock_sddc.CREDENTIAL_TYPE,
                            "accountType": mock_sddc.ACCOUNT_TYPE,
                            "username": mock_sddc.SERVICE_USERNAME,
                        }
                    ],
                }
            ],
        }
        self.assertEqual(
            entry["body"],
            expected,
            "CredentialsUpdateSpec must carry exactly the configured values; unset "
            "optional properties (resourceId, password, autoRotatePolicy) must be "
            "absent, not null and not empty.\nsent: %s" % json.dumps(entry["body"], indent=2),
        )

    def test_token_spec_omits_unset_optional_fields(self):
        self.broker.rotate()

        tokens = self.mock.log.by_operation("createToken")
        self.assertTrue(tokens, "the broker must authenticate with createToken")
        for entry in tokens:
            self.assertEqual(entry["method"], "POST")
            self.assertEqual(entry["path"], "/v1/tokens")
            self.assertEqual(
                sorted(entry["body"]),
                ["password", "username"],
                "TokenCreationSpec must carry only the properties that have a "
                "value; apiKey and idToken are unset and must be absent.\n"
                "sent: %s" % json.dumps(entry["body"]),
            )

    def test_token_spec_omits_unset_username_and_password(self):
        self.assertEqual(build_token_spec(None, None), {})

    def test_minimal_update_spec_omits_every_unset_optional_field(self):
        target = TargetCredential(
            credential_id=None,
            username="service-account",
            resource_type="VCENTER",
        )
        self.assertEqual(
            build_update_spec(target, "ROTATE"),
            {
                "operationType": "ROTATE",
                "elements": [
                    {
                        "resourceType": "VCENTER",
                        "credentials": [{"username": "service-account"}],
                    }
                ],
            },
        )

    def test_rotate_never_sends_a_caller_supplied_password(self):
        target = TargetCredential(
            credential_id=None,
            username="service-account",
            resource_type="VCENTER",
        )
        spec = build_update_spec(target, "ROTATE", password="must-not-be-sent")
        self.assertNotIn("password", spec["elements"][0]["credentials"][0])

    def test_update_keeps_a_caller_supplied_password(self):
        target = TargetCredential(
            credential_id=None,
            username="service-account",
            resource_type="VCENTER",
        )
        spec = build_update_spec(target, "UPDATE", password="caller-chosen")
        self.assertEqual(
            spec["elements"][0]["credentials"][0].get("password"),
            "caller-chosen",
        )


class TestTaskIsPolledToTerminal(BrokerTestCase):
    def test_rotation_waits_for_the_credentials_task(self):
        result = self.broker.rotate()
        self.assertEqual(result.status, "SUCCESSFUL")

        polls = self.mock.log.by_operation("getCredentialsTask")
        self.assertGreaterEqual(
            len(polls),
            mock_sddc.POLLS_UNTIL_TERMINAL,
            "the 202 Task is an acknowledgement; the outcome must be polled from "
            "getCredentialsTask until it reaches a terminal status",
        )
        self.assertEqual(polls[-1]["status"], 200)

        reads = self.mock.log.by_operation("getCredential")
        self.assertTrue(reads, "the rotated password must be read back with getCredential")
        self.assertGreater(
            reads[-1]["seq"],
            polls[-1]["seq"],
            "the replacement password may only be read after the task settled",
        )

        self.assertEqual(self.broker.current_password(), mock_sddc.ROTATED_PASSWORD)
        self.assertEqual(self.mock.current_password(), mock_sddc.ROTATED_PASSWORD)


class TestNoStrandedRequests(BrokerTestCase):
    WORKERS = 4

    def test_in_flight_requests_are_not_stranded_on_the_retired_secret(self):
        leased = threading.Barrier(self.WORKERS + 1)
        release = threading.Event()
        used = []
        failures = []
        lock = threading.Lock()

        def worker(index):
            try:
                with self.broker.lease() as lease:
                    leased.wait(timeout=JOIN_TIMEOUT)
                    release.wait(timeout=JOIN_TIMEOUT)
                    lease.create_token()
                    with lock:
                        used.append((index, lease.password))
            except BaseException as exc:  # noqa: BLE001 - reported below
                with lock:
                    failures.append((index, repr(exc)))

        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(self.WORKERS)
        ]
        for thread in threads:
            thread.start()
        leased.wait(timeout=JOIN_TIMEOUT)
        self.assertEqual(self.broker.leases_in_flight(), self.WORKERS)

        resolution_started = threading.Event()
        allow_resolution = threading.Event()
        resolution_complete = threading.Event()
        list_credentials = self.broker.client.list_credentials

        def controlled_list_credentials(token, **filters):
            resolution_started.set()
            if not allow_resolution.wait(timeout=JOIN_TIMEOUT):
                raise TimeoutError("test did not release credential resolution")
            result = list_credentials(token, **filters)
            resolution_complete.set()
            return result

        self.broker.client.list_credentials = controlled_list_credentials

        rotation_error = []

        def do_rotate():
            try:
                self.broker.rotate()
            except BaseException as exc:  # noqa: BLE001 - reported below
                rotation_error.append(repr(exc))

        rotation = threading.Thread(target=do_rotate, daemon=True)
        rotation.start()

        self.assertTrue(
            resolution_started.wait(timeout=JOIN_TIMEOUT),
            "rotation did not begin credential resolution",
        )

        # Resolution happens while the gate is still open. This probe must be
        # able to borrow and release the current generation before resolution
        # is allowed to complete.
        probe_started = threading.Event()
        probe_done = threading.Event()
        probe = {}

        def resolution_probe():
            try:
                probe_started.set()
                with self.broker.lease() as lease:
                    probe["password"] = lease.password
            except BaseException as exc:  # noqa: BLE001
                probe["error"] = repr(exc)
            finally:
                probe_done.set()

        probe_thread = threading.Thread(target=resolution_probe, daemon=True)
        probe_thread.start()
        self.assertTrue(probe_started.wait(timeout=JOIN_TIMEOUT))
        probe_finished_before_resolution = probe_done.wait(timeout=OBSERVATION_TIMEOUT)
        allow_resolution.set()

        self.assertTrue(
            resolution_complete.wait(timeout=JOIN_TIMEOUT),
            "credential resolution did not complete",
        )
        gate_closed_while_leased = wait_for_gate_closed(self.broker)
        submitted_while_leased = self.mock.log.wait_for(
            "updateOrRotatePasswords", timeout=OBSERVATION_TIMEOUT
        )

        release.set()
        for thread in threads:
            thread.join(timeout=JOIN_TIMEOUT)
        rotation.join(timeout=JOIN_TIMEOUT)
        probe_thread.join(timeout=JOIN_TIMEOUT)
        self.assertFalse(rotation.is_alive(), "rotation did not finish")
        self.assertFalse(probe_thread.is_alive(), "resolution probe did not finish")
        self.assertEqual(rotation_error, [], "rotation raised")
        self.assertTrue(
            probe_finished_before_resolution,
            "rotation shut the lease gate before credential resolution completed",
        )
        self.assertTrue(
            gate_closed_while_leased,
            "rotation did not shut the lease gate after credential resolution",
        )
        self.assertNotIn("error", probe, "resolution-time caller failed: %s" % probe.get("error"))
        self.assertEqual(probe.get("password"), mock_sddc.INITIAL_PASSWORD)
        self.assertFalse(
            submitted_while_leased,
            "rotation was submitted while %d leases against the retiring secret "
            "were still open" % self.WORKERS,
        )
        self.assertEqual(failures, [], "leased requests failed")
        self.assertEqual(len(used), self.WORKERS)

        for index, password in used:
            self.assertEqual(
                password,
                mock_sddc.INITIAL_PASSWORD,
                "worker %d leased a password it should not have seen" % index,
            )

        service_calls = self.service_token_calls()
        self.assertEqual(len(service_calls), self.WORKERS)
        for entry in service_calls:
            self.assertEqual(
                entry["status"],
                201,
                "a request that had already leased the secret was answered %s; "
                "the secret was retired underneath it" % entry["status"],
            )
            self.assertEqual(entry["presented_secret"], "service-current")

        patch_seq = self.mock.log.index_of_first("updateOrRotatePasswords")
        self.assertIsNotNone(patch_seq)
        for entry in service_calls:
            self.assertLess(
                entry["seq"],
                patch_seq,
                "request %d reached the service after the rotation was submitted; "
                "rotation must wait for outstanding leases to drain" % entry["seq"],
            )

        self.assertEqual(self.broker.leases_in_flight(), 0)
        self.assertEqual(self.broker.current_password(), mock_sddc.ROTATED_PASSWORD)


class TestBlockedCallerGetsReplacement(BrokerTestCase):
    def test_caller_that_waited_through_rotation_uses_the_new_secret(self):
        holding = threading.Event()
        release = threading.Event()
        holder_error = []

        def holder():
            try:
                with self.broker.lease() as lease:
                    holding.set()
                    release.wait(timeout=JOIN_TIMEOUT)
                    lease.create_token()
            except BaseException as exc:  # noqa: BLE001
                holder_error.append(repr(exc))

        holder_thread = threading.Thread(target=holder, daemon=True)
        holder_thread.start()
        self.assertTrue(holding.wait(timeout=JOIN_TIMEOUT))

        resolution_complete = threading.Event()
        list_credentials = self.broker.client.list_credentials

        def observed_list_credentials(token, **filters):
            result = list_credentials(token, **filters)
            resolution_complete.set()
            return result

        self.broker.client.list_credentials = observed_list_credentials

        rotation_error = []

        def do_rotate():
            try:
                self.broker.rotate()
            except BaseException as exc:  # noqa: BLE001
                rotation_error.append(repr(exc))

        rotation = threading.Thread(target=do_rotate, daemon=True)
        rotation.start()
        self.assertTrue(resolution_complete.wait(timeout=JOIN_TIMEOUT))
        gate_closed_during_drain = wait_for_gate_closed(self.broker)

        # Arrives while rotation is draining, so it has to wait for the gate.
        latecomer = {}
        late_started = threading.Event()
        late_acquired = threading.Event()

        def late_caller():
            try:
                late_started.set()
                with self.broker.lease() as lease:
                    latecomer["password"] = lease.password
                    late_acquired.set()
                    latecomer["token"] = lease.create_token()
            except BaseException as exc:  # noqa: BLE001
                latecomer["error"] = repr(exc)

        late_thread = threading.Thread(target=late_caller, daemon=True)
        late_thread.start()
        self.assertTrue(
            late_started.wait(timeout=JOIN_TIMEOUT), "the late caller did not start"
        )
        acquired_during_rotation = late_acquired.wait(timeout=OBSERVATION_TIMEOUT)

        release.set()
        holder_thread.join(timeout=JOIN_TIMEOUT)
        rotation.join(timeout=JOIN_TIMEOUT)
        late_thread.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(holder_error, [], "the request holding the lease failed")
        self.assertEqual(rotation_error, [], "rotation raised")
        self.assertTrue(gate_closed_during_drain, "rotation did not shut the lease gate")
        self.assertFalse(
            acquired_during_rotation,
            "a caller arriving during rotation did not wait on the shut gate",
        )
        self.assertFalse(late_thread.is_alive(), "the waiting caller was never released")
        self.assertNotIn("error", latecomer, "the waiting caller failed: %s" % latecomer.get("error"))
        self.assertEqual(
            latecomer.get("password"),
            mock_sddc.ROTATED_PASSWORD,
            "the caller that waited through the rotation resumed on the retired "
            "secret; the secret must be read after the wait, not before it",
        )


class TestFailedRotation(BrokerTestCase):
    fail_rotation = True

    def test_failed_rotation_keeps_the_existing_secret_usable(self):
        with self.assertRaises(RotationFailed):
            self.broker.rotate()

        self.assertEqual(self.broker.current_password(), mock_sddc.INITIAL_PASSWORD)
        self.assertEqual(self.mock.current_password(), mock_sddc.INITIAL_PASSWORD)

        done = threading.Event()
        outcome = {}

        def caller():
            try:
                with self.broker.lease() as lease:
                    outcome["password"] = lease.password
                    lease.create_token()
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = repr(exc)
            finally:
                done.set()

        threading.Thread(target=caller, daemon=True).start()
        self.assertTrue(
            done.wait(timeout=JOIN_TIMEOUT),
            "a failed rotation left callers blocked; the gate must reopen",
        )
        self.assertNotIn("error", outcome, "callers failed after a failed rotation: %s" % outcome.get("error"))
        self.assertEqual(outcome.get("password"), mock_sddc.INITIAL_PASSWORD)
        self.assertEqual(self.broker.leases_in_flight(), 0)


class TestStaysInsideTheContract(BrokerTestCase):
    def test_only_contract_operations_are_used(self):
        self.broker.rotate()

        contract = Contract()
        allowed = set(contract.operation_ids())
        seen = set()
        for entry in self.mock.log.entries():
            self.assertNotEqual(
                entry["operationId"],
                "<unmatched>",
                "%s %s is not an operation named by docs/contract.json"
                % (entry["method"], entry["path"]),
            )
            seen.add(entry["operationId"])
        self.assertTrue(seen)
        self.assertTrue(seen <= allowed, "unexpected operations: %s" % (seen - allowed))

    def test_mock_serves_nothing_outside_the_contract(self):
        request = urllib.request.Request(self.mock.base_url + "/v1/hosts", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 404)


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromTestCase(case)
        for case in (
            TestRequestWireShape,
            TestTaskIsPolledToTerminal,
            TestNoStrandedRequests,
            TestBlockedCallerGetsReplacement,
            TestFailedRotation,
            TestStaysInsideTheContract,
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
