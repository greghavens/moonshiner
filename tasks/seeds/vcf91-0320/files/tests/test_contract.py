"""Contract verifier for vcfa-storage-profile.

Asserts the exact wire shape of every request the client makes against a
loopback mock pinned to docs/contract.json, and asserts that a failing
placement precheck sends no mutating request at all.

Deterministic and offline: the only host contacted is 127.0.0.1.

Run with: python3 tests/test_contract.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
sys.path.insert(0, HERE)

import vcfa_mock  # noqa: E402

TOKEN = "test-token-0320"
EXPECTED_AUTHORIZATION = "Bearer " + TOKEN

REQUIRED_BODY_FIELDS = {"name", "regionId", "defaultItem"}

_TMPDIR = None
_MOCK = None


def setUpModule():
    global _TMPDIR, _MOCK
    _TMPDIR = tempfile.mkdtemp(prefix="vcfa-storage-contract-")
    _MOCK = vcfa_mock.MockAppliance(log_path=os.path.join(_TMPDIR, "requests.jsonl"))
    _MOCK.start()


def tearDownModule():
    if _MOCK is not None:
        _MOCK.stop()
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


class ContractCase(unittest.TestCase):
    """Base case: a fresh request log and a client per test."""

    def setUp(self):
        self.mock = _MOCK
        self.mock.reset()
        self.client = self.make_client()

    def tearDown(self):
        # Whatever the test did, the client must never have strayed off the
        # three operations docs/contract.json names.
        unrouted = self.mock.unrouted_requests()
        self.assertEqual(
            [], unrouted, "client called operations outside the contract: %r" % (unrouted,)
        )

    def make_client(self, api_version=None):
        from vcfa_storage import StorageProfileClient

        return StorageProfileClient(
            self.mock.base_url, TOKEN, api_version=api_version, timeout=10.0
        )

    # -- assertions shared across cases -------------------------------------

    def assertNothingMutated(self):
        """No createVsphereStorageProfile request was sent, so nothing changed."""
        posts = self.mock.requests_for("createVsphereStorageProfile")
        self.assertEqual(
            [],
            posts,
            "the precheck failed but a mutating request was still sent: %r" % (posts,),
        )
        self.assertEqual([], self.mock.created_profiles())

    def only_request(self, operation=None):
        entries = self.mock.requests()
        self.assertEqual(1, len(entries), "expected exactly one request, got %r" % (entries,))
        if operation is not None:
            self.assertEqual(operation, entries[0]["operation"])
        return entries[0]

    def only_post(self):
        posts = self.mock.requests_for("createVsphereStorageProfile")
        self.assertEqual(1, len(posts), "expected exactly one create, got %r" % (posts,))
        return posts[0]


class ProvenanceTests(unittest.TestCase):
    """The contract has to admit what it is."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as handle:
            cls.contract = json.load(handle)
        with open(
            os.path.join(REPO_ROOT, "docs", "official_sources.json"), encoding="utf-8"
        ) as handle:
            cls.sources = json.load(handle)

    def test_contract_states_it_is_reference_derived_not_a_specification(self):
        self.assertIs(False, self.contract["specification_available"])
        statement = self.contract["source_statement"].lower()
        self.assertIn("reference documentation", statement)
        self.assertIn("not", statement)
        self.assertIn("specification", statement)

    def test_contract_names_exactly_the_three_operations(self):
        self.assertEqual(
            {"getRegion", "getFabricVsphereDatastore", "createVsphereStorageProfile"},
            set(self.contract["operations"]),
        )

    def test_official_sources_record_url_operation_and_fetch_date(self):
        pages = self.sources["pages"]
        self.assertGreaterEqual(len(pages), 5)
        for page in pages:
            self.assertTrue(
                page["url"].startswith("https://developer.broadcom.com/xapis/"),
                "not an xAPIs reference URL: %r" % page["url"],
            )
            self.assertTrue(page["documents"].strip(), "page records no operation: %r" % page)
            self.assertRegex(page["date_fetched"], r"^\d{4}-\d{2}-\d{2}$")

    def test_every_contract_operation_is_covered_by_a_recorded_page(self):
        blob = json.dumps(self.sources)
        for operation in self.contract["operations"]:
            self.assertIn(operation, blob, "no source page records %s" % operation)


class MockPinningTests(ContractCase):
    """The mock serves the contract and nothing else."""

    def test_routing_table_comes_from_the_contract(self):
        served = {route["operation"] for route in self.mock.routes}
        self.assertEqual(set(self.mock.contract["operations"]), served)

    def test_off_contract_request_is_refused_and_flagged(self):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            self.mock.base_url + "/iaas/api/storage-profiles",
            headers={"Authorization": EXPECTED_AUTHORIZATION},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10.0)
        self.assertEqual(501, caught.exception.code)
        self.assertEqual(1, len(self.mock.unrouted_requests()))
        self.mock.reset()  # this probe is deliberately off-contract


class GetRegionTests(ContractCase):
    def test_wire_shape(self):
        region = self.client.get_region("r-dc2")
        self.assertEqual("Datacenter:datacenter-2", region["externalRegionId"])
        self.assertEqual("ca-vc01", region["cloudAccountId"])

        entry = self.only_request("getRegion")
        self.assertEqual("GET", entry["method"])
        self.assertEqual("/iaas/api/regions/r-dc2", entry["path"])
        self.assertEqual("", entry["raw_query"])
        self.assertEqual(EXPECTED_AUTHORIZATION, entry["headers"].get("authorization"))
        self.assertEqual("application/json", entry["headers"].get("accept"))
        self.assertIsNone(entry["raw_body"])
        self.assertNotIn(
            "content-type",
            entry["headers"],
            "Content-Type must not be sent on a request with no body",
        )

    def test_missing_region_returns_none(self):
        self.assertIsNone(self.client.get_region("r-does-not-exist"))
        self.assertEqual(404, self.only_request("getRegion")["status"])

    def test_forbidden_region_raises_api_error(self):
        from vcfa_storage import ApiError

        with self.assertRaises(ApiError) as caught:
            self.client.get_region("r-forbidden")
        self.assertEqual(403, caught.exception.status)

    def test_path_parameter_is_percent_encoded(self):
        self.assertIsNone(self.client.get_region("missing/with ?#%"))
        entry = self.only_request("getRegion")
        self.assertEqual(
            "/iaas/api/regions/missing%2Fwith%20%3F%23%25", entry["path"]
        )
        self.assertEqual("", entry["raw_query"])

    def test_redirect_is_an_api_error_and_is_not_followed(self):
        from vcfa_storage import ApiError

        with self.assertRaises(ApiError) as caught:
            self.client.get_region("r-redirect")
        self.assertEqual(302, caught.exception.status)
        self.only_request("getRegion")


class GetDatastoreTests(ContractCase):
    def test_wire_shape(self):
        datastore = self.client.get_datastore("ds-gold")
        self.assertEqual("Datacenter:datacenter-2", datastore["externalRegionId"])
        self.assertEqual(["ca-vc01"], datastore["cloudAccountIds"])

        entry = self.only_request("getFabricVsphereDatastore")
        self.assertEqual("GET", entry["method"])
        self.assertEqual("/iaas/api/fabric-vsphere-datastores/ds-gold", entry["path"])
        self.assertEqual("", entry["raw_query"])
        self.assertEqual(EXPECTED_AUTHORIZATION, entry["headers"].get("authorization"))
        self.assertEqual("application/json", entry["headers"].get("accept"))
        self.assertIsNone(entry["raw_body"])

    def test_missing_datastore_returns_none(self):
        self.assertIsNone(self.client.get_datastore("ds-nope"))
        self.assertEqual(404, self.only_request("getFabricVsphereDatastore")["status"])

    def test_path_parameter_is_percent_encoded(self):
        self.assertIsNone(self.client.get_datastore("missing/with ?#%"))
        entry = self.only_request("getFabricVsphereDatastore")
        self.assertEqual(
            "/iaas/api/fabric-vsphere-datastores/missing%2Fwith%20%3F%23%25",
            entry["path"],
        )
        self.assertEqual("", entry["raw_query"])


class ApiVersionQueryTests(ContractCase):
    def test_omitted_entirely_when_not_configured(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-gold"
        )
        entries = self.mock.requests()
        self.assertEqual(3, len(entries))
        for entry in entries:
            self.assertEqual("", entry["raw_query"])
            self.assertEqual({}, entry["query"])

    def test_sent_on_every_operation_when_configured(self):
        client = self.make_client(api_version="2026-03-01")
        client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-gold"
        )
        entries = self.mock.requests()
        self.assertEqual(3, len(entries))
        for entry in entries:
            self.assertEqual({"apiVersion": ["2026-03-01"]}, entry["query"])
            self.assertEqual("apiVersion=2026-03-01", entry["raw_query"])


class CreateWireShapeTests(ContractCase):
    def test_minimal_request_carries_exactly_the_required_fields(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True
        )
        entry = self.only_post()
        self.assertEqual("POST", entry["method"])
        self.assertEqual("/iaas/api/storage-profiles-vsphere", entry["path"])
        self.assertEqual(
            {"name": "gold", "regionId": "r-dc2", "defaultItem": True}, entry["body"]
        )
        self.assertEqual(REQUIRED_BODY_FIELDS, set(entry["body"]))

    def test_headers(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True
        )
        headers = self.only_post()["headers"]
        self.assertEqual(EXPECTED_AUTHORIZATION, headers.get("authorization"))
        self.assertEqual("application/json", headers.get("accept"))
        self.assertEqual(
            "application/json", (headers.get("content-type") or "").split(";")[0].strip()
        )

    def test_unsupplied_optional_fields_are_absent_not_empty(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, description="tier one"
        )
        entry = self.only_post()
        self.assertEqual(REQUIRED_BODY_FIELDS | {"description"}, set(entry["body"]))
        for absent in (
            "supportsEncryption",
            "tags",
            "datastoreId",
            "storagePolicyId",
            "provisioningType",
            "limitIops",
            "diskMode",
            "diskType",
            "priority",
            "storageFilterType",
            "tagsToMatch",
            "computeHostId",
        ):
            self.assertNotIn(absent, entry["body"], "%s was sent but never supplied" % absent)

    def test_no_field_is_ever_sent_as_null(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True
        )
        entry = self.only_post()
        self.assertNotIn("null", entry["raw_body"])
        for name, value in entry["body"].items():
            self.assertIsNotNone(value, "%s was sent as null" % name)

    def test_full_request_body_is_exact(self):
        self.client.create_vsphere_storage_profile(
            name="gold",
            region_id="r-dc2",
            default_item=True,
            description="tier one",
            supports_encryption=True,
            tags=[{"key": "tier", "value": "gold"}],
            datastore_id="ds-gold",
            storage_policy_id="sp-vsan-default",
            provisioning_type="thin",
            limit_iops="2000",
            disk_mode="independent-persistent",
            disk_type="Standard",
            priority=3,
            storage_filter_type="INCLUDE_ALL",
            tags_to_match=[{"key": "capability", "value": "ssd"}],
            compute_host_id="ch-cluster-1",
        )
        self.assertEqual(
            {
                "name": "gold",
                "regionId": "r-dc2",
                "defaultItem": True,
                "description": "tier one",
                "supportsEncryption": True,
                "tags": [{"key": "tier", "value": "gold"}],
                "datastoreId": "ds-gold",
                "storagePolicyId": "sp-vsan-default",
                "provisioningType": "thin",
                "limitIops": "2000",
                "diskMode": "independent-persistent",
                "diskType": "Standard",
                "priority": 3,
                "storageFilterType": "INCLUDE_ALL",
                "tagsToMatch": [{"key": "capability", "value": "ssd"}],
                "computeHostId": "ch-cluster-1",
            },
            self.only_post()["body"],
        )

    def test_precheck_objects_are_not_leaked_into_the_body(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-gold"
        )
        body = self.only_post()["body"]
        for leaked in ("externalRegionId", "cloudAccountId", "cloudAccountIds", "_links", "id"):
            self.assertNotIn(leaked, body, "%s leaked from the precheck into the body" % leaked)

    def test_returns_the_decoded_profile_from_the_201(self):
        profile = self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True
        )
        self.assertEqual("st-prof-0001", profile["id"])
        self.assertEqual("gold", profile["name"])
        self.assertEqual("Datacenter:datacenter-2", profile["externalRegionId"])
        self.assertEqual(1, len(self.mock.created_profiles()))


class FalsyButSuppliedTests(ContractCase):
    """Presence is decided by whether the caller supplied a value, not by truth."""

    def test_default_item_false_is_still_sent(self):
        self.client.create_vsphere_storage_profile(
            name="not-default", region_id="r-dc2", default_item=False
        )
        body = self.only_post()["body"]
        self.assertIn("defaultItem", body, "defaultItem is required even when false")
        self.assertIs(False, body["defaultItem"])

    def test_priority_zero_is_sent(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, priority=0
        )
        body = self.only_post()["body"]
        self.assertIn("priority", body, "priority 0 is the highest priority, not an absent value")
        self.assertEqual(0, body["priority"])

    def test_supports_encryption_false_is_sent(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, supports_encryption=False
        )
        body = self.only_post()["body"]
        self.assertIn("supportsEncryption", body)
        self.assertIs(False, body["supportsEncryption"])

    def test_empty_string_description_is_sent_when_supplied(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, description=""
        )
        body = self.only_post()["body"]
        self.assertIn("description", body)
        self.assertEqual("", body["description"])

    def test_empty_tag_list_is_sent_when_supplied(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, tags=[]
        )
        body = self.only_post()["body"]
        self.assertIn("tags", body)
        self.assertEqual([], body["tags"])


class PrecheckGatesMutationTests(ContractCase):
    """A failing precheck must leave the appliance untouched."""

    def test_missing_region_gates_the_mutation(self):
        from vcfa_storage import RegionNotFoundError

        with self.assertRaises(RegionNotFoundError):
            self.client.create_vsphere_storage_profile(
                name="gold", region_id="r-ghost", default_item=True, datastore_id="ds-gold"
            )
        self.assertNothingMutated()

    def test_region_is_checked_before_the_datastore(self):
        from vcfa_storage import RegionNotFoundError

        with self.assertRaises(RegionNotFoundError):
            self.client.create_vsphere_storage_profile(
                name="gold", region_id="r-ghost", default_item=True, datastore_id="ds-gold"
            )
        entry = self.only_request("getRegion")
        self.assertEqual("/iaas/api/regions/r-ghost", entry["path"])
        self.assertEqual(
            [],
            self.mock.requests_for("getFabricVsphereDatastore"),
            "the datastore was looked up even though the region did not resolve",
        )

    def test_missing_datastore_gates_the_mutation(self):
        from vcfa_storage import DatastoreNotFoundError

        with self.assertRaises(DatastoreNotFoundError):
            self.client.create_vsphere_storage_profile(
                name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-ghost"
            )
        self.assertNothingMutated()
        self.assertEqual(1, len(self.mock.requests_for("getFabricVsphereDatastore")))

    def test_datacenter_mismatch_gates_the_mutation(self):
        from vcfa_storage import PlacementMismatchError

        with self.assertRaises(PlacementMismatchError):
            self.client.create_vsphere_storage_profile(
                name="gold",
                region_id="r-dc2",
                default_item=True,
                datastore_id="ds-other-datacenter",
            )
        self.assertNothingMutated()

    def test_cloud_account_mismatch_gates_the_mutation(self):
        from vcfa_storage import PlacementMismatchError

        with self.assertRaises(PlacementMismatchError):
            self.client.create_vsphere_storage_profile(
                name="gold",
                region_id="r-dc2",
                default_item=True,
                datastore_id="ds-other-cloud-account",
            )
        self.assertNothingMutated()

    def test_forbidden_datastore_gates_the_mutation(self):
        from vcfa_storage import ApiError

        with self.assertRaises(ApiError) as caught:
            self.client.create_vsphere_storage_profile(
                name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-forbidden"
            )
        self.assertEqual(403, caught.exception.status)
        self.assertNothingMutated()

    def test_mismatch_is_decided_without_extra_calls(self):
        from vcfa_storage import PlacementMismatchError

        with self.assertRaises(PlacementMismatchError):
            self.client.create_vsphere_storage_profile(
                name="gold",
                region_id="r-dc2",
                default_item=True,
                datastore_id="ds-other-datacenter",
            )
        self.assertEqual(
            ["getRegion", "getFabricVsphereDatastore"],
            [entry["operation"] for entry in self.mock.requests()],
        )


class PrecheckPassesTests(ContractCase):
    """The precheck must not refuse placements the contract permits."""

    def test_datastore_lookup_is_skipped_when_no_datastore_is_supplied(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True
        )
        self.assertEqual(
            ["getRegion", "createVsphereStorageProfile"],
            [entry["operation"] for entry in self.mock.requests()],
        )

    def test_region_without_a_cloud_account_skips_the_cloud_account_check(self):
        profile = self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2-unbound", default_item=True, datastore_id="ds-gold"
        )
        self.assertEqual("st-prof-0001", profile["id"])

    def test_datastore_in_several_cloud_accounts_is_accepted(self):
        profile = self.client.create_vsphere_storage_profile(
            name="silver", region_id="r-dc2", default_item=False, datastore_id="ds-silver"
        )
        self.assertEqual("silver", profile["name"])

    def test_precheck_runs_before_the_mutation_in_order(self):
        self.client.create_vsphere_storage_profile(
            name="gold", region_id="r-dc2", default_item=True, datastore_id="ds-gold"
        )
        self.assertEqual(
            ["getRegion", "getFabricVsphereDatastore", "createVsphereStorageProfile"],
            [entry["operation"] for entry in self.mock.requests()],
        )


class CreateFailureTests(ContractCase):
    def test_rejected_name_surfaces_as_api_error(self):
        from vcfa_storage import ApiError

        with self.assertRaises(ApiError) as caught:
            self.client.create_vsphere_storage_profile(
                name="already-taken", region_id="r-dc2", default_item=True
            )
        self.assertEqual(400, caught.exception.status)
        self.assertEqual(1, len(self.mock.requests_for("createVsphereStorageProfile")))

    def test_create_failure_is_not_reported_as_a_precheck_failure(self):
        from vcfa_storage import PrecheckFailed

        with self.assertRaises(Exception) as caught:
            self.client.create_vsphere_storage_profile(
                name="already-taken", region_id="r-dc2", default_item=True
            )
        self.assertNotIsInstance(
            caught.exception,
            PrecheckFailed,
            "a rejected mutation must not masquerade as a precheck refusal",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
