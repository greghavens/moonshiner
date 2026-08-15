#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0067.

Live research calls remain a trace requirement. The verifier checks the local
research log without making network requests and grades compatibility decisions
against the pinned local authority.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "migration-plan.json"
INSTALLER_SPEC_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PLAN_SCHEMA_PATH = ROOT / "schema/migration-plan.schema.json"
INVENTORY_PATH = ROOT / "estate/inventory.json"
SNAPSHOT_PATH = ROOT / "authority/compatibility-snapshot.json"
MODULE_PATH = ROOT / "src/VcfBrownfieldArchitecture/VcfBrownfieldArchitecture.psm1"
MANIFEST_PATH = ROOT / "src/VcfBrownfieldArchitecture/VcfBrownfieldArchitecture.psd1"
RESEARCH_PATH = ROOT / "research/consulted-sources.md"


class ValidationFailure(Exception):
    pass


def fail(message: str) -> None:
    raise ValidationFailure(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"unsupported non-local schema reference: {pointer}")
    value = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, ValueError, TypeError):
            fail(f"unresolvable schema reference: {pointer}")
    return value


def type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    fail(f"unsupported JSON Schema type: {expected}")


def schema_errors(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> list[str]:
    """Validate the JSON Schema keywords used by the pinned OpenAPI and plan schemas."""
    errors: list[str] = []
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]

    if "$ref" in schema:
        return schema_errors(instance, json_pointer(root_schema, schema["$ref"]), root_schema, path)
    if instance is None and schema.get("nullable") is True:
        return []

    for subschema in schema.get("allOf", []):
        errors.extend(schema_errors(instance, subschema, root_schema, path))
    if "anyOf" in schema:
        options = [schema_errors(instance, item, root_schema, path) for item in schema["anyOf"]]
        if not any(not option for option in options):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, item, root_schema, path) for item in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must satisfy exactly one oneOf branch (matched {matches})")
    if "not" in schema and not schema_errors(instance, schema["not"], root_schema, path):
        errors.append(f"{path}: matches a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        alternatives = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, choice) for choice in alternatives):
            errors.append(f"{path}: expected type {expected_type!r}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(schema_errors(value, properties[key], root_schema, f"{path}.{key}"))
                continue
            matches = [sub for pattern, sub in pattern_properties.items() if re.search(pattern, key)]
            if matches:
                for subschema in matches:
                    errors.extend(schema_errors(value, subschema, root_schema, f"{path}.{key}"))
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                errors.extend(schema_errors(value, additional, root_schema, f"{path}.{key}"))
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
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, schema["items"], root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: string does not match {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid pattern in protected schema at {path}: {exc}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema:
            bound = schema["exclusiveMinimum"]
            if isinstance(bound, bool):
                if bound and "minimum" in schema and instance <= schema["minimum"]:
                    errors.append(f"{path}: value is not above exclusive minimum")
            elif instance <= bound:
                errors.append(f"{path}: value is not above exclusive minimum")
        if "exclusiveMaximum" in schema:
            bound = schema["exclusiveMaximum"]
            if isinstance(bound, bool):
                if bound and "maximum" in schema and instance >= schema["maximum"]:
                    errors.append(f"{path}: value is not below exclusive maximum")
            elif instance >= bound:
                errors.append(f"{path}: value is not below exclusive maximum")
    return errors


def require_schema_valid(instance: Any, schema: Any, root_schema: Any, label: str) -> None:
    errors = schema_errors(instance, schema, root_schema)
    if errors:
        shown = "; ".join(errors[:8])
        suffix = f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""
        fail(f"{label} schema validation failed: {shown}{suffix}")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def find_network(spec: dict[str, Any], network_type: str) -> dict[str, Any]:
    matches = [item for item in spec.get("networkSpecs", []) if item.get("networkType") == network_type]
    if len(matches) != 1:
        fail(f"targetSddcSpec must contain exactly one {network_type} network")
    return matches[0]


def check_research_log() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing required file: research/consulted-sources.md")
    except UnicodeDecodeError as exc:
        fail(f"research/consulted-sources.md is not valid UTF-8: {exc}")

    # Accept bullets, numbered entries, or heading-separated entries. Research is
    # intentionally not checked against a fixed answer-key URL list: source choice
    # belongs to the task author, while these structural checks keep the artifact
    # useful and rule out fixture, loopback, and fictional research URLs.
    blocks = re.split(
        r"\n(?=\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+))|\n\s*\n+",
        text,
    )
    url_pattern = re.compile(r"https://[^\s)>\]}]+", re.IGNORECASE)
    dated_source_count = 0
    seen_urls: set[str] = set()
    for block in blocks:
        matches = list(url_pattern.finditer(block))
        if not matches:
            continue
        month = (
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)"
        )
        date_value = (
            rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{month}\s+\d{{1,2}},?\s+\d{{4}}|"
            rf"\d{{1,2}}\s+{month}\s+\d{{4}}|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{4}})"
        )
        date_match = re.search(
            rf"\b(?:access(?:ed|\s+date)?|retrieved|consulted|visited)"
            rf"(?:\s+on)?\s*:?[ ]*{date_value}\b",
            block,
            re.IGNORECASE,
        )
        if date_match is None:
            fail("each consulted source entry must include an access date")

        for match in matches:
            url = match.group(0).rstrip(".,;")
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or parsed.username or parsed.password:
                fail(f"research source is not a public HTTPS URL: {url}")
            official_host = (
                host == "broadcom.com"
                or host.endswith(".broadcom.com")
                or host == "vmware.com"
                or host.endswith(".vmware.com")
                or host == "vmware.github.io"
                or (host == "github.com" and parsed.path.lower().startswith("/vmware/"))
            )
            if not official_host:
                fail(f"research source is not published by Broadcom or VMware: {url}")
            if url in seen_urls:
                fail(f"duplicate research source URL: {url}")
            seen_urls.add(url)

            title_text = re.sub(r"[^A-Za-z0-9 ]", " ", block[: match.start()])
            if len(re.findall(r"[A-Za-z0-9]+", title_text)) < 2:
                fail(f"research source entry has no meaningful title: {url}")
            decision_text = block[match.end() :]
            if re.search(
                r"\b(?:fact|used?|decision|inform(?:ed)?|confirm(?:ed)?|"
                r"support(?:ed)?|requires?|sequenc(?:e|ing)|minimum|path|"
                r"upgrad(?:e|es|ed|ing)|needs?|must|cannot)\b",
                decision_text,
                re.IGNORECASE,
            ) is None:
                fail(f"research source entry does not state the fact or decision it informed: {url}")
            dated_source_count += 1

    if dated_source_count < 1:
        fail("research/consulted-sources.md must record a consulted Broadcom source")


def check_credential_placeholders(spec: dict[str, Any], inventory: dict[str, Any]) -> None:
    allowed = set(inventory["credentialPlaceholders"].values())

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if isinstance(child, str):
                    if re.search(r"password$", key, re.IGNORECASE) and child not in allowed:
                        fail(f"{child_path} is not an inventory-supplied credential placeholder")
                    if re.fullmatch(r"\$\{[^{}]+\}", child) and child not in allowed:
                        fail(f"{child_path} uses an unknown credential placeholder")
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(spec, "targetSddcSpec")


def check_installer_architecture(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    spec = plan["targetSddcSpec"]
    target = snapshot["targetVcfVersion"]
    domain = inventory["topology"]["managementDomain"]
    networking = inventory["networking"]
    appliances = inventory["appliances"]

    require_equal(spec.get("sddcId"), domain["name"], "targetSddcSpec.sddcId")
    require_equal(spec.get("workflowType"), "VCF", "targetSddcSpec.workflowType")
    require_equal(spec.get("version"), target, "targetSddcSpec.version")
    expected_hosts = [host["hostname"] for host in inventory["hosts"]]
    actual_hosts = [host.get("hostname") for host in spec.get("hostSpecs", [])]
    require_equal(actual_hosts, expected_hosts, "targetSddcSpec host order")
    require_equal(len(actual_hosts), snapshot["topologyRules"]["minimumHostCount"], "target host count")

    cluster = spec.get("clusterSpec", {})
    require_equal(cluster.get("datacenterName"), domain["datacenterName"], "target datacenter")
    require_equal(cluster.get("clusterName"), domain["clusterName"], "target cluster")
    require_equal(spec.get("datastoreSpec", {}).get("existingDatastoreName"), domain["datastoreName"], "target datastore")

    vcenter = spec.get("vcenterSpec", {})
    require_equal(vcenter.get("vcenterHostname"), appliances["vcenterFqdn"], "target vCenter FQDN")
    require_equal(vcenter.get("version"), target, "target vCenter version")
    require_equal(vcenter.get("useExistingDeployment"), True, "target vCenter reuse flag")
    require_equal(vcenter.get("rootVcenterPassword"), inventory["credentialPlaceholders"]["vcenterRootPassword"], "vCenter credential placeholder")

    manager = spec.get("sddcManagerSpec", {})
    require_equal(manager.get("hostname"), appliances["sddcManagerFqdn"], "target SDDC Manager FQDN")
    require_equal(manager.get("version"), target, "target SDDC Manager version")
    require_equal(manager.get("useExistingDeployment"), True, "target SDDC Manager reuse flag")
    require_equal(manager.get("rootPassword"), inventory["credentialPlaceholders"]["sddcManagerRootPassword"], "SDDC Manager root placeholder")
    require_equal(manager.get("sshPassword"), inventory["credentialPlaceholders"]["sddcManagerSshPassword"], "SDDC Manager SSH placeholder")

    nsx = spec.get("nsxtSpec", {})
    require_equal([node.get("hostname") for node in nsx.get("nsxtManagers", [])], appliances["nsxManagerFqdns"], "target NSX managers")
    require_equal(nsx.get("vipFqdn"), appliances["nsxVipFqdn"], "target NSX VIP")
    require_equal(nsx.get("version"), target, "target NSX version")
    require_equal(nsx.get("useExistingDeployment"), True, "target NSX reuse flag")
    for field, placeholder in (
        ("rootNsxtManagerPassword", "nsxRootPassword"),
        ("nsxtAdminPassword", "nsxAdminPassword"),
        ("nsxtAuditPassword", "nsxAuditPassword"),
    ):
        require_equal(nsx.get(field), inventory["credentialPlaceholders"][placeholder], f"NSX {field} placeholder")

    require_equal(spec.get("dnsSpec", {}).get("subdomain"), networking["dnsSubdomain"], "DNS subdomain")
    require_equal(spec.get("dnsSpec", {}).get("nameservers"), networking["dnsServers"], "DNS servers")
    require_equal(spec.get("ntpServers"), networking["ntpServers"], "NTP servers")
    for network_type, fixture_name in (("MANAGEMENT", "management"), ("VMOTION", "vmotion"), ("VSAN", "vsan")):
        actual = find_network(spec, network_type)
        fixture = networking[fixture_name]
        for key in ("vlanId", "subnet", "gateway", "subnetMask"):
            require_equal(actual.get(key), fixture[key], f"{network_type} {key}")

    fleet = find_network(spec, "FLEET_MANAGEMENT")
    fixture_pool = networking["managementServices"]
    for key in ("vlanId", "subnet", "gateway", "subnetMask"):
        require_equal(fleet.get(key), fixture_pool[key], f"FLEET_MANAGEMENT {key}")
    ranges = fleet.get("includeIpAddressRanges", [])
    if len(ranges) != 1:
        fail("FLEET_MANAGEMENT must contain the fixture's single management-services IP range")
    require_equal(ranges[0].get("startIpAddress"), fixture_pool["startIpAddress"], "management-services range start")
    require_equal(ranges[0].get("endIpAddress"), fixture_pool["endIpAddress"], "management-services range end")
    start = ipaddress.ip_address(ranges[0]["startIpAddress"])
    end = ipaddress.ip_address(ranges[0]["endIpAddress"])
    if int(end) - int(start) + 1 < snapshot["topologyRules"]["managementServicesMinimumIpCount"]:
        fail("management-services range has fewer addresses than the pinned minimum")
    pool_network = ipaddress.ip_network(fixture_pool["subnet"])
    if start not in pool_network or end not in pool_network:
        fail("management-services range is outside its declared subnet")
    for cidr in snapshot["topologyRules"]["reservedInternalCidrs"]:
        if pool_network.overlaps(ipaddress.ip_network(cidr)):
            fail(f"management network overlaps reserved internal CIDR {cidr}")

    operations = spec.get("vcfOperationsSpec", {})
    require_equal(operations.get("version"), target, "VCF Operations version")
    require_equal(operations.get("useExistingDeployment"), True, "VCF Operations reuse flag")
    require_equal([node.get("hostname") for node in operations.get("nodes", [])], [appliances["operationsFqdn"]], "VCF Operations nodes")
    require_equal(operations.get("adminUserPassword"), inventory["credentialPlaceholders"]["operationsAdminPassword"], "Operations credential placeholder")
    require_equal(spec.get("fleetLcmSpec", {}).get("version"), target, "Fleet LCM version")
    require_equal(spec.get("sddcLcmSpec", {}).get("version"), target, "SDDC LCM version")
    license_server = spec.get("licenseServerSpec", {})
    require_equal(license_server.get("hostname"), appliances["licenseServerFqdn"], "license server FQDN")
    require_equal(license_server.get("version"), target, "license server version")
    require_equal(license_server.get("useExistingDeployment"), False, "license server deployment flag")
    check_credential_placeholders(spec, inventory)


def check_plan_against_authority(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require_equal(plan["estateId"], inventory["estateId"], "estateId")
    require_equal(plan["sourceVcfVersion"], inventory["vcfVersion"], "sourceVcfVersion")
    require_equal(plan["targetVcfVersion"], snapshot["targetVcfVersion"], "targetVcfVersion")
    expected_path = [inventory["vcfVersion"], snapshot["targetVcfVersion"]]
    require_equal(plan["platformPath"], expected_path, "direct platformPath")
    supported = {(item["from"], item["to"]) for item in snapshot["supportedPlatformPaths"]}
    if tuple(expected_path) not in supported:
        fail("platformPath is not present in the pinned supported paths")

    require_equal(plan["topology"], inventory["topology"], "preserved topology")
    rules = sorted(snapshot["componentRules"], key=lambda item: item["order"])
    steps = plan["steps"]
    require_equal(len(steps), len(rules), "migration step count")
    inventory_components = {item["id"]: item for item in inventory["components"]}
    expected_component_ids = [rule["id"] for rule in rules]
    require_equal([step["componentId"] for step in steps], expected_component_ids, "ordered component sequence")
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "contiguous step order")

    for step, rule in zip(steps, rules):
        component = inventory_components.get(rule["id"])
        current = component["currentVersion"] if component else "NOT_INSTALLED"
        if current not in rule["allowedCurrentVersions"]:
            fail(f"fixture version {current!r} is not allowed for {rule['id']}")
        expected = {
            "order": rule["order"],
            "componentId": rule["id"],
            "componentName": rule["name"],
            "currentVersion": current,
            "targetVersion": rule["targetVersion"],
            "action": rule["action"],
            "gates": rule["requiredGates"],
        }
        require_equal(step, expected, f"step {rule['order']} ({rule['id']})")


def inspect_powershell() -> None:
    if not MANIFEST_PATH.is_file():
        fail("missing PowerShell module manifest")
    if not MODULE_PATH.is_file():
        fail("missing PowerShell module implementation")
    env = os.environ.copy()
    env["VCF_ARCH_MODULE_PATH"] = str(MODULE_PATH)
    env["VCF_ARCH_MANIFEST_PATH"] = str(MANIFEST_PATH)
    parser_script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_ARCH_MODULE_PATH,
    [ref]$tokens,
    [ref]$errors
)
$function = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'New-VcfBrownfieldMigrationPlan'
}, $true)
$commands = if ($null -ne $function) {
    @($function.Body.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
} else { @() }
$parameters = if ($null -ne $function -and $null -ne $function.Body.ParamBlock) {
    @($function.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
} else { @() }
$variables = if ($null -ne $function) {
    @($function.Body.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.VariableExpressionAst]
    }, $true) | ForEach-Object { $_.VariablePath.UserPath })
} else { @() }
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCF_ARCH_MANIFEST_PATH
$requiredModules = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ }
    elseif ($_.ModuleName) { $_.ModuleName }
    elseif ($_.Name) { $_.Name }
})
[pscustomobject]@{
    errors = @($errors | ForEach-Object { $_.Message })
    functionFound = ($null -ne $function)
    functionText = if ($null -ne $function) { $function.Extent.Text } else { '' }
    parameters = $parameters
    variables = $variables
    commands = $commands
    rootModule = $manifest.RootModule
    requiredModules = $requiredModules
    functionsToExport = @($manifest.FunctionsToExport)
} | ConvertTo-Json -Compress -Depth 5
"""
    try:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parser_script],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fail(f"PowerShell parser unavailable: {exc}")
    if result.returncode != 0:
        fail(f"PowerShell parser failed: {result.stderr.strip()}")
    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        fail(f"PowerShell parser returned invalid output: {result.stdout!r}")
    if parsed["errors"]:
        fail("PowerShell syntax errors: " + "; ".join(parsed["errors"]))
    if not parsed["functionFound"]:
        fail("New-VcfBrownfieldMigrationPlan is not implemented")
    require_equal(parsed["rootModule"], "VcfBrownfieldArchitecture.psm1", "manifest RootModule")
    if "VMware.Sdk.Vcf.SddcManager" not in parsed["requiredModules"]:
        fail("manifest does not require VMware.Sdk.Vcf.SddcManager")
    if "New-VcfBrownfieldMigrationPlan" not in parsed["functionsToExport"]:
        fail("manifest does not export New-VcfBrownfieldMigrationPlan")
    require_equal(
        set(parsed["parameters"]),
        {"InventoryPath", "CompatibilitySnapshotPath", "OutputPath"},
        "New-VcfBrownfieldMigrationPlan parameters",
    )
    for parameter in ("InventoryPath", "CompatibilitySnapshotPath", "OutputPath"):
        if parsed["variables"].count(parameter) < 2:
            fail(f"New-VcfBrownfieldMigrationPlan does not use -{parameter}")
    commands = {command.lower() for command in parsed["commands"]}
    initialize_commands = {command for command in commands if command.startswith("initialize-vcf")}
    if not initialize_commands:
        fail("New-VcfBrownfieldMigrationPlan does not invoke an Initialize-Vcf* model cmdlet")
    function_text = parsed["functionText"]
    if re.search(
        r"(?:\bConvertTo-Json\b|JsonSerializer\s*\]?\s*::\s*Serialize)",
        function_text,
        re.IGNORECASE,
    ) is None:
        fail("New-VcfBrownfieldMigrationPlan does not serialize its plan as JSON")
    if re.search(
        r"(?:(?:\bSet-Content\b|\bOut-File\b|::\s*WriteAll(?:Text|Bytes))"
        r"[\s\S]{0,300}\$OutputPath|>\s*\$OutputPath\b)",
        function_text,
        re.IGNORECASE,
    ) is None:
        fail("New-VcfBrownfieldMigrationPlan does not write JSON to -OutputPath")


def main() -> int:
    try:
        # Required ordering: validate the candidate's embedded target SddcSpec
        # against the installer specification before plan-schema or semantic checks.
        plan = load_json(PLAN_PATH)
        installer = load_json(INSTALLER_SPEC_PATH)
        if not isinstance(plan, dict):
            fail("migration-plan.json must contain an object")
        target_sddc_spec = plan.get("targetSddcSpec")
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
        require_schema_valid(target_sddc_spec, sddc_schema, installer, "targetSddcSpec (installer SddcSpec)")

        plan_schema = load_json(PLAN_SCHEMA_PATH)
        require_schema_valid(plan, plan_schema, plan_schema, "migration-plan.json")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        check_plan_against_authority(plan, inventory, snapshot)
        check_installer_architecture(plan, inventory, snapshot)
        check_research_log()
        inspect_powershell()
    except (ValidationFailure, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: installer schema, migration schema, pinned compatibility, topology, and PowerShell module")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
