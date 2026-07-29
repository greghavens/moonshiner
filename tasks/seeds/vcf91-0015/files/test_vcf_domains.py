"""Protected acceptance verifier for vcf_domain_inventory.

All HTTP traffic is restricted to the contract-pinned loopback mock.
"""

import io
import json
import os

from test_support.mock_sddc import CONTRACT, MockSddcManager
from vcf_domain_inventory import (
    PaginationError,
    SddcApiError,
    SddcClient,
    export_domains,
)


ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, "docs", "official_sources.json"), encoding="utf-8") as h:
    SOURCES = json.load(h)

TOKEN = "fixture-token-vcf91"
CHECKS = 0

DOMAINS = [
    {
        "id": "domain-04",
        "name": "charlie",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
        "customField": {"preserve": True},
    },
    {
        "id": "domain-02",
        "name": "Alpha",
        "type": "MANAGEMENT",
        "status": "ACTIVE",
        "isManagementSsoDomain": True,
        "owners": ["ops"],
    },
    {
        "id": "domain-01",
        "name": "alpha",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
        "vcenters": [
            {
                "fqdn": "vc-alpha.lab.local",
                "instanceId": "instance/01",
            }
        ],
    },
    {
        "id": "domain-03",
        "name": "Bravo",
        "type": "VI",
        "status": "UPGRADING",
        "isManagementSsoDomain": False,
    },
    {
        "id": "domain-05",
        "name": "Délta",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
    },
]


def check(condition, label):
    global CHECKS
    CHECKS += 1
    assert condition, "FAIL: " + label


def expect_raises(exception_type, callable_, label):
    try:
        callable_()
    except exception_type as exc:
        check(True, label)
        return exc
    except Exception as exc:
        raise AssertionError(
            "FAIL: %s (raised %s instead of %s)"
            % (label, type(exc).__name__, exception_type.__name__)
        ) from exc
    raise AssertionError("FAIL: %s (did not raise)" % label)


def canonical_tie(domain):
    return json.dumps(
        domain, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def expected_order(domains):
    return sorted(
        domains,
        key=lambda domain: (
            domain.get("name", "").casefold()
            if isinstance(domain.get("name"), str)
            else "",
            domain.get("id", "") if isinstance(domain.get("id"), str) else "",
            canonical_tie(domain),
        ),
    )


def verify_provenance():
    operation = CONTRACT["operations"]
    check(len(operation) == 1, "contract names exactly one operation")
    check(operation[0]["operationId"] == "getDomains", "exact operationId")
    check(operation[0]["method"] == "GET", "getDomains method")
    check(operation[0]["path"] == "/v1/domains", "getDomains path")
    check(
        [item["name"] for item in operation[0]["query_parameters"]]
        == [
            "type",
            "name",
            "vcFqdn",
            "vcInstanceId",
            "isManagementSsoDomain",
            "pageNumber",
            "pageSize",
            "useCache",
        ],
        "query parameters extracted in specification order",
    )
    check(
        SOURCES["repository"]["commit_sha"]
        == "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
        "official source commit is pinned",
    )
    check(
        SOURCES["specification"]["path"]
        == "specifications/sddc-manager/sddc-manager-openapi.json",
        "official source specification path",
    )
    check(
        SOURCES["repository"]["name"] == "vmware/vcf-api-specs"
        and SOURCES["repository"]["license"]["spdx"] == "Apache-2.0"
        and SOURCES["specification"]["version"] == "9.1.0.0",
        "official repository, license, and VCF specification version",
    )
    check(
        [item["operationId"] for item in SOURCES["operations"]] == ["getDomains"],
        "official sources records every operationId",
    )


def verify_complete_export_and_unset_omission():
    with MockSddcManager(DOMAINS) as mock:
        client = SddcClient(mock.base_url + "/", TOKEN, timeout=2)
        output = io.StringIO()
        result = export_domains(client, output, page_size=2)

        expected = expected_order(DOMAINS)
        check(result == expected, "all pages returned in stable order")
        check(
            json.loads(output.getvalue()) == expected,
            "export contains the complete ordered objects",
        )
        check(
            output.getvalue()
            == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "export has canonical formatting and one newline",
        )

        log = list(mock.request_log)
        check(len(log) == 3, "three pages fetched")
        check(
            [entry["target"] for entry in log]
            == [
                "/v1/domains?pageNumber=0&pageSize=2",
                "/v1/domains?pageNumber=1&pageSize=2",
                "/v1/domains?pageNumber=2&pageSize=2",
            ],
            "exact unfiltered request targets and no empty optionals",
        )
        for request in log:
            check(request["method"] == "GET", "request method is GET")
            check(request["body"] == b"", "GET request has no body")
            check(
                request["headers"].get("authorization") == "Bearer " + TOKEN,
                "bearer authorization header",
            )
            check(
                request["headers"].get("accept") == "application/json",
                "JSON accept header",
            )
            check(
                "content-type" not in request["headers"],
                "GET omits content type",
            )
            query_keys = [key for key, _ in request["query_pairs"]]
            check(
                query_keys == ["pageNumber", "pageSize"],
                "all unset optional fields are omitted",
            )


def verify_missing_sort_fields():
    unusual = [
        {"id": "z-last", "name": None, "status": "ACTIVE"},
        {"id": "a-first", "name": 7, "status": "ACTIVE"},
        {"status": "ACTIVE", "opaque": {"still": "preserved"}},
    ]
    with MockSddcManager(unusual) as mock:
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        output = io.StringIO()
        result = export_domains(client, output, page_size=10)
        check(
            result == expected_order(unusual),
            "missing and non-string sort fields follow the stable policy",
        )
        check(
            json.loads(output.getvalue()) == expected_order(unusual),
            "unusual complete Domain objects are preserved",
        )


def verify_filter_wire_shape():
    with MockSddcManager(DOMAINS) as mock:
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        result = client.list_domains(
            domain_type="VI",
            name="alpha",
            vc_fqdn="vc-alpha.lab.local",
            vc_instance_id="instance/01",
            is_management_sso_domain=False,
            use_cache=False,
            page_size=10,
        )
        check(result == [DOMAINS[2]], "explicit filters still decode a page")
        check(len(mock.request_log) == 1, "filtered collection uses one page")
        check(
            mock.request_log[0]["target"]
            == (
                "/v1/domains?type=VI&name=alpha&vcFqdn=vc-alpha.lab.local"
                "&vcInstanceId=instance%2F01&isManagementSsoDomain=false"
                "&pageNumber=0&pageSize=10&useCache=false"
            ),
            "exact filter names, order, escaping, and false encoding",
        )


def verify_local_validation():
    with MockSddcManager(DOMAINS) as mock:
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            ValueError,
            lambda: client.list_domains(name="", page_size=2),
            "empty string filter rejected",
        )
        expect_raises(
            ValueError,
            lambda: client.list_domains(is_management_sso_domain=0, page_size=2),
            "non-boolean filter rejected",
        )
        expect_raises(
            ValueError,
            lambda: client.list_domains(page_size=True),
            "boolean page size rejected",
        )
        expect_raises(
            ValueError,
            lambda: client.list_domains(page_size=0),
            "non-positive page size rejected",
        )
        expect_raises(
            ValueError,
            lambda: client.list_domains(page_size=2.0),
            "non-integer page size rejected",
        )
        check(mock.request_log == [], "local validation happens before HTTP")


def verify_api_error():
    payload = {
        "errorCode": "VCF_SYSTEM_ERROR",
        "message": "Inventory read failed",
        "referenceToken": "REF-41A",
    }
    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(500, payload)
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        exc = expect_raises(
            SddcApiError,
            lambda: client.list_domains(page_size=2),
            "HTTP error decoded",
        )
        check(exc.status_code == 500, "API error status")
        check(exc.error_code == "VCF_SYSTEM_ERROR", "API error code")
        check(exc.message == "Inventory read failed", "API error message")
        check(exc.payload == payload, "API error payload preserved")
        check(
            "500" in str(exc)
            and "VCF_SYSTEM_ERROR" in str(exc)
            and "Inventory read failed" in str(exc),
            "API error string is useful",
        )


def verify_pagination_error():
    malformed = {
        "elements": [DOMAINS[0]],
        "pageMetadata": {
            "pageNumber": 1,
            "pageSize": 1,
            "totalElements": 1,
            "totalPages": 1,
        },
    }
    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(200, malformed)
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=2),
            "mismatched returned page rejected",
        )

    incomplete = {
        "elements": [],
        "pageMetadata": {
            "pageNumber": 0,
            "pageSize": 0,
            "totalElements": 1,
            "totalPages": 1,
        },
    }
    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(200, incomplete)
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=2),
            "incomplete collection rejected",
        )

    wrong_size = {
        "elements": [DOMAINS[0]],
        "pageMetadata": {
            "pageNumber": 0,
            "pageSize": 2,
            "totalElements": 1,
            "totalPages": 1,
        },
    }
    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(200, wrong_size)
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=2),
            "pageSize inconsistent with current elements rejected",
        )

    boolean_metadata = {
        "elements": [],
        "pageMetadata": {
            "pageNumber": 0,
            "pageSize": 0,
            "totalElements": False,
            "totalPages": 0,
        },
    }
    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(200, boolean_metadata)
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=2),
            "boolean pagination integer rejected",
        )

    with MockSddcManager(DOMAINS) as mock:
        mock.queue_response(
            200,
            {
                "elements": [DOMAINS[0]],
                "pageMetadata": {
                    "pageNumber": 0,
                    "pageSize": 1,
                    "totalElements": 2,
                    "totalPages": 2,
                },
            },
        )
        mock.queue_response(
            200,
            {
                "elements": [DOMAINS[1]],
                "pageMetadata": {
                    "pageNumber": 1,
                    "pageSize": 1,
                    "totalElements": 3,
                    "totalPages": 2,
                },
            },
        )
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=1),
            "totals changing between pages rejected",
        )

    with MockSddcManager(DOMAINS) as mock:
        mock.queue_raw_response(200, b"{this is not JSON")
        client = SddcClient(mock.base_url, TOKEN, timeout=2)
        expect_raises(
            PaginationError,
            lambda: client.list_domains(page_size=2),
            "malformed success JSON rejected as PaginationError",
        )


def main():
    verify_provenance()
    verify_complete_export_and_unset_omission()
    verify_missing_sort_fields()
    verify_filter_wire_shape()
    verify_local_validation()
    verify_api_error()
    verify_pagination_error()
    print("PASS: %d VCF 9.1 domain inventory checks" % CHECKS)


if __name__ == "__main__":
    main()
