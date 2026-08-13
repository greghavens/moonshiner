"""Wire-shape verification for the vcfops_customgroups package.

Everything here runs against the loopback mock in mock/vcfops_mock.py. No live
VMware endpoint is contacted. The assertions are on the bytes that left the
client, read back out of the mock's request log.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mock"))

from vcfops_mock import MockServer  # noqa: E402

CONTRACT = json.loads((REPO_ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))

ADAPTER_KIND = "VMWARE"
RESOURCE_KIND = "VirtualMachine"
GROUP_NAME = "Payments Tier VMs"

# Deliberately unsorted so the "ascending lexicographic order" convention bites.
RESOURCE_A = "b1d0f21e-1f7a-4f52-9c6f-0b3d4a71c001"
RESOURCE_B = "1c9a77aa-2e55-4d61-8a3b-7f5c1d20e002"
RESOURCE_C = "e37f5c02-9a41-4bd3-b0d2-5c8e2f31a003"


class WireContractTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mock = MockServer(
            contract_path=REPO_ROOT / "docs" / "contract.json",
            request_log=Path(self._tmp.name) / "requests.jsonl",
        ).start()
        self.addCleanup(self.mock.stop)

        import vcfops_customgroups

        self.pkg = vcfops_customgroups
        self.client = vcfops_customgroups.OperationsClient(self.mock.base_url)
        self.token = self.client.acquire_token("svc-automation", "s3cret")

    # -- helpers ----------------------------------------------------------
    def log(self) -> list[dict]:
        return self.mock.read_log()

    def requests_for(self, operation_id: str) -> list[dict]:
        return [r for r in self.log() if r["operation_id"] == operation_id]

    def only(self, operation_id: str) -> dict:
        matches = self.requests_for(operation_id)
        self.assertEqual(
            1, len(matches), f"expected exactly one {operation_id} request, got {len(matches)}"
        )
        return matches[0]

    def header(self, record: dict, name: str) -> str | None:
        for key, value in record["headers"].items():
            if key.lower() == name.lower():
                return value
        return None

    def ensure(self, **overrides):
        params = {
            "name": GROUP_NAME,
            "adapter_kind_key": ADAPTER_KIND,
            "resource_kind_key": RESOURCE_KIND,
            "included_resources": [RESOURCE_A, RESOURCE_B],
        }
        params.update(overrides)
        return self.pkg.ensure_custom_group(self.client, **params)

    def assertSucceeded(self, records: list[dict]) -> None:
        for record in records:
            self.assertIn(
                record["status"],
                (200, 201),
                f"{record['operation_id']} returned {record['status']}: {record['body_raw']}",
            )

    # -- tests ------------------------------------------------------------
    def test_01_public_api_surface(self) -> None:
        for name in ("OperationsClient", "ensure_custom_group", "OperationsApiError"):
            self.assertTrue(hasattr(self.pkg, name), f"vcfops_customgroups.{name} is missing")
        for method in ("acquire_token", "get_custom_groups", "create_custom_group", "modify_custom_group"):
            self.assertTrue(
                callable(getattr(self.client, method, None)),
                f"OperationsClient.{method} is missing",
            )

        fresh = self.pkg.OperationsClient(self.mock.base_url)
        self.assertIsNone(fresh.token, "a new client must not invent an authentication token")
        self.assertEqual(30.0, fresh.timeout)

        client_params = inspect.signature(self.pkg.OperationsClient).parameters
        self.assertEqual(["base_url", "timeout", "contract_path"], list(client_params))
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, client_params["timeout"].kind)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, client_params["contract_path"].kind)
        self.assertEqual(30.0, client_params["timeout"].default)
        self.assertIsNone(client_params["contract_path"].default)

        ensure_params = inspect.signature(self.pkg.ensure_custom_group).parameters
        self.assertEqual(
            [
                "client",
                "name",
                "adapter_kind_key",
                "resource_kind_key",
                "included_resources",
                "policy",
                "auto_resolve_membership",
            ],
            list(ensure_params),
        )
        for name in list(ensure_params)[1:]:
            self.assertEqual(inspect.Parameter.KEYWORD_ONLY, ensure_params[name].kind)

        method_shapes = {
            "acquire_token": ["self", "username", "password", "auth_source"],
            "get_custom_groups": ["self", "group_ids", "include_policy"],
            "create_custom_group": ["self", "group"],
            "modify_custom_group": ["self", "group"],
        }
        for method_name, expected in method_shapes.items():
            params = inspect.signature(
                getattr(self.pkg.OperationsClient, method_name)
            ).parameters
            self.assertEqual(expected, list(params), f"wrong signature for {method_name}")
        self.assertIsNone(
            inspect.signature(self.pkg.OperationsClient.acquire_token)
            .parameters["auth_source"]
            .default
        )

    def test_02_acquire_token_wire_shape(self) -> None:
        record = self.only("acquireToken")
        self.assertEqual("POST", record["method"])
        self.assertEqual("/suite-api/api/auth/token/acquire", record["path"])
        self.assertEqual("", record["raw_query"])
        self.assertEqual(200, record["status"])

        self.assertIsNone(
            self.header(record, "Authorization"),
            "acquireToken is declared with empty security; it must not send Authorization",
        )
        self.assertEqual(
            "application/json",
            (self.header(record, "Content-Type") or "").split(";")[0].strip(),
        )
        self.assertIn("application/json", self.header(record, "Accept") or "")

        # authSource was not supplied: it must be absent, not "" and not null.
        self.assertEqual({"username": "svc-automation", "password": "s3cret"}, record["body"])
        self.assertEqual(self.token, record["issued_token"])

    def test_03_auth_source_is_sent_when_supplied(self) -> None:
        self.mock.truncate_log()
        client = self.pkg.OperationsClient(self.mock.base_url)
        client.acquire_token("svc-automation", "s3cret", auth_source="LOCAL")
        record = self.only("acquireToken")
        self.assertEqual(
            {"username": "svc-automation", "password": "s3cret", "authSource": "LOCAL"},
            record["body"],
        )

    def test_04_create_request_omits_every_unset_optional_field(self) -> None:
        self.mock.truncate_log()
        result = self.ensure()
        self.assertEqual("created", result.action)
        self.assertSucceeded(self.log())

        lookup = self.only("getCustomGroups")
        self.assertEqual("GET", lookup["method"])
        self.assertEqual("/suite-api/api/resources/groups", lookup["path"])
        self.assertEqual(
            "",
            lookup["raw_query"],
            "neither groupId nor includePolicy was set, so the query string must be empty "
            "(not 'includePolicy=false', not 'groupId=')",
        )
        self.assertEqual(f"OpsToken {self.token}", self.header(lookup, "Authorization"))
        self.assertIn("application/json", self.header(lookup, "Accept") or "")

        create = self.only("createCustomGroup")
        self.assertEqual("POST", create["method"])
        self.assertEqual("/suite-api/api/resources/groups", create["path"])
        self.assertEqual("", create["raw_query"])
        self.assertEqual(201, create["status"])
        self.assertEqual(f"OpsToken {self.token}", self.header(create, "Authorization"))

        # Exact body. id/links/policy/autoResolveMembership are unset optionals and
        # must be absent; so are excludedResources/rules/custom-group-properties.
        # includedResources is serialized in ascending lexicographic order.
        self.assertEqual(
            {
                "resourceKey": {
                    "adapterKindKey": ADAPTER_KIND,
                    "name": GROUP_NAME,
                    "resourceKindKey": RESOURCE_KIND,
                },
                "membershipDefinition": {
                    "includedResources": sorted([RESOURCE_A, RESOURCE_B]),
                },
            },
            create["body"],
        )

        # The lookup must come before the mutating call.
        order = [r["operation_id"] for r in self.log()]
        self.assertLess(order.index("getCustomGroups"), order.index("createCustomGroup"))

        self.assertTrue(result.group_id)
        self.assertEqual(result.group_id, result.group["id"])

    def test_05_optional_fields_are_sent_when_the_caller_sets_them(self) -> None:
        policy_id = "9f3c1e77-45aa-4a0e-8f1e-7c2b6d9e4f10"
        self.mock.truncate_log()
        self.ensure(policy=policy_id, auto_resolve_membership=False)
        create = self.only("createCustomGroup")
        self.assertEqual(policy_id, create["body"].get("policy"))
        self.assertIn(
            "autoResolveMembership",
            create["body"],
            "autoResolveMembership=False is a value the caller chose; it must be sent as false, "
            "not treated as unset",
        )
        self.assertIs(False, create["body"]["autoResolveMembership"])

    def test_06_repeating_the_call_does_not_duplicate_the_effect(self) -> None:
        first = self.ensure()
        self.mock.truncate_log()
        second = self.ensure()
        third = self.ensure()

        self.assertEqual(first.group_id, second.group_id)
        self.assertEqual(first.group_id, third.group_id)
        self.assertEqual("unchanged", second.action)
        self.assertEqual("unchanged", third.action)

        replay = self.log()
        self.assertSucceeded(replay)
        self.assertEqual(
            [],
            [r for r in replay if r["operation_id"] in ("createCustomGroup", "modifyCustomGroup")],
            "re-running an already-satisfied request must issue no mutating call at all",
        )

        groups = self.client.get_custom_groups()
        matching = [
            g
            for g in groups
            if g["resourceKey"]["name"] == GROUP_NAME
            and g["resourceKey"]["adapterKindKey"] == ADAPTER_KIND
            and g["resourceKey"]["resourceKindKey"] == RESOURCE_KIND
        ]
        self.assertEqual(1, len(matching), "the retry duplicated the custom group")

    def test_07_drift_is_reconciled_with_modify_carrying_the_existing_id(self) -> None:
        created = self.ensure()
        self.mock.truncate_log()
        updated = self.ensure(included_resources=[RESOURCE_C, RESOURCE_A, RESOURCE_B])

        self.assertEqual(created.group_id, updated.group_id)
        self.assertEqual("updated", updated.action)
        self.assertEqual([], self.requests_for("createCustomGroup"))

        modify = self.only("modifyCustomGroup")
        self.assertEqual("PUT", modify["method"])
        self.assertEqual("/suite-api/api/resources/groups", modify["path"])
        self.assertEqual(200, modify["status"])
        self.assertEqual(
            {
                "id": created.group_id,
                "resourceKey": {
                    "adapterKindKey": ADAPTER_KIND,
                    "name": GROUP_NAME,
                    "resourceKindKey": RESOURCE_KIND,
                },
                "membershipDefinition": {
                    "includedResources": sorted([RESOURCE_A, RESOURCE_B, RESOURCE_C]),
                },
            },
            modify["body"],
            "modifyCustomGroup carries the server-assigned id and still omits every unset optional",
        )

        # And a further replay of the new desired state is again a no-op.
        self.mock.truncate_log()
        again = self.ensure(included_resources=[RESOURCE_A, RESOURCE_B, RESOURCE_C])
        self.assertEqual("unchanged", again.action)
        self.assertEqual([], self.requests_for("modifyCustomGroup"))

    def test_08_query_parameters_follow_the_contract(self) -> None:
        self.mock.truncate_log()
        self.client.get_custom_groups()
        self.assertEqual("", self.only("getCustomGroups")["raw_query"])

        self.mock.truncate_log()
        self.client.get_custom_groups(include_policy=False)
        self.assertEqual(
            "includePolicy=false",
            self.only("getCustomGroups")["raw_query"],
            "an explicitly requested false must be sent; only an unset parameter is omitted",
        )

        self.mock.truncate_log()
        self.client.get_custom_groups(group_ids=[RESOURCE_A, RESOURCE_B], include_policy=True)
        self.assertEqual(
            f"groupId={RESOURCE_A}&groupId={RESOURCE_B}&includePolicy=true",
            self.only("getCustomGroups")["raw_query"],
            "style=form/explode=true repeats groupId; parameters keep their declared order",
        )

    def test_09_create_conflict_surfaces_as_an_api_error(self) -> None:
        result = self.ensure()
        duplicate = {
            "resourceKey": {
                "adapterKindKey": ADAPTER_KIND,
                "name": GROUP_NAME,
                "resourceKindKey": RESOURCE_KIND,
            },
            "membershipDefinition": {"includedResources": sorted([RESOURCE_A, RESOURCE_B])},
        }
        with self.assertRaises(self.pkg.OperationsApiError) as ctx:
            self.client.create_custom_group(duplicate)
        self.assertEqual(409, ctx.exception.status)
        self.assertEqual("createCustomGroup", ctx.exception.operation_id)
        self.assertIn("already exists", ctx.exception.body)
        self.assertTrue(result.group_id)

    def test_10_client_touches_only_contracted_loopback_operations(self) -> None:
        created = self.ensure()
        self.ensure(included_resources=[RESOURCE_A, RESOURCE_B, RESOURCE_C])

        contracted = {
            (op["method"], op["full_path"]) for op in CONTRACT["operations"].values()
        }
        for record in self.log():
            self.assertIn(
                (record["method"], record["path"]),
                contracted,
                f"{record['method']} {record['path']} is not named by the contract",
            )
            self.assertIsNotNone(record["operation_id"])

        # The mock really is pinned: a real suite-api route outside the contract 404s.
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.mock.base_url}/suite-api/api/resources/groups/{created.group_id}",
            method="DELETE",
            headers={"Accept": "application/json", "Authorization": f"OpsToken {self.token}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(404, ctx.exception.code)

    def test_11_package_is_stdlib_only(self) -> None:
        package_dir = REPO_ROOT / "vcfops_customgroups"
        self.assertTrue(package_dir.is_dir(), "expected a vcfops_customgroups package directory")
        sources = sorted(package_dir.rglob("*.py"))
        self.assertTrue(sources, "the package contains no Python modules")

        local = {"vcfops_customgroups"}
        offenders: list[str] = []
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    if not root or root in local:
                        continue
                    if root not in sys.stdlib_module_names:
                        offenders.append(f"{source.relative_to(REPO_ROOT)}: {root}")
        self.assertEqual([], offenders, f"non-stdlib imports found: {offenders}")

    def test_12_unmanaged_existing_fields_do_not_cause_drift(self) -> None:
        created = self.ensure(policy="9f3c1e77-45aa-4a0e-8f1e-7c2b6d9e4f10", auto_resolve_membership=False)
        self.mock.truncate_log()

        result = self.pkg.ensure_custom_group(
            self.client,
            name=GROUP_NAME,
            adapter_kind_key=ADAPTER_KIND,
            resource_kind_key=RESOURCE_KIND,
        )

        self.assertEqual(created.group_id, result.group_id)
        self.assertEqual("unchanged", result.action)
        self.assertEqual([], self.requests_for("createCustomGroup"))
        self.assertEqual([], self.requests_for("modifyCustomGroup"))
        lookup = self.only("getCustomGroups")
        self.assertEqual("", lookup["raw_query"], "unset policy must not request includePolicy")

    def test_13_policy_drift_is_compared_when_policy_is_managed(self) -> None:
        old_policy = "9f3c1e77-45aa-4a0e-8f1e-7c2b6d9e4f10"
        new_policy = "0550d6c8-9c3b-49f2-8cc8-cae9062f7881"
        created = self.ensure(policy=old_policy)
        self.mock.truncate_log()

        updated = self.ensure(policy=new_policy)

        self.assertEqual(created.group_id, updated.group_id)
        self.assertEqual("updated", updated.action)
        self.assertEqual("includePolicy=true", self.only("getCustomGroups")["raw_query"])
        self.assertEqual(new_policy, self.only("modifyCustomGroup")["body"]["policy"])

    def test_14_direct_group_methods_sort_every_unique_items_array(self) -> None:
        self.mock.truncate_log()
        group = {
            "resourceKey": {
                "adapterKindKey": ADAPTER_KIND,
                "name": "Direct API group",
                "resourceKindKey": RESOURCE_KIND,
            },
            "membershipDefinition": {
                "includedResources": [RESOURCE_A, RESOURCE_B, RESOURCE_A],
                "excludedResources": [RESOURCE_C, RESOURCE_B],
            },
        }

        created = self.client.create_custom_group(group)
        record = self.only("createCustomGroup")

        self.assertTrue(created["id"])
        self.assertNotIn("id", record["body"])
        self.assertEqual(
            sorted({RESOURCE_A, RESOURCE_B}),
            record["body"]["membershipDefinition"]["includedResources"],
        )
        self.assertEqual(
            sorted({RESOURCE_B, RESOURCE_C}),
            record["body"]["membershipDefinition"]["excludedResources"],
        )

    def test_15_create_never_puts_server_fields_on_the_wire(self) -> None:
        group = {
            "id": "11111111-1111-1111-1111-111111111111",
            "links": [{"href": "server-only"}],
            "resourceKey": {
                "adapterKindKey": ADAPTER_KIND,
                "name": "Server-field create probe",
                "resourceKindKey": RESOURCE_KIND,
                "links": [{"href": "server-only"}],
            },
            "membershipDefinition": {"includedResources": [RESOURCE_A]},
        }
        self.mock.truncate_log()

        try:
            created = self.client.create_custom_group(group)
        except Exception:
            self.assertEqual(
                [],
                self.requests_for("createCustomGroup"),
                "rejecting server fields locally is valid, but they must never reach the wire",
            )
            return

        record = self.only("createCustomGroup")
        self.assertNotEqual(group["id"], created["id"])
        self.assertNotIn("id", record["body"])
        self.assertNotIn("links", record["body"])
        self.assertNotIn("links", record["body"]["resourceKey"])

    def test_16_modify_sends_existing_id_but_never_server_links(self) -> None:
        created = self.ensure()
        payload = dict(created.group)
        payload["links"] = [{"href": "server-only"}]
        payload["resourceKey"] = dict(payload["resourceKey"])
        payload["resourceKey"]["links"] = [{"href": "server-only"}]
        payload["membershipDefinition"] = {
            "includedResources": [RESOURCE_C, RESOURCE_A, RESOURCE_B]
        }
        self.mock.truncate_log()

        try:
            updated = self.client.modify_custom_group(payload)
        except Exception:
            self.assertEqual(
                [],
                self.requests_for("modifyCustomGroup"),
                "rejecting server links locally is valid, but they must never reach the wire",
            )
            return

        record = self.only("modifyCustomGroup")

        self.assertEqual(created.group_id, updated["id"])
        self.assertEqual(created.group_id, record["body"]["id"])
        self.assertNotIn("links", record["body"])
        self.assertNotIn("links", record["body"]["resourceKey"])
        self.assertEqual(
            sorted([RESOURCE_A, RESOURCE_B, RESOURCE_C]),
            record["body"]["membershipDefinition"]["includedResources"],
        )

    def test_17_contract_path_controls_the_operation_base_path(self) -> None:
        custom_contract = json.loads(json.dumps(CONTRACT))
        custom_contract["api"]["base_path"] = "/contract-probe"
        for operation in custom_contract["operations"].values():
            operation.pop("full_path", None)

        contract_path = Path(self._tmp.name) / "contract-probe.json"
        request_log = Path(self._tmp.name) / "contract-probe-requests.jsonl"
        contract_path.write_text(json.dumps(custom_contract), encoding="utf-8")

        with MockServer(contract_path=contract_path, request_log=request_log) as mock:
            client = self.pkg.OperationsClient(mock.base_url, contract_path=contract_path)
            client.acquire_token("contract-user", "contract-password")
            client.get_custom_groups()
            records = mock.read_log()

        self.assertEqual(
            [
                "/contract-probe/api/auth/token/acquire",
                "/contract-probe/api/resources/groups",
            ],
            [record["path"] for record in records],
        )


if __name__ == "__main__":
    unittest.main()
