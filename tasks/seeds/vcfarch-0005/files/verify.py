#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0005. No network access is used."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
SDDC_PATH = ROOT / "output/sddc-spec.json"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        label = str(path.relative_to(ROOT))
    except ValueError:
        label = str(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {label}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {label}: {exc}")


def json_type_matches(value: Any, wanted: str) -> bool:
    if wanted == "object":
        return isinstance(value, dict)
    if wanted == "array":
        return isinstance(value, list)
    if wanted == "string":
        return isinstance(value, str)
    if wanted == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if wanted == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if wanted == "boolean":
        return isinstance(value, bool)
    if wanted == "null":
        return value is None
    return True


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            fail(f"unresolvable schema reference: {ref}")
        current = current[part]
    if not isinstance(current, dict):
        fail(f"schema reference is not an object: {ref}")
    return current


def schema_errors(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-Schema keywords used by the pinned OpenAPI and local schemas."""
    errors: list[str] = []
    if "$ref" in schema:
        return schema_errors(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
    if instance is None and schema.get("nullable") is True:
        return errors

    for subschema in schema.get("allOf", []):
        errors.extend(schema_errors(instance, subschema, root_schema, path))
    if "anyOf" in schema and not any(not schema_errors(instance, candidate, root_schema, path) for candidate in schema["anyOf"]):
        errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, candidate, root_schema, path) for candidate in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: satisfies {matches} oneOf branches, expected exactly one")

    wanted = schema.get("type")
    if wanted:
        allowed = wanted if isinstance(wanted, list) else [wanted]
        if not any(json_type_matches(instance, item) for item in allowed):
            errors.append(f"{path}: expected type {wanted}, got {type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(schema_errors(value, properties[key], root_schema, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(schema_errors(value, schema["additionalProperties"], root_schema, child_path))
        if len(instance) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(set(serialized)) != len(serialized):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: string does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid regular expression in pinned schema at {path}: {exc}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema:
            bound = schema["exclusiveMinimum"]
            if not isinstance(bound, bool) and instance <= bound:
                errors.append(f"{path}: value is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema:
            bound = schema["exclusiveMaximum"]
            if not isinstance(bound, bool) and instance >= bound:
                errors.append(f"{path}: value is not below exclusiveMaximum")
    return errors


def validate_or_fail(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], label: str) -> None:
    errors = schema_errors(instance, schema, root_schema)
    if errors:
        fail(f"{label} schema validation failed:\n  " + "\n  ".join(errors[:30]))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# These values protect every grading input from candidate edits.
PROTECTED_SHA256 = {
    "authority/compatibility-snapshot.json": "b9a89430a487336d9904de78b64e921f43b884ad989f234ee1bcf549280f12ca",
    "fixtures/estate-inventory.json": "5de86fce46d0a3636d0d2f49220fffe5b7c04547226097150b305b4e7e8ee16f",
    "fixtures/scenario.json": "06bb8fa7d9908dfb4d53038263f3ee15c6709c0b0b594111f6b231349eca8cd9",
    "schemas/edge-design.schema.json": "9e4e1951e4e87e02045aa6f867753849267a99b11bbfc194c1f31f3b20cbf8f6",
    "schemas/migration-plan.schema.json": "f882897273ef3e6ddd94996f136cd94fc9ffa59f1ba07553ad2dfef055ebecb8",
    "specifications/LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
}


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def assert_truth(value: Any, label: str) -> None:
    if not value:
        fail(label)


def check_protected_inputs() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected input is missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            fail(f"protected input was modified: {relative}")


def check_sddc_design(spec: dict[str, Any], scenario: dict[str, Any]) -> None:
    install = scenario["installation"]
    availability = scenario["availability"]
    assert_equal(spec.get("sddcId"), install["sddcId"], "SddcSpec.sddcId")
    assert_equal(spec.get("workflowType"), "VCF", "SddcSpec.workflowType")
    assert_equal(spec.get("version"), scenario["targetRelease"], "SddcSpec.version")
    assert_equal(spec.get("vcfInstanceName"), install["vcfInstanceName"], "SddcSpec.vcfInstanceName")
    assert_equal(spec.get("managementPoolName"), install["managementPoolName"], "management pool")
    assert_equal(spec.get("ceipEnabled"), False, "ceipEnabled")
    assert_equal(spec.get("skipEsxThumbprintValidation"), False, "skipEsxThumbprintValidation")
    assert_equal(spec.get("skipGatewayPingValidation"), False, "skipGatewayPingValidation")

    hostnames = [host.get("hostname") for host in spec.get("hostSpecs", [])]
    assert_equal(hostnames, install["hostnames"], "management host list")
    assert_equal(len(hostnames), scenario["capacity"]["managementHostCount"], "management host count")

    vc = spec.get("vcenterSpec", {})
    assert_equal(vc.get("vcenterHostname"), install["vcenter"]["hostname"], "vCenter hostname")
    assert_equal(vc.get("rootVcenterPassword"), install["vcenter"]["rootPassword"], "vCenter fixture credential")
    assert_equal(vc.get("vmSize"), install["vcenter"]["vmSize"], "vCenter size")
    assert_equal(vc.get("storageSize"), install["vcenter"]["storageSize"], "vCenter storage size")
    assert_equal(vc.get("ssoDomain"), install["vcenter"]["ssoDomain"], "SSO domain")
    assert_equal(vc.get("useExistingDeployment"), False, "vCenter must be greenfield")

    cluster = spec.get("clusterSpec", {})
    assert_equal(cluster.get("datacenterName"), install["cluster"]["datacenterName"], "datacenter name")
    assert_equal(cluster.get("clusterName"), install["cluster"]["clusterName"], "cluster name")
    assert_equal(cluster.get("clusterEvcMode"), install["cluster"]["evcMode"], "EVC mode")

    dns = spec.get("dnsSpec", {})
    assert_equal(dns.get("subdomain"), install["dnsSubdomain"], "DNS subdomain")
    assert_equal(dns.get("nameservers"), install["dnsServers"], "DNS servers")
    assert_equal(spec.get("ntpServers"), install["ntpServers"], "NTP servers")

    manager = spec.get("sddcManagerSpec", {})
    expected_manager = install["sddcManager"]
    assert_equal(manager.get("hostname"), expected_manager["hostname"], "SDDC Manager hostname")
    assert_equal(manager.get("rootPassword"), expected_manager["rootPassword"], "SDDC Manager root fixture credential")
    assert_equal(manager.get("sshPassword"), expected_manager["sshPassword"], "SDDC Manager SSH fixture credential")
    assert_equal(manager.get("localUserPassword"), expected_manager["localUserPassword"], "SDDC Manager local fixture credential")
    assert_equal(manager.get("useExistingDeployment"), False, "SDDC Manager must be greenfield")

    actual_networks = spec.get("networkSpecs", [])
    assert_equal(len(actual_networks), len(scenario["networks"]), "network count")
    for expected, actual in zip(scenario["networks"], actual_networks):
        for field in ("networkType", "vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            assert_equal(actual.get(field), expected[field], f"network {expected['networkType']} {field}")
        assert_equal(actual.get("ipAddressVersion"), "IPv4", f"network {expected['networkType']} IP version")
        assert_equal(actual.get("ipAddressAssignmentMode"), "STATIC", f"network {expected['networkType']} assignment")
        assert_equal(actual.get("includeIpAddressRanges"), [{"startIpAddress": expected["start"], "endIpAddress": expected["end"]}], f"network {expected['networkType']} range")

    actual_dvs = spec.get("dvsSpecs", [])
    assert_equal(len(actual_dvs), len(scenario["distributedSwitches"]), "distributed-switch count")
    for expected, actual in zip(scenario["distributedSwitches"], actual_dvs):
        assert_equal(actual.get("dvsName"), expected["name"], f"DVS {expected['name']} name")
        assert_equal(actual.get("networks"), expected["networks"], f"DVS {expected['name']} networks")
        assert_equal(actual.get("mtu"), expected["mtu"], f"DVS {expected['name']} MTU")
        assert_equal(actual.get("vmnicsToUplinks"), expected["vmnicsToUplinks"], f"DVS {expected['name']} uplinks")
        if "transportZones" in expected:
            switch_config = actual.get("nsxtSwitchConfig", {})
            assert_equal(switch_config.get("hostSwitchOperationalMode"), "STANDARD", "NSX host-switch mode")
            assert_equal(switch_config.get("ipAssignmentType"), "STATIC", "NSX TEP assignment")
            assert_equal(switch_config.get("transportZones"), expected["transportZones"], "NSX transport zones")
            assert_equal(actual.get("nsxTeamings"), [expected["teaming"]], "NSX teaming")

    nsx = spec.get("nsxtSpec", {})
    expected_nsx = install["nsx"]
    assert_equal([item.get("hostname") for item in nsx.get("nsxtManagers", [])], expected_nsx["managerHostnames"], "NSX manager nodes")
    assert_equal(nsx.get("nsxtManagerSize"), expected_nsx["managerSize"], "NSX manager size")
    assert_equal(nsx.get("vipFqdn"), expected_nsx["vipFqdn"], "NSX VIP")
    assert_equal(nsx.get("transportVlanId"), expected_nsx["transportVlanId"], "NSX transport VLAN")
    assert_equal(nsx.get("useExistingDeployment"), False, "NSX must be greenfield")
    assert_equal(nsx.get("skipNsxOverlayOverManagementNetwork"), False, "NSX overlay configuration")
    tep = nsx.get("ipAddressPoolSpec", {})
    assert_equal(tep.get("name"), expected_nsx["tepPoolName"], "TEP pool name")
    assert_equal(tep.get("subnets"), [{"ipAddressPoolRanges": [{"start": expected_nsx["tepStart"], "end": expected_nsx["tepEnd"]}], "cidr": expected_nsx["tepCidr"], "gateway": expected_nsx["tepGateway"]}], "TEP pool")

    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    assert_equal(vsan.get("datastoreName"), "dal01-m01-vsan-esa", "vSAN datastore")
    assert_equal(vsan.get("esaConfig", {}).get("enabled"), availability["requireVsanEsa"], "vSAN ESA")
    assert_equal(vsan.get("failuresToTolerate"), availability["managementHostFailuresToTolerate"], "vSAN FTT")
    assert_equal(spec.get("securitySpec", {}).get("esxiCertsMode"), "VMCA", "ESXi certificate mode")

    mgmt = install["managementServices"]
    vsp = spec.get("vspClusterSpec", {})
    assert_equal(vsp.get("platformFqdn"), mgmt["platformFqdn"], "management platform FQDN")
    assert_equal(vsp.get("instanceFqdn"), mgmt["instanceFqdn"], "management instance FQDN")
    assert_equal(vsp.get("fleetFqdn"), mgmt["fleetFqdn"], "fleet FQDN")
    assert_equal(vsp.get("internalClusterCidrIpv4"), mgmt["internalClusterCidr"], "management internal CIDR")
    assert_equal(vsp.get("ipv4Pool", {}).get("ipRange"), {"startIpAddress": mgmt["ipv4Start"], "endIpAddress": mgmt["ipv4End"]}, "management services IP range")
    assert_equal(vsp.get("useExistingDeployment"), False, "management services must be greenfield")
    for field in ("fleetLcmSpec", "sddcLcmSpec", "fleetDepotSpec", "telemetryAcceptorSpec", "saltSpec", "saltRaasSpec"):
        value = spec.get(field, {})
        assert_equal(value.get("version"), scenario["targetRelease"], f"{field} release")
        assert_equal(value.get("size"), "small", f"{field} size")
    assert_equal(spec.get("vidbSpec", {}).get("hostname"), mgmt["identityBrokerHostname"], "identity broker hostname")
    assert_equal(spec.get("vidbSpec", {}).get("version"), scenario["targetRelease"], "identity broker release")
    assert_equal(spec.get("licenseServerSpec", {}).get("hostname"), mgmt["licenseServerHostname"], "license server hostname")
    assert_equal(spec.get("licenseServerSpec", {}).get("useExistingDeployment"), False, "license server must be greenfield")


def check_edge_design(edge: dict[str, Any], scenario: dict[str, Any], snapshot: dict[str, Any]) -> None:
    assert_equal(edge.get("schemaVersion"), 1, "Edge schema version")
    assert_equal(edge.get("targetRelease"), scenario["targetRelease"], "Edge target release")
    actual_sites = edge.get("sites", [])
    assert_equal([item.get("siteId") for item in actual_sites], [item["siteId"] for item in scenario["sites"]], "Edge site order")
    form_factors = sorted(snapshot["edgeFormFactors"], key=lambda item: item["rank"])
    for requirement, actual in zip(scenario["sites"], actual_sites):
        suitable = [
            item for item in form_factors
            if item["maxPeakGbpsPerNode"] >= requirement["requiredPeakNorthSouthGbps"]
            and requirement["uplinkSpeedGbps"] in item["supportedUplinkSpeedsGbps"]
            and len(requirement["uplinks"]) >= item["minimumDatapathUplinks"]
        ]
        assert_truth(suitable, f"snapshot has no Edge form factor for {requirement['siteId']}")
        chosen = suitable[0]
        expected_scalar = {
            "siteId": requirement["siteId"],
            "role": requirement["role"],
            "edgeClusterName": requirement["edgeClusterName"],
            "haMode": "ACTIVE_ACTIVE",
            "nodeFailureTolerance": scenario["availability"]["edgeNodeFailureTolerance"],
            "requiredPeakGbps": requirement["requiredPeakNorthSouthGbps"],
            "edgeFormFactor": chosen["name"],
            "capacityPerNodeGbps": chosen["maxPeakGbpsPerNode"],
            "survivingCapacityGbps": chosen["maxPeakGbpsPerNode"]
        }
        for field, expected in expected_scalar.items():
            assert_equal(actual.get(field), expected, f"Edge {requirement['siteId']} {field}")
        assert_truth(actual["survivingCapacityGbps"] >= actual["requiredPeakGbps"], f"Edge {requirement['siteId']} cannot carry peak after one node failure")
        assert_equal([node.get("name") for node in actual.get("nodes", [])], requirement["edgeNodeNames"], f"Edge {requirement['siteId']} nodes")
        for node in actual["nodes"]:
            expected_uplinks = [dict(item, speedGbps=requirement["uplinkSpeedGbps"]) for item in requirement["uplinks"]]
            assert_equal(node.get("uplinks"), expected_uplinks, f"Edge {node.get('name')} uplinks")
            fabrics = [uplink["fabric"] for uplink in node["uplinks"]]
            assert_equal(len(fabrics), len(set(fabrics)), f"Edge {node.get('name')} must use distinct fabrics")


def check_migration_plan(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    assert_equal(plan.get("schemaVersion"), 1, "migration schema version")
    assert_equal(plan.get("estateId"), inventory["estateId"], "migration estate")
    assert_equal(plan.get("targetRelease"), snapshot["targetRelease"], "migration target release")
    components = {item["id"]: item for item in inventory["components"]}
    paths = sorted(snapshot["migrationPaths"], key=lambda item: item["sequence"])
    steps = plan.get("steps", [])
    assert_equal(len(steps), len(components), "migration must name every inventory component exactly once")
    assert_equal([step.get("componentId") for step in steps], [item["componentId"] for item in paths], "migration component order")
    assert_equal(len({step.get("componentId") for step in steps}), len(components), "migration component IDs must be unique")
    assert_equal(set(step.get("componentId") for step in steps), set(components), "migration component coverage")
    sequence_by_id = {item["componentId"]: item["sequence"] for item in paths}
    for step, authority in zip(steps, paths):
        component = components[authority["componentId"]]
        assert_equal(step.get("order"), authority["sequence"], f"migration order for {component['id']}")
        assert_equal(step.get("componentName"), component["name"], f"migration name for {component['id']}")
        assert_equal(step.get("componentType"), component["type"], f"migration type for {component['id']}")
        assert_equal(step.get("fromVersion"), component["version"], f"migration source version for {component['id']}")
        for field in ("targetProduct", "targetVersion", "action", "dependsOn"):
            assert_equal(step.get(field), authority[field], f"migration {field} for {component['id']}")
        actual_gate_ids = [gate.get("id") for gate in step.get("gates", [])]
        assert_equal(actual_gate_ids, authority["requiredGateIds"], f"migration gates for {component['id']}")
        for gate in step["gates"]:
            assert_equal(gate.get("condition"), snapshot["gateCatalog"][gate["id"]], f"gate condition {gate['id']}")
        for dependency in step["dependsOn"]:
            assert_truth(sequence_by_id[dependency] < step["order"], f"dependency {dependency} must precede {component['id']}")


def check_research_record() -> None:
    path = ROOT / "output/research.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing required file: output/research.md")
    assert_truth(text.strip(), "output/research.md must not be empty")

    date_strings = sorted(set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)))
    assert_truth(date_strings, "research record must include an ISO access date")
    for value in date_strings:
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            fail(f"research record has an invalid access date: {value}")

    raw_urls = re.findall(r"https?://[^\s<>\])]+", text)
    urls = [value.rstrip(".,;:") for value in raw_urls]
    assert_truth(urls, "research record must include at least one source URL")
    assert_equal(len(urls), len(set(urls)), "research source URLs must be unique")
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        assert_truth(
            host == "broadcom.com" or host.endswith(".broadcom.com") or
            host == "vmware.com" or host.endswith(".vmware.com"),
            f"research source is not Broadcom-published: {url}"
        )
    prose = text
    for value in urls + date_strings:
        prose = prose.replace(value, " ")
    assert_truth(
        len(re.findall(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b", prose)) >= 12,
        "research record must include source titles and the compatibility or upgrade claims used"
    )


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def run_powershell(script: str, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        fail("pwsh is required by this seed")
    result = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout
    )
    if result.returncode != 0:
        fail(f"{label} failed:\n{result.stdout}{result.stderr}")
    return result


def powershell_ast_details(module: Path) -> dict[str, Any]:
    script = (
        "$tokens=$null;$errors=$null;"
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({ps_quote(module)},[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{$_.ToString()};exit 1};"
        "$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Test-VcfInstallerArchitecture'},$true);"
        "if($null -eq $function){throw 'Test-VcfInstallerArchitecture was not found'};"
        "$commands=@($function.Body.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]},$true) "
        "| ForEach-Object {$_.GetCommandName()} | Where-Object {$_});"
        "[ordered]@{commands=$commands}|ConvertTo-Json -Depth 5 -Compress"
    )
    result = run_powershell(script, 30, "PowerShell syntax and AST check")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"PowerShell AST check returned invalid JSON: {exc}")


def read_manifest(manifest: Path) -> dict[str, Any]:
    script = f"Import-PowerShellDataFile -LiteralPath {ps_quote(manifest)} | ConvertTo-Json -Depth 20 -Compress"
    result = run_powershell(script, 30, "PowerShell manifest check")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"PowerShell manifest check returned invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail("PowerShell manifest must evaluate to a data object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def check_powershell_module_and_roundtrip(
    scenario_path: Path,
    inventory_path: Path,
    snapshot_path: Path,
    expected_outputs: dict[str, Any]
) -> None:
    manifest = ROOT / "VcfArchitecture/VcfArchitecture.psd1"
    module = ROOT / "VcfArchitecture/VcfArchitecture.psm1"
    if not manifest.is_file() or not module.is_file():
        fail("PowerShell module manifest and root module are required")

    manifest_data = read_manifest(manifest)
    required_modules = manifest_data.get("RequiredModules", [])
    if not isinstance(required_modules, list):
        required_modules = [required_modules]
    required_names = {
        item if isinstance(item, str) else item.get("ModuleName")
        for item in required_modules
        if isinstance(item, (str, dict))
    }
    for required in ("VMware.Sdk.Vcf.Installer", "VMware.Sdk.Vcf.SddcManager"):
        assert_truth(required in required_names, f"module manifest must require {required}")
    exports = manifest_data.get("FunctionsToExport", [])
    if isinstance(exports, str):
        exports = [exports]
    for exported in ("New-VcfArchitecture", "Test-VcfInstallerArchitecture"):
        assert_truth(exported in exports, f"module manifest must export {exported}")

    ast_details = powershell_ast_details(module)
    commands = ast_details.get("commands", [])
    if isinstance(commands, str):
        commands = [commands]
    for sdk_command in (
        "Get-VcfInstallerOperation",
        "Initialize-VcfInstallerSddcSpec",
        "Invoke-VcfInstallerValidateSddcSpec"
    ):
        assert_truth(sdk_command in commands, f"validation function must call {sdk_command}")

    allowed = {"VcfArchitecture.psd1", "VcfArchitecture.psm1"}
    unexpected = [path for path in (ROOT / "VcfArchitecture").rglob("*") if path.is_file() and path.name not in allowed]
    assert_equal(unexpected, [], "do not vendor PowerCLI modules or binaries")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        temp_root = Path(temp_name)

        def generate_and_compare(
            label: str,
            current_scenario: Path,
            current_inventory: Path,
            current_snapshot: Path,
            current_expected: dict[str, Any]
        ) -> None:
            output_dir = temp_root / label / "output"
            generate_script = (
                "$ErrorActionPreference='Stop';"
                f"Import-Module {ps_quote(module)} -Force;"
                f"New-VcfArchitecture -ScenarioPath {ps_quote(current_scenario)} "
                f"-InventoryPath {ps_quote(current_inventory)} "
                f"-CompatibilitySnapshotPath {ps_quote(current_snapshot)} "
                f"-OutputDirectory {ps_quote(output_dir)}"
            )
            run_powershell(generate_script, 45, f"New-VcfArchitecture {label} round trip")
            for filename, expected in current_expected.items():
                actual = load_json(output_dir / filename)
                assert_equal(actual, expected, f"module {label} round-trip output {filename}")

        generate_and_compare("baseline", scenario_path, inventory_path, snapshot_path, expected_outputs)

        # A second run changes independent values in all three inputs. This
        # discriminates a real input-driven generator from a copied or
        # hard-coded set of checked-in artifacts.
        variant_dir = temp_root / "variant-inputs"
        variant_dir.mkdir()
        scenario_variant = copy.deepcopy(load_json(scenario_path))
        inventory_variant = copy.deepcopy(load_json(inventory_path))
        snapshot_variant = copy.deepcopy(load_json(snapshot_path))

        scenario_variant["installation"]["sddcId"] = "dal01-m81"
        scenario_variant["installation"]["hostnames"][0] = "dal01-esx81"
        scenario_variant["networks"][0]["vlanId"] = 2181
        scenario_variant["sites"][1]["requiredPeakNorthSouthGbps"] = 30
        inventory_variant["components"][0]["name"] = "AUS01 Variant VCF Operations"
        inventory_variant["components"][0]["version"] = "9.0.3"
        snapshot_variant["migrationPaths"][0]["targetVersion"] = "9.1.7"
        variant_gate = "Variant verified backups and health checks are green."
        snapshot_variant["gateCatalog"]["backup-and-health-green"] = variant_gate

        scenario_variant_path = variant_dir / "scenario.json"
        inventory_variant_path = variant_dir / "estate-inventory.json"
        snapshot_variant_path = variant_dir / "compatibility-snapshot.json"
        write_json(scenario_variant_path, scenario_variant)
        write_json(inventory_variant_path, inventory_variant)
        write_json(snapshot_variant_path, snapshot_variant)

        variant_expected = copy.deepcopy(expected_outputs)
        variant_sddc = variant_expected["sddc-spec.json"]
        variant_sddc["sddcId"] = "dal01-m81"
        variant_sddc["hostSpecs"][0]["hostname"] = "dal01-esx81"
        variant_sddc["networkSpecs"][0]["vlanId"] = 2181
        variant_edge_site = variant_expected["edge-design.json"]["sites"][1]
        variant_edge_site["requiredPeakGbps"] = 30
        variant_edge_site["edgeFormFactor"] = "XLARGE"
        variant_edge_site["capacityPerNodeGbps"] = 40
        variant_edge_site["survivingCapacityGbps"] = 40
        variant_steps = variant_expected["migration-plan.json"]["steps"]
        variant_steps[0]["componentName"] = "AUS01 Variant VCF Operations"
        variant_steps[0]["fromVersion"] = "9.0.3"
        variant_steps[0]["targetVersion"] = "9.1.7"
        for step in variant_steps:
            for gate in step["gates"]:
                if gate["id"] == "backup-and-health-green":
                    gate["condition"] = variant_gate

        generate_and_compare(
            "variant",
            scenario_variant_path,
            inventory_variant_path,
            snapshot_variant_path,
            variant_expected
        )


def main() -> int:
    # The installer specification's own SddcSpec schema is deliberately the
    # first artifact check.
    openapi = load_json(OPENAPI_PATH)
    sddc_spec = load_json(SDDC_PATH)
    sddc_schema = openapi.get("components", {}).get("schemas", {}).get("SddcSpec")
    if not isinstance(sddc_schema, dict):
        fail("pinned installer specification has no components.schemas.SddcSpec")
    validate_or_fail(sddc_spec, sddc_schema, openapi, "output/sddc-spec.json against installer SddcSpec")

    check_protected_inputs()
    assert_equal(openapi.get("info", {}).get("version"), "9.1.0.0", "pinned installer specification version")

    scenario_path = ROOT / "fixtures/scenario.json"
    inventory_path = ROOT / "fixtures/estate-inventory.json"
    snapshot_path = ROOT / "authority/compatibility-snapshot.json"
    scenario = load_json(scenario_path)
    inventory = load_json(inventory_path)
    snapshot = load_json(snapshot_path)
    edge = load_json(ROOT / "output/edge-design.json")
    migration = load_json(ROOT / "output/migration-plan.json")

    edge_schema = load_json(ROOT / "schemas/edge-design.schema.json")
    migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    validate_or_fail(edge, edge_schema, edge_schema, "output/edge-design.json")
    validate_or_fail(migration, migration_schema, migration_schema, "output/migration-plan.json")

    check_sddc_design(sddc_spec, scenario)
    check_edge_design(edge, scenario, snapshot)
    check_migration_plan(migration, inventory, snapshot)
    check_research_record()
    check_powershell_module_and_roundtrip(
        scenario_path,
        inventory_path,
        snapshot_path,
        {
            "sddc-spec.json": sddc_spec,
            "edge-design.json": edge,
            "migration-plan.json": migration
        }
    )
    print("PASS: VCF 9.1 architecture artifacts and PowerShell module are valid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
