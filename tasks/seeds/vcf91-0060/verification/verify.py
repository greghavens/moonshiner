"""Protected verification for the VCF 9.1 NSX Policy token-resume client."""

from __future__ import annotations

import ast
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mock_nsx_policy import CONTRACT, MockNsxPolicy

EXPIRED_TOKEN = "fixture-access-token-before-expiry"
FRESH_TOKEN = "fixture-access-token-after-refresh"
DOMAIN_ID = "tenant blue"
GROUP_ID = "payments/egress"


class TokenProvider:
    def __init__(self, *tokens):
        self._tokens = list(tokens)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self._tokens:
            raise AssertionError("token provider called more often than expected")
        return self._tokens.pop(0)


def make_model():
    from vcf_nsx_policy import IPAddressGroup

    return IPAddressGroup(
        display_name="Payments egress",
        ip_addresses=["192.0.2.8", "198.51.100.0/28"],
    )


def realized_group():
    value = make_model().to_wire()
    value.update(
        {
            "id": GROUP_ID,
            "path": f"/infra/domains/{DOMAIN_ID}/groups/{GROUP_ID}",
            "parent_path": f"/infra/domains/{DOMAIN_ID}",
            "_last_modified_time": 1785337200000,
        }
    )
    return value


def unauthorized():
    return {
        "error_code": 403,
        "error_message": "The access token has expired",
        "module_name": "common-services",
        "details": "Acquire a fresh access token and retry the request",
        "related_errors": [],
    }


class ContractAndMockTests(unittest.TestCase):
    def test_contract_is_pinned_to_spec_and_exact_operation_ids(self):
        root = Path(__file__).resolve().parent
        sources = json.loads(
            (root / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        blob = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
        spec_path = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
        expected = {"PatchGroupForDomain", "ReadGroupForDomain"}

        self.assertEqual(CONTRACT["info_version"], "9.1.0.0")
        self.assertEqual(CONTRACT["basePath"], "/policy/api/v1")
        self.assertEqual(CONTRACT["source"]["repository_commit_sha"], sha)
        self.assertEqual(CONTRACT["source"]["spec_blob_sha"], blob)
        self.assertEqual(CONTRACT["source"]["spec_path"], spec_path)
        self.assertEqual(set(CONTRACT["operations"]), expected)
        self.assertEqual(sources["repository_commit_sha"], sha)
        self.assertEqual(sources["spec_blob_sha"], blob)
        self.assertEqual(sources["spec_path"], spec_path)
        self.assertEqual(
            {item["operationId"] for item in sources["operations"]}, expected
        )
        for item in sources["operations"]:
            self.assertEqual(item["repository_commit_sha"], sha)
            self.assertEqual(item["spec_path"], spec_path)
        self.assertNotIn("developer.broadcom.com", json.dumps(sources))

    def test_mock_only_routes_contract_operations(self):
        with MockNsxPolicy() as server:
            self.assertEqual(
                server.operation_ids, frozenset(CONTRACT["operations"])
            )
            request = urllib.request.Request(
                server.base_url
                + "/policy/api/v1/infra/domains/default/groups/g/uncontracted",
                data=b"{}",
                method="PATCH",
                headers={
                    "Authorization": f"Bearer {EXPIRED_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 404)
            self.assertIsNone(server.request_log[0]["operationId"])


class WireAndRefreshTests(unittest.TestCase):
    def _client(self, server, provider):
        from vcf_nsx_policy import NsxPolicyClient

        return NsxPolicyClient(server.base_url, provider, timeout=2)

    def test_exact_wire_shape_refreshes_get_without_replaying_patch(self):
        expected_group = realized_group()
        with MockNsxPolicy() as server:
            server.script("PatchGroupForDomain", [(200, {})])
            server.script(
                "ReadGroupForDomain",
                [(401, unauthorized()), (200, expected_group)],
            )
            provider = TokenProvider(EXPIRED_TOKEN, FRESH_TOKEN)
            result = self._client(server, provider).ensure_ip_group(
                DOMAIN_ID, GROUP_ID, make_model()
            )

        self.assertEqual(result.group, expected_group)
        self.assertEqual(result.group["_last_modified_time"], 1785337200000)
        self.assertEqual(result.token_refreshes, 1)
        self.assertEqual(
            list(result.completed_operations),
            ["PatchGroupForDomain", "ReadGroupForDomain"],
        )
        self.assertEqual(provider.calls, 2)

        expected_body = {
            "resource_type": "Group",
            "display_name": "Payments egress",
            "expression": [
                {
                    "resource_type": "IPAddressExpression",
                    "ip_addresses": ["192.0.2.8", "198.51.100.0/28"],
                }
            ],
        }
        expected_raw = (
            b'{"resource_type":"Group","display_name":"Payments egress",'
            b'"expression":[{"resource_type":"IPAddressExpression",'
            b'"ip_addresses":["192.0.2.8","198.51.100.0/28"]}]}'
        )
        expected_path = (
            "/policy/api/v1/infra/domains/tenant%20blue/"
            "groups/payments%2Fegress"
        )
        self.assertEqual(
            [
                (
                    item["operationId"],
                    item["method"],
                    item["path"],
                    item["query"],
                    item["json"],
                )
                for item in server.request_log
            ],
            [
                (
                    "PatchGroupForDomain",
                    "PATCH",
                    expected_path,
                    "",
                    expected_body,
                ),
                ("ReadGroupForDomain", "GET", expected_path, "", None),
                ("ReadGroupForDomain", "GET", expected_path, "", None),
            ],
        )
        self.assertEqual(server.request_log[0]["body"], expected_raw)
        self.assertEqual(
            server.request_log[0]["path_parameters"],
            {"domain-id": DOMAIN_ID, "group-id": GROUP_ID},
        )
        self.assertEqual(
            [
                request["headers"]["authorization"]
                for request in server.request_log
            ],
            [
                f"Bearer {EXPIRED_TOKEN}",
                f"Bearer {EXPIRED_TOKEN}",
                f"Bearer {FRESH_TOKEN}",
            ],
        )
        for request in server.request_log:
            self.assertEqual(request["headers"]["accept"], "application/json")
        self.assertEqual(
            server.request_log[0]["headers"]["content-type"],
            "application/json",
        )
        for request in server.request_log[1:]:
            self.assertNotIn("content-type", request["headers"])
            self.assertEqual(request["body"], b"")

        patch_json = server.request_log[0]["json"]
        self.assertEqual(
            set(patch_json), {"resource_type", "display_name", "expression"}
        )
        self.assertNotIn("description", patch_json)
        self.assertNotIn("tags", patch_json)
        self.assertNotIn("group_type", patch_json)
        self.assertNotIn("id", patch_json)
        self.assertEqual(
            set(patch_json["expression"][0]),
            {"resource_type", "ip_addresses"},
        )

    def test_unset_optional_description_is_omitted_not_sent_empty(self):
        from vcf_nsx_policy import IPAddressGroup

        self.assertEqual(
            IPAddressGroup("DB allowlist", ["203.0.113.4"]).to_wire(),
            {
                "resource_type": "Group",
                "display_name": "DB allowlist",
                "expression": [
                    {
                        "resource_type": "IPAddressExpression",
                        "ip_addresses": ["203.0.113.4"],
                    }
                ],
            },
        )
        self.assertEqual(
            IPAddressGroup(
                "DB allowlist",
                ["203.0.113.4"],
                description="Database egress destinations",
            ).to_wire()["description"],
            "Database egress destinations",
        )

    def test_second_401_is_terminal_and_does_not_replay_completed_patch(self):
        from vcf_nsx_policy import NsxApiError

        with MockNsxPolicy() as server:
            server.script("PatchGroupForDomain", [(200, {})])
            server.script(
                "ReadGroupForDomain",
                [(401, unauthorized()), (401, unauthorized())],
            )
            provider = TokenProvider(EXPIRED_TOKEN, FRESH_TOKEN)
            with self.assertRaises(NsxApiError) as caught:
                self._client(server, provider).ensure_ip_group(
                    "default", "restricted", make_model()
                )

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.error_code, 403)
        self.assertEqual(caught.exception.error_message, "The access token has expired")
        self.assertEqual(caught.exception.module_name, "common-services")
        self.assertEqual(caught.exception.envelope["related_errors"], [])
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [item["operationId"] for item in server.request_log],
            [
                "PatchGroupForDomain",
                "ReadGroupForDomain",
                "ReadGroupForDomain",
            ],
        )

    def test_non_401_failure_does_not_refresh_or_continue(self):
        from vcf_nsx_policy import NsxApiError

        failure = {
            "error_code": 500045,
            "error_message": "Policy realization is unavailable",
            "module_name": "policy",
            "details": "Retry after the service recovers",
        }
        with MockNsxPolicy() as server:
            server.script("PatchGroupForDomain", [(503, failure)])
            provider = TokenProvider(EXPIRED_TOKEN)
            with self.assertRaises(NsxApiError) as caught:
                self._client(server, provider).ensure_ip_group(
                    "default", "restricted", make_model()
                )

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.error_code, 500045)
        self.assertEqual(caught.exception.details, failure["details"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            [item["operationId"] for item in server.request_log],
            ["PatchGroupForDomain"],
        )

    def test_malformed_read_success_is_protocol_error(self):
        from vcf_nsx_policy import ProtocolError

        with MockNsxPolicy() as server:
            server.script("PatchGroupForDomain", [(200, {})])
            server.script("ReadGroupForDomain", [(200, ["not", "a", "group"])])
            provider = TokenProvider(EXPIRED_TOKEN)
            with self.assertRaises(ProtocolError):
                self._client(server, provider).ensure_ip_group(
                    "default", "restricted", make_model()
                )
        self.assertEqual(provider.calls, 1)

    def test_local_validation_happens_before_token_or_http(self):
        from vcf_nsx_policy import IPAddressGroup

        invalid_models = [
            lambda: IPAddressGroup("", ["192.0.2.1"]),
            lambda: IPAddressGroup("group", []),
            lambda: IPAddressGroup("group", [""]),
            lambda: IPAddressGroup("group", [42]),
            lambda: IPAddressGroup("group", ["192.0.2.1"], description=""),
        ]
        with MockNsxPolicy() as server:
            provider = TokenProvider(EXPIRED_TOKEN)
            client = self._client(server, provider)
            for build_model in invalid_models:
                with self.assertRaises((TypeError, ValueError)):
                    client.patch_group("default", "group", build_model())
            with self.assertRaises(ValueError):
                client.read_group("", "group")
            with self.assertRaises(ValueError):
                client.read_group("default", "")

        self.assertEqual(provider.calls, 0)
        self.assertEqual(server.request_log, [])

    def test_package_imports_only_python_standard_library(self):
        package = Path(__file__).resolve().parent / "vcf_nsx_policy"
        self.assertTrue(package.is_dir())
        allowed_local = {"vcf_nsx_policy"}
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = {(node.module or "").split(".", 1)[0]}
                else:
                    continue
                for root in roots - allowed_local:
                    self.assertIn(
                        root,
                        sys.stdlib_module_names,
                        f"{source.name} imports non-stdlib module {root}",
                    )


if __name__ == "__main__":
    unittest.main()
