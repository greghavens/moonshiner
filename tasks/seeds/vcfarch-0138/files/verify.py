#!/usr/bin/env python3
"""Protected verifier for the mixed-estate VCF architecture seed."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "output" / "migration-plan.json"
RESEARCH_SOURCES = ROOT / "output" / "research-sources.json"
INSTALLER_SCHEMA = ROOT / "docs" / "vcf-installer-openapi.json"
MODULE = ROOT / "VcfMixedEstate.psm1"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc.msg} at line {exc.lineno}"
        ) from exc


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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def resolve_local_ref(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {ref}")
    node: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {ref}") from exc
    return node


def validate(instance: Any, schema: Any, document: dict[str, Any], path: str = "$") -> None:
    if schema is True:
        return
    if schema is False:
        raise VerificationError(f"{path}: rejected by schema")
    if not isinstance(schema, dict):
        raise VerificationError(f"{path}: malformed verifier schema")

    if "$ref" in schema:
        validate(instance, resolve_local_ref(document, schema["$ref"]), document, path)

    for subschema in schema.get("allOf", []):
        validate(instance, subschema, document, path)

    if instance is None and schema.get("nullable") is True:
        return

    if "const" in schema and instance != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise VerificationError(f"{path}: value {instance!r} is not in the allowed set")

    declared_type = schema.get("type")
    if isinstance(declared_type, list):
        if not any(json_type_matches(instance, item) for item in declared_type):
            raise VerificationError(f"{path}: expected one of types {declared_type}")
    elif isinstance(declared_type, str) and not json_type_matches(instance, declared_type):
        raise VerificationError(f"{path}: expected type {declared_type}")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                raise VerificationError(f"{path}: missing required property {name!r}")
        if len(instance) < schema.get("minProperties", 0):
            raise VerificationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise VerificationError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate(value, properties[name], document, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(value, schema["additionalProperties"], document, f"{path}.{name}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                validate(item, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def expected_target(component: dict[str, Any], snapshot: dict[str, Any]) -> str:
    if component["scope"] == "management":
        return component["currentVersion"]
    return snapshot["targets"][component["type"]]


def check_installer_projection(artifact: dict[str, Any], inventory: dict[str, Any]) -> None:
    expected = inventory["candidateInstallerSpec"]
    require(artifact["sddcId"] == expected["sddcId"], "installer sddcId does not match fixture")
    require(
        artifact.get("workflowType") == expected["workflowType"],
        "installer workflowType does not match fixture",
    )
    require(artifact.get("version") == inventory["targetRelease"], "installer target version is wrong")

    expected_vcenter = dict(expected["vcenterSpec"])
    candidate_vcenter = next(
        item
        for item in inventory["components"]
        if item["scope"] == "candidate-workload" and item["type"] == "VCENTER"
    )
    expected_vcenter["version"] = candidate_vcenter["currentVersion"]
    require(artifact["vcenterSpec"] == expected_vcenter, "installer vcenterSpec is not fixture-derived")
    require(artifact["networkSpecs"] == expected["networkSpecs"], "installer networkSpecs changed")
    require(artifact["dnsSpec"] == expected["dnsSpec"], "installer dnsSpec changed")
    require(artifact.get("ntpServers") == expected["ntpServers"], "installer ntpServers changed")


def check_components(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    actual = plan["components"]
    source = inventory["components"]
    actual_ids = [item["id"] for item in actual]
    source_ids = [item["id"] for item in source]
    require(
        len(actual_ids) == len(set(actual_ids)),
        "migrationPlan.components contains duplicate component ids",
    )
    require(set(actual_ids) == set(source_ids), "migrationPlan.components must cover inventory exactly")
    by_id = {item["id"]: item for item in actual}
    for component in source:
        got = by_id[component["id"]]
        for key in ("id", "scope", "domainId", "type", "currentVersion"):
            require(got[key] == component[key], f"component {component['id']} has wrong {key}")
        require(
            got["targetVersion"] == expected_target(component, snapshot),
            f"component {component['id']} has wrong targetVersion",
        )
        if component["scope"] == "management":
            expected_disposition = "retain"
            expected_gates = snapshot["componentGates"]["management"]
        else:
            expected_disposition = "migrate"
            expected_gates = snapshot["componentGates"][component["type"]]
        require(got["disposition"] == expected_disposition, f"component {component['id']} disposition is wrong")
        require(
            set(got["gates"]) == set(expected_gates),
            f"component {component['id']} gates do not match snapshot",
        )


def check_gate_catalog(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = snapshot["gateCatalog"]
    actual = plan["gates"]
    actual_ids = [item["id"] for item in actual]
    expected_ids = [item["id"] for item in expected]
    require(len(actual_ids) == len(set(actual_ids)), "gate catalog contains duplicate ids")
    require(
        set(actual_ids) == set(expected_ids),
        "gate catalog ids do not match pinned compatibility snapshot",
    )
    by_id = {item["id"]: item for item in actual}
    for wanted in expected:
        got = by_id[wanted["id"]]
        require(got["type"] == wanted["type"], f"gate {wanted['id']} has wrong type")
        require(bool(got["condition"].strip()), f"gate {wanted['id']} needs a concrete condition")


def check_steps(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["steps"]
    require(
        [step["order"] for step in steps] == list(range(1, len(steps) + 1)),
        "step order must be contiguous from 1",
    )
    stages = [step["stage"] for step in steps]
    require(stages.count("IMPORT_WORKLOAD_DOMAIN") == 1, "plan needs exactly one import step")
    require(stages.count("UPGRADE_NSX") == 1, "plan needs exactly one NSX upgrade step")
    require(stages.count("UPGRADE_VCENTER") == 1, "plan needs exactly one vCenter upgrade step")
    require(stages.count("VERIFY_TARGET_BOM") == 1, "plan needs exactly one final BOM verification")

    candidate = [item for item in inventory["components"] if item["scope"] == "candidate-workload"]
    management_ids = {item["id"] for item in inventory["components"] if item["scope"] == "management"}
    candidate_ids = [item["id"] for item in candidate]
    by_id = {item["id"]: item for item in inventory["components"]}
    host_ids = [item["id"] for item in candidate if item["type"] == "ESX_HOST"]
    require(stages.count("UPGRADE_ESXI_HOST") == len(host_ids), "each candidate ESXi host needs one upgrade step")

    positions = {stage: stages.index(stage) for stage in ("IMPORT_WORKLOAD_DOMAIN", "UPGRADE_NSX", "UPGRADE_VCENTER")}
    require(positions["IMPORT_WORKLOAD_DOMAIN"] < positions["UPGRADE_NSX"], "NSX upgrade must follow import")
    require(positions["UPGRADE_NSX"] < positions["UPGRADE_VCENTER"], "NSX must reach target before vCenter")
    for index, step in enumerate(steps):
        require(not (management_ids & set(step["componentIds"])), "an actionable step touches the management domain")
        require(
            set(step["gates"]) == set(snapshot["stepGates"][step["stage"]]),
            f"step {step['order']} gates do not match the pinned constraints",
        )
        require(
            set(step["resultVersions"]) == set(step["componentIds"]),
            f"step {step['order']} must name one resulting version per affected component",
        )
        if step["stage"] == "UPGRADE_ESXI_HOST":
            require(index > positions["UPGRADE_VCENTER"], "ESXi upgrades must follow vCenter")

    import_step = next(step for step in steps if step["stage"] == "IMPORT_WORKLOAD_DOMAIN")
    require(
        set(import_step["componentIds"]) == set(candidate_ids),
        "import must include the complete candidate stack",
    )
    require(
        import_step["resultVersions"] == {item["id"]: item["currentVersion"] for item in candidate},
        "import must retain the supported brownfield versions",
    )

    upgraded_ids: list[str] = []
    expected_type = {
        "UPGRADE_NSX": "NSX_T_MANAGER",
        "UPGRADE_VCENTER": "VCENTER",
        "UPGRADE_ESXI_HOST": "ESX_HOST",
    }
    for step in steps:
        if step["stage"] not in expected_type:
            continue
        require(len(step["componentIds"]) == 1, f"{step['stage']} must identify one component")
        component_id = step["componentIds"][0]
        require(component_id in by_id, f"{step['stage']} names an unknown component")
        require(by_id[component_id]["type"] == expected_type[step["stage"]], f"{step['stage']} names the wrong type")
        require(component_id not in upgraded_ids, f"component {component_id} is upgraded more than once")
        upgraded_ids.append(component_id)
        require(
            step["resultVersions"][component_id] == snapshot["targets"][by_id[component_id]["type"]],
            f"step {step['order']} has an unsupported target",
        )
    require(set(upgraded_ids) == set(candidate_ids), "every candidate component must be upgraded exactly once")

    verify_step = next(step for step in steps if step["stage"] == "VERIFY_TARGET_BOM")
    require(verify_step is steps[-1], "target-BOM verification must be the final step")
    require(
        set(verify_step["componentIds"]) == set(candidate_ids),
        "final verification must cover the candidate stack",
    )
    require(
        verify_step["resultVersions"]
        == {item["id"]: snapshot["targets"][item["type"]] for item in candidate},
        "final BOM versions do not match the compatibility snapshot",
    )


def check_semantics(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(inventory["targetRelease"] == snapshot["targetRelease"], "fixture/snapshot target mismatch")
    candidate = [item for item in inventory["components"] if item["scope"] == "candidate-workload"]
    for component in candidate:
        require(
            component["currentVersion"] == snapshot["brownfieldImportCombination"][component["type"]],
            f"fixture component {component['id']} is outside the pinned import combination",
        )
    plan = artifact["migrationPlan"]
    require(plan["estateId"] == inventory["estateId"], "migrationPlan estateId is wrong")
    require(plan["targetRelease"] == snapshot["targetRelease"], "migrationPlan targetRelease is wrong")
    check_installer_projection(artifact, inventory)
    check_components(plan, inventory, snapshot)
    check_gate_catalog(plan, snapshot)
    check_steps(plan, inventory, snapshot)


def check_research_sources(inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    record = load_json(RESEARCH_SOURCES)
    require(isinstance(record, dict), "output/research-sources.json must contain an object")
    researched_at = record.get("researchedAt")
    require(isinstance(researched_at, str) and bool(researched_at.strip()), "researchedAt is required")

    sources = record.get("sources")
    require(isinstance(sources, list) and bool(sources), "research sources must be a non-empty array")
    consulted_text: list[str] = []
    for index, source in enumerate(sources, start=1):
        label = f"research source {index}"
        require(isinstance(source, dict), f"{label} must be an object")
        require(
            isinstance(source.get("title"), str) and bool(source["title"].strip()),
            f"{label} title is required",
        )
        url = source.get("url")
        require(isinstance(url, str), f"{label} URL is required")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme in {"http", "https"}
            and bool(parsed.path)
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"{label} must use an absolute Broadcom-published HTTP(S) URL",
        )
        consulted_for = source.get("consultedFor")
        require(
            isinstance(consulted_for, list)
            and bool(consulted_for)
            and all(isinstance(item, str) and bool(item.strip()) for item in consulted_for),
            f"{label} consultedFor must contain non-empty strings",
        )
        consulted_text.extend(consulted_for)

    combined = " ".join(consulted_text)
    exact_versions = set(snapshot["brownfieldImportCombination"].values())
    exact_versions.update(snapshot["targets"].values())
    exact_versions.add(inventory["targetRelease"])
    missing_versions = sorted(version for version in exact_versions if version not in combined)
    require(
        not missing_versions,
        f"research record does not cover exact source/target versions: {missing_versions}",
    )


def inspect_module(pwsh: str) -> dict[str, Any]:
    inspection_command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCFARCH_MODULE, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw (($parseErrors | ForEach-Object Message) -join '; ')
}
$commandAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$functionAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
[ordered]@{
    requiredModules = @($ast.ScriptRequirements.RequiredModules | ForEach-Object {
        [ordered]@{ name = $_.Name; version = $_.Version.ToString() }
    })
    commandNames = @($commandAsts | ForEach-Object { $_.GetCommandName() })
    commandTexts = @($commandAsts | ForEach-Object { $_.Extent.Text })
    functionNames = @($functionAsts | ForEach-Object { $_.Name })
} | ConvertTo-Json -Depth 20 -Compress
"""
    child_env = os.environ.copy()
    child_env["VCFARCH_MODULE"] = str(MODULE)
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
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("PowerShell module parsing timed out") from exc
    require(
        result.returncode == 0,
        f"PowerShell module parse failed: {result.stderr.strip() or result.stdout.strip()}",
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise VerificationError("could not inspect the PowerShell module") from exc


def check_module(
    artifact: dict[str, Any],
    installer_document: dict[str, Any],
    migration_schema: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    require(MODULE.is_file(), "missing required PowerShell module: VcfMixedEstate.psm1")
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required to verify the PowerShell module")
    inspection = inspect_module(pwsh)

    required_version = "13.5.0.25380678"
    module_requirements = {
        item.get("name"): item.get("version")
        for item in inspection.get("requiredModules", [])
        if isinstance(item, dict)
    }
    for module_name in ("VMware.Sdk.Vcf.Installer", "VMware.Sdk.Vcf.SddcManager"):
        require(
            module_requirements.get(module_name) == required_version,
            f"module must require {module_name} {required_version}",
        )

    command_names = {
        name.casefold(): name
        for name in inspection.get("commandNames", [])
        if isinstance(name, str)
    }
    command_texts = [text for text in inspection.get("commandTexts", []) if isinstance(text, str)]
    required_bindings = {
        "Initialize-VcfInstallerDnsSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcSpec",
        "Get-VcfInstallerOperation",
        "Get-VcfSddcManagerOperation",
    }
    missing_bindings = sorted(
        name for name in required_bindings if name.casefold() not in command_names
    )
    require(not missing_bindings, f"PowerShell module is missing SDK bindings: {missing_bindings}")

    import_text = "\n".join(
        text
        for name, text in zip(inspection.get("commandNames", []), inspection.get("commandTexts", []))
        if isinstance(name, str) and name.casefold() == "import-module" and isinstance(text, str)
    )
    require(
        "VMware.Sdk.Vcf.Installer" in import_text
        and "VMware.Sdk.Vcf.SddcManager" in import_text,
        "PowerShell module must explicitly import both VMware SDK modules",
    )

    function_names = {
        name.casefold()
        for name in inspection.get("functionNames", [])
        if isinstance(name, str)
    }
    require(
        "new-vcfmixedestatearchitecture" in function_names,
        "PowerShell module must implement New-VcfMixedEstateArchitecture",
    )
    require(
        not any(name.casefold() in function_names for name in required_bindings),
        "PowerShell module must not imitate required VMware SDK cmdlets",
    )
    forbidden_commands = {
        "install-module",
        "save-module",
        "invoke-webrequest",
        "invoke-restmethod",
        "curl",
        "wget",
    }
    used_forbidden = sorted(name for name in forbidden_commands if name in command_names)
    require(not used_forbidden, f"PowerShell module uses forbidden commands: {used_forbidden}")
    for name, text in zip(inspection.get("commandNames", []), inspection.get("commandTexts", [])):
        if isinstance(name, str) and isinstance(text, str) and name.casefold() in {"set-alias", "new-alias"}:
            require(
                re.search(r"(?i)\b(?:Initialize|Get)-Vcf(?:Installer|SddcManager)", text) is None,
                "PowerShell module must not intercept VMware SDK cmdlets",
            )

    for candidate in ROOT.rglob("*"):
        if not candidate.is_file():
            continue
        lowered_parts = [part.casefold() for part in candidate.parts]
        looks_vendored = any(part.startswith("vmware.sdk.vcf.") for part in lowered_parts)
        looks_vendored = looks_vendored or (
            candidate.suffix.casefold() in {".dll", ".nupkg", ".psd1", ".psm1"}
            and candidate.name.casefold().startswith("vmware")
        )
        require(not looks_vendored, f"VMware SDK must not be vendored: {candidate.relative_to(ROOT)}")

    runtime_command = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Import-Module -Name $env:VCFARCH_MODULE -Force
$expectedSources = [ordered]@{
    'Initialize-VcfInstallerDnsSpec' = 'VMware.Sdk.Vcf.Installer'
    'Initialize-VcfInstallerSddcNetworkSpec' = 'VMware.Sdk.Vcf.Installer'
    'Initialize-VcfInstallerSddcVcenterSpec' = 'VMware.Sdk.Vcf.Installer'
    'Initialize-VcfInstallerSddcSpec' = 'VMware.Sdk.Vcf.Installer'
    'Get-VcfInstallerOperation' = 'VMware.Sdk.Vcf.Installer'
    'Get-VcfSddcManagerOperation' = 'VMware.Sdk.Vcf.SddcManager'
}
foreach ($entry in $expectedSources.GetEnumerator()) {
    $binding = Get-Command -Name $entry.Key -CommandType Cmdlet -ErrorAction Stop
    if ($binding.ModuleName -ne $entry.Value) {
        throw "Unexpected SDK binding source for $($entry.Key): $($binding.ModuleName)"
    }
}
New-VcfMixedEstateArchitecture `
    -InventoryPath $env:VCFARCH_INVENTORY `
    -CompatibilityPath $env:VCFARCH_COMPATIBILITY `
    -OutputPath $env:VCFARCH_OUTPUT
"""

    def run_module(inventory_path: Path, compatibility_path: Path, output_path: Path) -> dict[str, Any]:
        runtime_env = os.environ.copy()
        runtime_env.update(
            {
                "VCFARCH_MODULE": str(MODULE),
                "VCFARCH_INVENTORY": str(inventory_path),
                "VCFARCH_COMPATIBILITY": str(compatibility_path),
                "VCFARCH_OUTPUT": str(output_path),
            }
        )
        try:
            result = subprocess.run(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", runtime_command],
                cwd=ROOT,
                env=runtime_env,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VerificationError("PowerShell module execution timed out") from exc
        require(
            result.returncode == 0,
            f"PowerShell module execution failed: {result.stderr.strip() or result.stdout.strip()}",
        )
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise VerificationError("PowerShell module did not create its requested output") from exc
        except json.JSONDecodeError as exc:
            raise VerificationError(f"PowerShell module emitted invalid JSON: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="vcfarch-0138-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        generated_path = temporary_root / "migration-plan-a.json"
        repeated_path = temporary_root / "migration-plan-b.json"
        generated = run_module(ROOT / "fixtures" / "estate.json", ROOT / "docs" / "compatibility-snapshot.json", generated_path)
        repeated = run_module(ROOT / "fixtures" / "estate.json", ROOT / "docs" / "compatibility-snapshot.json", repeated_path)
        require(generated == artifact, "committed migration plan is not the module's output")
        require(repeated == artifact, "repeated module output changed")
        require(generated_path.read_bytes() == repeated_path.read_bytes(), "module JSON output is not deterministic")

        varied_inventory = json.loads(json.dumps(inventory))
        varied_inventory["estateId"] = "chi-edge-alt-02"
        varied_inventory["candidateDomainId"] = "chi-edge-w02"
        source_spec = varied_inventory["candidateInstallerSpec"]
        source_spec["sddcId"] = "chi-edge-w02"
        source_spec["vcenterSpec"]["vcenterHostname"] = "chi-edge-vc02.example.test"
        source_spec["vcenterSpec"]["rootVcenterPassword"] = "Fixture2!"
        source_spec["dnsSpec"]["subdomain"] = "alt.example.test"
        source_spec["ntpServers"] = ["192.0.2.20", "192.0.2.21"]
        alternate_ids = {
            "VCENTER": ["alt-vcenter"],
            "NSX_T_MANAGER": ["alt-nsx"],
            "ESX_HOST": ["alt-esx-01", "alt-esx-02"],
        }
        counters = {key: 0 for key in alternate_ids}
        for component in varied_inventory["components"]:
            if component["scope"] != "candidate-workload":
                continue
            component["domainId"] = "chi-edge-w02"
            component_type = component["type"]
            component["id"] = alternate_ids[component_type][counters[component_type]]
            counters[component_type] += 1

        varied_inventory_path = temporary_root / "estate-varied.json"
        varied_output_path = temporary_root / "migration-plan-varied.json"
        varied_inventory_path.write_text(json.dumps(varied_inventory), encoding="utf-8")
        varied = run_module(
            varied_inventory_path,
            ROOT / "docs" / "compatibility-snapshot.json",
            varied_output_path,
        )
        require(varied != artifact, "PowerShell module ignored alternate inventory input")
        validate(
            varied,
            installer_document["components"]["schemas"]["SddcSpec"],
            installer_document,
        )
        validate(varied, migration_schema, migration_schema)
        check_semantics(varied, varied_inventory, snapshot)


def main() -> int:
    # Phase 1 is intentionally isolated: the upstream SddcSpec contract is the
    # first validation performed on the submitted artifact.
    artifact = load_json(ARTIFACT)
    installer_document = load_json(INSTALLER_SCHEMA)
    validate(artifact, installer_document["components"]["schemas"]["SddcSpec"], installer_document)
    print("installer SddcSpec: PASS")

    migration_schema = load_json(ROOT / "docs" / "migration-plan.schema.json")
    validate(artifact, migration_schema, migration_schema)
    print("migration-plan schema: PASS")

    inventory = load_json(ROOT / "fixtures" / "estate.json")
    snapshot = load_json(ROOT / "docs" / "compatibility-snapshot.json")
    check_semantics(artifact, inventory, snapshot)
    print("pinned compatibility and estate isolation: PASS")

    check_research_sources(inventory, snapshot)
    print("live-research record: PASS")

    check_module(artifact, installer_document, migration_schema, inventory, snapshot)
    print("PowerShell SDK architecture: PASS")

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
