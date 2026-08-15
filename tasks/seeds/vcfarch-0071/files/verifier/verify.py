#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "migration-plan.json"
SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT = ROOT / "authority" / "compatibility-snapshot.json"
MANIFEST = ROOT / "VcfBrownfieldPlanner" / "VcfBrownfieldPlanner.psd1"
MODULE = ROOT / "VcfBrownfieldPlanner" / "VcfBrownfieldPlanner.psm1"
RESEARCH = ROOT / "research-notes.md"

PROTECTED_SHA256 = {
    "schemas/migration-plan.schema.json": "83bb04772287b1054247ff7af4e86146c2276b82717c054fff99c3af848289c7",
    "fixtures/estate-inventory.json": "912a7af45f045bdde2d4152ebcac870414e868e1a950ab8c5522862450b5f09b",
    "authority/compatibility-snapshot.json": "591034e3a29c14ba34f5cd86ab9ca2bde7a734f1ac6c0fde9c8046f375d6df45",
}


class VerificationError(Exception):
    pass


def fail(message):
    raise VerificationError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except VerificationError:
        raise
    except Exception as exc:
        fail(f"cannot parse {path.relative_to(ROOT)}: {exc}")


def validate_artifact_schema_first():
    if not ARTIFACT.is_file():
        fail("migration-plan.json is missing")
    command = r'''
$ErrorActionPreference = 'Stop'
$raw = Get-Content -LiteralPath $env:VCF_PLAN_ARTIFACT -Raw
if (-not ($raw | Test-Json -SchemaFile $env:VCF_PLAN_SCHEMA -ErrorAction Stop)) {
    exit 2
}
'''
    result = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            command,
        ],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "VCF_PLAN_ARTIFACT": str(ARTIFACT),
            "VCF_PLAN_SCHEMA": str(SCHEMA),
        },
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        fail(f"migration-plan.json fails its JSON schema: {details}")


def verify_protected_inputs():
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected input changed: {relative}")


def keyed(items, label):
    result = {}
    for item in items:
        item_id = item["id"]
        if item_id in result:
            fail(f"duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def verify_plan(plan, inventory, snapshot):
    if plan["schemaVersion"] != "1.0.0":
        fail("wrong schemaVersion")
    if plan["estateId"] != inventory["estateId"]:
        fail("estateId does not match inventory")
    if plan["sourceVcfVersion"] != inventory["sourceVcfVersion"]:
        fail("sourceVcfVersion does not match inventory")
    if plan["targetVcfVersion"] != inventory["requestedTargetVcfVersion"]:
        fail("targetVcfVersion does not match inventory")
    if plan["vcfPath"] != snapshot["vcfPath"]:
        fail("VCF path is not the pinned supported direct path")

    for forbidden in snapshot["forbiddenVcfHops"]:
        for source, target in zip(plan["vcfPath"], plan["vcfPath"][1:]):
            if source == forbidden["fromVersion"] and target.startswith(forbidden["toVersionPrefix"]):
                fail(f"plan uses forbidden VCF hop {source} -> {target}")

    inventory_components = keyed(inventory["components"], "inventory component")
    plan_components = keyed(plan["components"], "plan component")
    if set(plan_components) != set(inventory_components):
        fail("plan must contain every and only inventory component exactly once")

    expected_gate_ids_by_component = {component_id: set() for component_id in inventory_components}
    for transition in snapshot["transitions"]:
        expected_gate_ids_by_component[transition["componentId"]].update(transition["requiredGateIds"])

    for component_id, source in inventory_components.items():
        actual = plan_components[component_id]
        for field in ("name", "kind", "scope", "currentVersion"):
            if actual[field] != source[field]:
                fail(f"{component_id}.{field} does not match inventory")
        if actual["targetVersion"] != snapshot["componentTargets"].get(component_id):
            fail(f"{component_id} has the wrong pinned target")
        if set(actual["gateIds"]) != expected_gate_ids_by_component[component_id]:
            fail(f"{component_id} does not name exactly the gates on its transitions")
        if len(actual["gateIds"]) != len(set(actual["gateIds"])):
            fail(f"{component_id} repeats a gate")

    authority_gates = keyed(snapshot["gates"], "authority gate")
    plan_gates = keyed(plan["gates"], "plan gate")
    if set(plan_gates) != set(authority_gates):
        fail("plan gate catalog differs from the pinned gate catalog")
    for gate_id, expected in authority_gates.items():
        if plan_gates[gate_id]["condition"] != expected["condition"]:
            fail(f"gate condition changed: {gate_id}")

    wave_orders = [wave["order"] for wave in plan["waves"]]
    if wave_orders != list(range(1, len(plan["waves"]) + 1)):
        fail("wave order must be contiguous and start at 1")
    wave_ids = [wave["id"] for wave in plan["waves"]]
    if len(wave_ids) != len(set(wave_ids)):
        fail("wave ids must be unique")

    flattened = []
    for wave in plan["waves"]:
        flattened.extend(wave["transitions"])
    actual_transitions = keyed(flattened, "transition")
    expected_transitions = keyed(snapshot["transitions"], "authority transition")
    if set(actual_transitions) != set(expected_transitions):
        fail("transition set differs from the pinned supported transition set")

    expected_order = [item["id"] for item in sorted(snapshot["transitions"], key=lambda item: item["order"])]
    actual_order = [item["id"] for item in flattened]
    if actual_order != expected_order:
        fail("transitions are not in the required supported order")

    for transition_id, expected in expected_transitions.items():
        actual = actual_transitions[transition_id]
        for field in ("componentId", "fromVersion", "toVersion"):
            if actual[field] != expected[field]:
                fail(f"unsupported value in {transition_id}.{field}")
        if actual["gateIds"] != expected["requiredGateIds"]:
            fail(f"wrong or reordered technical gates for {transition_id}")

    transitions_by_component = {component_id: [] for component_id in inventory_components}
    for transition in flattened:
        transitions_by_component[transition["componentId"]].append(transition)
    for component_id, chain in transitions_by_component.items():
        version = inventory_components[component_id]["currentVersion"]
        for transition in chain:
            if transition["fromVersion"] != version:
                fail(f"broken version chain for {component_id}")
            version = transition["toVersion"]
        if version != snapshot["componentTargets"][component_id]:
            fail(f"version chain for {component_id} does not reach its target")


def verify_module(plan, inventory, snapshot):
    if not MANIFEST.is_file() or not MODULE.is_file():
        fail("PowerShell module manifest or implementation is missing")

    command = r'''
$ErrorActionPreference = 'Stop'
$data = Import-PowerShellDataFile -LiteralPath $env:VCF_PLAN_MANIFEST
[pscustomobject]@{
    RootModule = $data.RootModule
    FunctionsToExport = @($data.FunctionsToExport)
    RequiredModules = @($data.RequiredModules | ForEach-Object {
        if ($_ -is [string]) { $_ } else { $_.ModuleName }
    })
} | ConvertTo-Json -Compress
'''
    result = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            command,
        ],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "VCF_PLAN_MANIFEST": str(MANIFEST)},
    )
    if result.returncode != 0:
        fail(f"invalid module manifest: {(result.stderr or result.stdout).strip()}")
    try:
        manifest_data = json.loads(result.stdout)
    except Exception as exc:
        fail(f"cannot inspect module manifest: {exc}")
    if manifest_data["RootModule"] != "VcfBrownfieldPlanner.psm1":
        fail("module manifest has the wrong RootModule")
    if "New-VcfMigrationPlan" not in manifest_data["FunctionsToExport"]:
        fail("New-VcfMigrationPlan is not exported")
    if "VMware.Sdk.Vcf.SddcManager" not in manifest_data["RequiredModules"]:
        fail("VMware.Sdk.Vcf.SddcManager is not a required module")

    command = r'''
$ErrorActionPreference = 'Stop'
$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_PLAN_MODULE,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 10
}
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
$planner = @($functions | Where-Object Name -EQ 'New-VcfMigrationPlan')
if ($planner.Count -ne 1) { exit 11 }
$shadowed = @($functions | Where-Object Name -EQ 'Get-VcfSddcManagerOperation')
if ($shadowed.Count) { exit 12 }
$parameterNames = @($planner[0].Body.ParamBlock.Parameters | ForEach-Object {
    $_.Name.VariablePath.UserPath
})
$expectedParameters = @('InventoryPath', 'CompatibilityPath', 'OutputPath')
if (@($parameterNames).Count -ne 3) { exit 13 }
foreach ($name in $expectedParameters) {
    if ($name -notin $parameterNames) { exit 14 }
}
$plannerCommands = @($planner[0].FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$resolvers = @($plannerCommands | Where-Object {
    $_.GetCommandName() -eq 'Get-Command' -and
    $_.Extent.Text -match '(?i)Get-VcfSddcManagerOperation' -and
    $_.Extent.Text -match '(?i)VMware\.Sdk\.Vcf\.SddcManager'
})
if ($resolvers.Count -ne 1) { exit 15 }
$forbidden = @($plannerCommands | Where-Object {
    $_.GetCommandName() -in @('Install-Module', 'Save-Module')
})
if ($forbidden.Count) { exit 16 }
'''
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "VCF_PLAN_MODULE": str(MODULE)},
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        fail(f"invalid PowerShell module implementation (AST check {result.returncode}): {details}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_directory:
        temp_path = Path(temp_directory)
        generated_path = temp_path / "migration-plan.json"
        env = {
            **os.environ,
            "VCF_PLAN_MODULE": str(MODULE),
            "VCF_PLAN_INVENTORY": str(INVENTORY),
            "VCF_PLAN_COMPATIBILITY": str(SNAPSHOT),
            "VCF_PLAN_OUTPUT": str(generated_path),
        }
        command = r'''
$ErrorActionPreference = 'Stop'
Import-Module $env:VCF_PLAN_MODULE -Force
New-VcfMigrationPlan `
    -InventoryPath $env:VCF_PLAN_INVENTORY `
    -CompatibilityPath $env:VCF_PLAN_COMPATIBILITY `
    -OutputPath $env:VCF_PLAN_OUTPUT | Out-Null
'''
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            fail(f"New-VcfMigrationPlan failed: {details}")
        if load_json(generated_path) != plan:
            fail("New-VcfMigrationPlan does not reproduce migration-plan.json")

        probe_inventory = json.loads(json.dumps(inventory))
        probe_snapshot = json.loads(json.dumps(snapshot))
        probe_inventory["estateId"] = "verifier-probe-estate"
        probe_inventory["components"][0]["name"] = "verifier-probe-sddc-manager"
        probe_snapshot["gates"][0]["condition"] = "Verifier probe gate condition."
        probe_inventory_path = temp_path / "probe-inventory.json"
        probe_snapshot_path = temp_path / "probe-compatibility.json"
        probe_output_path = temp_path / "probe-plan.json"
        probe_inventory_path.write_text(json.dumps(probe_inventory), encoding="utf-8")
        probe_snapshot_path.write_text(json.dumps(probe_snapshot), encoding="utf-8")
        env.update(
            {
                "VCF_PLAN_INVENTORY": str(probe_inventory_path),
                "VCF_PLAN_COMPATIBILITY": str(probe_snapshot_path),
                "VCF_PLAN_OUTPUT": str(probe_output_path),
            }
        )
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            fail(f"New-VcfMigrationPlan failed with alternate supplied inputs: {details}")
        probe_plan = load_json(probe_output_path)
        if probe_plan["estateId"] != "verifier-probe-estate":
            fail("New-VcfMigrationPlan does not consume InventoryPath")
        if probe_plan["components"][0]["name"] != "verifier-probe-sddc-manager":
            fail("New-VcfMigrationPlan does not derive components from InventoryPath")
        if probe_plan["gates"][0]["condition"] != "Verifier probe gate condition.":
            fail("New-VcfMigrationPlan does not consume CompatibilityPath")


def verify_research():
    try:
        text = RESEARCH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("research-notes.md is missing")

    entries = []
    for line in text.splitlines():
        if not re.match(r"^\s*-\s+", line):
            continue
        fields = {}
        for label, value in re.findall(
            r"(?i)(Title|Publisher|URL|Consulted|Conclusion)\s*:\s*(.*?)(?=\s*\|\s*(?:Title|Publisher|URL|Consulted|Conclusion)\s*:|$)",
            re.sub(r"^\s*-\s+", "", line),
        ):
            key = label.lower()
            if key in fields:
                fail(f"research source repeats the {label} field")
            fields[key] = value.strip()
        if fields:
            entries.append(fields)

    if not entries:
        fail("research-notes.md must contain labeled source records")

    urls = set()
    topics = []
    for fields in entries:
        required = {"title", "publisher", "url", "consulted", "conclusion"}
        if set(fields) != required or any(not fields[name] for name in required):
            fail("each research source needs Title, Publisher, URL, Consulted, and Conclusion")
        try:
            date.fromisoformat(fields["consulted"])
        except ValueError:
            fail("each research source needs a valid YYYY-MM-DD consultation date")
        parsed = urlsplit(fields["url"])
        hostname = (parsed.hostname or "").lower()
        official_host = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
        )
        if parsed.scheme != "https" or not official_host:
            fail("research sources must use exact HTTPS URLs on official Broadcom or VMware sites")
        if fields["url"] in urls:
            fail("research source URLs must be unique")
        urls.add(fields["url"])
        if not re.search(r"(?i)broadcom|vmware", fields["publisher"]):
            fail("research source publisher must identify Broadcom or VMware")
        topics.append(f'{fields["title"]} {fields["conclusion"]}'.lower())

    combined_topics = " ".join(topics)
    if not re.search(r"compatib|interop|supported|bom", combined_topics):
        fail("research conclusions must cover supported combinations or interoperability")
    if not re.search(r"upgrad|path|hop|sequenc|transition|bundle", combined_topics):
        fail("research conclusions must cover upgrade-path or sequencing decisions")


def main():
    try:
        validate_artifact_schema_first()
        verify_protected_inputs()
        plan = load_json(ARTIFACT)
        inventory = load_json(INVENTORY)
        snapshot = load_json(SNAPSHOT)
        verify_plan(plan, inventory, snapshot)
        verify_module(plan, inventory, snapshot)
        verify_research()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: malformed fixture or artifact structure: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: verifier runtime error: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration architecture matches the pinned VCF compatibility authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
