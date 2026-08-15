#!/usr/bin/env python3
"""Deterministic, offline acceptance verifier for the VCF architecture artifacts."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required artifact: {display_path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {display_path}: {exc}")


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def resolve_ref(document: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        fail(f"schema contains unsupported external reference: {reference}")
    value: Any = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"schema contains unresolved reference: {reference}")
        value = value[part]
    if not isinstance(value, dict):
        fail(f"schema reference does not resolve to an object: {reference}")
    return value


def validate_json_schema(
    instance: Any,
    schema: Any,
    document: dict[str, Any],
    path: str = "$",
) -> None:
    """Validate the JSON-Schema keywords used by the pinned OpenAPI and plan schemas."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: value is forbidden by schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: invalid schema node")

    if "$ref" in schema:
        validate_json_schema(instance, resolve_ref(document, schema["$ref"]), document, path)
        return
    if instance is None and schema.get("nullable") is True:
        return

    if "allOf" in schema:
        for subschema in schema["allOf"]:
            validate_json_schema(instance, subschema, document, path)
    if "anyOf" in schema:
        matches = 0
        for subschema in schema["anyOf"]:
            try:
                validate_json_schema(instance, subschema, document, path)
                matches += 1
            except VerificationError:
                pass
        if matches == 0:
            fail(f"{path}: value does not match any allowed schema")
    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_json_schema(instance, subschema, document, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: value must match exactly one allowed schema")
    if "not" in schema:
        try:
            validate_json_schema(instance, schema["not"], document, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: value matches a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: {instance!r} is not in the schema enum")

    expected_types = schema.get("type")
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    if isinstance(expected_types, list) and not any(
        json_type_matches(instance, item) for item in expected_types
    ):
        fail(f"{path}: expected type {' or '.join(expected_types)}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                fail(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_json_schema(value, properties[name], document, child_path)
            elif schema.get("additionalProperties") is False:
                fail(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(value, schema["additionalProperties"], document, child_path)
        count = len(instance)
        if count < schema.get("minProperties", 0):
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and count > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                fail(f"{path}: array items must be unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                validate_json_schema(value, items, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"{path}: invalid pattern in pinned schema: {exc}")
            if not matched:
                fail(f"{path}: string does not match schema pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if instance <= exclusive_minimum:
                fail(f"{path}: number is not above exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if instance >= exclusive_maximum:
                fail(f"{path}: number is not below exclusiveMaximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def assert_sdk_module() -> None:
    manifest_path = ROOT / "VcfArchitecture" / "VcfArchitecture.psd1"
    module_path = ROOT / "VcfArchitecture" / "VcfArchitecture.psm1"
    require(manifest_path.is_file(), "missing VcfArchitecture/VcfArchitecture.psd1")
    require(module_path.is_file(), "missing VcfArchitecture/VcfArchitecture.psm1")
    manifest = manifest_path.read_text(encoding="utf-8")
    module = module_path.read_text(encoding="utf-8")

    require(re.search(r"RootModule\s*=\s*['\"]VcfArchitecture\.psm1['\"]", manifest) is not None,
            "module manifest must name VcfArchitecture.psm1 as RootModule")
    require(re.search(r"PowerShellVersion\s*=\s*['\"]7\.4['\"]", manifest) is not None,
            "module manifest must require PowerShell 7.4")
    require("VMware.Sdk.Vcf.Installer" in manifest and "13.5.0.25380678" in manifest,
            "module manifest must require VMware.Sdk.Vcf.Installer 13.5.0.25380678")
    require("New-VcfArchitecture" in manifest, "module manifest must export New-VcfArchitecture")

    parse_script = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; exit 2 }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'New-VcfArchitecture' }, $true)
if ($null -eq $function) { [Console]::Error.WriteLine('New-VcfArchitecture function not found'); exit 3 }
$parameters = @($function.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
$commands = @($function.Body.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
@{ parameters = $parameters; commands = $commands } | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", parse_script, str(module_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    require(result.returncode == 0, f"PowerShell module parse failed: {result.stderr.strip()}")
    try:
        ast = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        fail(f"could not inspect PowerShell module AST: {result.stdout.strip()}")
    require(set(ast["parameters"]) >= {"InventoryPath", "OutputDirectory"},
            "New-VcfArchitecture must accept InventoryPath and OutputDirectory")
    commands = set(ast["commands"])
    required_commands = {
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
        "ConvertTo-Json",
        "Set-Content",
    }
    missing = sorted(required_commands - commands)
    require(not missing, f"New-VcfArchitecture does not use required SDK/output commands: {missing}")


def assert_module_reproduces_artifacts(sddc: dict[str, Any], plan: dict[str, Any]) -> None:
    manifest_path = ROOT / "VcfArchitecture" / "VcfArchitecture.psd1"
    inventory_path = ROOT / "fixtures" / "estate-inventory.json"
    invoke_script = r"""
Import-Module $args[0] -Force -ErrorAction Stop
New-VcfArchitecture -InventoryPath $args[1] -OutputDirectory $args[2] -ErrorAction Stop
"""
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        result = subprocess.run(
            [
                "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs",
                invoke_script, str(manifest_path), str(inventory_path), temporary,
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=60,
        )
        require(result.returncode == 0, f"New-VcfArchitecture execution failed: {result.stderr.strip()}")
        generated_root = Path(temporary)
        generated_sddc = load_json(generated_root / "sddc-spec.json")
        generated_plan = load_json(generated_root / "migration-plan.json")
        require(generated_sddc == sddc, "module-generated SddcSpec differs from output/sddc-spec.json")
        require(generated_plan == plan, "module-generated migration plan differs from output/migration-plan.json")


def assert_placeholders(value: Any, path: str = "$", key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            assert_placeholders(child, f"{path}.{child_key}", child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_placeholders(child, f"{path}[{index}]", key)
    elif "password" in key.casefold():
        require(isinstance(value, str) and re.fullmatch(r"<[A-Z0-9_]+>", value) is not None,
                f"{path} must be an obvious deployment-time placeholder")


def assert_research_sources(research: Any) -> None:
    require(isinstance(research, dict), "research-sources.json must contain an object")
    consulted_at = research.get("consultedAt")
    require(isinstance(consulted_at, str), "research sources must record consultedAt as an ISO date")
    try:
        date.fromisoformat(consulted_at)
    except ValueError:
        fail("research sources consultedAt is not a valid ISO date")

    sources = research.get("sources")
    require(isinstance(sources, list) and sources, "research sources must contain a nonempty sources array")
    urls: list[str] = []
    hostnames: set[str] = set()
    all_claims: list[str] = []
    for index, source in enumerate(sources):
        path = f"$.sources[{index}]"
        require(isinstance(source, dict), f"{path} must be an object")
        title = source.get("title")
        url = source.get("url")
        claims = source.get("claimsUsed")
        require(isinstance(title, str) and title.strip(), f"{path}.title must be a nonempty string")
        require(isinstance(url, str), f"{path}.url must be an HTTPS URL")
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").casefold()
        except ValueError:
            fail(f"{path}.url must be a valid HTTPS URL")
        require(parsed.scheme == "https" and bool(hostname), f"{path}.url must be an HTTPS URL")
        require(
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com"),
            f"{path}.url must identify an official Broadcom or VMware source",
        )
        require(
            isinstance(claims, list)
            and claims
            and all(isinstance(claim, str) and claim.strip() for claim in claims),
            f"{path}.claimsUsed must be a nonempty string array",
        )
        urls.append(url)
        hostnames.add(hostname)
        all_claims.extend(claims)

    require(len(urls) == len(set(urls)), "research source URLs must be unique")
    for required_hostname, label in (
        ("interopmatrix.broadcom.com", "Product Interoperability Matrix"),
        ("compatibilityguide.broadcom.com", "Broadcom Compatibility Guide"),
        ("techdocs.broadcom.com", "VCF 9.1 upgrade documentation"),
        ("knowledge.broadcom.com", "Broadcom knowledge-base material"),
    ):
        require(required_hostname in hostnames, f"research sources must include the {label}")

    claims_text = " ".join(all_claims).casefold()
    require("9.1" in claims_text and "upgrade" in claims_text,
            "research claims must record the VCF 9.1 upgrade conclusion")
    require(
        "log" in claims_text
        and "service" in claims_text
        and ("migrat" in claims_text or "decommission" in claims_text),
        "research claims must record the Logs appliance-to-service migration conclusion",
    )


def assert_sddc_architecture(sddc: dict[str, Any], inventory: dict[str, Any]) -> None:
    requirements = inventory["designRequirements"]
    require(sddc.get("sddcId") == requirements["sddcId"], "SddcSpec has the wrong sddcId")
    require(sddc.get("version") == inventory["targetRelease"], "SddcSpec has the wrong target version")
    require(sddc.get("workflowType") == "VCF_COMPLETE", "SddcSpec must use the complete greenfield VCF workflow")
    require(sddc.get("vcfInstanceName") == requirements["vcfInstanceName"], "SddcSpec has the wrong VCF instance name")
    require(sddc.get("dnsSpec", {}).get("subdomain") == requirements["dnsSubdomain"], "DNS subdomain does not match the target site")
    require(sddc.get("dnsSpec", {}).get("nameservers") == requirements["nameServers"], "DNS nameservers do not match the inventory")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "NTP servers do not match the inventory")

    selected = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    catalog = {host["hostname"]: host for host in inventory["targetHostCatalog"]}
    require(len(selected) == requirements["requiredManagementHosts"], "management host count does not meet N+2 requirement")
    require(len(selected) == len(set(selected)), "management hosts must be unique")
    require(set(selected) == set(catalog), "SddcSpec must select exactly the DFW01 target hosts")
    selected_records = [catalog[name] for name in selected]
    require(all(host["site"] == inventory["targetSite"]["id"] for host in selected_records), "management cluster crosses sites")
    failures = requirements["managementHostFailureTolerance"]
    for field, minimum_field, label in (
        ("physicalCores", "minimumPhysicalCores", "physical core"),
        ("memoryGiB", "minimumMemoryGiB", "memory"),
        ("rawStorageTiB", "minimumRawStorageTiB", "raw storage"),
    ):
        capacities = sorted((host[field] for host in selected_records), reverse=True)
        remaining = sum(capacities) - sum(capacities[:failures])
        require(remaining >= requirements[minimum_field], f"{label} capacity is insufficient after N+2 host loss")

    actual_networks = {network.get("networkType"): network for network in sddc.get("networkSpecs", [])}
    expected_networks = {network["networkType"]: network for network in requirements["networks"]}
    require(set(actual_networks) == set(expected_networks), "SddcSpec network types do not match the inventory")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for field in ("vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(field) == expected[field], f"{network_type} network has the wrong {field}")

    dvs_specs = sddc.get("dvsSpecs", [])
    require(bool(dvs_specs), "SddcSpec must define a distributed switch")
    mapped_uplinks = {
        mapping.get("uplink")
        for dvs in dvs_specs
        for mapping in dvs.get("vmnicsToUplinks", [])
    }
    require(mapped_uplinks >= set(requirements["requiredUplinks"]), "distributed switch lacks both required uplinks")
    datastore = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(datastore.get("failuresToTolerate") == requirements["managementHostFailureTolerance"], "vSAN FTT does not meet N+2 availability")

    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX must not reuse the CHI01 deployment")
    nsx_managers = nsx.get("nsxtManagers", [])
    require(len(nsx_managers) == requirements["requiredNsxManagers"], "NSX manager count does not meet HA requirement")
    nsx_hostnames = [manager.get("hostname") for manager in nsx_managers]
    require(all(isinstance(name, str) and name for name in nsx_hostnames), "NSX managers must have hostnames")
    require(len(nsx_hostnames) == len(set(nsx_hostnames)), "NSX HA managers must be unique")
    require(isinstance(nsx.get("vipFqdn"), str) and nsx["vipFqdn"], "NSX HA requires a VIP FQDN")
    operations = sddc.get("vcfOperationsSpec", {})
    require(operations.get("useExistingDeployment") is False, "greenfield VCF Operations must be a new deployment")
    operation_nodes = operations.get("nodes", [])
    require(len(operation_nodes) == requirements["requiredOperationsNodes"], "VCF Operations node count does not meet HA requirement")
    operation_hostnames = [node.get("hostname") for node in operation_nodes]
    require(all(isinstance(name, str) and name for name in operation_hostnames), "VCF Operations nodes must have hostnames")
    require(len(operation_hostnames) == len(set(operation_hostnames)), "VCF Operations HA nodes must be unique")
    require({node.get("type") for node in operation_nodes} == {"master", "replica", "data"},
            "VCF Operations HA requires master, replica, and data nodes")
    require(isinstance(operations.get("loadBalancerFqdn"), str) and operations["loadBalancerFqdn"],
            "VCF Operations HA requires a load balancer FQDN")
    require(sddc.get("vcenterSpec", {}).get("useExistingDeployment") is False, "greenfield vCenter must be a new deployment")
    require(sddc.get("sddcManagerSpec", {}).get("useExistingDeployment") is False, "greenfield SDDC Manager must be a new deployment")

    for property_name in (
        "vcenterSpec",
        "nsxtSpec",
        "sddcManagerSpec",
        "vspClusterSpec",
        "vcfOperationsSpec",
    ):
        require(sddc.get(property_name, {}).get("version") == inventory["targetRelease"],
                f"{property_name} has the wrong target version")

    vsp = sddc.get("vspClusterSpec", {})
    require(vsp.get("internalClusterCidrIpv4") == requirements["internalClusterCidrIpv4"], "VSP internal CIDR is wrong")
    require(vsp.get("ipv4Pool", {}).get("addresses") == requirements["vspAddresses"], "VSP must receive the exact 12-address management pool")
    for property_name in ("fleetLcmSpec", "sddcLcmSpec", "fleetDepotSpec", "licenseServerSpec"):
        require(property_name in sddc, f"SddcSpec is missing mandatory VCF 9.1 service {property_name}")
        require(sddc[property_name].get("version") == inventory["targetRelease"], f"{property_name} has the wrong version")
    assert_placeholders(sddc)


def assert_migration_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    require(plan.get("estateId") == inventory["estateId"], "migration plan has the wrong estateId")
    require(plan.get("targetRelease") == inventory["targetRelease"], "migration plan has the wrong target release")
    components = {component["id"]: component for component in inventory["components"]}
    steps = plan.get("steps", [])
    require(len(steps) == len(components), "migration plan must contain every inventory component exactly once")
    require([step.get("order") for step in steps] == list(range(1, len(steps) + 1)), "migration steps must be in contiguous order")
    ids = [step.get("componentId") for step in steps]
    require(len(ids) == len(set(ids)), "migration plan contains a component more than once")
    require(set(ids) == set(components), "migration plan component set does not match inventory")

    for step in steps:
        component = components[step["componentId"]]
        require(step["componentName"] == component["name"], f"{step['componentId']} has the wrong component name")
        require(step["sourceVersion"] == component["currentVersion"], f"{step['componentId']} has the wrong source version")

    by_id = {step["componentId"]: step for step in steps}
    target_release = inventory["targetRelease"]
    suite_lifecycle = by_id["aria-suite-lifecycle"]
    logs = by_id["aria-operations-for-logs"]
    require(suite_lifecycle["targetVersion"] == "retired", "Aria Suite Lifecycle must finish retired")
    require(suite_lifecycle["action"] == "patch-then-decommission",
            "Aria Suite Lifecycle must be patched before it is decommissioned")
    require(logs["targetVersion"] == "VCF Log Management 9.1.0.0", "Logs has the wrong service target")
    require(logs["action"] == "replace-migrate-decommission",
            "Logs must cross the appliance-to-service support boundary")

    for component_id in set(components) - {"aria-suite-lifecycle", "aria-operations-for-logs"}:
        require(by_id[component_id]["targetVersion"] == target_release,
                f"{component_id} has the wrong target version")
    for component_id in ("aria-operations", "aria-automation"):
        require(by_id[component_id]["action"] == "in-place-upgrade",
                f"{component_id} has the wrong migration action")
    require(by_id["aria-operations-for-networks"]["action"] == "fleet-lcm-upgrade",
            "Aria Operations for Networks must use Fleet LCM")
    for component_id in ("sddc-manager", "hcx", "nsx", "vcenter", "esxi"):
        require(by_id[component_id]["action"] == "lifecycle-upgrade",
                f"{component_id} has the wrong lifecycle action")
    require(by_id["vsan"]["action"] == "on-disk-format-upgrade",
            "vSAN must finish with an on-disk format upgrade")

    positions = {component_id: step["order"] for component_id, step in by_id.items()}
    dependencies = (
        ("aria-suite-lifecycle", "aria-operations"),
        ("aria-operations", "aria-automation"),
        ("aria-operations", "aria-operations-for-networks"),
        ("aria-automation", "sddc-manager"),
        ("aria-operations-for-networks", "sddc-manager"),
        ("sddc-manager", "hcx"),
        ("hcx", "nsx"),
        ("nsx", "vcenter"),
        ("vcenter", "esxi"),
        ("esxi", "vsan"),
        ("vsan", "aria-operations-for-logs"),
    )
    for predecessor, successor in dependencies:
        require(positions[predecessor] < positions[successor],
                f"{predecessor} must precede {successor}")

    def require_gate_terms(component_id: str, *term_groups: tuple[str, ...]) -> None:
        gate_text = " ".join(by_id[component_id]["gates"]).casefold()
        for alternatives in term_groups:
            require(any(term in gate_text for term in alternatives),
                    f"{component_id} is missing a required compatibility or sequencing gate")

    require_gate_terms("aria-suite-lifecycle", ("patch",), ("8.18",), ("management",), ("identity", "broker"))
    require_gate_terms("aria-operations", ("8.18",), ("assessment", "precheck"))
    require_gate_terms("aria-automation", ("operations",), ("fleet", "import"))
    require_gate_terms("aria-operations-for-networks", ("operations",), ("fleet",), ("health", "green"))
    require_gate_terms("sddc-manager", ("operations",), ("management",), ("license",))
    require_gate_terms("hcx", ("sddc",), ("health",))
    require_gate_terms("nsx", ("hcx",), ("precheck",))
    require_gate_terms("vcenter", ("nsx",), ("license",))
    require_gate_terms("esxi", ("vcenter",), ("hardware",), ("n-plus-two", "n+2"))
    require_gate_terms("vsan", ("esxi",), ("health",), ("resync",))
    require_gate_terms(
        "aria-operations-for-logs",
        ("management",),
        ("9.1",),
        ("log",),
        ("service",),
        ("transfer", "migrat"),
        ("ninety", "90"),
    )


def main() -> int:
    # This is intentionally the first verification phase. The candidate SddcSpec
    # is validated against the installer specification's own SddcSpec schema
    # before the verifier loads fixtures, module code, or plan data.
    openapi = load_json(ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json")
    sddc = load_json(ROOT / "output" / "sddc-spec.json")
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer OpenAPI document does not contain SddcSpec")
    validate_json_schema(sddc, sddc_schema, openapi)
    require(openapi.get("info", {}).get("version") == "9.1.0.0", "pinned installer OpenAPI document is not version 9.1.0.0")
    print("PASS: SddcSpec validates against installer OpenAPI 9.1.0.0")

    inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
    plan_schema = load_json(ROOT / "tests" / "migration-plan.schema.json")
    plan = load_json(ROOT / "output" / "migration-plan.json")
    research = load_json(ROOT / "output" / "research-sources.json")
    validate_json_schema(plan, plan_schema, plan_schema)
    assert_sddc_architecture(sddc, inventory)
    assert_migration_plan(plan, inventory)
    assert_research_sources(research)
    assert_sdk_module()
    assert_module_reproduces_artifacts(sddc, plan)
    print("PASS: capacity, availability, site, network, service, and migration contracts")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
