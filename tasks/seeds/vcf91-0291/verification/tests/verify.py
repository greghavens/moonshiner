#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks pod inventory task.

Every assertion runs against the contract-pinned loopback mock in
``tests/contract_mock.py``. No live VMware endpoint is contacted.

Run from the repository root::

    python3 tests/verify.py
"""

import ast
import json
import os
import socket
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from contract_mock import ContractMock, load_contract  # noqa: E402

from vofn_inventory import (  # noqa: E402
    APIError,
    InventoryOptions,
    NetworksClient,
    PodRecord,
    ProtocolError,
    TransportError,
    render_inventory,
)
from vofn_inventory.client import (  # noqa: E402
    LIST_OPERATION_ID,
    LIST_PATH,
    NAMES_OPERATION_ID,
    NAMES_PATH,
)

TOKEN = "netins-token"
AUTHORIZATION = "NetworkInsight " + TOKEN

P1 = "18230:1505:115896"
P2 = "18230:1505:87533"
P3 = "18230:1505:20117"
P4 = "18230:1505:44012"
P5 = "18230:1505:99001"
P6 = "18230:1505:31337"
P7 = "18230:1505:70004"

PODS = [
    {"entity_id": P1, "entity_type": "KubernetesPod", "time": 1509283414},
    {"entity_id": P2, "entity_type": "KubernetesPod", "time": 1509283476},
    {"entity_id": P3, "entity_type": "KubernetesPod", "time": 1509283501},
    {"entity_id": P4, "entity_type": "KubernetesPod"},
    {"entity_id": P5, "entity_type": "KubernetesPod", "time": 1509283333},
    {"entity_id": P6, "entity_type": "KubernetesPod", "time": 1509283777},
    {"entity_id": P7, "entity_type": "KubernetesPod", "time": 1509283888},
]

NAMES = {
    P1: "apiserver-0",
    P2: "Édge-router-pod",
    P3: "API-gateway",
    P4: "apiserver-0",
    P5: "coredns-2",
}

# Named records first, ascending by name code point, ties broken by entity id;
# unresolved records last, ascending by entity id.
EXPECTED_ORDER = [P3, P1, P4, P5, P2, P6, P7]


def header_values(entry, name):
    lowered = name.lower()
    return [value for key, value in entry["headers"] if key.lower() == lowered]


def single_header(entry, name):
    values = header_values(entry, name)
    if len(values) != 1:
        raise AssertionError(
            "expected exactly one %s header, saw %r" % (name, values)
        )
    return values[0]


class MockCase(unittest.TestCase):
    """Base case that always tears the loopback server down."""

    def make_mock(self, **kwargs):
        kwargs.setdefault("token", TOKEN)
        mock = ContractMock(**kwargs).start()
        self.addCleanup(mock.stop)
        return mock

    def make_client(self, mock, token=TOKEN, **kwargs):
        return NetworksClient(mock.base_url, token, **kwargs)

    def assert_only_contract_paths(self, mock):
        allowed = {LIST_PATH, NAMES_PATH}
        for entry in mock.requests:
            self.assertIn(
                entry["path"],
                allowed,
                "client touched a path outside the pinned contract: %r" % entry["path"],
            )


class TestPaginationWireShape(MockCase):
    def test_every_page_is_fetched_with_exact_query_strings(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=3))

        self.assertEqual([record.entity_id for record in records], EXPECTED_ORDER)
        self.assert_only_contract_paths(mock)

        listings = mock.requests_for(LIST_OPERATION_ID)
        self.assertEqual(len(listings), 3, "expected exactly three pages to be read")
        self.assertEqual(
            [entry["raw_query"] for entry in listings],
            ["size=3", "size=3&cursor=3", "size=3&cursor=6"],
        )
        for entry in listings:
            self.assertEqual(entry["method"], "GET")
            self.assertEqual(entry["path"], LIST_PATH)
            self.assertEqual(entry["body_bytes"], b"")

    def test_first_page_never_sends_an_empty_cursor(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=10))

        listings = mock.requests_for(LIST_OPERATION_ID)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["raw_query"], "size=10")
        self.assertNotIn("cursor", listings[0]["raw_query"])

    def test_unset_optional_query_parameters_are_omitted_not_blank(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=2))

        for entry in mock.requests_for(LIST_OPERATION_ID):
            names = [name for name, _ in entry["query_pairs"]]
            self.assertNotIn("start_time", names)
            self.assertNotIn("end_time", names)
            for name, value in entry["query_pairs"]:
                self.assertNotEqual(
                    value, "", "%s was sent as an empty value instead of omitted" % name
                )

    def test_time_window_is_sent_in_contract_parameter_order(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(
            InventoryOptions(page_size=4, start_time=1509000000, end_time=1509999999)
        )

        listings = mock.requests_for(LIST_OPERATION_ID)
        self.assertEqual(
            [entry["raw_query"] for entry in listings],
            [
                "size=4&start_time=1509000000&end_time=1509999999",
                "size=4&cursor=4&start_time=1509000000&end_time=1509999999",
            ],
        )

    def test_pages_stop_when_the_appliance_stops_returning_a_cursor(self):
        mock = self.make_mock(pods=PODS[:2], names=NAMES)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=50))

        self.assertEqual(len(mock.requests_for(LIST_OPERATION_ID)), 1)
        self.assertEqual([record.entity_id for record in records], [P1, P2])

    def test_repeated_cursor_is_refused_instead_of_looping(self):
        page = {"results": [dict(PODS[0])], "cursor": "same", "total_count": 9}
        mock = self.make_mock(list_script=[(200, page), (200, page), (200, page)])
        client = self.make_client(mock)

        with self.assertRaises(ProtocolError) as caught:
            client.collect_pod_inventory(InventoryOptions(page_size=1))
        self.assertEqual(caught.exception.operation_id, LIST_OPERATION_ID)
        self.assertLessEqual(len(mock.requests_for(LIST_OPERATION_ID)), 2)

    def test_duplicate_entities_across_overlapping_pages_are_collected_once(self):
        mock = self.make_mock(pods=PODS, names=NAMES, page_overlap=1)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=3))

        ids = [record.entity_id for record in records]
        self.assertEqual(ids, EXPECTED_ORDER)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(mock.requests_for(LIST_OPERATION_ID)), 3)

        batched = [
            item["entity_id"]
            for entry in mock.requests_for(NAMES_OPERATION_ID)
            for item in entry["body_json"]["entities"]
        ]
        self.assertEqual(len(batched), len(set(batched)), "a duplicate was re-resolved")


class TestNameResolutionWireShape(MockCase):
    def test_batches_carry_exact_compact_bodies_in_collection_order(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(
            InventoryOptions(page_size=3, name_batch_size=2)
        )

        batches = mock.requests_for(NAMES_OPERATION_ID)
        self.assertEqual(len(batches), 4, "seven records at batch size two need four calls")
        self.assertEqual(
            [entry["body_text"] for entry in batches],
            [
                '{"entities":[{"entity_id":"%s","time":1509283414},'
                '{"entity_id":"%s","time":1509283476}]}' % (P1, P2),
                '{"entities":[{"entity_id":"%s","time":1509283501},'
                '{"entity_id":"%s"}]}' % (P3, P4),
                '{"entities":[{"entity_id":"%s","time":1509283333},'
                '{"entity_id":"%s","time":1509283777}]}' % (P5, P6),
                '{"entities":[{"entity_id":"%s","time":1509283888}]}' % P7,
            ],
        )

    def test_entity_without_a_timestamp_omits_the_optional_field(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=10))

        batches = mock.requests_for(NAMES_OPERATION_ID)
        self.assertEqual(len(batches), 1)
        entities = batches[0]["body_json"]["entities"]
        self.assertEqual(list(batches[0]["body_json"]), ["entities"])
        by_id = {item["entity_id"]: item for item in entities}
        self.assertNotIn("time", by_id[P4])
        self.assertEqual(by_id[P1]["time"], 1509283414)
        for item in entities:
            self.assertEqual(sorted(item), sorted(set(item)))
            for key, value in item.items():
                self.assertIsNotNone(value, "%s was serialized as null" % key)

    def test_no_name_request_is_sent_for_an_empty_collection(self):
        mock = self.make_mock(pods=[], names={})
        client = self.make_client(mock)
        records = client.collect_pod_inventory()

        self.assertEqual(records, [])
        self.assertEqual(len(mock.requests_for(LIST_OPERATION_ID)), 1)
        self.assertEqual(mock.requests_for(NAMES_OPERATION_ID), [])

    def test_batch_size_never_exceeds_the_specification_limit(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=7))

        for entry in mock.requests_for(NAMES_OPERATION_ID):
            self.assertLessEqual(len(entry["body_json"]["entities"]), 1000)


class TestHeaders(MockCase):
    def test_every_request_carries_the_contract_authorization_and_accept(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=3))

        self.assertTrue(mock.requests)
        for entry in mock.requests:
            self.assertEqual(single_header(entry, "Authorization"), AUTHORIZATION)
            self.assertEqual(single_header(entry, "Accept"), "application/json")

    def test_list_requests_are_bodyless_and_name_requests_are_json(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(InventoryOptions(page_size=3))

        for entry in mock.requests_for(LIST_OPERATION_ID):
            self.assertEqual(header_values(entry, "Content-Type"), [])
            self.assertEqual(entry["body_bytes"], b"")
            lengths = header_values(entry, "Content-Length")
            self.assertIn(lengths, ([], ["0"]))

        for entry in mock.requests_for(NAMES_OPERATION_ID):
            self.assertEqual(entry["method"], "POST")
            self.assertEqual(
                single_header(entry, "Content-Type").split(";")[0].strip(),
                "application/json",
            )
            self.assertEqual(
                single_header(entry, "Content-Length"), str(len(entry["body_bytes"]))
            )
            self.assertEqual(entry["body_bytes"].decode("utf-8"), entry["body_text"])


class TestOrdering(MockCase):
    def test_named_records_sort_by_code_point_then_entity_id(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=3))

        self.assertEqual(
            [(record.name, record.entity_id) for record in records],
            [
                ("API-gateway", P3),
                ("apiserver-0", P1),
                ("apiserver-0", P4),
                ("coredns-2", P5),
                ("Édge-router-pod", P2),
                (None, P6),
                (None, P7),
            ],
        )

    def test_order_is_independent_of_page_size(self):
        for page_size in (1, 2, 3, 5, 7, 100):
            mock = self.make_mock(pods=PODS, names=NAMES)
            client = self.make_client(mock)
            records = client.collect_pod_inventory(
                InventoryOptions(page_size=page_size, name_batch_size=2)
            )
            self.assertEqual(
                [record.entity_id for record in records],
                EXPECTED_ORDER,
                "page size %d changed the emitted order" % page_size,
            )

    def test_records_preserve_entity_type_and_time(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=4))
        by_id = {record.entity_id: record for record in records}

        self.assertEqual(by_id[P1].entity_type, "KubernetesPod")
        self.assertEqual(by_id[P1].time, 1509283414)
        self.assertIsNone(by_id[P4].time)

    def test_blank_or_missing_names_count_as_unresolved(self):
        page = {"results": [dict(PODS[0]), dict(PODS[1])], "total_count": 2}
        names = {
            "entities": [
                {"entity_id": P1, "name": ""},
                {"entity_id": P2, "name": "zeta-pod"},
            ]
        }
        mock = self.make_mock(list_script=[(200, page)], names_script=[(200, names)])
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=5))

        self.assertEqual(
            [(record.entity_id, record.name) for record in records],
            [(P2, "zeta-pod"), (P1, None)],
        )


class TestRendering(MockCase):
    def test_document_is_canonical_json_with_trailing_newline(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)
        records = client.collect_pod_inventory(InventoryOptions(page_size=3))
        rendered = render_inventory(records)

        by_id = {pod["entity_id"]: pod for pod in PODS}
        expected_payload = {
            "total_count": len(EXPECTED_ORDER),
            "pods": [
                {
                    "entity_id": entity_id,
                    "entity_type": by_id[entity_id].get("entity_type"),
                    "time": by_id[entity_id].get("time"),
                    "name": NAMES.get(entity_id),
                }
                for entity_id in EXPECTED_ORDER
            ],
        }
        expected = json.dumps(expected_payload, indent=2, ensure_ascii=False) + "\n"

        self.assertEqual(rendered, expected)
        self.assertTrue(rendered.endswith("\n"))
        self.assertIn("Édge-router-pod", rendered)

    def test_rendering_is_repeatable(self):
        records = [
            PodRecord(entity_id=P3, entity_type="KubernetesPod", time=1, name="a"),
            PodRecord(entity_id=P6, entity_type="KubernetesPod", time=None, name=None),
        ]
        self.assertEqual(render_inventory(records), render_inventory(list(records)))

    def test_empty_inventory_renders_an_empty_document(self):
        self.assertEqual(
            render_inventory([]),
            json.dumps({"total_count": 0, "pods": []}, indent=2, ensure_ascii=False) + "\n",
        )


class TestFailureHandling(MockCase):
    def test_list_failure_reports_the_operation_and_api_error_body(self):
        body = {"code": 500, "message": "collector unavailable"}
        mock = self.make_mock(list_script=[(500, body)])
        client = self.make_client(mock)

        with self.assertRaises(APIError) as caught:
            client.collect_pod_inventory(InventoryOptions(page_size=3))
        error = caught.exception
        self.assertEqual(error.operation_id, LIST_OPERATION_ID)
        self.assertEqual(error.status_code, 500)
        self.assertEqual(error.code, 500)
        self.assertEqual(error.message, "collector unavailable")
        self.assertEqual(mock.requests_for(NAMES_OPERATION_ID), [])

    def test_name_failure_reports_the_names_operation(self):
        page = {"results": [dict(PODS[0])], "total_count": 1}
        mock = self.make_mock(
            list_script=[(200, page)],
            names_script=[(503, {"code": 503, "message": "busy"})],
        )
        client = self.make_client(mock)

        with self.assertRaises(APIError) as caught:
            client.collect_pod_inventory(InventoryOptions(page_size=3))
        self.assertEqual(caught.exception.operation_id, NAMES_OPERATION_ID)
        self.assertEqual(caught.exception.status_code, 503)

    def test_rejected_token_surfaces_as_an_unauthorized_api_error(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock, token="wrong-token")

        with self.assertRaises(APIError) as caught:
            client.collect_pod_inventory()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.operation_id, LIST_OPERATION_ID)

    def test_malformed_results_raise_a_protocol_error(self):
        mock = self.make_mock(list_script=[(200, {"results": "not-a-list"})])
        client = self.make_client(mock)

        with self.assertRaises(ProtocolError) as caught:
            client.collect_pod_inventory()
        self.assertEqual(caught.exception.operation_id, LIST_OPERATION_ID)

    def test_malformed_names_payload_raises_a_protocol_error(self):
        page = {"results": [dict(PODS[0])], "total_count": 1}
        mock = self.make_mock(
            list_script=[(200, page)], names_script=[(200, {"entities": 17})]
        )
        client = self.make_client(mock)

        with self.assertRaises(ProtocolError) as caught:
            client.collect_pod_inventory()
        self.assertEqual(caught.exception.operation_id, NAMES_OPERATION_ID)

    def test_unreachable_appliance_raises_a_secret_safe_transport_error(self):
        probe = socket.socket()
        self.addCleanup(probe.close)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

        client = NetworksClient("http://127.0.0.1:%d" % port, TOKEN, timeout=2.0)
        with self.assertRaises(TransportError) as caught:
            client.collect_pod_inventory()
        error = caught.exception
        self.assertEqual(error.operation_id, LIST_OPERATION_ID)
        self.assertNotIn(TOKEN, str(error))
        self.assertNotIn(TOKEN, repr(error))


class TestValidation(MockCase):
    def test_bad_service_roots_and_tokens_are_refused(self):
        bad_roots = [
            "",
            "   ",
            "ftp://appliance.example.com",
            "appliance.example.com",
            "http://",
            "https://user:pass@appliance.example.com",
            "https://appliance.example.com/api/ni",
            "https://appliance.example.com/?a=b",
            "https://appliance.example.com/#frag",
            None,
            42,
        ]
        for root in bad_roots:
            with self.subTest(base_url=root):
                with self.assertRaises(ValueError):
                    NetworksClient(root, TOKEN)

        bad_tokens = ["", "   ", "tok\nen", "tok\ren", "tok\x00en", "tok\x7fen", None, 7]
        for token in bad_tokens:
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    NetworksClient("https://appliance.example.com", token)

    def test_service_root_scheme_is_case_insensitive(self):
        NetworksClient("HTTPS://appliance.example.com", TOKEN)
        NetworksClient("https://appliance.example.com/", TOKEN)

    def test_bad_options_are_refused_before_any_request(self):
        mock = self.make_mock(pods=PODS, names=NAMES)
        client = self.make_client(mock)

        bad_options = [
            InventoryOptions(page_size=0),
            InventoryOptions(page_size=-1),
            InventoryOptions(page_size=1001),
            InventoryOptions(page_size=True),
            InventoryOptions(page_size=3.0),
            InventoryOptions(page_size="10"),
            InventoryOptions(name_batch_size=0),
            InventoryOptions(name_batch_size=1001),
            InventoryOptions(name_batch_size=False),
            InventoryOptions(start_time=-1),
            InventoryOptions(end_time=-5),
            InventoryOptions(start_time=2.5),
            InventoryOptions(start_time=200, end_time=100),
        ]
        for options in bad_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    client.collect_pod_inventory(options)

        self.assertEqual(mock.requests, [], "a rejected option still hit the wire")

    def test_equal_time_bounds_are_accepted(self):
        mock = self.make_mock(pods=PODS[:1], names=NAMES)
        client = self.make_client(mock)
        client.collect_pod_inventory(
            InventoryOptions(page_size=5, start_time=1509000000, end_time=1509000000)
        )
        self.assertEqual(
            mock.requests_for(LIST_OPERATION_ID)[0]["raw_query"],
            "size=5&start_time=1509000000&end_time=1509000000",
        )


class TestContractPinning(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract()
        with open(os.path.join(ROOT, "docs", "official_sources.json"), encoding="utf-8") as fh:
            self.sources = json.load(fh)

    def test_module_constants_match_the_contract(self):
        by_id = {op["operationId"]: op for op in self.contract["operations"]}
        self.assertEqual(
            sorted(by_id), sorted([LIST_OPERATION_ID, NAMES_OPERATION_ID])
        )
        self.assertEqual(by_id[LIST_OPERATION_ID]["fullPath"], LIST_PATH)
        self.assertEqual(by_id[LIST_OPERATION_ID]["method"], "GET")
        self.assertEqual(by_id[NAMES_OPERATION_ID]["fullPath"], NAMES_PATH)
        self.assertEqual(by_id[NAMES_OPERATION_ID]["method"], "POST")

    def test_provenance_records_the_pinned_specification(self):
        self.assertEqual(
            self.sources["repositoryCommitSha"],
            self.contract["source"]["repositoryCommitSha"],
        )
        self.assertEqual(self.sources["specPath"], self.contract["source"]["specPath"])
        self.assertEqual(
            self.sources["specPath"],
            "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml",
        )
        self.assertEqual(len(self.sources["repositoryCommitSha"]), 40)
        self.assertEqual(
            sorted(self.sources["operationIds"]),
            sorted(self.contract["operationIds"]),
        )
        for operation in self.sources["operations"]:
            self.assertEqual(operation["specPath"], self.sources["specPath"])
            self.assertEqual(
                operation["repositoryCommitSha"], self.sources["repositoryCommitSha"]
            )
        self.assertFalse(self.sources["derivation"]["documentationPageUsedAsContractSource"])

    def test_list_parameter_order_is_the_specification_order(self):
        by_id = {op["operationId"]: op for op in self.contract["operations"]}
        self.assertEqual(
            [param["name"] for param in by_id[LIST_OPERATION_ID]["parameters"]],
            ["size", "cursor", "start_time", "end_time"],
        )
        for param in by_id[LIST_OPERATION_ID]["parameters"]:
            self.assertFalse(param["required"])

    def test_mock_refuses_routes_the_contract_does_not_name(self):
        import urllib.error
        import urllib.request

        mock = ContractMock(pods=PODS, names=NAMES, token=TOKEN).start()
        self.addCleanup(mock.stop)
        for path in ("/api/ni/entities/vms", "/api/ni/entities/kubernetes-nodes"):
            request = urllib.request.Request(
                mock.base_url + path, headers={"Authorization": AUTHORIZATION}
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 404)


class TestImplementationHygiene(unittest.TestCase):
    def test_client_module_imports_only_the_standard_library(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        if not stdlib:
            self.skipTest("interpreter does not expose sys.stdlib_module_names")

        allowed = set(stdlib) | {"vofn_inventory"}
        package = os.path.join(ROOT, "vofn_inventory")
        for filename in sorted(os.listdir(package)):
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(package, filename), encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(
                        root,
                        allowed,
                        "%s imports non-standard-library module %r" % (filename, root),
                    )


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
