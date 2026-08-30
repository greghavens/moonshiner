#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for the VCF Installer task."""

from __future__ import annotations

import ast
import importlib
import json
import sys
from dataclasses import is_dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mock_vcf_installer import running_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
FIXTURE_PATH = ROOT / "tests" / "fixture.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract_and_provenance() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    expected_ids = {"validateSddcSpec", "getSddcSpecValidation", "deploySddc"}

    require(contract["openapi"] == "3.0.1", "contract must retain its OpenAPI version")
    require(contract["info"]["version"] == "9.1.0.0", "contract must be for VCF 9.1")
    require(set(contract["operations"]) == expected_ids, "contract operation set changed")
    require(set(sources["operationIds"]) == expected_ids, "source operationIds do not match contract")
    require(
        sources["specPath"] == "specifications/vcf-installer/vcf-installer-openapi.json",
        "official source must name the repository specification path",
    )
    require(
        sources["commitSha"] == "c3f3b52c845dd967cabbc21680e893292077d5ba",
        "official source commit is not pinned",
    )
    require(sources["license"] == "Apache-2.0", "official source license changed")

    triples = {
        (entry["operationId"], entry["method"], entry["path"])
        for entry in contract["operations"].values()
    }
    require(
        triples
        == {
            ("validateSddcSpec", "POST", "/v1/sddcs/validations"),
            ("getSddcSpecValidation", "GET", "/v1/sddcs/validations/{id}"),
            ("deploySddc", "POST", "/v1/sddcs"),
        },
        "operation method/path wire contract changed",
    )


def verify_stdlib_only() -> None:
    package_dir = ROOT / "vcf_installer"
    require(package_dir.is_dir(), "vcf_installer package is missing")
    sources = list(package_dir.rglob("*.py"))
    require(bool(sources), "vcf_installer package has no Python modules")

    stdlib = set(sys.stdlib_module_names)
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top_level = name.split(".", 1)[0]
                require(
                    top_level in stdlib or top_level == "vcf_installer",
                    f"third-party import is not allowed: {name} in {source.relative_to(ROOT)}",
                )


def import_public_api():
    sys.path.insert(0, str(ROOT))
    package = importlib.import_module("vcf_installer")
    names = (
        "VcfInstallerClient",
        "SddcSpec",
        "DnsSpec",
        "VcenterSpec",
        "NetworkSpec",
        "PrecheckFailed",
        "VcfInstallerError",
    )
    missing = [name for name in names if not hasattr(package, name)]
    require(not missing, f"missing public API exports: {', '.join(missing)}")
    return package


def make_spec(package, fixture: dict[str, object]):
    vcenter = fixture["vcenterSpec"]
    dns = fixture["dnsSpec"]
    networks = fixture["networkSpecs"]
    return package.SddcSpec(
        sddc_id=fixture["sddcId"],
        vcenter_spec=package.VcenterSpec(
            vcenter_hostname=vcenter["vcenterHostname"],
            root_vcenter_password=vcenter["rootVcenterPassword"],
        ),
        network_specs=[
            package.NetworkSpec(
                network_type=network["networkType"],
                vlan_id=network["vlanId"],
            )
            for network in networks
        ],
        dns_spec=package.DnsSpec(subdomain=dns["subdomain"]),
        ceip_enabled=fixture["ceipEnabled"],
    )


def verify_json_post(
    record,
    expected_path: str,
    expected_payload: dict[str, object],
    *,
    expected_query: dict[str, list[str]] | None = None,
    verify_unset_omission: bool = True,
) -> None:
    parsed_target = urlsplit(record.target)
    require(record.method == "POST", f"expected POST, got {record.method}")
    require(parsed_target.path == expected_path, f"wrong request path: {record.target}")
    if expected_query is None:
        require(not parsed_target.query, f"unset query fields must be omitted: {record.target}")
    else:
        require(parse_qs(parsed_target.query) == expected_query, f"wrong request query: {record.target}")
    require(record.headers.get("authorization") == "Bearer fixture-token", "wrong bearer authorization wire value")
    require(record.headers.get("accept") == "application/json", "Accept must be application/json")
    require(
        record.headers.get("content-type") == "application/json",
        "JSON requests must have application/json content type",
    )
    require(record.headers.get("content-length") == str(len(record.body)), "Content-Length does not match body")
    actual_payload = json.loads(record.body.decode("utf-8"))
    require(actual_payload == expected_payload, f"JSON wire payload differs: {actual_payload!r}")
    if not verify_unset_omission:
        return
    require(
        {
            "workflowType",
            "version",
            "ntpServers",
            "managementPoolName",
            "skipEsxThumbprintValidation",
            "skipGatewayPingValidation",
            "vcfInstanceName",
        }.isdisjoint(actual_payload),
        "unset top-level optionals must be absent, not null or empty",
    )
    require(
        {"nameservers"}.isdisjoint(actual_payload["dnsSpec"]),
        "unset DNS optionals must be absent, not empty",
    )
    require(
        {
            "vmSize",
            "storageSize",
            "ssoDomain",
            "adminUserSsoUsername",
            "adminUserSsoPassword",
            "version",
            "useExistingDeployment",
            "sslThumbprint",
        }.isdisjoint(actual_payload["vcenterSpec"]),
        "unset vCenter optionals must be absent, not null or empty",
    )
    require(
        {
            "subnet",
            "gateway",
            "subnetMask",
            "includeIpAddress",
            "mtu",
            "teamingPolicy",
            "activeUplinks",
            "standbyUplinks",
            "portGroupKey",
            "ipAddressVersion",
            "ipAddressAssignmentMode",
        }.isdisjoint(actual_payload["networkSpecs"][0]),
        "unset network optionals must be absent, not null or empty",
    )


def verify_get(record, expected_path: str) -> None:
    parsed_target = urlsplit(record.target)
    require(record.method == "GET", f"expected GET, got {record.method}")
    require(parsed_target.path == expected_path, f"wrong polling path: {record.target}")
    require(not parsed_target.query, "poll request must not have a query")
    require(record.body == b"", "GET poll must not have a request body")
    require("content-type" not in record.headers, "GET without a body must omit Content-Type")
    require(record.headers.get("authorization") == "Bearer fixture-token", "poll request lost authorization")
    require(record.headers.get("accept") == "application/json", "poll request lost JSON Accept")


def verify_success_path(package, fixture: dict[str, object]) -> None:
    spec = make_spec(package, fixture)
    with running_mock(CONTRACT_PATH, "SUCCEEDED") as server:
        require(
            {operation_id for _, _, operation_id in server.routes}
            == set(server.contract["operations"]),
            "mock must expose only contract operations",
        )
        client = package.VcfInstallerClient(server.base_url + "/", "fixture-token", timeout=2)
        result = client.precheck_and_deploy(spec, poll_interval=0, max_polls=3)

        require(result["status"] == "IN_PROGRESS", "deployment response was not decoded")
        require(server.scenario.deployment_count == 1, "successful precheck must deploy exactly once")
        log = server.scenario.request_log
        require(
            [record.operation_id for record in log]
            == ["validateSddcSpec", "getSddcSpecValidation", "deploySddc"],
            "wrong success operation sequence",
        )
        verify_json_post(log[0], "/v1/sddcs/validations", fixture)
        verify_get(log[1], f"/v1/sddcs/validations/{server.scenario.validation_id}")
        verify_json_post(log[2], "/v1/sddcs", fixture)
        require(log[0].body == log[2].body, "validation and deployment must send the same spec wire body")


def verify_failed_gate(package, fixture: dict[str, object]) -> None:
    spec = make_spec(package, fixture)
    with running_mock(CONTRACT_PATH, "FAILED") as server:
        client = package.VcfInstallerClient(server.base_url, "fixture-token", timeout=2)
        try:
            client.precheck_and_deploy(spec, poll_interval=0, max_polls=3)
        except package.PrecheckFailed as error:
            require(error.validation["resultStatus"] == "FAILED", "exception must expose last validation")
        else:
            raise AssertionError("failed precheck did not raise PrecheckFailed")

        require(server.scenario.deployment_count == 0, "failed precheck changed deployment state")
        log = server.scenario.request_log
        require(
            [record.operation_id for record in log]
            == ["validateSddcSpec", "getSddcSpecValidation"],
            "failed precheck must stop before deploySddc",
        )
        verify_json_post(log[0], "/v1/sddcs/validations", fixture)
        verify_get(log[1], f"/v1/sddcs/validations/{server.scenario.validation_id}")


def verify_explicit_query_value(package, fixture: dict[str, object]) -> None:
    spec = make_spec(package, fixture)
    with running_mock(CONTRACT_PATH, "SUCCEEDED") as server:
        client = package.VcfInstallerClient(server.base_url, "fixture-token", timeout=2)
        client.deploy_sddc(spec, skip_validations=False)
        require(server.scenario.deployment_count == 1, "direct deploy response was not handled")
        record = server.scenario.request_log[0]
        verify_json_post(
            record,
            "/v1/sddcs",
            fixture,
            expected_query={"skipValidations": ["false"]},
        )


def make_full_spec(package):
    expected = {
        "sddcId": "chi02-m02",
        "vcenterSpec": {
            "vcenterHostname": "vcsa02.example.com",
            "rootVcenterPassword": "StrongPassword2!",
            "vmSize": "medium",
            "storageSize": "large",
            "ssoDomain": "vsphere.local",
            "adminUserSsoUsername": "administrator",
            "adminUserSsoPassword": "SsoPassword2!",
            "version": "9.1.0",
            "useExistingDeployment": False,
            "sslThumbprint": "AA:BB:CC",
        },
        "networkSpecs": [
            {
                "networkType": "MANAGEMENT",
                "vlanId": 0,
                "subnet": "192.0.2.0/24",
                "gateway": "192.0.2.1",
                "subnetMask": "255.255.255.0",
                "includeIpAddress": ["192.0.2.10"],
                "mtu": 1280,
                "teamingPolicy": "LOADBALANCE_SRCID",
                "activeUplinks": ["uplink1"],
                "standbyUplinks": ["uplink2"],
                "portGroupKey": "dvportgroup-10",
                "ipAddressVersion": "IPv4",
                "ipAddressAssignmentMode": "STATIC",
            }
        ],
        "dnsSpec": {
            "subdomain": "vcf.example.com",
            "nameservers": ["192.0.2.53"],
        },
        "workflowType": "VCF",
        "version": "9.1.0",
        "ntpServers": ["192.0.2.123"],
        "managementPoolName": "management-pool",
        "ceipEnabled": False,
        "skipEsxThumbprintValidation": False,
        "skipGatewayPingValidation": False,
        "vcfInstanceName": "chi02-vcf",
    }
    spec = package.SddcSpec(
        sddc_id=expected["sddcId"],
        vcenter_spec=package.VcenterSpec(
            vcenter_hostname=expected["vcenterSpec"]["vcenterHostname"],
            root_vcenter_password=expected["vcenterSpec"]["rootVcenterPassword"],
            vm_size="medium",
            storage_size="large",
            sso_domain="vsphere.local",
            admin_user_sso_username="administrator",
            admin_user_sso_password="SsoPassword2!",
            version="9.1.0",
            use_existing_deployment=False,
            ssl_thumbprint="AA:BB:CC",
        ),
        network_specs=(
            package.NetworkSpec(
                network_type="MANAGEMENT",
                vlan_id=0,
                subnet="192.0.2.0/24",
                gateway="192.0.2.1",
                subnet_mask="255.255.255.0",
                include_ip_address=("192.0.2.10",),
                mtu=1280,
                teaming_policy="LOADBALANCE_SRCID",
                active_uplinks=("uplink1",),
                standby_uplinks=("uplink2",),
                port_group_key="dvportgroup-10",
                ip_address_version="IPv4",
                ip_address_assignment_mode="STATIC",
            ),
        ),
        dns_spec=package.DnsSpec(
            subdomain="vcf.example.com",
            nameservers=("192.0.2.53",),
        ),
        workflow_type="VCF",
        version="9.1.0",
        ntp_servers=("192.0.2.123",),
        management_pool_name="management-pool",
        ceip_enabled=False,
        skip_esx_thumbprint_validation=False,
        skip_gateway_ping_validation=False,
        vcf_instance_name="chi02-vcf",
    )
    return spec, expected


def verify_models_and_direct_methods(package) -> None:
    for name in ("SddcSpec", "DnsSpec", "VcenterSpec", "NetworkSpec"):
        require(is_dataclass(getattr(package, name)), f"{name} must be a dataclass")

    spec, expected = make_full_spec(package)
    with running_mock(CONTRACT_PATH, "SUCCEEDED") as server:
        client = package.VcfInstallerClient(server.base_url, "fixture-token", timeout=2)
        submitted = client.validate_sddc_spec(spec)
        require(submitted["executionStatus"] == "IN_PROGRESS", "validation response was not decoded")
        validation = client.get_sddc_spec_validation(submitted["id"])
        require(validation["resultStatus"] == "SUCCEEDED", "poll response was not decoded")
        deployment = client.deploy_sddc(spec, skip_validations=True)
        require(deployment["status"] == "IN_PROGRESS", "deployment response was not decoded")

        log = server.scenario.request_log
        require(
            [record.operation_id for record in log]
            == ["validateSddcSpec", "getSddcSpecValidation", "deploySddc"],
            "direct REST methods used the wrong operations",
        )
        verify_json_post(log[0], "/v1/sddcs/validations", expected, verify_unset_omission=False)
        verify_get(log[1], f"/v1/sddcs/validations/{server.scenario.validation_id}")
        verify_json_post(
            log[2],
            "/v1/sddcs",
            expected,
            expected_query={"skipValidations": ["true"]},
            verify_unset_omission=False,
        )


def verify_exhausted_polling_budget(package, fixture: dict[str, object]) -> None:
    spec = make_spec(package, fixture)
    with running_mock(CONTRACT_PATH, "SUCCEEDED", complete_after=None) as server:
        client = package.VcfInstallerClient(server.base_url, "fixture-token", timeout=2)
        try:
            client.precheck_and_deploy(spec, poll_interval=0, max_polls=2)
        except package.PrecheckFailed as error:
            require(error.validation["executionStatus"] == "IN_PROGRESS", "timeout lost last validation")
            require(error.validation["id"] == server.scenario.validation_id, "timeout lost validation id")
        else:
            raise AssertionError("exhausted polling budget did not raise PrecheckFailed")

        require(server.scenario.poll_count == 2, "max_polls did not bound follow-up requests")
        require(server.scenario.deployment_count == 0, "exhausted precheck deployed the SDDC")
        require(
            [record.operation_id for record in server.scenario.request_log]
            == ["validateSddcSpec", "getSddcSpecValidation", "getSddcSpecValidation"],
            "exhausted precheck used the wrong operation sequence",
        )


def verify_client_error_translation(package, fixture: dict[str, object]) -> None:
    spec = make_spec(package, fixture)
    for response_mode in ("http_error", "malformed_json", "json_array", "disconnect"):
        with running_mock(CONTRACT_PATH, "SUCCEEDED", validate_response=response_mode) as server:
            client = package.VcfInstallerClient(server.base_url, "fixture-token", timeout=2)
            try:
                client.validate_sddc_spec(spec)
            except package.VcfInstallerError:
                pass
            except Exception as error:
                raise AssertionError(
                    f"{response_mode} failure escaped as {type(error).__name__}"
                ) from error
            else:
                raise AssertionError(f"{response_mode} response did not raise VcfInstallerError")
            require(server.scenario.deployment_count == 0, "request failure changed deployment state")


def main() -> int:
    verify_contract_and_provenance()
    verify_stdlib_only()
    package = import_public_api()
    fixture = load_json(FIXTURE_PATH)
    verify_models_and_direct_methods(package)
    verify_success_path(package, fixture)
    verify_failed_gate(package, fixture)
    verify_exhausted_polling_budget(package, fixture)
    verify_explicit_query_value(package, fixture)
    verify_client_error_translation(package, fixture)
    print("VCF Installer contract verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise
