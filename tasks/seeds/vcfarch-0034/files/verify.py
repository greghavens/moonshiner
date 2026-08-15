#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for vcfarch-0034."""

from __future__ import annotations

import json
import ipaddress
import math
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {path.name}: {exc}") from exc


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"unsupported non-local schema reference: {pointer}")
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and token in current, f"unresolved schema reference: {pointer}")
        current = current[token]
    return current


def validate_json_schema(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> None:
    """Validate the JSON-Schema keywords used by the pinned OpenAPI document and plan schema."""
    require(isinstance(schema, dict), f"invalid schema node at {path}")

    if "$ref" in schema:
        validate_json_schema(instance, json_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    if "allOf" in schema:
        for child in schema["allOf"]:
            validate_json_schema(instance, child, root_schema, path)
    if "anyOf" in schema:
        matches = 0
        for child in schema["anyOf"]:
            try:
                validate_json_schema(instance, child, root_schema, path)
                matches += 1
            except VerificationError:
                pass
        require(matches >= 1, f"{path} does not match any allowed schema")
    if "oneOf" in schema:
        matches = 0
        for child in schema["oneOf"]:
            try:
                validate_json_schema(instance, child, root_schema, path)
                matches += 1
            except VerificationError:
                pass
        require(matches == 1, f"{path} must match exactly one schema, matched {matches}")
    if "not" in schema:
        try:
            validate_json_schema(instance, schema["not"], root_schema, path)
        except VerificationError:
            pass
        else:
            raise VerificationError(f"{path} matches a forbidden schema")

    if instance is None and schema.get("nullable") is True:
        return

    expected_type = schema.get("type")
    if expected_type == "object":
        require(isinstance(instance, dict), f"{path} must be an object")
    elif expected_type == "array":
        require(isinstance(instance, list), f"{path} must be an array")
    elif expected_type == "string":
        require(isinstance(instance, str), f"{path} must be a string")
    elif expected_type == "integer":
        require(isinstance(instance, int) and not isinstance(instance, bool), f"{path} must be an integer")
    elif expected_type == "number":
        require(isinstance(instance, (int, float)) and not isinstance(instance, bool), f"{path} must be a number")
    elif expected_type == "boolean":
        require(isinstance(instance, bool), f"{path} must be a boolean")
    elif expected_type == "null":
        require(instance is None, f"{path} must be null")

    if "const" in schema:
        require(instance == schema["const"], f"{path} must equal {schema['const']!r}")
    if "enum" in schema:
        require(instance in schema["enum"], f"{path} is not an allowed value")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            require(name in instance, f"{path}.{name} is required")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate_json_schema(value, properties[name], root_schema, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path}.{name} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(value, schema["additionalProperties"], root_schema, f"{path}.{name}")
        if "minProperties" in schema:
            require(len(instance) >= schema["minProperties"], f"{path} has too few properties")
        if "maxProperties" in schema:
            require(len(instance) <= schema["maxProperties"], f"{path} has too many properties")

    if isinstance(instance, list):
        if "minItems" in schema:
            require(len(instance) >= schema["minItems"], f"{path} has too few items")
        if "maxItems" in schema:
            require(len(instance) <= schema["maxItems"], f"{path} has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            require(len(encoded) == len(set(encoded)), f"{path} items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                validate_json_schema(value, schema["items"], root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema:
            require(len(instance) >= schema["minLength"], f"{path} is too short")
        if "maxLength" in schema:
            require(len(instance) <= schema["maxLength"], f"{path} is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], instance) is not None, f"{path} does not match its pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            require(instance >= schema["minimum"], f"{path} is below its minimum")
        if "maximum" in schema:
            require(instance <= schema["maximum"], f"{path} is above its maximum")
        if "exclusiveMinimum" in schema:
            require(instance > schema["exclusiveMinimum"], f"{path} is below its exclusive minimum")
        if "exclusiveMaximum" in schema:
            require(instance < schema["exclusiveMaximum"], f"{path} is above its exclusive maximum")


def compile_and_render() -> tuple[Any, str]:
    with tempfile.TemporaryDirectory(prefix=".vcfarch-classes-", dir=ROOT) as classes:
        compile_result = subprocess.run(
            ["javac", "-Xlint:all", "-Werror", "-d", classes, "ArchitectureClient.java", "TestMain.java"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        require(compile_result.returncode == 0, f"javac failed:\n{compile_result.stderr}")
        run_result = subprocess.run(
            [
                "java",
                "-cp",
                classes,
                "TestMain",
                "design-requirements.json",
                "estate-inventory.json",
                "compatibility-snapshot.json",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        require(run_result.returncode == 0, f"client failed:\n{run_result.stderr}")
        try:
            artifact = json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"client stdout is not one JSON document: {exc}") from exc
        return artifact, run_result.stdout


def validate_sddc_first(artifact: Any) -> None:
    """This is deliberately the first artifact assertion made by the verifier."""
    require(isinstance(artifact, dict), "architecture artifact must be a JSON object")
    require("greenfieldSddcSpec" in artifact, "artifact is missing greenfieldSddcSpec")
    openapi = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
    schema = json_pointer(openapi, "#/components/schemas/SddcSpec")
    validate_json_schema(artifact["greenfieldSddcSpec"], schema, openapi, "$.greenfieldSddcSpec")


def assert_networks(sddc: dict[str, Any], design: dict[str, Any]) -> None:
    actual = {entry.get("networkType"): entry for entry in sddc.get("networkSpecs", [])}
    expected = {entry["networkType"]: entry for entry in design["networks"]}
    require(set(actual) == set(expected), "networkSpecs must contain exactly the required traffic networks")
    for kind, wanted in expected.items():
        got = actual[kind]
        for key in ("vlanId", "subnet", "gateway", "mtu"):
            require(got.get(key) == wanted[key], f"{kind} {key} does not match the design fixture")
        require(got.get("subnetMask") == "255.255.255.0", f"{kind} must use the /24 subnet mask")
        require(got.get("ipAddressVersion") == "IPv4", f"{kind} must explicitly use IPv4")
        require(got.get("ipAddressAssignmentMode") == "STATIC", f"{kind} must use static addressing")
        ranges = got.get("includeIpAddressRanges")
        require(isinstance(ranges, list) and len(ranges) == 1, f"{kind} must have one address range")
        require(ranges[0].get("startIpAddress") == wanted["rangeStart"], f"{kind} range start is wrong")
        require(ranges[0].get("endIpAddress") == wanted["rangeEnd"], f"{kind} range end is wrong")


def assert_switches(sddc: dict[str, Any], design: dict[str, Any]) -> None:
    actual = {entry.get("dvsName"): entry for entry in sddc.get("dvsSpecs", [])}
    expected = {entry["name"]: entry for entry in design["switches"]}
    require(set(actual) == set(expected), "dvsSpecs must contain exactly the two required switches")
    for name, wanted in expected.items():
        got = actual[name]
        require(got.get("mtu") == wanted["mtu"], f"{name} MTU is wrong")
        require(set(got.get("networks", [])) == set(wanted["networks"]), f"{name} network assignment is wrong")
        mappings = got.get("vmnicsToUplinks", [])
        require({m.get("id") for m in mappings} == set(wanted["vmnics"]), f"{name} vmnic assignment is wrong")
        require({m.get("uplink") for m in mappings} == {"uplink1", "uplink2"}, f"{name} must use two uplinks")


def assert_greenfield(artifact: dict[str, Any], design: dict[str, Any], snapshot: dict[str, Any]) -> None:
    sddc = artifact["greenfieldSddcSpec"]
    naming = design["naming"]
    availability = design["availability"]
    capacity = design["capacity"]
    target_components = snapshot["bundleComponents"][design["targetVcfVersion"]]

    require(sddc.get("sddcId") == naming["sddcId"], "sddcId is wrong")
    require(sddc.get("workflowType") == "VCF", "greenfield workflowType must be VCF")
    require(sddc.get("version") == design["targetVcfVersion"], "SddcSpec VCF version is wrong")
    require(sddc.get("vcfInstanceName") == design["designId"], "VCF instance name is wrong")

    expected_hosts = [host for rack in design["site"]["failureDomains"] for host in design["site"]["hostPlacement"][rack]]
    hosts = sddc.get("hostSpecs", [])
    require(len(hosts) == capacity["managementHostCount"], "SddcSpec host count is wrong")
    require([host.get("hostname") for host in hosts] == expected_hosts, "SddcSpec hosts must follow rack-stable order")

    dns = sddc.get("dnsSpec", {})
    require(dns.get("subdomain") == naming["dnsSubdomain"], "DNS subdomain is wrong")
    require(dns.get("nameservers") == design["services"]["dnsServers"], "DNS servers are wrong")
    require(sddc.get("ntpServers") == design["services"]["ntpServers"], "NTP servers are wrong")

    vcenter = sddc.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == naming["vcenterFqdn"], "vCenter FQDN is wrong")
    require(vcenter.get("version") == target_components["VCENTER"], "vCenter target build is wrong")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot be existing")
    require(vcenter.get("rootVcenterPassword") == "${VC_ROOT_PASS}", "vCenter must use the schema-valid secret reference")

    manager = sddc.get("sddcManagerSpec", {})
    require(manager.get("hostname") == naming["sddcManagerHostname"], "SDDC Manager FQDN is wrong")
    require(manager.get("version") == target_components["SDDC_MANAGER"], "SDDC Manager target build is wrong")
    require(manager.get("useExistingDeployment") is False, "greenfield SDDC Manager cannot be existing")

    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("vipFqdn") == naming["nsxVipFqdn"], "NSX VIP FQDN is wrong")
    require(nsx.get("version") == target_components["NSX_T_MANAGER"], "NSX target build is wrong")
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX cannot be existing")
    require(nsx.get("transportVlanId") == 140, "NSX transport VLAN is wrong")
    require([node.get("hostname") for node in nsx.get("nsxtManagers", [])] == naming["nsxManagerHostnames"], "NSX manager topology is wrong")
    nsx_range = nsx.get("ipAddressPoolSpec", {}).get("subnets", [{}])[0]
    overlay = next(network for network in design["networks"] if network["networkType"] == "NSX_HOST_OVERLAY")
    require(nsx_range.get("cidr") == overlay["subnet"], "NSX TEP pool CIDR is wrong")
    require(nsx_range.get("gateway") == overlay["gateway"], "NSX TEP pool gateway is wrong")
    require(nsx_range.get("ipAddressPoolRanges") == [{"start": overlay["rangeStart"], "end": overlay["rangeEnd"]}], "NSX TEP range is wrong")

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(vsan.get("failuresToTolerate") == availability["vsanFailuresToTolerate"], "vSAN FTT is wrong")
    require(vsan.get("encryptionConfig", {}).get("dataInTransitConfig", {}).get("enable") is True, "vSAN data-in-transit encryption must be enabled")

    management_services = design["managementServices"]
    vsp = sddc.get("vspClusterSpec", {})
    require(vsp.get("platformFqdn") == naming["managementServicesPlatformFqdn"], "VCF Management Services platform FQDN is wrong")
    require(vsp.get("instanceFqdn") == naming["managementServicesInstanceFqdn"], "VCF Management Services instance FQDN is wrong")
    require(vsp.get("fleetFqdn") == naming["managementServicesFleetFqdn"], "VCF Management Services fleet FQDN is wrong")
    require(vsp.get("size") == management_services["size"], "VCF Management Services size is wrong")
    require(vsp.get("internalClusterCidrIpv4") == management_services["internalClusterCidr"], "VCF Management Services internal CIDR is wrong")
    require(vsp.get("version") == target_components["VSP"], "VCF Management Services target build is wrong")
    require(vsp.get("useExistingDeployment") is False, "greenfield VCF Management Services cannot be existing")
    vsp_range = vsp.get("ipv4Pool", {}).get("ipRange", {})
    require(vsp_range.get("startIpAddress") == management_services["addressRangeStart"], "VCF Management Services address range start is wrong")
    require(vsp_range.get("endIpAddress") == management_services["addressRangeEnd"], "VCF Management Services address range end is wrong")
    address_count = int(ipaddress.ip_address(vsp_range["endIpAddress"])) - int(ipaddress.ip_address(vsp_range["startIpAddress"])) + 1
    require(address_count >= management_services["minimumAddressCount"], "VCF Management Services address pool is too small")

    license_server = sddc.get("licenseServerSpec", {})
    require(license_server.get("hostname") == naming["licenseServerFqdn"], "license server FQDN is wrong")
    require(license_server.get("version") == target_components["LICENSE_SERVER"], "license server target build is wrong")
    require(license_server.get("useExistingDeployment") is False, "greenfield license server cannot be existing")
    local_region = sddc.get("vcfManagementComponentsInfrastructureSpec", {}).get("localRegionNetwork", {})
    require(local_region == {
        "networkName": "MANAGEMENT",
        "subnetMask": "255.255.255.0",
        "gateway": next(network["gateway"] for network in design["networks"] if network["networkType"] == "MANAGEMENT"),
    }, "VCF Management Services infrastructure network is wrong")
    assert_networks(sddc, design)
    assert_switches(sddc, design)

    site = artifact.get("siteDesign", {})
    require(site.get("siteCode") == design["site"]["code"], "site code is wrong")
    require(site.get("role") == design["site"]["role"], "site role is wrong")
    require(site.get("failureDomains") == design["site"]["failureDomains"], "rack failure domains are wrong")
    require(site.get("hostPlacement") == design["site"]["hostPlacement"], "host rack placement is wrong")

    capacity_design = artifact.get("capacityDesign", {})
    active_hosts = capacity["managementHostCount"] - capacity["reserveHostFailures"]
    require(capacity_design.get("hostCount") == capacity["managementHostCount"], "capacity host count is wrong")
    require(capacity_design.get("perHost") == {
        "cores": capacity["perHostCores"],
        "memoryGiB": capacity["perHostMemoryGiB"],
        "rawNvmeTiB": capacity["perHostRawNvmeTiB"],
    }, "per-host capacity is wrong")
    require(capacity_design.get("total") == {
        "cores": capacity["managementHostCount"] * capacity["perHostCores"],
        "memoryGiB": capacity["managementHostCount"] * capacity["perHostMemoryGiB"],
        "rawNvmeTiB": capacity["managementHostCount"] * capacity["perHostRawNvmeTiB"],
    }, "total capacity arithmetic is wrong")
    survivable = capacity_design.get("afterReservedFailures", {})
    require(survivable.get("cores") == active_hosts * capacity["perHostCores"], "survivable cores are wrong")
    require(survivable.get("memoryGiB") == active_hosts * capacity["perHostMemoryGiB"], "survivable memory is wrong")
    require(math.isclose(survivable.get("rawNvmeTiB", -1), active_hosts * capacity["perHostRawNvmeTiB"], rel_tol=0, abs_tol=1e-9), "survivable storage is wrong")
    require(survivable["cores"] >= capacity["minimumSurvivableCores"], "survivable core requirement is unmet")
    require(survivable["memoryGiB"] >= capacity["minimumSurvivableMemoryGiB"], "survivable memory requirement is unmet")
    require(survivable["rawNvmeTiB"] >= capacity["minimumSurvivableRawStorageTiB"], "survivable storage requirement is unmet")
    require(capacity_design.get("requirementsMet") is True, "capacity must explicitly be marked satisfied")

    availability_design = artifact.get("availabilityDesign", {})
    require(availability_design == {
        "reservedHostFailures": capacity["reserveHostFailures"],
        "vsanFailuresToTolerate": availability["vsanFailuresToTolerate"],
        "nsxManagerNodeCount": availability["nsxManagerNodeCount"],
        "dualTopOfRackUplinks": availability["dualTopOfRackUplinks"],
        "dnsServerCount": availability["independentDnsServers"],
        "ntpServerCount": availability["independentNtpServers"],
        "managementServicesAddressCount": management_services["minimumAddressCount"],
    }, "availability design does not implement the fixture")


def assert_migration(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact.get("migrationPlan")
    require(isinstance(plan, dict), "artifact is missing migrationPlan")
    plan_schema = load_json(ROOT / "migration-plan-schema.json")
    validate_json_schema(plan, plan_schema, plan_schema, "$.migrationPlan")

    supported = snapshot["supportedPlan"]
    target_versions = snapshot["bundleComponents"][supported["effectiveTarget"]]
    components = inventory["components"]
    require(plan["estateId"] == inventory["estateId"], "migration estateId is wrong")
    require(plan["requestedTarget"] == inventory["evaluatedInterimBundle"], "requested target is wrong")
    require(plan["effectiveTarget"] == inventory["desiredFinalVcfVersion"] == supported["effectiveTarget"], "effective target is wrong")
    require(plan["strategy"] == supported["strategy"], "migration strategy is wrong")
    require(len(plan["steps"]) == len(components), "migration plan must have exactly one step per inventory component")
    require([step["order"] for step in plan["steps"]] == list(range(1, len(components) + 1)), "migration order values must be contiguous")
    require([step["component"] for step in plan["steps"]] == supported["componentOrder"], "component upgrade order is wrong")

    inventory_by_component = {entry["component"]: entry for entry in components}
    for step in plan["steps"]:
        component = step["component"]
        require(step["fromVersion"] == inventory_by_component[component]["version"], f"{component} source version is wrong")
        require(step["targetVersion"] == target_versions[component], f"{component} target version is wrong")
        require(step["action"] == "UPGRADE", f"{component} action must be UPGRADE")
        require(step["gates"] == supported["requiredGates"][component], f"{component} gates or gate order are wrong")

    require(plan["rejectedTransitions"] == [dict(snapshot["blockedTransitions"][0], resolution="BYPASS_9_0_2_AND_TARGET_9_1")], "back-in-time rejected transition is wrong")


def assert_research_and_secrets(artifact: dict[str, Any]) -> None:
    research = artifact.get("researchConsulted")
    require(isinstance(research, list) and len(research) >= 1,
            "researchConsulted must contain the public compatibility and upgrade research consulted")

    seen_urls: set[str] = set()
    broadcom_source_found = False
    research_text: list[str] = []
    for index, entry in enumerate(research):
        path = f"$.researchConsulted[{index}]"
        require(isinstance(entry, dict), f"{path} must be an object")
        require({"title", "url", "accessedOn", "finding"}.issubset(entry),
                f"{path} must contain title, url, accessedOn, and finding")
        for field in ("title", "url", "accessedOn", "finding"):
            require(isinstance(entry[field], str) and entry[field].strip(), f"{path}.{field} must be a nonblank string")

        parsed = urlsplit(entry["url"])
        hostname = (parsed.hostname or "").lower().rstrip(".")
        require(parsed.scheme in {"http", "https"} and hostname and parsed.username is None and parsed.password is None,
                f"{path}.url must be a public HTTP(S) URL")
        require(hostname != "localhost" and not hostname.endswith((".localhost", ".invalid", ".test", ".example")),
                f"{path}.url cannot name a fixture or local host")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            require("." in hostname, f"{path}.url must have a public hostname")
        else:
            require(address.is_global, f"{path}.url must not use a private or reserved address")
        broadcom_source_found |= hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        require(entry["url"] not in seen_urls, "researchConsulted URLs must be unique")
        seen_urls.add(entry["url"])

        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["accessedOn"]) is not None,
                f"{path}.accessedOn must use YYYY-MM-DD")
        try:
            date.fromisoformat(entry["accessedOn"])
        except ValueError as exc:
            raise VerificationError(f"{path}.accessedOn is not a calendar date") from exc
        require(len(entry["title"].strip()) >= 5, f"{path}.title is too short")
        require(len(entry["finding"].strip()) >= 10, f"{path}.finding is too short")
        research_text.extend((entry["title"].lower(), entry["finding"].lower()))

    require(broadcom_source_found, "researchConsulted must include a Broadcom-published source")
    combined = " ".join(research_text)
    require(any(term in combined for term in ("compatibility", "interoperability", "matrix")),
            "researchConsulted must identify the compatibility/interoperability research")
    require(any(term in combined for term in ("upgrade", "migration")),
            "researchConsulted must identify the VCF upgrade guidance")

    secret_name = re.compile(
        r"(?:password|passphrase|secret|apiKey|licenseKey|downloadToken|accessToken|refreshToken|idToken)$",
        re.IGNORECASE,
    )
    secret_reference = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}")

    def check_secrets(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if secret_name.search(key):
                    require(isinstance(child, str) and secret_reference.fullmatch(child) is not None,
                            f"{child_path} must be a secret reference, not a deployable credential")
                check_secrets(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_secrets(child, f"{path}[{index}]")

    check_secrets(artifact, "$")


def main() -> int:
    try:
        artifact, first_render = compile_and_render()

        # Binding ordering requirement: validate the installer's SddcSpec before any
        # fixture, migration, topology, capacity, source-shape, or compatibility check.
        validate_sddc_first(artifact)

        second_artifact, second_render = compile_and_render()
        require(second_render == first_render and second_artifact == artifact, "ArchitectureClient output is not deterministic")

        design = load_json(ROOT / "design-requirements.json")
        inventory = load_json(ROOT / "estate-inventory.json")
        snapshot = load_json(ROOT / "compatibility-snapshot.json")
        assert_greenfield(artifact, design, snapshot)
        assert_migration(artifact, inventory, snapshot)
        assert_research_and_secrets(artifact)

        production_java = sorted(path.name for path in ROOT.glob("*.java") if path.name != "TestMain.java")
        require(production_java == ["ArchitectureClient.java"], "ArchitectureClient.java must be the only production Java source")
    except (VerificationError, KeyError, IndexError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: SddcSpec schema, greenfield architecture, and ordered migration plan validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
