#!/usr/bin/env python3
"""Offline acceptance verifier for vcfarch-0103.

Research provenance is checked as an artifact without making network requests.
All compatibility assertions use the protected snapshot in this seed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "migration-plan.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas" / "brownfield-migration-plan.schema.json"
INVENTORY = ROOT / "inventory" / "estate.json"
SNAPSHOT = ROOT / "compatibility" / "compatibility-snapshot.json"
RESEARCH_SOURCES = ROOT / "research-sources.json"
MODULE_DIR = ROOT / "VcfBrownfieldArchitecture"

PROTECTED_HASHES = {
    "inventory/estate.json": "63c85510022e679c516762189349837b061fce77ed215a93f4032f40aadb8bf5",
    "compatibility/compatibility-snapshot.json": "058a421b845430fef18809fdaaeedb11e8d682b247d97c1775f50a1e46800304",
    "schemas/brownfield-migration-plan.schema.json": "a14b8739510ab4b47f10229f87d50fe0ff9089ddfcbc4ea981a9a7295ef9aa70",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/licenses/vcf-api-specs-Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value = document
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[key]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {ref}")
    return value


def type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def schema_errors(
    value: Any,
    schema: Any,
    root_schema: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema/OpenAPI keywords used by the pinned documents."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: disallowed by schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema"]

    if "$ref" in schema:
        target = pointer(root_schema, schema["$ref"])
        siblings = {key: item for key, item in schema.items() if key != "$ref"}
        errors = schema_errors(value, target, root_schema, path)
        if siblings:
            errors.extend(schema_errors(value, siblings, root_schema, path))
        return errors

    errors: list[str] = []

    for subschema in schema.get("allOf", []):
        errors.extend(schema_errors(value, subschema, root_schema, path))

    if "anyOf" in schema:
        matches = [
            not schema_errors(value, subschema, root_schema, path)
            for subschema in schema["anyOf"]
        ]
        if not any(matches):
            errors.append(f"{path}: does not match anyOf")

    if "oneOf" in schema:
        matches = sum(
            not schema_errors(value, subschema, root_schema, path)
            for subschema in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: must match exactly one oneOf branch")

    if "not" in schema and not schema_errors(value, schema["not"], root_schema, path):
        errors.append(f"{path}: matches a forbidden schema")

    if value is None and schema.get("nullable") is True:
        return errors

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(value, item) for item in expected_types):
            return errors + [f"{path}: expected type {expected_type}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                errors.extend(schema_errors(item, properties[name], root_schema, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    schema_errors(item, schema["additionalProperties"], root_schema, child_path)
                )
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(set(encoded)) != len(encoded):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, (dict, bool)):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: string does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid pattern in protected schema at {path}: {exc}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
            if value <= schema["exclusiveMinimum"]:
                errors.append(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and isinstance(schema["exclusiveMaximum"], (int, float)):
            if value >= schema["exclusiveMaximum"]:
                errors.append(f"{path}: number is not below exclusiveMaximum")
        if "multipleOf" in schema:
            quotient = value / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient)):
                errors.append(f"{path}: number is not a multipleOf {schema['multipleOf']}")

    return errors


def check_hashes() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file changed: {relative}")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def check_research_sources() -> None:
    record = load_json(RESEARCH_SOURCES)
    if not isinstance(record, dict):
        fail("research-sources.json must contain a JSON object")
    sources = record.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        fail("research-sources.json must record at least two consulted sources")

    seen_urls: set[str] = set()
    searchable_text: list[str] = []
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        if not isinstance(source, dict):
            fail(f"{label} must be an object")
        title = source.get("title")
        url = source.get("url")
        retrieved_at = source.get("retrievedAt")
        claims = source.get("claims")
        if not isinstance(title, str) or not title.strip():
            fail(f"{label} has no title")
        if not isinstance(url, str):
            fail(f"{label} has no URL")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail(f"{label} must use an HTTPS Broadcom-published URL")
        if url in seen_urls:
            fail(f"{label} duplicates an earlier URL")
        seen_urls.add(url)
        if not isinstance(retrieved_at, str):
            fail(f"{label} has no retrieval date")
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", retrieved_at) is None:
                raise ValueError
            date.fromisoformat(retrieved_at)
        except ValueError:
            fail(f"{label} retrieval date must use YYYY-MM-DD")
        if not isinstance(claims, list) or not claims or not all(
            isinstance(claim, str) and claim.strip() for claim in claims
        ):
            fail(f"{label} must record at least one non-empty claim")
        searchable_text.extend([title, *claims])

    combined = " ".join(searchable_text).casefold()
    product_terms = {
        "vCenter": ("vcenter",),
        "ESXi": ("esxi", "esx"),
        "vSAN": ("vsan",),
        "NSX": ("nsx",),
        "vSphere Replication": ("vsphere replication",),
        "VMware Live Site Recovery": ("live site recovery", "srm"),
        "9.1 target": ("9.1",),
    }
    missing = [
        product
        for product, terms in product_terms.items()
        if not any(term in combined for term in terms)
    ]
    if missing:
        fail(f"research claims do not cover the requested products/target: {missing}")


def check_sddc_semantics(plan: dict[str, Any], inventory: dict[str, Any]) -> None:
    spec = plan["sddcSpec"]
    inputs = inventory["installerInputs"]
    require_equal(spec.get("sddcId"), inputs["sddcId"], "sddcSpec.sddcId")
    require_equal(spec.get("workflowType"), inputs["workflowType"], "sddcSpec.workflowType")
    require_equal(spec.get("version"), inputs["targetVersion"], "sddcSpec.version")
    require_equal(spec.get("vcfInstanceName"), inputs["vcfInstanceName"], "sddcSpec.vcfInstanceName")

    vcenter = spec.get("vcenterSpec", {})
    expected_vcenter = inputs["vcenter"]
    require_equal(vcenter.get("vcenterHostname"), expected_vcenter["hostname"], "vcenter hostname")
    require_equal(vcenter.get("rootVcenterPassword"), expected_vcenter["rootPasswordPlaceholder"], "vcenter password placeholder")
    require_equal(vcenter.get("version"), expected_vcenter["version"], "vcenter source version")
    require_equal(vcenter.get("useExistingDeployment"), True, "vcenter existing-deployment flag")
    require_equal(vcenter.get("sslThumbprint"), expected_vcenter["sslThumbprint"], "vcenter thumbprint")

    nsx = spec.get("nsxtSpec", {})
    expected_nsx = inputs["nsx"]
    require_equal(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])],
        expected_nsx["managerHostnames"],
        "NSX manager inventory",
    )
    require_equal(nsx.get("vipFqdn"), expected_nsx["vipFqdn"], "NSX VIP")
    require_equal(nsx.get("version"), expected_nsx["version"], "NSX source version")
    require_equal(nsx.get("useExistingDeployment"), True, "NSX existing-deployment flag")
    require_equal(nsx.get("sslThumbprint"), expected_nsx["sslThumbprint"], "NSX thumbprint")

    require_equal(spec.get("dnsSpec"), inputs["dns"], "DNS specification")
    require_equal(spec.get("ntpServers"), inputs["ntpServers"], "NTP servers")
    require_equal(spec.get("networkSpecs"), inputs["networks"], "network specifications")
    require_equal(
        spec.get("clusterSpec"),
        {
            "datacenterName": inputs["cluster"]["datacenterName"],
            "clusterName": inputs["cluster"]["clusterName"],
        },
        "cluster specification",
    )
    require_equal(
        spec.get("datastoreSpec"),
        {"existingDatastoreName": inputs["cluster"]["existingDatastoreName"]},
        "existing datastore specification",
    )
    require_equal(spec.get("hostSpecs"), inputs["hosts"], "host specifications")


def check_plan_semantics(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    workload = inventory["workloadEstate"]
    management = inventory["fleet"]["managementDomain"]
    require_equal(plan["estateId"], workload["id"], "estateId")
    require_equal(plan["fleetId"], inventory["fleet"]["id"], "fleetId")
    require_equal(plan["targetRelease"], snapshot["targetRelease"], "targetRelease")
    require_equal(
        plan["protectedManagementDomain"],
        {"id": management["id"], "change": "NONE"},
        "protected management domain declaration",
    )

    inventory_by_id = {item["id"]: item for item in workload["components"]}
    plan_components = plan["components"]
    require_equal(
        [item["id"] for item in plan_components],
        snapshot["componentOrder"],
        "component coverage/order",
    )
    if set(inventory_by_id) != set(snapshot["components"]):
        fail("protected inventory and compatibility snapshot component sets disagree")

    for component in plan_components:
        component_id = component["id"]
        source = inventory_by_id[component_id]
        authority = snapshot["components"][component_id]
        require_equal(component["name"], source["name"], f"{component_id} name")
        require_equal(component["kind"], source["kind"], f"{component_id} kind")
        require_equal(component["currentVersion"], source["version"], f"{component_id} current version")
        require_equal(component["currentVersion"], authority["currentVersion"], f"{component_id} snapshot source")
        require_equal(
            component["target"],
            {"product": authority["targetProduct"], "version": authority["targetVersion"]},
            f"{component_id} target",
        )
        require_equal(component["disposition"], authority["disposition"], f"{component_id} disposition")
        require_equal(component["gates"], authority["requiredGates"], f"{component_id} gates")

    expected_gate_ids = list(snapshot["gates"])
    require_equal([item["id"] for item in plan["gates"]], expected_gate_ids, "gate coverage/order")
    for gate in plan["gates"]:
        require_equal(gate["condition"], snapshot["gates"][gate["id"]], f"gate {gate['id']} condition")

    gate_ids = set(expected_gate_ids)
    component_ids = set(inventory_by_id)
    management_ids = {item["id"] for item in management["components"]}
    steps = plan["steps"]
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "step order")
    step_ids = [step["id"] for step in steps]
    if len(step_ids) != len(set(step_ids)):
        fail("step IDs must be unique")

    prior: set[str] = set()
    for step in steps:
        unknown_components = set(step["components"]) - component_ids
        if unknown_components:
            fail(f"step {step['id']} names unknown components: {sorted(unknown_components)}")
        if set(step["components"]) & management_ids:
            fail(f"step {step['id']} touches the protected management domain")
        unknown_gates = set(step["gates"]) - gate_ids
        if unknown_gates:
            fail(f"step {step['id']} names unknown gates: {sorted(unknown_gates)}")
        if not set(step["requires"]).issubset(prior):
            fail(f"step {step['id']} depends on a missing or later step")
        prior.add(step["id"])

    import_steps = [step for step in steps if step["action"] == "IMPORT"]
    if len(import_steps) != 1:
        fail("the architecture must contain exactly one workload-domain IMPORT step")
    import_step = import_steps[0]
    if import_step["result"] != "wld-import-complete":
        fail("the IMPORT step must produce wld-import-complete")
    if not {"vc-wld01", "nsx-wld01"}.issubset(import_step["components"]):
        fail("the IMPORT step must converge the workload vCenter and NSX")
    if "fleet-attachment-ready" not in import_step["gates"]:
        fail("the IMPORT step is missing fleet-attachment-ready")

    transition_indexes: dict[str, int] = {}
    for component in plan_components:
        matches = [
            index
            for index, step in enumerate(steps)
            if component["id"] in step["components"] and step["action"] == component["disposition"]
        ]
        if len(matches) != 1:
            fail(f"{component['id']} must have exactly one {component['disposition']} transition step")
        transition_index = matches[0]
        if transition_index <= steps.index(import_step):
            fail(f"{component['id']} transitions before workload-domain import")
        transition_indexes[component["id"]] = transition_index
        transition_step = steps[transition_index]
        missing = set(component["gates"]) - set(transition_step["gates"])
        if missing:
            fail(f"transition step for {component['id']} omits gates: {sorted(missing)}")

    previous = -1
    for group in snapshot["requiredTransitionSequence"]:
        indexes = {transition_indexes[item] for item in group}
        if len(indexes) != 1:
            fail(f"components {group!r} must transition in one step")
        current = indexes.pop()
        if current <= previous:
            fail("component transitions do not follow the pinned compatibility sequence")
        previous = current


def check_module(
    expected_plan: dict[str, Any],
    openapi: dict[str, Any],
    plan_schema: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    manifest = MODULE_DIR / "VcfBrownfieldArchitecture.psd1"
    implementation = MODULE_DIR / "VcfBrownfieldArchitecture.psm1"
    if not manifest.is_file() or not implementation.is_file():
        fail("missing VcfBrownfieldArchitecture PowerShell module")
    pwsh = shutil.which("pwsh")
    if not pwsh:
        fail("pwsh is required to parse the PowerShell deliverable")

    inspection_command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCFARCH_IMPLEMENTATION, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw (($parseErrors | ForEach-Object Message) -join '; ')
}
$data = Import-PowerShellDataFile -LiteralPath $env:VCFARCH_MANIFEST
$commandAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$functionAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
$memberCalls = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.InvokeMemberExpressionAst]
}, $true))
[ordered]@{
    manifest = $data
    commandNames = @($commandAsts | ForEach-Object { $_.GetCommandName() })
    commandTexts = @($commandAsts | ForEach-Object { $_.Extent.Text })
    functionNames = @($functionAsts | ForEach-Object { $_.Name })
    invokedMembers = @($memberCalls | ForEach-Object { $_.Member.Value })
} | ConvertTo-Json -Depth 30 -Compress
"""
    child_env = os.environ.copy()
    child_env["VCFARCH_IMPLEMENTATION"] = str(implementation)
    child_env["VCFARCH_MANIFEST"] = str(manifest)
    try:
        result = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", inspection_command],
            cwd=ROOT,
            env=child_env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("PowerShell module parsing timed out")
    if result.returncode != 0:
        fail(f"PowerShell module parse/manifest failure: {result.stderr.strip() or result.stdout.strip()}")
    try:
        inspection = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        fail("could not inspect the PowerShell module manifest")
    manifest_data = inspection.get("manifest", {})
    required = manifest_data.get("RequiredModules", [])
    names = {
        item if isinstance(item, str) else item.get("ModuleName")
        for item in (required if isinstance(required, list) else [required])
    }
    expected_modules = {"VMware.Sdk.Vcf.Installer", "VMware.Sdk.Vcf.SddcManager"}
    if not expected_modules.issubset(names):
        fail(f"module manifest must require {sorted(expected_modules)}")
    exports = manifest_data.get("FunctionsToExport", [])
    if "New-VcfBrownfieldMigrationPlan" not in exports:
        fail("module manifest must export New-VcfBrownfieldMigrationPlan")

    required_bindings = {
        "Initialize-VcfInstallerDnsSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerNsxtManagerSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
        "Initialize-VcfInstallerSddcClusterSpec",
        "Initialize-VcfInstallerSddcDatastoreSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcSpec",
    }
    command_names = {
        name.casefold(): name
        for name in inspection.get("commandNames", [])
        if isinstance(name, str)
    }
    missing = sorted(
        name for name in required_bindings if name.casefold() not in command_names
    )
    if missing:
        fail(f"PowerShell module is not SDK-driven; missing bindings: {missing}")
    function_names = {
        name.casefold()
        for name in inspection.get("functionNames", [])
        if isinstance(name, str)
    }
    if "new-vcfbrownfieldmigrationplan" not in function_names:
        fail("PowerShell module does not implement New-VcfBrownfieldMigrationPlan")
    if any(name.casefold().startswith("initialize-vcfinstaller") for name in function_names):
        fail("PowerShell module must not imitate VCF Installer initializer cmdlets")
    invoked_members = {
        name.casefold()
        for name in inspection.get("invokedMembers", [])
        if isinstance(name, str)
    }
    if "tojson" not in invoked_members:
        fail("PowerShell module must serialize the SDK SddcSpec through ToJson()")

    forbidden_commands = {
        "install-module",
        "save-module",
        "invoke-webrequest",
        "invoke-restmethod",
        "curl",
        "wget",
    }
    used_forbidden = sorted(forbidden_commands.intersection(command_names))
    if used_forbidden:
        fail(f"PowerShell module uses forbidden commands: {used_forbidden}")
    for name, text in zip(
        inspection.get("commandNames", []), inspection.get("commandTexts", [])
    ):
        if (
            isinstance(name, str)
            and isinstance(text, str)
            and name.casefold() in {"set-alias", "new-alias"}
            and re.search(r"(?i)\bInitialize-VcfInstaller", text)
        ):
            fail("PowerShell module must not intercept VCF Installer initializer cmdlets")

    for candidate in MODULE_DIR.rglob("*"):
        if candidate.is_file() and candidate.name.startswith("VMware.Sdk.Vcf"):
            fail("VMware.Sdk.Vcf modules must remain external prerequisites, not vendored files")

    runtime_command = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Import-Module -Name $env:VCFARCH_MANIFEST -Force
New-VcfBrownfieldMigrationPlan `
    -InventoryPath $env:VCFARCH_INVENTORY `
    -CompatibilitySnapshotPath $env:VCFARCH_SNAPSHOT `
    -OutputPath $env:VCFARCH_OUTPUT | Out-Null
"""
    def run_module(inventory_path: Path, snapshot_path: Path, generated_path: Path) -> dict[str, Any]:
        runtime_env = os.environ.copy()
        runtime_env.update(
            {
                "VCFARCH_MANIFEST": str(manifest),
                "VCFARCH_INVENTORY": str(inventory_path),
                "VCFARCH_SNAPSHOT": str(snapshot_path),
                "VCFARCH_OUTPUT": str(generated_path),
            }
        )
        try:
            runtime = subprocess.run(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", runtime_command],
                cwd=ROOT,
                env=runtime_env,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail("PowerShell module execution timed out")
        if runtime.returncode != 0:
            detail = runtime.stderr.strip() or runtime.stdout.strip()
            fail(f"PowerShell module execution failed: {detail}")
        try:
            generated_plan = json.loads(generated_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            fail("PowerShell module did not write its requested output artifact")
        except json.JSONDecodeError as exc:
            fail(f"PowerShell module emitted invalid JSON: {exc}")
        return generated_plan

    with tempfile.TemporaryDirectory(prefix="vcfarch-0103-") as temp_directory:
        temporary_root = Path(temp_directory)
        generated_path = temporary_root / "migration-plan.json"
        generated_plan = run_module(INVENTORY, SNAPSHOT, generated_path)
        require_equal(generated_plan, expected_plan, "PowerShell-generated migration plan")

        varied_inventory = json.loads(json.dumps(inventory))
        varied_snapshot = json.loads(json.dumps(snapshot))
        varied_inventory["fleet"]["id"] = "fleet-alt-02"
        varied_inventory["fleet"]["managementDomain"]["id"] = "mgmt-alt-02"
        varied_inventory["workloadEstate"]["id"] = "estate-alt-wld02"
        varied_snapshot["estateId"] = "estate-alt-wld02"

        inputs = varied_inventory["installerInputs"]
        inputs["sddcId"] = "alt-wld02"
        inputs["vcfInstanceName"] = "Alternate workload domain"
        inputs["vcenter"]["hostname"] = "vc-alt02.example.test"
        inputs["vcenter"]["rootPasswordPlaceholder"] = "ALT-REPLACE-ME!12345"
        inputs["vcenter"]["sslThumbprint"] = "AA:" * 31 + "AA"
        inputs["nsx"]["managerHostnames"] = [
            "nsx-alt02-a.example.test",
            "nsx-alt02-b.example.test",
            "nsx-alt02-c.example.test",
        ]
        inputs["nsx"]["vipFqdn"] = "nsx-alt02.example.test"
        inputs["nsx"]["sslThumbprint"] = "BB:" * 31 + "BB"
        inputs["cluster"]["datacenterName"] = "ALT-WLD02-DC"
        inputs["cluster"]["clusterName"] = "ALT-WLD02-CL01"
        inputs["cluster"]["existingDatastoreName"] = "vsanDatastore-alt02"
        for index, host in enumerate(inputs["hosts"], start=1):
            host["hostname"] = f"esx-alt02-{index:02d}"
        inputs["dns"]["subdomain"] = "example.test"
        inputs["dns"]["nameservers"] = ["203.0.113.53", "203.0.113.54"]
        inputs["ntpServers"] = ["203.0.113.123", "203.0.113.124"]

        inventory_components = {
            item["id"]: item for item in varied_inventory["workloadEstate"]["components"]
        }
        inventory_components["vc-wld01"]["name"] = "Alternate workload vCenter"
        inventory_components["vc-wld01"]["version"] = "8.0.3.00700"
        varied_snapshot["components"]["vc-wld01"]["currentVersion"] = "8.0.3.00700"
        varied_snapshot["components"]["vc-wld01"]["targetProduct"] = "Alternate vCenter Server"
        inputs["vcenter"]["version"] = "8.0.3.00700"
        inventory_components["nsx-wld01"]["version"] = "4.2.1.5"
        varied_snapshot["components"]["nsx-wld01"]["currentVersion"] = "4.2.1.5"
        inputs["nsx"]["version"] = "4.2.1.5"
        varied_snapshot["gates"]["inventory-health-green"] = (
            "The alternate vCenter, hosts, vSAN and NSX report healthy before discovery."
        )

        varied_inventory_path = temporary_root / "estate-alt.json"
        varied_snapshot_path = temporary_root / "compatibility-alt.json"
        varied_output_path = temporary_root / "migration-plan-alt.json"
        varied_inventory_path.write_text(json.dumps(varied_inventory), encoding="utf-8")
        varied_snapshot_path.write_text(json.dumps(varied_snapshot), encoding="utf-8")
        varied_plan = run_module(varied_inventory_path, varied_snapshot_path, varied_output_path)
        if varied_plan == expected_plan:
            fail("PowerShell module ignored alternate inventory and snapshot inputs")
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        varied_sddc_errors = schema_errors(
            varied_plan.get("sddcSpec"), sddc_schema, openapi, "$.sddcSpec"
        )
        if varied_sddc_errors:
            fail(
                "alternate-input SddcSpec validation failed:\n  "
                + "\n  ".join(varied_sddc_errors[:40])
            )
        varied_plan_errors = schema_errors(varied_plan, plan_schema, plan_schema)
        if varied_plan_errors:
            fail(
                "alternate-input migration-plan schema validation failed:\n  "
                + "\n  ".join(varied_plan_errors[:40])
            )
        check_sddc_semantics(varied_plan, varied_inventory)
        check_plan_semantics(varied_plan, varied_inventory, varied_snapshot)


def main() -> int:
    # Parse only what is necessary to perform the mandatory first validation.
    plan = load_json(ARTIFACT)
    openapi = load_json(OPENAPI)

    # FIRST ACCEPTANCE CHECK: validate the embedded target with the installer
    # specification's own SddcSpec schema from the pinned 9.1.0.0 OpenAPI file.
    sddc_schema = openapi.get("components", {}).get("schemas", {}).get("SddcSpec")
    if sddc_schema is None:
        fail("pinned installer specification has no SddcSpec schema")
    sddc_errors = schema_errors(plan.get("sddcSpec"), sddc_schema, openapi, "$.sddcSpec")
    if sddc_errors:
        fail("installer SddcSpec validation failed:\n  " + "\n  ".join(sddc_errors[:40]))

    # Only after the installer schema succeeds may the verifier check the seed
    # schema, fixture, snapshot, module, or protected hashes.
    check_hashes()
    plan_schema = load_json(PLAN_SCHEMA)
    plan_errors = schema_errors(plan, plan_schema, plan_schema)
    if plan_errors:
        fail("migration-plan schema validation failed:\n  " + "\n  ".join(plan_errors[:40]))

    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)
    check_research_sources()
    check_sddc_semantics(plan, inventory)
    check_plan_semantics(plan, inventory, snapshot)
    check_module(plan, openapi, plan_schema, inventory, snapshot)
    print(
        "PASS: installer schema, research record, migration architecture, "
        "compatibility snapshot, and PowerShell module"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
