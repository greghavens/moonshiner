"""Wire-shape and async-polling acceptance tests.

PROTECTED FILE -- do not modify.

Everything asserted here is read back out of the mock's request log, so the
assertions are about the bytes the client actually sent, not about how it is
structured internally.
"""

import ast
import os
import sys
import unittest

from tests.support import (
    API_VERSION,
    MINIMAL_WIRE_KEYS,
    POLL_INTERVAL,
    TOKEN,
    MockServiceTestCase,
    minimal_spec,
    walk,
)

PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "vcfa_provision")


class CreateRequestBodyTests(MockServiceTestCase):
    """The create-machine body must carry set fields and omit unset ones."""

    def test_minimal_spec_sends_only_the_fields_that_were_set(self):
        client = self.make_client()
        client.provision_machine(minimal_spec())

        create = self.requests()[0]
        self.assertEqual(create["method"], "POST")
        self.assertEqual(create["path"], "/iaas/api/machines")
        self.assertIsNotNone(create["body"], "create request carried no JSON body")
        self.assertEqual(set(create["body"]), MINIMAL_WIRE_KEYS)
        self.assertEqual(create["body"]["name"], "app-node-01")
        self.assertEqual(create["body"]["projectId"], "0f3a1c58-9b74-42d6-8e05-7c1d2b9a6e43")
        self.assertEqual(create["body"]["flavor"], "medium")
        self.assertEqual(create["body"]["flavorRef"], "vcf.medium")
        self.assertEqual(create["body"]["image"], "ubuntu-22-04")
        self.assertEqual(create["body"]["imageRef"], "content-library/ubuntu-22-04-server")

    def test_unset_optionals_are_absent_not_empty(self):
        client = self.make_client()
        client.provision_machine(minimal_spec(description="only this optional is set"))

        body = self.requests()[0]["body"]
        self.assertEqual(set(body), MINIMAL_WIRE_KEYS | {"description"})
        for absent in ("deploymentId", "machineCount", "customProperties",
                       "nics", "disks", "tags", "bootConfig", "constraints"):
            self.assertNotIn(absent, body,
                             "{0} was unset but still went on the wire".format(absent))

    def test_no_null_or_empty_placeholders_anywhere_in_the_body(self):
        from vcfa_provision import NetworkInterfaceSpec, Tag

        client = self.make_client()
        client.provision_machine(minimal_spec(
            description="web tier",
            machine_count=2,
            custom_properties={"osType": "LINUX"},
            tags=[Tag(key="tier", value="web")],
            nics=[NetworkInterfaceSpec(network_id="net-a1b2")],
            boot_config_content="#cloud-config\npackages:\n  - nginx\n",
        ))

        body = self.requests()[0]["body"]
        for path, value in walk(body):
            self.assertIsNotNone(value, "{0} was sent as null".format(path))
            if isinstance(value, (dict, list)) and path != "$":
                self.assertTrue(value, "{0} was sent as an empty container".format(path))

    def test_nested_objects_carry_only_their_set_properties(self):
        from vcfa_provision import DiskSpec, NetworkInterfaceSpec, Tag

        client = self.make_client()
        client.provision_machine(minimal_spec(
            nics=[
                NetworkInterfaceSpec(network_id="net-a1b2"),
                NetworkInterfaceSpec(network_id="net-c3d4", device_index=1,
                                     addresses=["10.24.6.51"]),
            ],
            disks=[DiskSpec(block_device_id="disk-77", unit_number="1")],
            tags=[Tag(key="tier", value="web"), Tag(key="env", value="prod")],
            boot_config_content="#cloud-config\n",
        ))

        body = self.requests()[0]["body"]
        self.assertEqual(body["nics"], [
            {"networkId": "net-a1b2"},
            {"networkId": "net-c3d4", "deviceIndex": 1, "addresses": ["10.24.6.51"]},
        ])
        self.assertEqual(body["disks"], [{"blockDeviceId": "disk-77", "unitNumber": "1"}])
        self.assertEqual(body["tags"], [
            {"key": "tier", "value": "web"},
            {"key": "env", "value": "prod"},
        ])
        self.assertEqual(body["bootConfig"], {"content": "#cloud-config\n"})

    def test_all_supported_nested_fields_use_the_documented_wire_names(self):
        from vcfa_provision import DiskSpec, NetworkInterfaceSpec

        client = self.make_client()
        client.provision_machine(minimal_spec(
            nics=[NetworkInterfaceSpec(
                network_id="net-a1b2",
                device_index=2,
                name="frontend",
                description="public interface",
                fabric_network_id="fabric-net-12",
                addresses=["10.24.6.51"],
                mac_address="00:50:56:aa:bb:cc",
                security_group_ids=["sg-web"],
                custom_properties={"assignment": "static"},
            )],
            disks=[DiskSpec(
                block_device_id="disk-77",
                name="data",
                description="application data",
                scsi_controller="SCSI_Controller_1",
                unit_number="2",
                disk_attachment_properties={"thinProvisioned": "true"},
            )],
        ))

        body = self.requests()[0]["body"]
        self.assertEqual(body["nics"], [{
            "networkId": "net-a1b2",
            "deviceIndex": 2,
            "name": "frontend",
            "description": "public interface",
            "fabricNetworkId": "fabric-net-12",
            "addresses": ["10.24.6.51"],
            "macAddress": "00:50:56:aa:bb:cc",
            "securityGroupIds": ["sg-web"],
            "customProperties": {"assignment": "static"},
        }])
        self.assertEqual(body["disks"], [{
            "blockDeviceId": "disk-77",
            "name": "data",
            "description": "application data",
            "scsiController": "SCSI_Controller_1",
            "unitNumber": "2",
            "diskAttachmentProperties": {"thinProvisioned": "true"},
        }])

    def test_optional_constraints_use_the_documented_property_names(self):
        from vcfa_provision import Constraint

        client = self.make_client()
        client.provision_machine(minimal_spec(
            flavor_ref="small-2vcpu",
            deployment_id="b7a41c92-6d38-4f51-9c07-2a8e6b4d3f15",
            constraints=[Constraint(expression="ha:true", mandatory=True),
                         Constraint(expression="location:eu")],
        ))

        body = self.requests()[0]["body"]
        self.assertEqual(body["flavorRef"], "small-2vcpu")
        self.assertEqual(body["deploymentId"], "b7a41c92-6d38-4f51-9c07-2a8e6b4d3f15")
        self.assertEqual(body["constraints"], [
            {"expression": "ha:true", "mandatory": True},
            {"expression": "location:eu"},
        ])


class RequestEnvelopeTests(MockServiceTestCase):
    """Headers, query parameters and the set of endpoints touched."""

    def test_every_request_is_authorized_and_version_pinned(self):
        client = self.make_client()
        client.provision_machine(minimal_spec())

        entries = self.requests()
        self.assertGreaterEqual(len(entries), 3)
        for entry in entries:
            self.assertEqual(entry["headers"].get("authorization"), TOKEN,
                             "Authorization must be sent verbatim, with no added prefix")
            self.assertEqual(entry["query"].get("apiVersion"), [API_VERSION],
                             "apiVersion missing from {0} {1}".format(entry["method"],
                                                                      entry["path"]))

    def test_only_the_create_request_carries_a_body_and_it_is_json(self):
        client = self.make_client()
        client.provision_machine(minimal_spec())

        for entry in self.requests():
            if entry["method"] == "POST":
                self.assertIn("application/json",
                              entry["headers"].get("content-type", ""))
                self.assertNotIn("body_error", entry)
                self.assertIsInstance(entry["body"], dict)
            else:
                self.assertEqual(entry["raw_body"], "")

    def test_client_touches_only_the_three_contracted_operations(self):
        client = self.make_client()
        client.provision_machine(minimal_spec())

        for method, path in self.wire_calls():
            segments = [segment for segment in path.split("/") if segment]
            allowed = (
                (method == "POST" and segments == ["iaas", "api", "machines"])
                or (method == "GET" and len(segments) == 4
                    and segments[:3] in (["iaas", "api", "request-tracker"],
                                         ["iaas", "api", "machines"]))
            )
            self.assertTrue(allowed, "{0} {1} is not a contracted operation".format(method, path))


class PollingTests(MockServiceTestCase):
    """The request must be driven to a terminal state, not assumed complete."""

    def test_polls_until_terminal_then_reads_the_machine(self):
        client = self.make_client()
        result = client.provision_machine(minimal_spec())

        self.assertEqual(self.wire_calls(), [
            ("POST", "/iaas/api/machines"),
            ("GET", "/iaas/api/request-tracker/" + self.config.request_id),
            ("GET", "/iaas/api/request-tracker/" + self.config.request_id),
            ("GET", "/iaas/api/request-tracker/" + self.config.request_id),
            ("GET", "/iaas/api/machines/" + self.config.machine_id),
        ])
        self.assertEqual(result.request_id, self.config.request_id)
        self.assertEqual(result.machine_id, self.config.machine_id)
        self.assertEqual(result.poll_count, 3)
        self.assertEqual(result.tracker["status"], "FINISHED")
        self.assertEqual(result.machine["id"], self.config.machine_id)
        self.assertEqual(result.machine["powerState"], "ON")

    def test_sleeps_between_polls_only(self):
        client = self.make_client()
        client.provision_machine(minimal_spec())

        self.assertEqual(self.sleeper.calls, [POLL_INTERVAL, POLL_INTERVAL],
                         "expected one wait between each pair of polls and no other waits")


class ImmediateCompletionTests(MockServiceTestCase):
    config_overrides = {"inprogress_polls": 0}

    def test_terminal_on_first_poll_stops_immediately(self):
        client = self.make_client()
        result = client.provision_machine(minimal_spec())

        tracker_reads = [call for call in self.wire_calls()
                         if call[1].startswith("/iaas/api/request-tracker/")]
        self.assertEqual(len(tracker_reads), 1)
        self.assertEqual(result.poll_count, 1)
        self.assertEqual(self.sleeper.calls, [])


class FailedRequestTests(MockServiceTestCase):
    config_overrides = {"terminal_status": "FAILED", "inprogress_polls": 1}

    def test_terminal_failure_raises_and_does_not_read_the_machine(self):
        from vcfa_provision import ProvisioningFailed

        client = self.make_client()
        with self.assertRaises(ProvisioningFailed) as caught:
            client.provision_machine(minimal_spec())

        self.assertEqual(caught.exception.request_id, self.config.request_id)
        self.assertEqual(caught.exception.message, self.config.failure_message)
        self.assertEqual(
            [call for call in self.wire_calls() if call == ("GET", "/iaas/api/machines/"
                                                            + self.config.machine_id)],
            [],
            "the machine must not be read back after a failed request",
        )


class TimeoutTests(MockServiceTestCase):
    config_overrides = {"never_finish": True}

    def test_bounded_polling_raises_timeout(self):
        from vcfa_provision import ProvisioningTimeout

        client = self.make_client(max_poll_attempts=4)
        with self.assertRaises(ProvisioningTimeout) as caught:
            client.provision_machine(minimal_spec())

        self.assertEqual(caught.exception.attempts, 4)
        tracker_reads = [call for call in self.wire_calls()
                         if call[1].startswith("/iaas/api/request-tracker/")]
        self.assertEqual(len(tracker_reads), 4)
        self.assertEqual(self.sleeper.calls, [POLL_INTERVAL] * 3)


class ApiErrorTests(MockServiceTestCase):
    def test_rejected_submission_raises_before_any_polling(self):
        from vcfa_provision import ApiError, VcfAutomationClient

        client = VcfAutomationClient(self.base_url, "wrong-token", API_VERSION,
                                     poll_interval=POLL_INTERVAL, max_poll_attempts=10,
                                     sleep=self.sleeper)
        with self.assertRaises(ApiError) as caught:
            client.provision_machine(minimal_spec())

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.wire_calls(), [("POST", "/iaas/api/machines")])
        self.assertEqual(self.sleeper.calls, [])


class StdlibOnlyTests(unittest.TestCase):
    def test_package_imports_nothing_outside_the_standard_library(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:  # pragma: no cover - Python < 3.10
            self.skipTest("sys.stdlib_module_names is unavailable")

        for filename in sorted(os.listdir(PACKAGE_DIR)):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(PACKAGE_DIR, filename)
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                roots = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
                for root in roots:
                    self.assertIn(root, stdlib,
                                  "{0} imports non-stdlib module {1}".format(filename, root))

    def test_package_does_not_hardcode_the_mock_endpoint(self):
        for filename in sorted(os.listdir(PACKAGE_DIR)):
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(PACKAGE_DIR, filename), "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("127.0.0.1", source)
            self.assertNotIn("localhost", source)


if __name__ == "__main__":
    unittest.main()
