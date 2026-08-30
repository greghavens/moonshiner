#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "dist" / "architecture.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
MIGRATION_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
MODULE_PATH = ROOT / "src" / "VcfFleetArchitecture" / "VcfFleetArchitecture.psd1"
MODULE_IMPLEMENTATION_PATH = ROOT / "src" / "VcfFleetArchitecture" / "VcfFleetArchitecture.psm1"
EXERCISE_PATH = ROOT / ".moonshiner" / "exercise.ps1"
RESEARCH_PATH = ROOT / "research" / "consulted-sources.json"


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
        fail(f"missing required file: {display_path}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON in {display_path}: {exc}")


def resolve_pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    value = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
    return value


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
        return isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(instance)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    fail(f"unsupported schema type: {expected}")


def validate_json(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate_json(instance, resolve_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")

    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: value is outside the schema enum")

    for branch in schema.get("allOf", []):
        validate_json(instance, branch, root_schema, path)

    if "anyOf" in schema:
        successes = 0
        for branch in schema["anyOf"]:
            try:
                validate_json(instance, branch, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if successes == 0:
            fail(f"{path}: value does not match any anyOf branch")

    if "oneOf" in schema:
        successes = 0
        for branch in schema["oneOf"]:
            try:
                validate_json(instance, branch, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if successes != 1:
            fail(f"{path}: value must match exactly one oneOf branch")

    expected_type = schema.get("type")
    if expected_type is not None:
        if isinstance(expected_type, list):
            if not any(type_matches(instance, candidate) for candidate in expected_type):
                fail(f"{path}: expected one of the declared schema types")
        elif not type_matches(instance, expected_type):
            fail(f"{path}: expected schema type {expected_type}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                fail(f"{path}: missing required property {name!r}")

        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate_json(value, properties[name], root_schema, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json(value, schema["additionalProperties"], root_schema, f"{path}.{name}")

        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                fail(f"{path}: duplicate array item")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_json(value, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"invalid regular expression in schema at {path}: {exc}")
            if matched is None:
                fail(f"{path}: string does not match schema pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is below exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is above exclusiveMaximum")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


EXPECTED_HASHES = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "fixtures/estate-inventory.json": "f0a7a9eaa3ff12f3836c4ccb42e6b2424a90d86be69d4250dfb280790ccbfd4d",
    "fixtures/compatibility-snapshot.json": "fb4f9378a6a77bb56da6ac3593f65ff87384631324c506c8e54e6e819fb9c628",
    "schemas/migration-plan.schema.json": "5f79a8515112314a676ab7f2f935bb1a1ff79f6a7f7777af045314bf580f77ae"
}


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            fail(f"protected input changed: {relative}")


def index_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            fail(f"{label} has a missing {key}")
        if value in result:
            fail(f"duplicate {label} {key}: {value}")
        result[value] = item
    return result


ISO_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def verify_research_record() -> None:
    record = load_json(RESEARCH_PATH)
    if not isinstance(record, dict):
        fail("research/consulted-sources.json must contain a JSON object")

    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("research record must contain at least one consulted source")

    seen_urls: set[str] = set()
    allowed_domains = ("broadcom.com", "vmware.com")
    required_fields = {"title", "publisher", "url", "consultedAt", "claimsChecked"}
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        if not isinstance(source, dict) or not required_fields.issubset(source):
            fail(f"{label} is missing a required field")

        for field in ("title", "publisher", "url", "consultedAt"):
            if not isinstance(source[field], str) or not source[field].strip():
                fail(f"{label} has an invalid {field}")

        if ISO_INSTANT.fullmatch(source["consultedAt"]) is None:
            fail(f"{label} consultedAt must be an ISO 8601 timestamp with a timezone")

        claims = source["claimsChecked"]
        if (
            not isinstance(claims, list)
            or not claims
            or any(not isinstance(claim, str) or not claim.strip() for claim in claims)
        ):
            fail(f"{label} claimsChecked must contain non-empty claim summaries")

        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
            fail(f"{label} must use a public HTTPS URL")
        if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains):
            fail(f"{label} is not an official Broadcom or VMware source")
        if source["url"] in seen_urls:
            fail(f"duplicate research source URL: {source['url']}")
        seen_urls.add(source["url"])


def verify_module_source_contract() -> None:
    try:
        MODULE_IMPLEMENTATION_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing module implementation")
    except UnicodeDecodeError as exc:
        fail(f"module implementation is not UTF-8: {exc}")

    parser_script = r"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCFARCH_MODULE_PATH, [ref] $tokens, [ref] $parseErrors
)
if ($parseErrors.Count -gt 0) {
    $parseErrors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
    exit 2
}
$commandAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
[ordered]@{
    commands = @($commandAsts | ForEach-Object { $_.GetCommandName() })
    commandTexts = @($commandAsts | ForEach-Object { $_.Extent.Text })
    functions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
    }, $true) | ForEach-Object { $_.Name })
    types = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.TypeExpressionAst]
    }, $true) | ForEach-Object { $_.TypeName.FullName })
} | ConvertTo-Json -Compress
"""
    try:
        parser_environment = os.environ.copy()
        parser_environment["VCFARCH_MODULE_PATH"] = str(MODULE_IMPLEMENTATION_PATH)
        parsed = subprocess.run(
            [
                "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", parser_script
            ],
            cwd=ROOT,
            env=parser_environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False
        )
    except FileNotFoundError:
        fail("pwsh is required by this task")
    except subprocess.TimeoutExpired:
        fail("PowerShell module parsing timed out")
    if parsed.returncode != 0:
        detail = parsed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        fail(f"module implementation has invalid PowerShell syntax{suffix}")
    try:
        syntax = json.loads(parsed.stdout)
    except json.JSONDecodeError:
        fail("PowerShell parser returned an invalid module analysis")

    command_names = {name for name in syntax["commands"] if isinstance(name, str)}
    constructor_names = {
        name for name in command_names
        if re.fullmatch(r"Initialize-VcfInstaller[A-Za-z0-9]+Spec", name)
    }
    if "Initialize-VcfInstallerSddcSpec" not in constructor_names:
        fail("module must build the SddcSpec with genuine VCF Installer model constructors")

    normalized_commands = {name.casefold() for name in command_names}
    if normalized_commands.intersection({"install-module", "save-module"}):
        fail("module implementation contains forbidden module installation or copying")
    if normalized_commands.intersection({"invoke-webrequest", "invoke-restmethod", "curl", "wget"}):
        fail("module implementation contains a forbidden direct HTTP client")
    if any(
        isinstance(name, str)
        and re.fullmatch(r"(?i)Initialize-VcfInstaller[A-Za-z0-9]+Spec", name)
        for name in syntax["functions"]
    ):
        fail("module implementation contains an imitated VCF Installer constructor")
    if any(
        name.casefold() in {"set-alias", "new-alias"}
        and re.search(r"(?i)\bInitialize-VcfInstaller", text)
        for name, text in zip(syntax["commands"], syntax["commandTexts"])
        if isinstance(name, str) and isinstance(text, str)
    ):
        fail("module implementation contains an intercepted VCF Installer constructor")
    if any(
        isinstance(name, str)
        and name.casefold() in {"system.net.http.httpclient", "system.net.webclient"}
        for name in syntax["types"]
    ):
        fail("module implementation contains a forbidden direct HTTP client")
    if any(
        name.casefold() == "new-object"
        and re.search(r"(?i)\bSystem\.Net\.(?:Http\.HttpClient|WebClient)\b", text)
        for name, text in zip(syntax["commands"], syntax["commandTexts"])
        if isinstance(name, str) and isinstance(text, str)
    ):
        fail("module implementation contains a forbidden direct HTTP client")


def verify_sddc_design(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    authority = snapshot["topologyAuthority"]
    topology = inventory["topology"]
    installer_input = inventory["installerInput"]

    if artifact.get("version") != snapshot["targetRelease"]:
        fail("SddcSpec version does not match the pinned target release")
    if artifact.get("workflowType") != "VCF":
        fail("SddcSpec workflowType must be VCF")
    if artifact.get("sddcId") != installer_input["sddcId"]:
        fail("SddcSpec sddcId does not match the inventory")
    if artifact.get("vcfInstanceName") != installer_input["vcfInstanceName"]:
        fail("SddcSpec vcfInstanceName does not match the inventory")

    expected_hosts = topology["hosts"]
    host_specs = artifact.get("hostSpecs")
    if not isinstance(host_specs, list) or [host.get("hostname") for host in host_specs] != expected_hosts:
        fail("SddcSpec hostSpecs must name the four inventoried hosts in order")
    if len(host_specs) != authority["minimumHostCount"]:
        fail("SddcSpec does not hold exactly at the pinned minimum host count")

    components = index_unique(inventory["components"], "componentId", "inventory component")
    rules = index_unique(snapshot["componentRules"], "componentId", "component rule")
    if set(components) != set(rules):
        fail("the compatibility snapshot and inventory name different components")

    vcenter = artifact.get("vcenterSpec", {})
    if vcenter.get("vcenterHostname") != installer_input["vcenter"]["hostname"]:
        fail("vcenterSpec hostname does not match the inventory")
    if vcenter.get("rootVcenterPassword") != installer_input["vcenter"]["rootPasswordReference"]:
        fail("vcenterSpec must preserve the fixture's secret reference")
    if vcenter.get("useExistingDeployment") is not True:
        fail("vcenterSpec must reuse the existing deployment")
    if vcenter.get("version") != rules["vcenter-01"]["conversionReadyVersion"]:
        fail("vcenterSpec must use the pinned conversion-ready version")

    nsx = artifact.get("nsxtSpec", {})
    if nsx.get("useExistingDeployment") is not True:
        fail("nsxtSpec must reuse the existing deployment")
    if nsx.get("version") != rules["nsx-01"]["conversionReadyVersion"]:
        fail("nsxtSpec must use the pinned conversion-ready version")
    if [item.get("hostname") for item in nsx.get("nsxtManagers", [])] != installer_input["nsx"]["managers"]:
        fail("nsxtSpec managers do not match the inventory")
    if nsx.get("vipFqdn") != installer_input["nsx"]["vipFqdn"]:
        fail("nsxtSpec VIP does not match the inventory")

    if artifact.get("dnsSpec") != installer_input["dns"]:
        fail("dnsSpec does not match the inventory")
    if artifact.get("ntpServers") != installer_input["ntpServers"]:
        fail("ntpServers do not match the inventory")
    if artifact.get("networkSpecs") != installer_input["networks"]:
        fail("networkSpecs do not match the inventory")
    if artifact.get("datastoreSpec", {}).get("existingDatastoreName") != topology["storage"]["datastoreName"]:
        fail("datastoreSpec must preserve the existing vSAN datastore")

    resource_pools = artifact.get("clusterSpec", {}).get("resourcePoolSpecs")
    if resource_pools != installer_input["cluster"]["resourcePools"]:
        fail("clusterSpec must provide the consolidated management and tenant resource pools")

    for service_name in ("fleetLcmSpec", "sddcLcmSpec", "fleetDepotSpec"):
        service = artifact.get(service_name)
        if service != {"version": snapshot["targetRelease"], "size": "small"}:
            fail(f"{service_name} must describe the target fleet service")

    architecture = artifact.get("architecture")
    expected_architecture = {
        "topology": authority["design"],
        "siteCount": authority["siteCount"],
        "managementDomain": {
            "hostCount": authority["minimumHostCount"],
            "principalStorage": authority["principalStorage"],
            "hosts": expected_hosts,
            "resourcePools": installer_input["cluster"]["resourcePools"]
        },
        "fleet": {
            "name": installer_input["fleetName"],
            "instanceName": installer_input["vcfInstanceName"],
            "targetVersion": snapshot["targetRelease"]
        },
        "generatedBy": {
            "module": "VMware.Sdk.Vcf.Installer",
            "moduleVersion": "13.5.0.25380678",
            "model": "SddcSpec"
        }
    }
    if architecture != expected_architecture:
        fail("architecture extension does not match the pinned single-site consolidated design")


def verify_migration_plan(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    migration_schema = load_json(MIGRATION_SCHEMA_PATH)
    validate_json(plan, migration_schema, migration_schema)

    expected_target = {
        "fleetVersion": snapshot["targetRelease"],
        "topology": snapshot["topologyAuthority"]["design"],
        "siteCount": snapshot["topologyAuthority"]["siteCount"],
        "hostCount": snapshot["topologyAuthority"]["minimumHostCount"],
        "sddcId": inventory["installerInput"]["sddcId"]
    }
    if plan["planVersion"] != "1.0" or plan["estateId"] != inventory["estateId"] or plan["target"] != expected_target:
        fail("migration plan identity or target does not match the protected inputs")

    components = index_unique(inventory["components"], "componentId", "inventory component")
    rules = index_unique(snapshot["componentRules"], "componentId", "component rule")
    gates = index_unique(snapshot["gates"], "id", "compatibility gate")

    expected_transitions: dict[str, tuple[str, dict[str, Any]]] = {}
    for component_id, rule in rules.items():
        if components[component_id]["version"] != rule["inventoryVersion"]:
            fail(f"pinned inventory version mismatch for {component_id}")
        previous = rule["inventoryVersion"]
        for transition in rule["transitions"]:
            if transition["sourceVersion"] != previous:
                fail(f"broken pinned transition chain for {component_id}")
            previous = transition["targetVersion"]
            expected_transitions[transition["stepId"]] = (component_id, transition)
        if previous != rule["finalVersion"]:
            fail(f"pinned transition chain does not reach the final target for {component_id}")

    steps = plan["steps"]
    ordered_ids = snapshot["orderedStepIds"]
    if [step["sequence"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration step sequence values must be contiguous and one-based")
    if [step["stepId"] for step in steps] != ordered_ids:
        fail("migration steps are not in the pinned supported order")
    if set(ordered_ids) != set(expected_transitions) or len(steps) != len(expected_transitions):
        fail("migration plan must contain every pinned transition exactly once")

    seen: set[str] = set()
    covered: set[str] = set()
    for step in steps:
        component_id, transition = expected_transitions[step["stepId"]]
        rule = rules[component_id]
        expected_gates = [
            {"id": gate_id, "condition": gates[gate_id]["condition"]}
            for gate_id in transition["gates"]
        ]
        expected_step = {
            "sequence": step["sequence"],
            "stepId": transition["stepId"],
            "componentId": component_id,
            "component": rule["component"],
            "sourceVersion": transition["sourceVersion"],
            "targetProduct": transition["targetProduct"],
            "targetVersion": transition["targetVersion"],
            "action": transition["action"],
            "gates": expected_gates,
            "dependsOn": transition["dependsOn"]
        }
        if step != expected_step:
            fail(f"migration step {step['stepId']} does not match the pinned compatibility transition")
        if not set(step["dependsOn"]).issubset(seen):
            fail(f"migration step {step['stepId']} runs before one of its dependencies")
        seen.add(step["stepId"])
        covered.add(component_id)

    if covered != set(components):
        fail("migration plan does not name every estate component")


def run_module(inventory_path: Path, snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    command = [
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(EXERCISE_PATH),
        "-ModulePath",
        str(MODULE_PATH),
        "-InventoryPath",
        str(inventory_path),
        "-CompatibilitySnapshotPath",
        str(snapshot_path),
        "-OutputPath",
        str(output_path)
    ]
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False
        )
    except FileNotFoundError:
        fail("pwsh is required by this task")
    except subprocess.TimeoutExpired:
        fail("PowerShell module execution timed out")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        fail(f"PowerShell module execution failed{suffix}")
    return load_json(output_path)


def write_fixture(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n"
    )


def verify_lf_without_bom(path: Path, label: str) -> None:
    artifact_bytes = path.read_bytes()
    if artifact_bytes.startswith(b"\xef\xbb\xbf"):
        fail(f"{label} must be UTF-8 without a BOM")
    if not artifact_bytes.endswith(b"\n") or artifact_bytes.endswith((b"\r\n", b"\n\n")):
        fail(f"{label} must end in exactly one LF")


def verify_module_reproduces(
    artifact: dict[str, Any],
    openapi: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any]
) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-0102-") as temporary:
        temporary_root = Path(temporary)
        generated_path = temporary_root / "architecture.json"
        generated = run_module(INVENTORY_PATH, SNAPSHOT_PATH, generated_path)
        verify_lf_without_bom(generated_path, "generated architecture artifact")
        if generated != artifact or generated_path.read_bytes() != ARTIFACT_PATH.read_bytes():
            fail("the PowerShell module does not byte-for-byte reproduce dist/architecture.json from the protected inputs")

        varied_inventory = json.loads(json.dumps(inventory))
        varied_snapshot = json.loads(json.dumps(snapshot))
        varied_inventory["estateId"] = "portable-estate-02"
        varied_inventory["topology"]["hosts"] = ["alt-esx01", "alt-esx02", "alt-esx03", "alt-esx04"]
        varied_inventory["topology"]["storage"]["datastoreName"] = "alternateVsanDatastore"
        varied_input = varied_inventory["installerInput"]
        varied_input["sddcId"] = "alt-m01"
        varied_input["fleetName"] = "Alternate-VCF-Fleet"
        varied_input["vcfInstanceName"] = "Alternate-VCF-01"
        varied_input["vcenter"]["hostname"] = "alt-vc01"
        varied_input["vcenter"]["rootPasswordReference"] = "${ALT_VC_ROOT_PWD}"
        varied_input["cluster"]["datacenterName"] = "ALT-DC01"
        varied_input["cluster"]["clusterName"] = "ALT-MGMT01"
        varied_input["cluster"]["resourcePools"][0]["name"] = "Alternate Management"
        varied_input["cluster"]["resourcePools"][1]["name"] = "Alternate Tenant"
        varied_input["nsx"]["managers"] = ["alt-nsx01", "alt-nsx02", "alt-nsx03"]
        varied_input["nsx"]["vipFqdn"] = "nsx.alt.example.com"
        varied_input["sddcManagerHostname"] = "alt-sddc01"
        varied_snapshot["gates"][0]["condition"] = "The alternate target remains a single-site consolidated management domain."
        varied_snapshot["componentRules"][0]["transitions"][0]["action"] = "upgrade-for-alternate-estate"

        varied_inventory_path = temporary_root / "estate-inventory.json"
        varied_snapshot_path = temporary_root / "compatibility-snapshot.json"
        varied_output_path = temporary_root / "varied-architecture.json"
        write_fixture(varied_inventory_path, varied_inventory)
        write_fixture(varied_snapshot_path, varied_snapshot)
        varied_artifact = run_module(varied_inventory_path, varied_snapshot_path, varied_output_path)
        verify_lf_without_bom(varied_output_path, "architecture artifact generated from alternate inputs")
        validate_json(varied_artifact, {"$ref": "#/components/schemas/SddcSpec"}, openapi)
        verify_sddc_design(varied_artifact, varied_inventory, varied_snapshot)
        varied_plan = varied_artifact.get("migrationPlan")
        if not isinstance(varied_plan, dict):
            fail("module output generated from alternate inputs is missing migrationPlan")
        verify_migration_plan(varied_plan, varied_inventory, varied_snapshot)


def main() -> None:
    # This is deliberately the first verification operation: validate the complete
    # architecture document as SddcSpec before consulting fixture or snapshot data.
    artifact = load_json(ARTIFACT_PATH)
    openapi = load_json(OPENAPI_PATH)
    validate_json(artifact, {"$ref": "#/components/schemas/SddcSpec"}, openapi)

    verify_lf_without_bom(ARTIFACT_PATH, "architecture artifact")

    verify_protected_inputs()
    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    verify_research_record()
    verify_module_source_contract()
    verify_sddc_design(artifact, inventory, snapshot)

    migration_plan = artifact.get("migrationPlan")
    if not isinstance(migration_plan, dict):
        fail("architecture artifact is missing migrationPlan")
    verify_migration_plan(migration_plan, inventory, snapshot)
    verify_module_reproduces(artifact, openapi, inventory, snapshot)
    print("verification passed")


if __name__ == "__main__":
    try:
        main()
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
