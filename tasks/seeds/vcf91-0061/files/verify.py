"""Protected verification for the VCF 9.1 NSX Policy group inventory."""

from __future__ import annotations

import ast
import base64
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mock_nsx_policy import CONTRACT, MockNsxPolicy

OPERATION_ID = "ListGroupForDomain"
DOMAIN_ID = "Finance / Edge"
ENCODED_DOMAIN = "Finance%20%2F%20Edge"
OPAQUE_CURSOR = "page/+/=opaque"
ENCODED_CURSOR = "page%2F%2B%2F%3Dopaque"
USERNAME = "inventory-user"
PASSWORD = "inventory:password"


def group_documents() -> dict[str, dict[str, object]]:
    return {
        "alpha": {
            "resource_type": "Group",
            "id": "alpha",
            "display_name": "App",
            "path": "/infra/domains/finance/groups/alpha",
            "expression": [
                {
                    "resource_type": "IPAddressExpression",
                    "ip_addresses": ["192.0.2.10"],
                }
            ],
        },
        "zeta": {
            "display_name": "App",
            "id": "zeta",
            "resource_type": "Group",
            "path": "/infra/domains/finance/groups/zeta",
            "description": "Primary app group",
        },
        "beta": {
            "path": "/infra/domains/finance/groups/beta",
            "resource_type": "Group",
            "display_name": "app",
            "id": "beta",
            "description": "München application group",
        },
        "omega": {
            "id": "omega",
            "path": "/infra/domains/finance/groups/omega",
            "display_name": "db",
            "resource_type": "Group",
            "group_type": ["IPAddress"],
        },
    }


def pages(
    first_ids: tuple[str, str] = ("omega", "zeta"),
    second_ids: tuple[str, str] = ("beta", "alpha"),
) -> list[tuple[int, object]]:
    documents = group_documents()
    return [
        (
            200,
            {
                "result_count": 4,
                "results": [documents[item] for item in first_ids],
                "cursor": OPAQUE_CURSOR,
            },
        ),
        (200, {"results": [documents[item] for item in second_ids]}),
    ]


def make_client(server: MockNsxPolicy):
    from vcf_nsx_inventory import NsxPolicyClient

    return NsxPolicyClient(
        server.base_url,
        USERNAME,
        PASSWORD,
        timeout=2,
    )


class ContractAndMockTests(unittest.TestCase):
    def test_contract_is_pinned_to_yaml_and_exact_operation_id(self):
        root = Path(__file__).resolve().parent
        sources = json.loads(
            (root / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        blob = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
        spec_path = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"

        self.assertEqual(CONTRACT["info_version"], "9.1.0.0")
        self.assertEqual(CONTRACT["swagger"], "2.0")
        self.assertEqual(CONTRACT["basePath"], "/policy/api/v1")
        self.assertEqual(CONTRACT["security"]["type"], "basic")
        self.assertEqual(CONTRACT["source"]["repository_commit_sha"], sha)
        self.assertEqual(CONTRACT["source"]["spec_blob_sha"], blob)
        self.assertEqual(CONTRACT["source"]["spec_path"], spec_path)
        self.assertEqual(set(CONTRACT["operations"]), {OPERATION_ID})

        operation = CONTRACT["operations"][OPERATION_ID]
        self.assertEqual(operation["operationId"], OPERATION_ID)
        self.assertEqual(operation["method"], "GET")
        self.assertEqual(
            operation["path"], "/infra/domains/{domain-id}/groups"
        )
        self.assertEqual(
            [parameter["name"] for parameter in operation["parameters"]],
            [
                "domain-id",
                "cursor",
                "include_mark_for_delete_objects",
                "included_fields",
                "member_types",
                "page_size",
                "sort_ascending",
                "sort_by",
            ],
        )
        self.assertEqual(
            operation["responses"]["200"]["schema_ref"],
            "#/definitions/GroupListResult",
        )
        self.assertEqual(CONTRACT["definitions"]["GroupListResult"]["required"], ["results"])

        self.assertEqual(sources["repository_commit_sha"], sha)
        self.assertEqual(sources["spec_blob_sha"], blob)
        self.assertEqual(sources["spec_path"], spec_path)
        self.assertEqual(
            [item["operationId"] for item in sources["operations"]],
            [OPERATION_ID],
        )
        for item in sources["operations"]:
            self.assertEqual(item["repository_commit_sha"], sha)
            self.assertEqual(item["spec_path"], spec_path)
        self.assertNotIn("developer.broadcom.com", json.dumps(sources))

    def test_mock_routes_only_operations_named_by_contract(self):
        authorization = "Basic " + base64.b64encode(b"user:password").decode("ascii")
        with MockNsxPolicy() as server:
            self.assertEqual(server.operation_ids, frozenset({OPERATION_ID}))
            request = urllib.request.Request(
                server.base_url
                + "/policy/api/v1/infra/domains/default/groups/outside-contract",
                method="GET",
                headers={"Authorization": authorization},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 404)
            self.assertIsNone(server.request_log[0]["operationId"])


class CollectionWireTests(unittest.TestCase):
    def test_all_pages_are_sorted_and_unset_options_are_absent_on_wire(self):
        with MockNsxPolicy() as server:
            server.script(OPERATION_ID, pages())
            result = make_client(server).list_groups(DOMAIN_ID)

        self.assertEqual(
            [item["id"] for item in result],
            ["alpha", "zeta", "beta", "omega"],
        )
        self.assertEqual(
            [request["target"] for request in server.request_log],
            [
                (
                    "/policy/api/v1/infra/domains/"
                    f"{ENCODED_DOMAIN}/groups"
                ),
                (
                    "/policy/api/v1/infra/domains/"
                    f"{ENCODED_DOMAIN}/groups?cursor={ENCODED_CURSOR}"
                ),
            ],
        )
        self.assertEqual(
            [request["query"] for request in server.request_log],
            ["", f"cursor={ENCODED_CURSOR}"],
        )
        expected_authorization = "Basic " + base64.b64encode(
            f"{USERNAME}:{PASSWORD}".encode("utf-8")
        ).decode("ascii")
        for request in server.request_log:
            self.assertEqual(request["operationId"], OPERATION_ID)
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["path_parameters"], {"domain-id": DOMAIN_ID})
            self.assertEqual(
                request["headers"]["authorization"], expected_authorization
            )
            self.assertEqual(request["headers"]["accept"], "application/json")
            self.assertNotIn("content-type", request["headers"])
            self.assertNotIn("content-length", request["headers"])
            self.assertEqual(request["body"], b"")

    def test_explicit_options_use_spec_order_and_repeat_on_every_page(self):
        with MockNsxPolicy() as server:
            server.script(OPERATION_ID, pages())
            make_client(server).list_groups(
                DOMAIN_ID,
                include_mark_for_delete_objects=False,
                included_fields="id,display_name,path",
                member_types="IPAddress,VirtualMachine",
                page_size=2,
                sort_ascending=False,
                sort_by="display_name",
            )

        options = (
            "include_mark_for_delete_objects=false"
            "&included_fields=id%2Cdisplay_name%2Cpath"
            "&member_types=IPAddress%2CVirtualMachine"
            "&page_size=2"
            "&sort_ascending=false"
            "&sort_by=display_name"
        )
        prefix = (
            f"/policy/api/v1/infra/domains/{ENCODED_DOMAIN}/groups?"
        )
        self.assertEqual(
            [request["target"] for request in server.request_log],
            [
                prefix + options,
                prefix + f"cursor={ENCODED_CURSOR}&" + options,
            ],
        )

    def test_export_is_byte_stable_and_preserves_complete_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            with MockNsxPolicy() as server:
                server.script(OPERATION_ID, pages())
                make_client(server).export_groups(DOMAIN_ID, first)
                server.script(
                    OPERATION_ID,
                    pages(
                        first_ids=("alpha", "beta"),
                        second_ids=("zeta", "omega"),
                    ),
                )
                make_client(server).export_groups(DOMAIN_ID, second)

            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()

        self.assertEqual(first_bytes, second_bytes)
        self.assertFalse(first_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(first_bytes.endswith(b"\n"))
        self.assertNotIn(b"\r\n", first_bytes)
        self.assertNotIn(b'": ', first_bytes)
        document = json.loads(first_bytes)
        self.assertEqual(list(document), ["operation_id", "results"])
        self.assertEqual(document["operation_id"], OPERATION_ID)
        self.assertEqual(
            [item["id"] for item in document["results"]],
            ["alpha", "zeta", "beta", "omega"],
        )
        for item in document["results"]:
            self.assertEqual(list(item), sorted(item))
        self.assertEqual(
            document["results"][0]["expression"][0]["ip_addresses"],
            ["192.0.2.10"],
        )
        self.assertEqual(
            document["results"][2]["description"],
            "München application group",
        )


class FailureAndValidationTests(unittest.TestCase):
    def test_api_error_exposes_complete_spec_envelope(self):
        from vcf_nsx_inventory import NsxApiError

        envelope = {
            "error_code": 500045,
            "error_message": "Group inventory is unavailable",
            "module_name": "policy",
            "details": "The inventory index is rebuilding",
            "error_data": {"retryable": True},
            "related_errors": [],
        }
        with MockNsxPolicy() as server:
            server.script(OPERATION_ID, [(503, envelope)])
            with self.assertRaises(NsxApiError) as caught:
                make_client(server).list_groups("default")

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.error_code, 500045)
        self.assertEqual(
            caught.exception.error_message, "Group inventory is unavailable"
        )
        self.assertEqual(caught.exception.module_name, "policy")
        self.assertEqual(
            caught.exception.details, "The inventory index is rebuilding"
        )
        self.assertEqual(caught.exception.envelope, envelope)
        self.assertEqual(len(server.request_log), 1)

    def test_malformed_page_and_repeated_cursor_are_protocol_errors(self):
        from vcf_nsx_inventory import ProtocolError

        with MockNsxPolicy() as server:
            server.script(OPERATION_ID, [(200, {"results": {}})])
            with self.assertRaises(ProtocolError):
                make_client(server).list_groups("default")
        self.assertEqual(len(server.request_log), 1)

        with MockNsxPolicy() as server:
            server.script(
                OPERATION_ID,
                [
                    (200, {"results": [], "cursor": "same"}),
                    (200, {"results": [], "cursor": "same"}),
                ],
            )
            with self.assertRaises(ProtocolError):
                make_client(server).list_groups("default")
        self.assertEqual(len(server.request_log), 2)

    def test_sort_fields_and_cursor_must_have_contract_types(self):
        from vcf_nsx_inventory import ProtocolError

        bad_pages = [
            {"results": [{"id": "x", "display_name": ""}]},
            {"results": [{"id": 7, "display_name": "x"}]},
            {"results": ["not-an-object"]},
            {"results": [], "cursor": 9},
        ]
        for page in bad_pages:
            with self.subTest(page=page), MockNsxPolicy() as server:
                server.script(OPERATION_ID, [(200, page)])
                with self.assertRaises(ProtocolError):
                    make_client(server).list_groups("default")

    def test_local_validation_precedes_http(self):
        from vcf_nsx_inventory import NsxPolicyClient

        with MockNsxPolicy() as server:
            client = make_client(server)
            invalid_calls = [
                lambda: client.list_groups(""),
                lambda: client.list_groups("default", page_size=True),
                lambda: client.list_groups("default", page_size=-1),
                lambda: client.list_groups("default", page_size=1001),
                lambda: client.list_groups(
                    "default", include_mark_for_delete_objects="false"
                ),
                lambda: client.list_groups("default", sort_ascending=0),
                lambda: client.list_groups("default", included_fields=""),
                lambda: client.list_groups("default", member_types="  "),
                lambda: client.list_groups("default", sort_by=""),
            ]
            for call in invalid_calls:
                with self.assertRaises((TypeError, ValueError)):
                    call()
            self.assertEqual(server.request_log, [])

            invalid_clients = [
                lambda: NsxPolicyClient(server.base_url + "/manager", "u", "p"),
                lambda: NsxPolicyClient(server.base_url, "", "p"),
                lambda: NsxPolicyClient(server.base_url, "u", ""),
                lambda: NsxPolicyClient(server.base_url, "u", "p", timeout=0),
            ]
            for constructor in invalid_clients:
                with self.assertRaises((TypeError, ValueError)):
                    constructor()
            self.assertEqual(server.request_log, [])

    def test_public_surface_and_standard_library_only(self):
        import vcf_nsx_inventory

        self.assertEqual(
            set(vcf_nsx_inventory.__all__),
            {"NsxApiError", "NsxPolicyClient", "ProtocolError"},
        )
        package = Path(__file__).resolve().parent / "vcf_nsx_inventory"
        self.assertTrue(package.is_dir())
        self.assertGreaterEqual(len(list(package.glob("*.py"))), 1)
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = {(node.module or "").split(".", 1)[0]}
                else:
                    continue
                for root in roots - {"vcf_nsx_inventory"}:
                    self.assertIn(
                        root,
                        sys.stdlib_module_names,
                        f"{source.name} imports non-stdlib module {root}",
                    )


if __name__ == "__main__":
    unittest.main()
