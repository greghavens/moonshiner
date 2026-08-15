#!/usr/bin/env python3
"""Deterministic, offline verifier for the VCF architecture deliverable."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
SDDC_ARTIFACT = ROOT / "artifacts" / "sddc-spec.json"
RESEARCH_ARTIFACT = ROOT / "artifacts" / "research-sources.json"
OPENAPI_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc.msg} at line {exc.lineno}"
        ) from exc


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def resolve_ref(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {ref}")
    node: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            node = node[token]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {ref}") from exc
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference does not resolve to an object: {ref}")
    return node


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    raise VerificationError(f"unsupported schema type: {expected}")


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(document, schema["$ref"]), document, path)
        schema = {key: value for key, value in schema.items() if key != "$ref"}

    if instance is None and schema.get("nullable") is True:
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, document, path)

    if "anyOf" in schema:
        if not any(schema_accepts(instance, option, document, path) for option in schema["anyOf"]):
            raise VerificationError(f"{path}: value does not satisfy anyOf")

    if "oneOf" in schema:
        matches = sum(schema_accepts(instance, option, document, path) for option in schema["oneOf"])
        if matches != 1:
            raise VerificationError(f"{path}: value must satisfy exactly one oneOf branch, got {matches}")

    if "not" in schema and schema_accepts(instance, schema["not"], document, path):
        raise VerificationError(f"{path}: value satisfies a forbidden schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, candidate) for candidate in allowed):
            raise VerificationError(f"{path}: expected type {expected_type}, got {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise VerificationError(f"{path}: {instance!r} is not in {schema['enum']!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                raise VerificationError(f"{path}: missing required property {name!r}")
        if len(instance) < schema.get("minProperties", 0):
            raise VerificationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise VerificationError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_schema(value, properties[name], document, child_path)
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], document, child_path)

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            values = [canonical(item) for item in instance]
            if len(values) != len(set(values)):
                raise VerificationError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as exc:
                raise VerificationError(f"{path}: invalid schema regex {schema['pattern']!r}") from exc
            if not matched:
                raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
            if instance <= schema["exclusiveMinimum"]:
                raise VerificationError(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and isinstance(schema["exclusiveMaximum"], (int, float)):
            if instance >= schema["exclusiveMaximum"]:
                raise VerificationError(f"{path}: number is not below exclusiveMaximum")


def schema_accepts(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> bool:
    try:
        validate_schema(instance, schema, document, path)
        return True
    except VerificationError:
        return False


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_close(actual: float, expected: float, label: str, tolerance: float = 0.001) -> None:
    if not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance):
        raise VerificationError(f"{label}: expected {expected!r}, got {actual!r}")


def check_research(research: Any) -> None:
    if not isinstance(research, dict) or "sources" not in research:
        raise VerificationError("research record must be an object containing a sources array")
    sources = research["sources"]
    if not isinstance(sources, list) or len(sources) < 2:
        raise VerificationError("research record must contain at least two sources")

    urls: list[str] = []
    findings: list[str] = []
    required_fields = {"title", "url", "accessedAt", "finding"}
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        if not isinstance(source, dict) or set(source) != required_fields:
            raise VerificationError(f"{label}: expected exactly {sorted(required_fields)!r}")
        for field in required_fields:
            if not isinstance(source[field], str) or not source[field].strip():
                raise VerificationError(f"{label}: {field} must be a nonempty string")

        parsed_url = urlsplit(source["url"])
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme not in {"http", "https"} or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            raise VerificationError(f"{label}: URL must be a Broadcom-published HTTP(S) source")
        if parsed_url.username or parsed_url.password or not parsed_url.path:
            raise VerificationError(f"{label}: malformed source URL")

        timestamp = source["accessedAt"]
        try:
            parsed_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VerificationError(f"{label}: accessedAt is not an ISO 8601 time") from exc
        if parsed_time.utcoffset() != timedelta(0):
            raise VerificationError(f"{label}: accessedAt must include a UTC offset")

        urls.append(source["url"])
        findings.append(source["finding"].lower())

    if len(urls) != len(set(urls)):
        raise VerificationError("research source URLs must be unique")
    combined_findings = " ".join(findings)
    if "osa" not in combined_findings or "esa" not in combined_findings:
        raise VerificationError("research findings must cover both vSAN OSA and ESA")
    if "9.1" not in combined_findings or not any(
        term in combined_findings for term in ("upgrade", "migration", "migrate")
    ):
        raise VerificationError("research findings must cover the estate path to VCF 9.1")


def expected_candidates(requirements: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    capacity = requirements["capacity"]
    compute = requirements["hostComputeProfile"]
    reserve = requirements["availability"]["hostReserve"]
    headroom_factor = 1.0 - capacity["headroomPercent"] / 100.0
    cpu_hosts = math.ceil(capacity["requiredCpuCores"] / (compute["coresPerHost"] * headroom_factor)) + reserve
    memory_hosts = (
        math.ceil(capacity["requiredMemoryGiB"] / (compute["memoryGiBPerHost"] * headroom_factor))
        + reserve
    )
    results: list[dict[str, Any]] = []
    for option in snapshot["storageOptions"]:
        raw_tib = option["rawCapacityTBPerHost"] * 1_000_000_000_000 / (2**40)
        storage_per_active_host = raw_tib * option["usableCapacityFactor"] * option["maxUtilization"]
        storage_hosts = math.ceil(capacity["requiredUsableStorageTiB"] / storage_per_active_host) + reserve
        dimensions = {
            "minimum": option["minimumHostsSingleAvailabilityZone"],
            "cpu": cpu_hosts,
            "memory": memory_hosts,
            "storage": storage_hosts,
        }
        host_count = max(dimensions.values())
        limiting = next(name for name in ("storage", "memory", "cpu", "minimum") if dimensions[name] == host_count)
        results.append(
            {
                "architecture": option["architecture"],
                "supported": option["supported"],
                "hostCount": host_count,
                "cpuHostCount": cpu_hosts,
                "memoryHostCount": memory_hosts,
                "storageHostCount": storage_hosts,
                "limitingDimension": limiting,
                "usableStorageTiBAfterReserve": round(
                    (host_count - reserve)
                    * raw_tib
                    * option["usableCapacityFactor"]
                    * option["maxUtilization"],
                    3,
                ),
                "minimumVsanBandwidthGbps": option["minimumVsanBandwidthGbps"],
                "configuredVsanBandwidthGbps": option["configuredVsanUplinks"]
                * option["uplinkSpeedGbps"],
                "uplinksPerHost": option["uplinksPerHost"],
                "uplinkSpeedGbps": option["uplinkSpeedGbps"],
                "dvsCount": len(option["dvsLayouts"]),
                "deviceProtocol": option["deviceProtocol"],
                "deviceAttachment": option["deviceAttachment"],
                "selectionPriority": option["selectionPriority"],
            }
        )
    return results


def selected_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    supported = [candidate for candidate in candidates if candidate["supported"]]
    if not supported:
        raise VerificationError("snapshot has no supported storage candidate")
    return min(supported, key=lambda item: (item["hostCount"], item["selectionPriority"]))


def check_architecture(
    sddc: dict[str, Any],
    decision: dict[str, Any],
    requirements: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    candidates = expected_candidates(requirements, snapshot)
    selected = selected_candidate(candidates)
    options = {item["architecture"]: item for item in snapshot["storageOptions"]}
    selected_option = options[selected["architecture"]]

    assert_equal(decision["targetRelease"], requirements["targetRelease"], "decision target release")
    assert_equal(decision["site"], requirements["site"], "site design")
    assert_equal(decision["availability"], requirements["availability"], "availability design")
    assert_equal(decision["selectionObjective"], requirements["selectionObjective"], "selection objective")
    assert_equal(decision["capacityRequirements"], requirements["capacity"], "capacity requirements")
    assert_equal(decision["selectedStorageArchitecture"], selected["architecture"], "selected storage")
    assert_equal(decision["selectedHostCount"], selected["hostCount"], "selected host count")

    actual_by_arch = {candidate["architecture"]: candidate for candidate in decision["candidates"]}
    assert_equal(set(actual_by_arch), {candidate["architecture"] for candidate in candidates}, "candidate set")
    for expected in candidates:
        actual = actual_by_arch[expected["architecture"]]
        for field, value in expected.items():
            if field == "selectionPriority":
                continue
            if field == "usableStorageTiBAfterReserve":
                assert_close(actual[field], value, f"{expected['architecture']} {field}")
            else:
                assert_equal(actual[field], value, f"{expected['architecture']} {field}")
        if actual["configuredVsanBandwidthGbps"] < actual["minimumVsanBandwidthGbps"]:
            raise VerificationError(f"{expected['architecture']}: configured vSAN bandwidth is insufficient")

    expected_layouts = [
        {
            "name": f"{requirements['sddcId']}-{layout['nameSuffix']}",
            "networks": layout["networks"],
            "vmnics": layout["vmnics"],
        }
        for layout in selected_option["dvsLayouts"]
    ]
    expected_network_design = {
        "uplinksPerHost": selected_option["uplinksPerHost"],
        "uplinkSpeedGbps": selected_option["uplinkSpeedGbps"],
        "configuredVsanBandwidthGbps": selected_option["configuredVsanUplinks"]
        * selected_option["uplinkSpeedGbps"],
        "dvsCount": len(expected_layouts),
        "dvsLayouts": expected_layouts,
    }
    assert_equal(decision["networkDesign"], expected_network_design, "selected network design")

    assert_equal(sddc["sddcId"], requirements["sddcId"], "SddcSpec sddcId")
    assert_equal(sddc["workflowType"], requirements["workflowType"], "SddcSpec workflow type")
    assert_equal(sddc["version"], requirements["targetRelease"], "SddcSpec version")
    assert_equal(sddc["vcfInstanceName"], requirements["vcfInstanceName"], "VCF instance name")
    assert_equal(sddc["managementPoolName"], requirements["naming"]["managementPoolName"], "network pool")

    expected_hosts = [
        f"{requirements['naming']['hostPrefix']}{index:02d}" for index in range(1, selected["hostCount"] + 1)
    ]
    assert_equal([host["hostname"] for host in sddc["hostSpecs"]], expected_hosts, "host inventory")
    assert_equal(sddc["clusterSpec"]["datacenterName"], requirements["naming"]["datacenterName"], "datacenter")
    assert_equal(sddc["clusterSpec"]["clusterName"], requirements["naming"]["clusterName"], "cluster")
    assert_equal(
        sddc["datastoreSpec"]["vsanSpec"]["datastoreName"],
        requirements["naming"]["datastoreName"],
        "datastore",
    )
    assert_equal(
        sddc["datastoreSpec"]["vsanSpec"]["esaConfig"]["enabled"],
        selected["architecture"] == "ESA",
        "ESA flag",
    )
    assert_equal(
        sddc["datastoreSpec"]["vsanSpec"]["failuresToTolerate"],
        requirements["availability"]["hostFailuresToTolerate"],
        "vSAN failures to tolerate",
    )

    actual_networks = {network["networkType"]: network for network in sddc["networkSpecs"]}
    assert_equal(set(actual_networks), {network["networkType"] for network in requirements["networks"]}, "network set")
    for expected in requirements["networks"]:
        actual = actual_networks[expected["networkType"]]
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu", "portGroupKey"):
            assert_equal(actual[field], expected[field], f"{expected['networkType']} {field}")
        assert_equal(
            actual["includeIpAddressRanges"],
            [{"startIpAddress": expected["startIpAddress"], "endIpAddress": expected["endIpAddress"]}],
            f"{expected['networkType']} address range",
        )
        assert_equal(actual["ipAddressVersion"], "IPv4", f"{expected['networkType']} IP version")
        assert_equal(actual["ipAddressAssignmentMode"], "STATIC", f"{expected['networkType']} assignment")

    assert_equal(len(sddc["dvsSpecs"]), len(expected_layouts), "DVS count")
    for actual, expected in zip(sddc["dvsSpecs"], expected_layouts):
        assert_equal(actual["dvsName"], expected["name"], "DVS name")
        assert_equal(actual["networks"], expected["networks"], f"{expected['name']} networks")
        assert_equal(
            [mapping["id"] for mapping in actual["vmnicsToUplinks"]],
            expected["vmnics"],
            f"{expected['name']} vmnics",
        )
        assert_equal(
            [mapping["uplink"] for mapping in actual["vmnicsToUplinks"]],
            [f"uplink{index}" for index in range(1, len(expected["vmnics"]) + 1)],
            f"{expected['name']} uplinks",
        )

    appliances = requirements["appliances"]
    assert_equal(sddc["vcenterSpec"]["vcenterHostname"], appliances["vcenter"]["hostname"], "vCenter hostname")
    assert_equal(sddc["dnsSpec"], requirements["dns"], "DNS design")
    assert_equal(sddc["ntpServers"], requirements["ntpServers"], "NTP design")
    assert_equal(sddc["sddcManagerSpec"]["hostname"], appliances["sddcManager"]["hostname"], "SDDC Manager")
    assert_equal(sddc["nsxtSpec"]["vipFqdn"], appliances["nsx"]["vipFqdn"], "NSX VIP")
    assert_equal(
        [node["hostname"] for node in sddc["nsxtSpec"]["nsxtManagers"]],
        appliances["nsx"]["managerHostnames"],
        "NSX managers",
    )
    assert_equal(sddc["licenseServerSpec"]["hostname"], appliances["licenseServer"]["hostname"], "license server")
    assert_equal(
        sddc["vcfOperationsSpec"]["nodes"][0]["hostname"],
        appliances["vcfOperations"]["hostname"],
        "VCF Operations",
    )


def check_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    rules = snapshot["migration"]
    components = {component["componentId"]: component for component in inventory["components"]}
    assert_equal(plan["estateId"], inventory["estateId"], "migration estate")
    assert_equal(plan["targetRelease"], rules["targetRelease"], "migration target release")
    assert_equal(len(plan["steps"]), len(inventory["components"]), "migration component coverage")
    assert_equal([step["order"] for step in plan["steps"]], list(range(1, len(plan["steps"]) + 1)), "step order")
    assert_equal(
        [step["componentId"] for step in plan["steps"]],
        [rule["componentId"] for rule in rules["steps"]],
        "pinned component sequence",
    )
    for step, rule in zip(plan["steps"], rules["steps"]):
        component = components.get(rule["componentId"])
        if component is None:
            raise VerificationError(f"snapshot component {rule['componentId']!r} missing from estate")
        assert_equal(step["componentName"], component["componentName"], f"{rule['componentId']} name")
        assert_equal(step["currentVersion"], component["version"], f"{rule['componentId']} current version")
        assert_equal(step["targetVersion"], rule["targetVersion"], f"{rule['componentId']} target")
        assert_equal(step["action"], rule["action"], f"{rule['componentId']} action")
        assert_equal(step["gatedBy"], rule["gatedBy"], f"{rule['componentId']} gates")


def run_module(manifest: Path, requirements: Path, snapshot: Path, inventory: Path, output: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "VCF_ARCH_MANIFEST": str(manifest),
            "VCF_ARCH_REQUIREMENTS": str(requirements),
            "VCF_ARCH_SNAPSHOT": str(snapshot),
            "VCF_ARCH_INVENTORY": str(inventory),
            "VCF_ARCH_OUTPUT": str(output),
        }
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$manifestData = Import-PowerShellDataFile -LiteralPath $env:VCF_ARCH_MANIFEST
$requiredModules = @($manifestData.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
if ($requiredModules -notcontains 'VMware.Sdk.Vcf.Installer') {
    throw 'The manifest does not require VMware.Sdk.Vcf.Installer.'
}
$modulePath = Join-Path (Split-Path -Parent $env:VCF_ARCH_MANIFEST) $manifestData.RootModule
$parseTokens = $null
$parseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath,
    [ref]$parseTokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw "PowerShell module parse error: $($parseErrors[0].Message)"
}
$sdkSerializers = @($moduleAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
        $node.Member.Value -eq 'ToJson'
}, $true))
if ($sdkSerializers.Count -lt 1) {
    throw 'The module does not invoke the SDK model JSON serializer.'
}
Import-Module $env:VCF_ARCH_MANIFEST -Force
$expectedExports = @(
    'Invoke-VcfInstallerSpecValidation',
    'New-VcfEstateMigrationPlan',
    'New-VcfGreenfieldArchitecture'
)
$actualExports = @(Get-Command -Module VcfArchitecture -CommandType Function | Select-Object -ExpandProperty Name | Sort-Object)
foreach ($expectedExport in $expectedExports) {
    if ($actualExports -notcontains $expectedExport) {
        throw "Missing module export: $expectedExport"
    }
}
$greenfieldCommand = Get-Command New-VcfGreenfieldArchitecture
$greenfieldCalls = @($greenfieldCommand.ScriptBlock.Ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() })
foreach ($requiredInitializer in @('Initialize-VcfInstallerSddcSpec', 'Initialize-VcfInstallerVsanSpec')) {
    if ($greenfieldCalls -notcontains $requiredInitializer) {
        throw "New-VcfGreenfieldArchitecture does not invoke $requiredInitializer."
    }
}
$validationCommand = Get-Command Invoke-VcfInstallerSpecValidation
$validationCalls = @($validationCommand.ScriptBlock.Ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'Invoke-VcfInstallerValidateSddcSpec'
}, $true))
if ($validationCalls.Count -lt 1 -or ($validationCalls.Extent.Text -join "`n") -notmatch '-SddcSpec\s+\$SddcSpec') {
    throw 'Invoke-VcfInstallerSpecValidation does not pass its SDK model to Invoke-VcfInstallerValidateSddcSpec.'
}
New-VcfGreenfieldArchitecture -RequirementsPath $env:VCF_ARCH_REQUIREMENTS -CompatibilitySnapshotPath $env:VCF_ARCH_SNAPSHOT -OutputDirectory $env:VCF_ARCH_OUTPUT | Out-Null
New-VcfEstateMigrationPlan -InventoryPath $env:VCF_ARCH_INVENTORY -CompatibilitySnapshotPath $env:VCF_ARCH_SNAPSHOT -OutputPath (Join-Path $env:VCF_ARCH_OUTPUT 'migration-plan.json') | Out-Null
"""
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise VerificationError(f"PowerShell module execution failed:\n{result.stdout.strip()}")


def verify() -> None:
    # Phase one is deliberately isolated: validate the submitted installer artifact
    # against the upstream SddcSpec schema before loading or checking any other input.
    openapi = load_json(OPENAPI_PATH)
    sddc = load_json(SDDC_ARTIFACT)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("installer OpenAPI document has no components.schemas.SddcSpec") from exc
    validate_schema(sddc, sddc_schema, openapi, "$")

    actual_digest = hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()
    assert_equal(actual_digest, OPENAPI_SHA256, "installer OpenAPI fixture digest")
    assert_equal(openapi.get("info", {}).get("version"), "9.1.0.0", "installer OpenAPI version")

    requirements_path = ROOT / "fixtures" / "greenfield-requirements.json"
    inventory_path = ROOT / "fixtures" / "existing-estate.json"
    snapshot_path = ROOT / "compatibility" / "compatibility-snapshot.json"
    decision_path = ROOT / "artifacts" / "architecture-decision.json"
    plan_path = ROOT / "artifacts" / "migration-plan.json"
    manifest = ROOT / "VcfArchitecture" / "VcfArchitecture.psd1"
    module = ROOT / "VcfArchitecture" / "VcfArchitecture.psm1"

    requirements = load_json(requirements_path)
    inventory = load_json(inventory_path)
    snapshot = load_json(snapshot_path)
    research = load_json(RESEARCH_ARTIFACT)
    decision = load_json(decision_path)
    plan = load_json(plan_path)
    decision_schema = load_json(ROOT / "schemas" / "architecture-decision.schema.json")
    plan_schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")

    validate_schema(decision, decision_schema, decision_schema, "$")
    validate_schema(plan, plan_schema, plan_schema, "$")
    check_research(research)
    check_architecture(sddc, decision, requirements, snapshot)
    check_migration(plan, inventory, snapshot)

    if not manifest.is_file() or not module.is_file():
        raise VerificationError("PowerShell module manifest and implementation are required")
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        temp = Path(temp_name)
        generated = temp / "generated"
        run_module(manifest, requirements_path, snapshot_path, inventory_path, generated)
        generated_sddc = load_json(generated / "sddc-spec.json")
        validate_schema(generated_sddc, sddc_schema, openapi, "$")
        assert_equal(generated_sddc, sddc, "module-generated SddcSpec")
        assert_equal(
            load_json(generated / "architecture-decision.json"),
            decision,
            "module-generated architecture decision",
        )
        assert_equal(load_json(generated / "migration-plan.json"), plan, "module-generated migration plan")

        changed_requirements = copy.deepcopy(requirements)
        changed_requirements["capacity"]["requiredMemoryGiB"] = 1800
        changed_requirements["sddcId"] = "lab99-m01"
        changed_requirements["vcfInstanceName"] = "LAB99-VCF01"
        changed_requirements["naming"]["hostPrefix"] = "lab99-m01-esx"
        changed_requirements["naming"]["datacenterName"] = "lab99-m01-dc01"
        changed_requirements["naming"]["clusterName"] = "lab99-m01-cl01"
        changed_requirements["naming"]["datastoreName"] = "lab99-m01-vsan01"
        changed_path = temp / "changed-requirements.json"
        changed_path.write_text(json.dumps(changed_requirements, indent=2) + "\n", encoding="utf-8")
        changed_output = temp / "changed"
        run_module(manifest, changed_path, snapshot_path, inventory_path, changed_output)
        changed_sddc = load_json(changed_output / "sddc-spec.json")
        changed_decision = load_json(changed_output / "architecture-decision.json")
        validate_schema(changed_sddc, sddc_schema, openapi, "$")
        validate_schema(changed_decision, decision_schema, decision_schema, "$")
        expected_changed = selected_candidate(expected_candidates(changed_requirements, snapshot))
        assert_equal(changed_decision["selectedHostCount"], expected_changed["hostCount"], "recalculated host count")
        assert_equal(len(changed_sddc["hostSpecs"]), expected_changed["hostCount"], "recalculated host list")
        check_architecture(changed_sddc, changed_decision, changed_requirements, snapshot)

        osa_requirements = copy.deepcopy(requirements)
        for network in osa_requirements["networks"]:
            if network["networkType"] in {"MANAGEMENT", "VMOTION", "VSAN"}:
                octets = network["endIpAddress"].split(".")
                octets[-1] = "43"
                network["endIpAddress"] = ".".join(octets)
        osa_snapshot = copy.deepcopy(snapshot)
        next(
            option for option in osa_snapshot["storageOptions"] if option["architecture"] == "ESA"
        )["supported"] = False
        osa_requirements_path = temp / "osa-requirements.json"
        osa_snapshot_path = temp / "osa-snapshot.json"
        osa_requirements_path.write_text(json.dumps(osa_requirements, indent=2) + "\n", encoding="utf-8")
        osa_snapshot_path.write_text(json.dumps(osa_snapshot, indent=2) + "\n", encoding="utf-8")
        osa_output = temp / "osa-selected"
        run_module(manifest, osa_requirements_path, osa_snapshot_path, inventory_path, osa_output)
        osa_sddc = load_json(osa_output / "sddc-spec.json")
        osa_decision = load_json(osa_output / "architecture-decision.json")
        validate_schema(osa_sddc, sddc_schema, openapi, "$")
        validate_schema(osa_decision, decision_schema, decision_schema, "$")
        check_architecture(osa_sddc, osa_decision, osa_requirements, osa_snapshot)
        assert_equal(osa_decision["selectedStorageArchitecture"], "OSA", "supported OSA selection")

        changed_inventory = copy.deepcopy(inventory)
        changed_inventory["estateId"] = "lab99-vcf90-test"
        changed_inventory["components"][0]["componentName"] = "VCF Operations Test Estate"
        changed_inventory["components"][0]["version"] = "9.0.2.1"
        changed_inventory_path = temp / "changed-inventory.json"
        changed_inventory_path.write_text(json.dumps(changed_inventory, indent=2) + "\n", encoding="utf-8")
        changed_inventory_output = temp / "changed-inventory"
        run_module(
            manifest,
            requirements_path,
            snapshot_path,
            changed_inventory_path,
            changed_inventory_output,
        )
        changed_plan = load_json(changed_inventory_output / "migration-plan.json")
        validate_schema(changed_plan, plan_schema, plan_schema, "$")
        check_migration(changed_plan, changed_inventory, snapshot)


def main() -> int:
    try:
        verify()
    except (VerificationError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 architecture artifacts and PowerShell module verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
