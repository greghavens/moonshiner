"""Protected acceptance verifier for the VCF 9.1 host-commission workflow."""

import ast
import json
from pathlib import Path
import sys
import unittest
import urllib.error
import urllib.request

from mock_sddc import CONTRACT, OPERATIONS, MockSddc
from vcf_commission import (
    HostCommissioner,
    HostSpec,
    PollTimeout,
    PrecheckFailed,
    SddcClient,
    VcfApiError,
)


ROOT = Path(__file__).parent
SOURCES = json.loads((ROOT / "docs" / "official_sources.json").read_text())
REQUIRED_OPERATION_IDS = {
    "validateHostCommissionSpec",
    "getHostCommissionValidationByID",
    "commissionHosts",
}


def required_host(**overrides):
    values = {
        "fqdn": "esx-01.example.test",
        "username": "root",
        "password": "do-not-print-this",
        "storage_type": "VSAN",
        "network_pool_id": "network-pool-001",
    }
    values.update(overrides)
    return HostSpec(**values)


def expected_required_wire():
    return [
        {
            "fqdn": "esx-01.example.test",
            "username": "root",
            "password": "do-not-print-this",
            "storageType": "VSAN",
            "networkPoolId": "network-pool-001",
        }
    ]


class ContractFixtureTests(unittest.TestCase):
    def test_contract_and_provenance_name_the_same_exact_operations(self):
        self.assertEqual(CONTRACT["openapi"], "3.0.1")
        self.assertEqual(CONTRACT["info"]["version"], "9.1.0.0")
        self.assertEqual(set(OPERATIONS), REQUIRED_OPERATION_IDS)
        self.assertEqual(
            {item["operationId"] for item in SOURCES["operations"]},
            REQUIRED_OPERATION_IDS,
        )
        for recorded in SOURCES["operations"]:
            extracted = OPERATIONS[recorded["operationId"]]
            self.assertEqual(extracted["method"], recorded["method"])
            self.assertEqual(extracted["path"], recorded["path"])
        self.assertEqual(
            SOURCES["repository_commit_sha"],
            "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
        )
        self.assertEqual(
            SOURCES["spec_path"],
            "specifications/sddc-manager/sddc-manager-openapi.json",
        )
        self.assertEqual(SOURCES["repository_license"], "Apache-2.0")

    def test_mock_serves_only_contract_named_operations(self):
        with MockSddc() as mock:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(mock.base_url + "/v1/hosts")
            self.assertEqual(caught.exception.code, 404)
            self.assertIsNone(mock.request_log[0]["operationId"])

    def test_package_imports_only_the_standard_library(self):
        package = ROOT / "vcf_commission"
        self.assertTrue(package.is_dir())
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".", 1)[0]]
                else:
                    roots = []
                for root in roots:
                    self.assertIn(root, sys.stdlib_module_names)


class WireAndWorkflowTests(unittest.TestCase):
    def make_workflow(self, base_url, sleeps, max_polls=4):
        client = SddcClient(base_url, "access-token-001")
        return HostCommissioner(
            client,
            sleep=sleeps.append,
            poll_interval=0.25,
            max_polls=max_polls,
        )

    def assert_common_headers(self, record, *, has_body):
        self.assertEqual(record["headers"].get("authorization"), "Bearer access-token-001")
        self.assertEqual(record["headers"].get("accept"), "application/json")
        if has_body:
            self.assertEqual(record["headers"].get("content-type"), "application/json")
        else:
            self.assertNotIn("content-type", record["headers"])

    def test_success_wire_shape_and_gate_order(self):
        sleeps = []
        with MockSddc(in_progress_polls=1) as mock:
            task = self.make_workflow(mock.base_url, sleeps).commission([required_host()])

            self.assertEqual(task["id"], "task-001")
            self.assertEqual(mock.commission_count, 1)
            self.assertEqual(sleeps, [0.25])
            self.assertEqual(
                [entry["operationId"] for entry in mock.request_log],
                [
                    "validateHostCommissionSpec",
                    "getHostCommissionValidationByID",
                    "getHostCommissionValidationByID",
                    "commissionHosts",
                ],
            )

            expected = expected_required_wire()
            expected_raw = json.dumps(expected, separators=(",", ":")).encode("utf-8")
            validation_post, poll_one, poll_two, mutation = mock.request_log
            for record in (validation_post, mutation):
                self.assertEqual(record["query"], "")
                self.assertEqual(record["json"], expected)
                self.assertEqual(record["raw_body"], expected_raw)
                self.assert_common_headers(record, has_body=True)
            for record in (poll_one, poll_two):
                self.assertEqual(record["path"], "/v1/hosts/validations/validation-001")
                self.assertEqual(record["query"], "")
                self.assertEqual(record["raw_body"], b"")
                self.assertIsNone(record["json"])
                self.assert_common_headers(record, has_body=False)

    def test_unset_optional_fields_are_omitted_not_empty(self):
        sleeps = []
        with MockSddc(in_progress_polls=0) as mock:
            self.make_workflow(mock.base_url, sleeps).commission([required_host()])

        body = mock.request_log[0]["json"][0]
        host_schema = CONTRACT["components"]["schemas"]["HostCommissionSpec"]
        optional = set(host_schema["properties"]) - set(
            host_schema["required"]
        )
        self.assertTrue(optional)
        self.assertTrue(optional.isdisjoint(body))
        self.assertNotIn(None, body.values())
        self.assertNotIn("", [body[key] for key in optional if key in body])

    def test_provided_optional_fields_use_exact_spec_names(self):
        sleeps = []
        host = required_host(
            vvol_storage_protocol_type="NFS",
            network_pool_name="rack-a-pool",
            ssh_thumbprint="ssh-rsa SHA256:abc",
            ssl_thumbprint="AA:BB:CC",
        )
        with MockSddc(in_progress_polls=0) as mock:
            self.make_workflow(mock.base_url, sleeps).commission([host])

        body = mock.request_log[0]["json"][0]
        self.assertEqual(body["vvolStorageProtocolType"], "NFS")
        self.assertEqual(body["networkPoolName"], "rack-a-pool")
        self.assertEqual(body["sshThumbprint"], "ssh-rsa SHA256:abc")
        self.assertEqual(body["sslThumbprint"], "AA:BB:CC")
        self.assertEqual(mock.request_log[-1]["json"], mock.request_log[0]["json"])

    def test_failed_precheck_never_calls_mutation(self):
        sleeps = []
        checks = [
            {
                "description": "Host DNS",
                "severity": "ERROR",
                "resultStatus": "FAILED",
            }
        ]
        with MockSddc(
            final_execution_status="COMPLETED",
            final_result_status="FAILED",
            in_progress_polls=0,
            validation_checks=checks,
        ) as mock:
            with self.assertRaises(PrecheckFailed) as caught:
                self.make_workflow(mock.base_url, sleeps).commission([required_host()])

            self.assertEqual(caught.exception.validation["resultStatus"], "FAILED")
            self.assertEqual(caught.exception.validation["validationChecks"], checks)
            self.assertEqual(mock.commission_count, 0)
            self.assertNotIn(
                "commissionHosts",
                [entry["operationId"] for entry in mock.request_log],
            )
            self.assertEqual(
                [entry["operationId"] for entry in mock.request_log],
                ["validateHostCommissionSpec", "getHostCommissionValidationByID"],
            )

    def test_warning_is_not_success_and_never_mutates(self):
        with MockSddc(
            final_execution_status="COMPLETED",
            final_result_status="WARNING",
            in_progress_polls=0,
        ) as mock:
            with self.assertRaises(PrecheckFailed):
                self.make_workflow(mock.base_url, []).commission([required_host()])
            self.assertEqual(mock.commission_count, 0)

    def test_poll_budget_exhaustion_never_mutates(self):
        sleeps = []
        with MockSddc(in_progress_polls=99) as mock:
            with self.assertRaises(PollTimeout) as caught:
                self.make_workflow(mock.base_url, sleeps, max_polls=2).commission(
                    [required_host()]
                )
            self.assertEqual(caught.exception.validation_id, "validation-001")
            self.assertEqual(caught.exception.max_polls, 2)
            self.assertEqual(sleeps, [0.25])
            self.assertEqual(mock.commission_count, 0)
            self.assertEqual(
                [entry["operationId"] for entry in mock.request_log],
                [
                    "validateHostCommissionSpec",
                    "getHostCommissionValidationByID",
                    "getHostCommissionValidationByID",
                ],
            )

    def test_empty_host_list_is_rejected_before_http(self):
        with MockSddc() as mock:
            with self.assertRaises(ValueError):
                self.make_workflow(mock.base_url, []).commission([])
            self.assertEqual(mock.request_log, [])

    def test_openapi_error_fields_are_preserved_without_secrets(self):
        with MockSddc(error_operation_id="validateHostCommissionSpec") as mock:
            with self.assertRaises(VcfApiError) as caught:
                self.make_workflow(mock.base_url, []).commission([required_host()])
            error = caught.exception
            self.assertEqual(error.status_code, 400)
            self.assertEqual(error.error_code, "VCF_HOST_VALIDATION_REJECTED")
            self.assertEqual(error.message, "validation request rejected")
            self.assertEqual(error.reference_token, "reference-001")
            rendered = str(error) + repr(error)
            self.assertNotIn("access-token-001", rendered)
            self.assertNotIn("do-not-print-this", rendered)
            self.assertEqual(mock.commission_count, 0)

    def test_contract_violating_success_status_is_rejected_before_mutation(self):
        with MockSddc(
            status_overrides={"validateHostCommissionSpec": 200}
        ) as mock:
            with self.assertRaises(VcfApiError) as caught:
                self.make_workflow(mock.base_url, []).commission([required_host()])
            self.assertEqual(caught.exception.status_code, 200)
            self.assertEqual(mock.commission_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
