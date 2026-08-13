#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0202."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = (
    ROOT / "src" / "VcfInstaller.Deployment" / "VcfInstaller.Deployment.psd1"
)
MODULE_PATH = (
    ROOT / "src" / "VcfInstaller.Deployment" / "VcfInstaller.Deployment.psm1"
)
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_cases.ps1"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATION_IDS = ["validateSddcSpec", "deploySddc"]
MODULE_NAME = "VMware.Sdk.Vcf.Installer"
MODULE_VERSION = "13.5.0.25380678"
ROOT_PROPERTIES = [
    "sddcId",
    "workflowType",
    "hostSpecs",
    "version",
    "vcenterSpec",
    "clusterSpec",
    "dvsSpecs",
    "nsxtSpec",
    "networkSpecs",
    "dnsSpec",
    "ntpServers",
    "sddcManagerSpec",
    "managementPoolName",
    "ceipEnabled",
    "skipEsxThumbprintValidation",
    "skipGatewayPingValidation",
    "securitySpec",
    "datastoreSpec",
    "vspClusterSpec",
    "fleetLcmSpec",
    "sddcLcmSpec",
    "fleetDepotSpec",
    "telemetryAcceptorSpec",
    "vidbSpec",
    "saltSpec",
    "saltRaasSpec",
    "vcfOperationsSpec",
    "vcfOperationsCollectorSpec",
    "vcfAutomationSpec",
    "vcfManagementComponentsInfrastructureSpec",
    "licenseServerSpec",
    "vcfInstanceName",
]
VCENTER_PROPERTIES = [
    "vcenterHostname",
    "rootVcenterPassword",
    "vmSize",
    "storageSize",
    "ssoDomain",
    "adminUserSsoUsername",
    "adminUserSsoPassword",
    "version",
    "useExistingDeployment",
    "sslThumbprint",
]
NETWORK_PROPERTIES = [
    "networkType",
    "subnet",
    "gateway",
    "subnetMask",
    "includeIpAddress",
    "includeIpAddressRanges",
    "vlanId",
    "mtu",
    "teamingPolicy",
    "activeUplinks",
    "standbyUplinks",
    "portGroupKey",
    "ipAddressVersion",
    "ipAddressAssignmentMode",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF API version changed")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations]
        == [
            ("POST", "/v1/sddcs/validations"),
            ("POST", "/v1/sddcs"),
        ],
        "contract routes changed",
    )
    validate, deploy = operations
    require(validate.get("parameters") == [], "precheck must have no parameters")
    for item, schema in ((validate, "Validation"), (deploy, "SddcTask")):
        require(
            item.get("requestBody")
            == {
                "required": True,
                "contentType": "application/json",
                "schema": "SddcSpec",
            },
            f"{item.get('operationId')} request body changed",
        )
        success_code = "200" if item is validate else "202"
        require(
            item.get("responses", {}).get(success_code, {}).get("schema") == schema,
            f"{item.get('operationId')} success schema changed",
        )
    require(
        deploy.get("parameters")
        == [
            {
                "name": "skipValidations",
                "in": "query",
                "required": False,
                "schema": {"type": "boolean", "default": False},
            }
        ],
        "deploySddc optional query projection changed",
    )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("SddcSpec", {}).get("required")
        == ["dnsSpec", "networkSpecs", "sddcId", "vcenterSpec"],
        "SddcSpec required fields changed",
    )
    require(
        list(schemas.get("SddcSpec", {}).get("properties", {})) == ROOT_PROPERTIES,
        "SddcSpec direct property projection changed",
    )
    require(
        schemas.get("DnsSpec", {}).get("required") == ["subdomain"]
        and list(schemas.get("DnsSpec", {}).get("properties", {}))
        == ["subdomain", "nameservers"],
        "DnsSpec projection changed",
    )
    require(
        schemas.get("SddcVcenterSpec", {}).get("required")
        == ["rootVcenterPassword", "vcenterHostname"]
        and list(schemas.get("SddcVcenterSpec", {}).get("properties", {}))
        == VCENTER_PROPERTIES,
        "SddcVcenterSpec projection changed",
    )
    require(
        schemas.get("SddcNetworkSpec", {}).get("required")
        == ["networkType", "vlanId"]
        and list(schemas.get("SddcNetworkSpec", {}).get("properties", {}))
        == NETWORK_PROPERTIES,
        "SddcNetworkSpec projection changed",
    )
    require(
        schemas.get("Validation", {}).get("required")
        == ["description", "executionStatus", "id", "resultStatus"],
        "Validation required fields changed",
    )
    require(
        schemas.get("SddcTask", {}).get("required")
        == ["creationTimestamp", "status"],
        "SddcTask required fields changed",
    )
    profile = contract.get("exerciseWireProfile", {})
    require(
        profile
        == {
            "precheckOperationId": "validateSddcSpec",
            "mutatingOperationId": "deploySddc",
            "requestBodySchema": "SddcSpec",
            "requiredRootMembers": [
                "sddcId",
                "vcenterSpec",
                "networkSpecs",
                "dnsSpec",
            ],
            "requiredVcenterMembers": [
                "vcenterHostname",
                "rootVcenterPassword",
            ],
            "requiredNetworkMembers": ["networkType", "vlanId"],
            "requiredDnsMembers": ["subdomain"],
            "unsetBehavior": "omit",
            "deployQueryMembers": [],
            "reuseIdenticalModel": True,
        },
        "exercise wire profile changed",
    )

    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "official source URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("specLine"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (
                "validateSddcSpec",
                "POST",
                "/v1/sddcs/validations",
                552,
                COMMIT,
                SPEC_PATH,
            ),
            ("deploySddc", "POST", "/v1/sddcs", 283, COMMIT, SPEC_PATH),
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "a documentation page must not be the contract source",
    )


def run_pwsh(command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def verify_manifest_and_sdk() -> None:
    command = (
        f"$d=Import-PowerShellDataFile -LiteralPath {ps_quote(str(MANIFEST_PATH))};"
        "if($d.RootModule -cne 'VcfInstaller.Deployment.psm1'){exit 2};"
        "if($d.PowerShellVersion -cne '7.4'){exit 3};"
        "if(($d.FunctionsToExport -join ',') -cne "
        "'Start-VcfInstallerValidatedSddcDeployment'){exit 4};"
        "if($d.RequiredModules.Count -ne 1){exit 5};"
        "$r=$d.RequiredModules[0];"
        f"if($r.ModuleName -cne '{MODULE_NAME}' -or "
        f"[string]$r.RequiredVersion -cne '{MODULE_VERSION}'){{exit 6}};"
        f"Import-Module '{MODULE_NAME}' -RequiredVersion '{MODULE_VERSION}' "
        "-Force -ErrorAction Stop;"
        "$expected=@{"
        "validateSddcSpec=@('POST','/v1/sddcs/validations','Invoke-VcfInstallerValidateSddcSpec');"
        "deploySddc=@('POST','/v1/sddcs','Invoke-VcfInstallerDeploySddc')};"
        "foreach($id in @('validateSddcSpec','deploySddc')){"
        "$o=@(Get-VcfInstallerOperation -Name $id);"
        "if($o.Count -ne 1 -or $null -eq $o[0].CommandInfo){exit 7};"
        "if($o[0].Method -cne $expected[$id][0] "
        "-or $o[0].Path -cne $expected[$id][1]){exit 8};"
        "$c=Get-Command $o[0].CommandInfo.Name -CommandType Cmdlet -ErrorAction Stop;"
        f"if($c.Source -cne '{MODULE_NAME}' -or "
        "$c.Name -cne $expected[$id][2]){exit 9}"
        "}"
    )
    result = run_pwsh(command)
    require(
        result.returncode == 0,
        "protected manifest or genuine Installer SDK prerequisite is invalid: "
        + (result.stderr.strip() or result.stdout.strip()),
    )


def verify_solution_shape() -> None:
    require(MODULE_PATH.is_file(), "create VcfInstaller.Deployment.psm1")
    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    for operation_id in OPERATION_IDS:
        require(
            operation_id in source,
            f"implementation must name exact operationId {operation_id}",
        )
    for required in (
        "get-vcfinstalleroperation",
        "validatesddcspec",
        "deploysddc",
        "commandinfo",
    ):
        require(
            required in folded,
            f"implementation does not use required SDK surface: {required}",
        )
    for forbidden in (
        "invoke-webrequest",
        "invoke-restmethod",
        "system.net.http",
        "httpclient",
        "webclient",
        "tcpclient",
        "system.net.sockets",
        "start-process",
        "curl",
        "wget",
        "invoke-vcfinstallervalidatesddcspec",
        "invoke-vcfinstallerdeploysddc",
        "connect-vcfinstallerserver",
        "disconnect-vcfinstallerserver",
    ):
        require(
            forbidden not in folded,
            f"implementation bypasses operation resolution or caller ownership: {forbidden}",
        )
    require(
        folded.count("export-modulemember") == 1
        and "export-modulemember -function start-vcfinstallervalidatedsddcdeployment"
        in folded,
        "module must export exactly the requested function",
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and (
            path.suffix.casefold() in {".dll", ".nupkg", ".snupkg", ".zip"}
            or path.name.casefold().startswith("vmware.sdk.vcf")
        )
    ]
    require(not vendored, "the seed must not vendor VMware or binary dependencies")


def make_spec(sddc_id: str, password: str) -> dict[str, Any]:
    return {
        "sddcId": sddc_id,
        "vcenterSpec": {
            "vcenterHostname": f"{sddc_id}-vc.lab.example",
            "rootVcenterPassword": password,
        },
        "networkSpecs": [{"networkType": "MANAGEMENT", "vlanId": 0}],
        "dnsSpec": {"subdomain": "lab.example"},
    }


def make_scenario() -> dict[str, Any]:
    suffix = secrets.token_hex(3)
    passed_id = "good-" + secrets.token_hex(4)
    password = "VcF9!" + secrets.token_hex(5)
    rejected_statuses: list[tuple[str, str | None, str | None]] = [
        ("failed", "COMPLETED", "FAILED"),
        ("warning", "COMPLETED", "WARNING"),
        ("progress", "IN_PROGRESS", "SUCCEEDED"),
        ("unknown", "COMPLETED", "MYSTERY"),
        ("blank", "COMPLETED", "   "),
        ("malformed", None, None),
    ]
    rejected_cases = []
    for name, execution_status, result_status in rejected_statuses:
        sddc_id = f"{name}-{suffix}"
        item: dict[str, Any] = {
            "name": name,
            "validationId": str(uuid.uuid4()),
            "spec": make_spec(sddc_id, password),
            "executionStatus": execution_status,
            "resultStatus": result_status,
        }
        rejected_cases.append(item)
    return {
        "token": "access-" + secrets.token_urlsafe(24),
        "taskId": str(uuid.uuid4()),
        "passedValidationId": str(uuid.uuid4()),
        "passedSddcId": passed_id,
        "vcenterPassword": password,
        "rejectedCases": rejected_cases,
        "passedSpec": make_spec(passed_id, password),
    }


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise VerificationError(
                "loopback mock exited during startup: " + (stderr or stdout).strip()
            )
        if port_file.is_file():
            value = port_file.read_text(encoding="utf-8").strip()
            if value:
                return int(value)
        time.sleep(0.04)
    raise VerificationError("loopback mock did not publish its port")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(request: dict[str, Any], name: str) -> str:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(len(values) == 1, f"{name} must occur exactly once")
    return values[0]


def verify_body_shape(body: dict[str, Any], expected: dict[str, Any]) -> None:
    require(body == expected, "request JSON values or exact member set changed")
    require(
        list(body) == ["sddcId", "vcenterSpec", "networkSpecs", "dnsSpec"],
        "SddcSpec wire member order changed",
    )
    require(
        list(body["vcenterSpec"]) == ["vcenterHostname", "rootVcenterPassword"],
        "unset SddcVcenterSpec members were not omitted",
    )
    require(
        len(body["networkSpecs"]) == 1
        and list(body["networkSpecs"][0]) == ["networkType", "vlanId"],
        "unset SddcNetworkSpec members were not omitted",
    )
    require(
        body["networkSpecs"][0]["vlanId"] == 0,
        "required zero-valued vlanId was lost",
    )
    require(
        list(body["dnsSpec"]) == ["subdomain"],
        "unset DnsSpec nameservers was not omitted",
    )
    require(
        not (set(body) & (set(ROOT_PROPERTIES) - set(expected))),
        "an unset optional SddcSpec property was serialized",
    )
    require(
        not (
            set(body["vcenterSpec"])
            & (set(VCENTER_PROPERTIES) - set(expected["vcenterSpec"]))
        ),
        "an unset optional SddcVcenterSpec property was serialized",
    )
    require(
        not (
            set(body["networkSpecs"][0])
            & (set(NETWORK_PROPERTIES) - set(expected["networkSpecs"][0]))
        ),
        "an unset optional SddcNetworkSpec property was serialized",
    )


def verify_runtime() -> None:
    require(shutil.which("pwsh") is not None, "pwsh is required")
    scenario = make_scenario()
    with tempfile.TemporaryDirectory(prefix="vcf91-0202-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        result_file = temp / "result.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":")), encoding="utf-8"
        )
        environment = {
            **os.environ,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        }
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(request_log),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        try:
            port = wait_for_port(port_file, server)
            invocation = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-ModuleManifest",
                    str(MANIFEST_PATH),
                    "-BaseUri",
                    f"http://127.0.0.1:{port}/",
                    "-Token",
                    scenario["token"],
                    "-ScenarioPath",
                    str(scenario_file),
                    "-PassedSddcId",
                    scenario["passedSddcId"],
                    "-VcenterPassword",
                    scenario["vcenterPassword"],
                    "-ExpectedTaskId",
                    scenario["taskId"],
                    "-OutputPath",
                    str(result_file),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=70,
                check=False,
                env=environment,
            )
            require(
                invocation.returncode == 0,
                "PowerShell acceptance cases failed: "
                + (invocation.stderr.strip() or invocation.stdout.strip()),
            )
            require(result_file.is_file(), "PowerShell did not write its result")
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

        result = load_json(result_file)
        requests = read_log(request_log)

    require(
        result
        == {
            "rejectedPrecheckExceptionTypes": [
                "System.InvalidOperationException"
                for _ in scenario["rejectedCases"]
            ],
            "successObjectCount": 1,
            "taskId": scenario["taskId"],
            "taskName": "Protected validated deployment",
            "taskStatus": "IN_PROGRESS",
            "connectionType": "VMware.Sdk.Vcf.Installer.VcfInstallerServerImpl",
        },
        "function result, fail-closed exception, or success pipeline changed",
    )
    serialized_result = json.dumps(result, separators=(",", ":"))
    for secret in (scenario["token"], scenario["vcenterPassword"]):
        require(secret not in serialized_result, "function output exposes a secret")

    validation_count = len(scenario["rejectedCases"]) + 1
    require(
        len(requests) == validation_count + 1,
        "wire request count must equal all prechecks plus one deployment",
    )
    require(
        [request.get("operationId") for request in requests]
        == ["validateSddcSpec"] * validation_count + ["deploySddc"],
        "precheck gate or exact operation order changed",
    )
    require(
        [request.get("responseStatus") for request in requests]
        == [200] * validation_count + [202],
        "protected precheck/deployment responses changed",
    )
    require(
        [request.get("rawTarget") for request in requests]
        == ["/v1/sddcs/validations"] * validation_count + ["/v1/sddcs"],
        "raw targets changed or an unset query parameter was serialized",
    )
    require(
        sum(request.get("operationId") == "deploySddc" for request in requests) == 1,
        "failed precheck caused a mutation or successful deployment was repeated",
    )

    expected_specs = [
        *[item["spec"] for item in scenario["rejectedCases"]],
        scenario["passedSpec"],
        scenario["passedSpec"],
    ]
    raw_bodies: list[str] = []
    for index, (request, expected) in enumerate(zip(requests, expected_specs)):
        require(request.get("method") == "POST", f"request {index} method changed")
        require(request.get("rawQuery") == "", f"request {index} query must be absent")
        expected_raw = json.dumps(expected, separators=(",", ":"))
        require(
            request.get("body") == expected_raw,
            f"request {index} body bytes or property order changed",
        )
        require(
            request.get("bodyLength") == len(expected_raw.encode("utf-8")),
            f"request {index} Content-Length/body length changed",
        )
        body = json.loads(request["body"])
        verify_body_shape(body, expected)
        raw_bodies.append(request["body"])

        require(
            one_header(request, "authorization") == f"Bearer {scenario['token']}",
            f"request {index} did not use the caller's SDK connection token",
        )
        content_type = one_header(request, "content-type").split(";", 1)[0].strip()
        require(
            content_type.casefold() == "application/json",
            f"request {index} Content-Type is not application/json",
        )
        require(
            "application/json" in one_header(request, "accept").casefold(),
            f"request {index} does not accept JSON",
        )

    require(
        raw_bodies[-2] == raw_bodies[-1],
        "deployment did not reuse the exact successfully validated SddcSpec",
    )


def main() -> int:
    verify_contract()
    verify_manifest_and_sdk()
    verify_solution_shape()
    verify_runtime()
    print(
        "PASS: failed VCF precheck caused no mutation and successful precheck gated one exact deployment"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
