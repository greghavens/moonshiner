#!/usr/bin/env python3
import json
import selectors
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATION = "updateSymptomDefinition"
EXPECTED_API_PATH = "/api/symptomdefinitions"
EXPECTED_WIRE_PATH = "/suite-api/api/symptomdefinitions"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def read_ready(process):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    events = selector.select(timeout=5)
    require(events, "loopback mock did not report readiness")
    line = process.stdout.readline()
    require(line, "loopback mock exited before reporting readiness")
    return json.loads(line)


def verify_provenance_and_contract():
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    require(sources["repository"] == "https://github.com/vmware/vcf-api-specs", "wrong source repository")
    require(sources["license"] == "Apache-2.0", "wrong source license")
    require(sources["tag"] == "9.0.0.0", "contract is not pinned to VCF 9.0")
    require(sources["repository_commit_sha"] == EXPECTED_SHA, "wrong 9.0.0.0 tag commit")
    require(sources["spec_path"] == EXPECTED_SPEC_PATH, "wrong specification path")
    require(sources["operationIds"] == [EXPECTED_OPERATION], "wrong operationIds provenance")

    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    require(contract["openapi"] == "3.0.1", "wrong OpenAPI version")
    require(contract["servers"] == [{"url": "/suite-api", "description": "Base path"}], "wrong server base path")
    require(set(contract["paths"]) == {EXPECTED_API_PATH}, "mock contract exposes extra paths")
    operation = contract["paths"][EXPECTED_API_PATH]["put"]
    require(operation["operationId"] == EXPECTED_OPERATION, "wrong operationId in contract")
    require(operation["requestBody"]["required"] is True, "request body must be required")
    require(set(operation["requestBody"]["content"]) == {"application/json"}, "wrong request media type")
    require(set(operation["responses"]) == {"200"}, "wrong success response contract")

    schemas = contract["components"]["schemas"]
    symptom = schemas["symptom-definition"]
    require(symptom["required"] == ["adapterKindKey", "name", "resourceKindKey", "state"], "wrong symptom required fields")
    require(set(symptom["properties"]) == {
        "id", "name", "adapterKindKey", "resourceKindKey", "waitCycles",
        "cancelCycles", "realtimeMonitoringEnabled", "state"
    }, "wrong symptom fields")
    ht = schemas["HT-condition"]
    require(ht["required"] == ["instanced", "key", "operator", "thresholdType", "valueType"], "wrong HT required fields")
    ht_properties = ht["allOf"][1]["properties"]
    require(ht_properties["value"]["type"] == "string", "HT value must be a JSON string")
    require(ht_properties["instanced"]["type"] == "boolean", "instanced must be boolean")
    require(ht_properties["thresholdType"]["enum"] == ["UNKNOWN", "STATIC", "STATKEY", "PROPERTY"], "wrong threshold types")
    security = contract["components"]["securitySchemes"]["Token-based-authorization"]
    require(security == {
        "type": "apiKey",
        "description": "Token based authorization",
        "name": "Authorization",
        "in": "header"
    }, "wrong authorization contract")


def expected_documents(case_token):
    return [
        {
            "id": f"SymptomDefinition-retry-{case_token}",
            "name": "CPU \"saturation\" \\ retry\nthreshold \u2603",
            "adapterKindKey": "VMWARE",
            "resourceKindKey": "VirtualMachine",
            "state": {
                "severity": "CRITICAL",
                "condition": {
                    "type": "CONDITION_HT",
                    "key": "cpu|demandmhz",
                    "operator": "GT_EQ",
                    "value": "95",
                    "valueType": "NUMERIC",
                    "instanced": False,
                    "thresholdType": "STATIC"
                }
            }
        },
        {
            "id": f"SymptomDefinition-second-{case_token}",
            "name": "Memory\bpressure\fwith\rcontrols\tand \u0001 emoji \U0001f680",
            "adapterKindKey": f"CUSTOM-{case_token}",
            "resourceKindKey": "HostSystem",
            "state": {
                "severity": "WARNING",
                "condition": {
                    "type": "CONDITION_HT",
                    "key": f"mem|host_usagePct/{case_token}",
                    "operator": "LT",
                    "value": "12.5",
                    "valueType": "NUMERIC",
                    "instanced": False,
                    "thresholdType": "STATIC"
                }
            }
        }
    ]


def verify_wire_log(log_path, port, case_token):
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    require(len(records) == 4, f"expected two failed responses and two retries, got {len(records)} requests")

    for document_index, document in enumerate(expected_documents(case_token)):
        pair = records[document_index * 2:document_index * 2 + 2]
        for retry_index, record in enumerate(pair, start=1):
            number = document_index * 2 + retry_index
            require(record["operationId"] == EXPECTED_OPERATION, f"request {number}: wrong operation")
            require(record["method"] == "PUT", f"request {number}: wrong method")
            require(record["path"] == EXPECTED_WIRE_PATH, f"request {number}: wrong path or query")
            require(record["valid"] is True and record["validationError"] is None,
                    f"request {number}: mock rejected request: {record['validationError']}")
            require(json.loads(record["body"]) == document,
                    f"request {number}: supplied fields or contract types differ")
            require(record["entityCount"] == document_index + 1,
                    f"request {number}: retry duplicated the mutation effect")

            headers = record["headers"]
            require(headers.get("host") == f"127.0.0.1:{port}", f"request {number}: wrong host")
            require(headers.get("authorization") == f"OpsToken integration-{case_token}",
                    f"request {number}: wrong authorization")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            require(content_type == "application/json", f"request {number}: wrong Content-Type")
            accept_types = {item.split(";", 1)[0].strip().lower()
                            for item in headers.get("accept", "").split(",")}
            require("application/json" in accept_types, f"request {number}: wrong Accept")

            parsed = json.loads(record["body"])
            require(set(parsed) == {"id", "name", "adapterKindKey", "resourceKindKey", "state"},
                    f"request {number}: optional symptom fields were emitted")
            require(set(parsed["state"]) == {"severity", "condition"},
                    f"request {number}: optional state fields were emitted")
            require(set(parsed["state"]["condition"]) == {
                "type", "key", "operator", "value", "valueType", "instanced", "thresholdType"
            }, f"request {number}: optional condition fields were emitted")

        require(pair[0]["bodyBase64"] == pair[1]["bodyBase64"],
                f"request pair {document_index + 1}: retry was not byte-for-byte identical")


def main():
    verify_provenance_and_contract()
    with tempfile.TemporaryDirectory(prefix="vcf90-0079-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        log_path = temp_dir / "requests.jsonl"
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests" / "mock_vcf_operations.py"),
                "--contract", str(ROOT / "docs" / "contract.json"),
                "--log", str(log_path)
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        try:
            ready = read_ready(server)
            require(ready["host"] == "127.0.0.1", "mock must bind to loopback")
            port = int(ready["port"])
            case_token = f"case-{port}"

            subprocess.run(
                [
                    "javac", "-encoding", "UTF-8", "-d", str(temp_dir),
                    str(ROOT / "VcfOperationsClient.java"),
                    str(ROOT / "TestMain.java")
                ],
                cwd=ROOT,
                check=True,
                timeout=20
            )
            result = subprocess.run(
                [
                    "java", "-cp", str(temp_dir), "TestMain",
                    f"http://127.0.0.1:{port}", case_token
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=20
            )
            require(result.stdout.strip() == "TestMain passed", "TestMain did not complete")
            verify_wire_log(log_path, port, case_token)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    print("verification passed")


if __name__ == "__main__":
    main()
