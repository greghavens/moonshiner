#!/usr/bin/env python3
"""Deterministic protected verification for vcf91-0304.

Compiles the client together with the offline in-process contract fixture, runs the harness, and
judges the recorded requests. No socket is opened and no live VMware endpoint is contacted.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SPEC_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
SPEC_VERSION = "9.1.0.0"
EXPECTED_OPERATIONS = {
    "getApplicationById": ("GET", "/groups/applications/{id}"),
    "listApplicationTiers": ("GET", "/groups/applications/{id}/tiers"),
    "addTier": ("POST", "/groups/applications/{id}/tiers"),
}

APP_ID = "18230:561:271275765"
APP_PATH = f"/api/ni/groups/applications/{APP_ID}"
TIERS_PATH = f"{APP_PATH}/tiers"
MISSING_APP_PATH = "/api/ni/groups/applications/18230:561:000000000"
AUTHORIZATION = "NetworkInsight 9f1c4b2e-tier-token"
FIRST_TIER_ID = "18230:562:100000001"

BEGIN = "---MOONSHINER-JSON-BEGIN---"
END = "---MOONSHINER-JSON-END---"

_HARNESS: dict[str, dict] | None = None


def harness() -> dict[str, dict]:
    """Compile and run the harness once, returning the scenarios keyed by name."""
    global _HARNESS
    if _HARNESS is not None:
        return _HARNESS

    for tool in ("javac", "java"):
        if shutil.which(tool) is None:
            raise AssertionError(f"required toolchain executable is unavailable: {tool}")

    sources = sorted(str(p) for p in (ROOT / "src").glob("*.java"))
    sources += sorted(str(p) for p in (ROOT / "tests").glob("*.java"))
    with tempfile.TemporaryDirectory() as build:
        compiled = subprocess.run(
            ["javac", "-nowarn", "-d", build, *sources],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if compiled.returncode != 0:
            raise AssertionError("javac failed:\n" + compiled.stdout + compiled.stderr)
        run = subprocess.run(
            ["java", "-cp", build, "TestMain"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    if run.returncode != 0:
        raise AssertionError(
            f"TestMain exited with {run.returncode}:\n{run.stdout}\n{run.stderr}"
        )
    if BEGIN not in run.stdout or END not in run.stdout:
        raise AssertionError("TestMain did not emit a result document:\n" + run.stdout)
    payload = run.stdout.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    document = json.loads(payload)
    _HARNESS = {scenario["name"]: scenario for scenario in document["scenarios"]}
    return _HARNESS


class ContractProvenance(unittest.TestCase):
    def test_contract_and_sources_pin_the_official_specification(self) -> None:
        self.assertEqual(
            sorted(path.name for path in (ROOT / "src").glob("*.java")),
            ["Json.java", "NiTierClient.java"],
            "the supplied codec and single-file client are the complete source surface",
        )
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )

        self.assertEqual(contract["source"]["commit"], SPEC_SHA)
        self.assertEqual(contract["source"]["path"], SPEC_PATH)
        self.assertEqual(contract["source"]["api_version"], SPEC_VERSION)
        self.assertEqual(contract["source"]["openapi"], "3.0.1")
        self.assertEqual(contract["source"]["repository_license"], "Apache-2.0")
        self.assertEqual(contract["base_path"], "/api/ni")

        self.assertEqual(sources["repository_commit_sha"], SPEC_SHA)
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertEqual(sources["spec_version"], SPEC_VERSION)

        self.assertEqual(
            {op["operationId"]: (op["method"], op["path"]) for op in contract["operations"]},
            EXPECTED_OPERATIONS,
        )
        self.assertEqual(
            {op["operationId"]: (op["method"], op["path"]) for op in sources["operations"]},
            EXPECTED_OPERATIONS,
        )
        for op in sources["operations"]:
            self.assertIn(SPEC_SHA, op["source"])
            self.assertIn(SPEC_PATH, op["source"])

        auth = contract["authentication"]
        self.assertEqual(auth["header_name"], "Authorization")
        self.assertEqual(auth["value_format"], "NetworkInsight {token}")

        tier_request = contract["schemas"]["TierRequest"]
        self.assertEqual(tier_request["required"], [])
        self.assertEqual(
            tier_request["property_order"],
            ["name", "entity_id", "group_membership_criteria", "member_list", "source_group_entity_id"],
        )


class WireShape(unittest.TestCase):
    maxDiff = None

    def scenario(self, name: str) -> dict:
        scenarios = harness()
        self.assertIn(name, scenarios, f"harness did not report scenario {name}")
        return scenarios[name]

    def succeeded(self, name: str) -> dict:
        scenario = self.scenario(name)
        self.assertIsNone(
            scenario["error"],
            f"{name} raised {scenario['error']}",
        )
        return scenario

    # ------------------------------------------------------------- assertions

    def assert_common(self, request: dict, where: str) -> None:
        self.assertEqual(
            request["headers"].get("authorization"),
            AUTHORIZATION,
            f"{where}: every request carries the NetworkInsight API key",
        )
        self.assertEqual(
            request["headers"].get("accept"),
            "application/json",
            f"{where}: every request accepts application/json",
        )
        self.assertEqual(request["query"], "", f"{where}: no query parameter is set")
        self.assertNotIn("?", request["target"], f"{where}: request target carries no query string")

    def assert_get(self, request: dict, path: str, where: str) -> None:
        self.assert_common(request, where)
        self.assertEqual(request["method"], "GET", where)
        self.assertEqual(request["path"], path, where)
        self.assertEqual(request["body"], "", f"{where}: a GET carries no body")
        self.assertNotIn(
            "content-type",
            request["headers"],
            f"{where}: a request without a body sends no Content-Type",
        )

    def assert_post(self, request: dict, path: str, body: str, where: str) -> None:
        self.assert_common(request, where)
        self.assertEqual(request["method"], "POST", where)
        self.assertEqual(request["path"], path, where)
        self.assertEqual(
            request["headers"].get("content-type"),
            "application/json",
            where,
        )
        self.assertEqual(request["body"], body, f"{where}: exact TierRequest wire body")

    def posts(self, scenario: dict) -> list[dict]:
        return [r for r in scenario["requests"] if r["method"] == "POST"]

    # -------------------------------------------------------------- scenarios

    def test_minimal_spec_sends_only_the_required_field(self) -> None:
        scenario = self.succeeded("minimal_create")
        results = scenario["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["created"])
        self.assertEqual(results[0]["tier_id"], FIRST_TIER_ID)
        self.assertEqual(results[0]["tier_name"], "web-tier")

        requests = scenario["requests"]
        self.assertEqual(
            [(r["method"], r["path"]) for r in requests],
            [("GET", APP_PATH), ("GET", TIERS_PATH), ("POST", TIERS_PATH)],
            "the application is resolved, then the tiers are listed, then the tier is created",
        )
        self.assert_get(requests[0], APP_PATH, "minimal_create/getApplicationById")
        self.assert_get(requests[1], TIERS_PATH, "minimal_create/listApplicationTiers")
        self.assert_post(
            requests[2],
            TIERS_PATH,
            '{"name":"web-tier"}',
            "minimal_create/addTier",
        )

    def test_unset_optional_fields_are_absent_rather_than_empty(self) -> None:
        scenario = self.succeeded("full_body")
        self.assertEqual(len(scenario["requests"]), 3)
        expected = (
            '{"name":"edge \\"dmz\\" tier",'
            '"group_membership_criteria":['
            '{"membership_type":"SearchMembershipCriteria",'
            '"search_membership_criteria":{"entity_type":"VirtualMachine",'
            '"filter":"security_groups.entity_id = \'18230:82:604573173\'"}},'
            '{"membership_type":"IPAddressMembershipCriteria",'
            '"ip_address_membership_criteria":{"ip_addresses":'
            '["10.0.0.1","10.0.0.1/24","10.0.0.1-10.0.0.200"]}}],'
            '"member_list":{'
            '"vms":[{"key":{"entity_id":"18230:1:1158969162","entity_type":"VIRTUALMACHINE"},'
            '"name":"VM1"},'
            '{"key":{"entity_id":"18230:601:863301375","entity_type":"EC2INSTANCE"}}],'
            '"physical_ips":[{"key":{"entity_id":"18230:541:365252372",'
            '"entity_type":"IPENDPOINT"},"name":"52.35.41.245"}]},'
            '"source_group_entity_id":["18230:566:264351372"]}'
        )
        self.assert_post(scenario["requests"][2], TIERS_PATH, expected, "full_body/addTier")

        body = json.loads(scenario["requests"][2]["body"])
        self.assertNotIn("entity_id", body, "the server assigns entity_id; it is not sent on create")
        self.assertNotIn(
            "kubernetes_services",
            body["member_list"],
            "an empty member list is omitted rather than sent as []",
        )
        self.assertNotIn(
            "ip_address_membership_criteria",
            body["group_membership_criteria"][0],
            "the criteria object that does not apply is absent, not null",
        )
        self.assertNotIn(
            "search_membership_criteria",
            body["group_membership_criteria"][1],
            "the criteria object that does not apply is absent, not null",
        )
        self.assertNotIn(
            "name",
            body["member_list"]["vms"][1],
            "a member without a name omits the field",
        )
        self.assertNotIn(
            "entity_name",
            body["member_list"]["vms"][0]["key"],
            "an unset reference field is omitted",
        )

    def test_repeating_the_call_does_not_create_a_second_tier(self) -> None:
        scenario = self.succeeded("repeated_call_converges")
        results = scenario["results"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["created"], "the first call creates the tier")
        self.assertFalse(results[1]["created"], "the second call must not report a creation")
        self.assertEqual(
            results[0]["tier_id"],
            results[1]["tier_id"],
            "both calls converge on the same tier",
        )
        self.assertEqual(results[0]["tier_id"], FIRST_TIER_ID)

        self.assertEqual(
            len(self.posts(scenario)),
            1,
            "addTier is issued once across both calls; a second POST would duplicate the tier",
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [
                ("GET", APP_PATH),
                ("GET", TIERS_PATH),
                ("POST", TIERS_PATH),
                ("GET", APP_PATH),
                ("GET", TIERS_PATH),
            ],
        )

    def test_an_existing_tier_is_adopted_without_mutating(self) -> None:
        scenario = self.succeeded("existing_tier_is_adopted")
        results = scenario["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["created"])
        self.assertEqual(results[0]["tier_id"], scenario["seeded_tier_id"])
        self.assertEqual(
            self.posts(scenario),
            [],
            "a tier that already exists must not be re-created",
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", APP_PATH), ("GET", TIERS_PATH)],
        )

    def test_tier_name_matching_is_exact_and_case_sensitive(self) -> None:
        scenario = self.succeeded("case_sensitive_near_match")
        result = scenario["results"][0]
        self.assertTrue(result["created"])
        self.assertEqual(result["tier_name"], "web-tier")
        self.assertNotIn(result["tier_id"], scenario["seeded_tier_ids"])
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", APP_PATH), ("GET", TIERS_PATH), ("POST", TIERS_PATH)],
            "a differently-cased name is not the requested tier",
        )
        self.assert_post(
            scenario["requests"][2],
            TIERS_PATH,
            '{"name":"web-tier"}',
            "case_sensitive_near_match/addTier",
        )

    def test_a_lost_race_is_absorbed_instead_of_duplicating(self) -> None:
        scenario = self.succeeded("lost_race_is_absorbed")
        results = scenario["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(
            results[0]["created"],
            "the concurrent creator won, so this call did not create the tier",
        )
        self.assertEqual(results[0]["tier_id"], scenario["seeded_tier_id"])
        self.assertEqual(
            len(self.posts(scenario)),
            1,
            "the rejected addTier is not retried",
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [
                ("GET", APP_PATH),
                ("GET", TIERS_PATH),
                ("POST", TIERS_PATH),
                ("GET", TIERS_PATH),
            ],
            "the name conflict is resolved by re-reading the tier list",
        )

    def test_a_400_without_the_tier_after_reread_preserves_the_api_error(self) -> None:
        scenario = self.scenario("conflict_without_tier")
        self.assertEqual(scenario["results"], [])
        self.assertEqual(
            scenario["error"],
            {
                "type": "NiApiException",
                "status_code": 400,
                "message": "Concurrent tier was rolled back",
            },
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [
                ("GET", APP_PATH),
                ("GET", TIERS_PATH),
                ("POST", TIERS_PATH),
                ("GET", TIERS_PATH),
            ],
            "a 400 is followed by exactly one tier-list re-read",
        )

    def test_a_non_conflict_error_is_reported_without_a_reread(self) -> None:
        scenario = self.scenario("non_conflict_failure")
        self.assertEqual(scenario["results"], [])
        self.assertEqual(
            scenario["error"],
            {
                "type": "NiApiException",
                "status_code": 500,
                "message": "Tier service is unavailable",
            },
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", APP_PATH), ("GET", TIERS_PATH), ("POST", TIERS_PATH)],
            "only the documented 400 conflict path triggers a re-read",
        )

    def test_a_tier_list_error_stops_before_mutating(self) -> None:
        scenario = self.scenario("list_failure")
        self.assertEqual(scenario["results"], [])
        self.assertEqual(
            scenario["error"],
            {
                "type": "NiApiException",
                "status_code": 500,
                "message": "Tier inventory is unavailable",
            },
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", APP_PATH), ("GET", TIERS_PATH)],
            "a failed list cannot be treated as an empty list and followed by addTier",
        )

    def test_a_missing_application_fails_before_mutating(self) -> None:
        scenario = self.scenario("unknown_application")
        error = scenario["error"]
        self.assertIsNotNone(error, "an unknown application must raise")
        self.assertEqual(error["type"], "NiApiException")
        self.assertEqual(error["status_code"], 404)
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", MISSING_APP_PATH)],
            "nothing is listed or created once the application is known to be absent",
        )

    def test_kubernetes_service_members_are_encoded_in_contract_order(self) -> None:
        scenario = self.succeeded("kubernetes_member")
        self.assertEqual(len(scenario["requests"]), 3)
        self.assert_post(
            scenario["requests"][2],
            TIERS_PATH,
            '{"name":"service-tier","member_list":{"kubernetes_services":['
            '{"key":{"entity_id":"18230:1504:321",'
            '"entity_type":"KUBERNETESSERVICE"},"name":"checkout"}]}}',
            "kubernetes_member/addTier",
        )

    def test_a_trailing_slash_on_the_base_url_does_not_double_the_separator(self) -> None:
        scenario = self.succeeded("base_url_with_trailing_slash")
        self.assertEqual(
            [(r["method"], r["path"]) for r in scenario["requests"]],
            [("GET", APP_PATH), ("GET", TIERS_PATH), ("POST", TIERS_PATH)],
        )
        for request in scenario["requests"]:
            self.assertNotIn("//", request["path"])

    def test_no_request_leaves_the_contract_surface(self) -> None:
        allowed = {APP_PATH, TIERS_PATH, MISSING_APP_PATH}
        for name, scenario in harness().items():
            for request in scenario["requests"]:
                self.assertIn(
                    request["path"],
                    allowed,
                    f"{name} called a path outside the pinned contract",
                )
                self.assertIn(
                    request["method"],
                    {"GET", "POST"},
                    f"{name} used a method outside the pinned contract",
                )
                self.assert_common(request, f"{name}/seq{request['sequence']}")


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=True, argv=[sys.argv[0]])
