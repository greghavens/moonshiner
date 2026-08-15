#!/usr/bin/env python3
"""Offline protected verifier for the VCF brownfield architecture."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "architecture" / "migration-plan.json"
INSTALLER_PATH = (
    ROOT
    / "specifications"
    / "vcf-installer"
    / "vcf-installer-openapi.json"
)


class VerificationError(AssertionError):
    pass


class SchemaError(VerificationError):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc}"
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
    raise SchemaError(f"unsupported schema type {expected!r}")


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"external schema reference is not supported: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[part]
        except (KeyError, TypeError) as exc:
            raise SchemaError(f"unresolvable schema reference: {ref}") from exc
    if not isinstance(current, dict):
        raise SchemaError(f"schema reference does not resolve to an object: {ref}")
    return current


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    for index, subschema in enumerate(schema.get("allOf", [])):
        validate_schema(value, subschema, root_schema, f"{path}<allOf:{index}>")

    if "anyOf" in schema:
        failures = []
        for subschema in schema["anyOf"]:
            try:
                validate_schema(value, subschema, root_schema, path)
                break
            except SchemaError as exc:
                failures.append(str(exc))
        else:
            raise SchemaError(f"{path}: no anyOf branch matched: {failures}")

    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_schema(value, subschema, root_schema, path)
                matches += 1
            except SchemaError:
                pass
        if matches != 1:
            raise SchemaError(f"{path}: expected one oneOf match, got {matches}")

    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else expected
        if not any(json_type_matches(value, item) for item in allowed):
            raise SchemaError(f"{path}: expected type {expected!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise SchemaError(f"{path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], root_schema, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    child,
                    schema["additionalProperties"],
                    root_schema,
                    f"{path}.{name}",
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: array items are not unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, child in enumerate(value):
                validate_schema(child, items, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                raise SchemaError(f"{path}: unsupported regex {schema['pattern']!r}") from exc
            if matched is None:
                raise SchemaError(
                    f"{path}: {value!r} does not match {schema['pattern']!r}"
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: {value} is above maximum {schema['maximum']}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_installer_spec_first(plan: dict[str, Any]) -> None:
    """This must remain the first acceptance check after parsing the artifact."""
    installer = read_json(INSTALLER_PATH)
    try:
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
        target = plan["targetSddcSpec"]
    except KeyError as exc:
        raise SchemaError(f"installer SddcSpec validation cannot start: missing {exc}") from exc
    validate_schema(target, sddc_schema, installer, "$.targetSddcSpec")
    check(installer.get("info", {}).get("version") == "9.1.0.0", "wrong installer spec tag")


def validate_plan_contract(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    check(plan["estateId"] == inventory["estateId"], "estateId does not match inventory")
    check(
        plan["sourceVcfVersion"] == inventory["sourceVcfVersion"] == snapshot["sourceVcfVersion"],
        "source VCF version mismatch",
    )
    check(
        plan["targetVcfVersion"] == inventory["targetVcfVersion"] == snapshot["targetVcfVersion"],
        "target VCF version mismatch",
    )
    check(plan["upgradeHops"] == snapshot["supportedUpgradeHops"], "unsupported or missing VCF hop")

    choice = plan["storageDecision"]
    current = inventory["storageDecisionInputs"]["current"]
    esa_input = inventory["storageDecisionInputs"]["esaOption"]
    esa_rule = snapshot["storageOptions"]["ESA"]
    check(choice["selected"] == snapshot["selectedStorageArchitecture"] == "ESA", "ESA must be selected")
    check(choice["rejected"] == "OSA", "the alternative must be identified as OSA")
    check(choice["migrationMode"] == "SIDE_BY_SIDE", "OSA to ESA cannot be represented as in-place")
    check(choice["source"] == {
        "architecture": current["architecture"],
        "hostCount": current["hostCount"],
        "linkSpeedGbps": current["linkSpeedGbps"],
    }, "source storage shape mismatch")
    expected_target = {
        "architecture": "ESA",
        "hostCount": esa_rule["minimumHostCount"],
        "linkSpeedGbps": esa_rule["minimumLinkSpeedGbps"],
        "readyNodeProfile": esa_rule["readyNodeProfile"],
        "vlanId": esa_input["vlanId"],
        "mtu": esa_rule["requiredMtu"],
    }
    check(choice["target"] == expected_target, "ESA host-count or network architecture mismatch")
    check(esa_rule["inPlaceOsaConversionSupported"] is False, "snapshot unexpectedly permits in-place conversion")

    expected_gates = snapshot["gates"]
    check(plan["gates"] == expected_gates, "plan gates differ from pinned compatibility snapshot")
    gate_ids = [gate["id"] for gate in plan["gates"]]
    check(len(gate_ids) == len(set(gate_ids)), "duplicate gate id")
    known_gates = set(gate_ids)

    components = {component["id"]: component for component in inventory["components"]}
    steps = plan["steps"]
    sequence = snapshot["componentSequence"]
    check([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order is not contiguous")
    check([step["componentId"] for step in steps] == sequence, "component upgrade/migration order is wrong")
    check(set(sequence) == set(components), "every inventory component must appear exactly once")
    check(len(sequence) == len(components), "a component appears more than once")
    for step in steps:
        component = components[step["componentId"]]
        check(step["componentName"] == component["name"], f"wrong name for {component['id']}")
        check(step["componentType"] == component["type"], f"wrong type for {component['id']}")
        check(step["fromVersion"] == component["version"], f"wrong source version for {component['id']}")
        check(step["targetVersion"] == component["targetVersion"], f"wrong target for {component['id']}")
        check(step["action"] == snapshot["componentActions"][component["id"]], f"wrong action for {component['id']}")
        check(step["gates"] == snapshot["componentGates"][component["id"]], f"wrong gates for {component['id']}")
        check(set(step["gates"]) <= known_gates, f"undefined gate for {component['id']}")

    spec = plan["targetSddcSpec"]
    target_inputs = inventory["targetSpecInputs"]
    check(spec["sddcId"] == target_inputs["sddcId"], "target SDDC id mismatch")
    check(spec["version"] == snapshot["targetVcfVersion"], "target SddcSpec version mismatch")
    check(spec["workflowType"] == "VCF_COMPLETE", "target workflow must be VCF_COMPLETE")
    check(spec["vcenterSpec"]["vcenterHostname"] == target_inputs["vcenterHostname"], "vCenter hostname mismatch")
    check(spec["vcenterSpec"]["rootVcenterPassword"] == "REDACTED-FIXTURE", "artifact must contain only the fixed non-secret placeholder")
    check(spec["vcenterSpec"]["useExistingDeployment"] is True, "brownfield vCenter must be reused")
    check(spec["dnsSpec"] == {
        "subdomain": target_inputs["subdomain"],
        "nameservers": target_inputs["nameservers"],
    }, "DNS spec mismatch")
    check(spec["ntpServers"] == target_inputs["ntpServers"], "NTP spec mismatch")
    check([host["hostname"] for host in spec["hostSpecs"]] == esa_input["hostnames"], "target host inventory mismatch")
    check(len(spec["hostSpecs"]) == esa_rule["minimumHostCount"], "ESA host count is not four")
    check(spec["networkSpecs"] == target_inputs["networks"], "network specs do not match fixture")
    vsan_network = next((network for network in spec["networkSpecs"] if network["networkType"] == "VSAN"), None)
    check(vsan_network is not None, "target SddcSpec lacks a VSAN network")
    check(vsan_network["vlanId"] == esa_input["vlanId"], "wrong ESA vSAN VLAN")
    check(vsan_network["mtu"] == esa_rule["requiredMtu"], "wrong ESA vSAN MTU")
    check(spec["datastoreSpec"]["vsanSpec"]["esaConfig"]["enabled"] is True, "SddcSpec does not enable ESA")


def validate_research_notes() -> None:
    notes = read_json(ROOT / "research-notes.json")
    check(isinstance(notes, list), "research-notes.json must contain a JSON array")
    check(notes, "research notes must include at least one consulted source")

    required = {"title", "url", "accessed", "conclusion"}
    for index, note in enumerate(notes):
        check(isinstance(note, dict), f"research note {index + 1} must be an object")
        check(required <= set(note), f"research note {index + 1} is missing a required field")
        for field in required:
            check(
                isinstance(note[field], str) and note[field].strip(),
                f"research note {index + 1} has an empty {field}",
            )

        try:
            parsed = urlparse(note["url"])
            hostname = (parsed.hostname or "").lower()
        except ValueError as exc:
            raise VerificationError(
                f"research note {index + 1} has a malformed URL"
            ) from exc
        check(parsed.scheme == "https", f"research note {index + 1} must use HTTPS")
        check(
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or (hostname == "github.com" and parsed.path.lower().startswith("/vmware/")),
            f"research note {index + 1} is not a Broadcom-published source",
        )
        check(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", note["accessed"]) is not None,
            f"research note {index + 1} has a non-ISO access date",
        )
        try:
            dt.date.fromisoformat(note["accessed"])
        except ValueError as exc:
            raise VerificationError(
                f"research note {index + 1} has a non-ISO access date"
            ) from exc
def read_manifest(manifest: Path, temp_dir: Path) -> dict[str, Any]:
    invocation = temp_dir / "read-manifest.ps1"
    invocation.write_text(
        "param([string] $ManifestPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$data = Import-PowerShellDataFile -LiteralPath $ManifestPath\n"
        "$required = @(foreach ($entry in @($data.RequiredModules)) {\n"
        "    if ($entry -is [string]) { $entry } else { $entry.ModuleName }\n"
        "})\n"
        "[pscustomobject]@{\n"
        "    rootModule = $data.RootModule\n"
        "    requiredModules = $required\n"
        "    functionsToExport = @($data.FunctionsToExport)\n"
        "} | ConvertTo-Json -Depth 10 -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(invocation), str(manifest)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    check(result.returncode == 0, f"module manifest is invalid: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"could not inspect module manifest: {exc}") from exc


def exercise_live_inventory_command(implementation: Path, temp_dir: Path) -> None:
    invocation = temp_dir / "invoke-live-inventory.ps1"
    invocation.write_text(
        "param([string] $ModulePath)\n"
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$global:VcfInventoryCalls = [System.Collections.Generic.List[string]]::new()\n"
        "$global:FailVcfInventory = $false\n"
        "function global:Connect-VcfSddcManagerServer {\n"
        "    param([string[]] $Server, [pscredential] $Credential, [switch] $NotDefault)\n"
        "    $global:VcfInventoryCalls.Add('connect')\n"
        "    [pscustomobject]@{ id = 'fixture-connection' }\n"
        "}\n"
        "function global:Get-SddcDomain { param($Server) $global:VcfInventoryCalls.Add('domains'); [pscustomobject]@{ id = 'domain-1' } }\n"
        "function global:Get-SddcCluster { param($Server) $global:VcfInventoryCalls.Add('clusters'); [pscustomobject]@{ id = 'cluster-1' } }\n"
        "function global:Get-SddcHost {\n"
        "    param($Server)\n"
        "    $global:VcfInventoryCalls.Add('hosts')\n"
        "    if ($global:FailVcfInventory) { throw 'fixture collection failure' }\n"
        "    [pscustomobject]@{ id = 'host-1' }\n"
        "}\n"
        "function global:Get-SddcVcenter { param($Server) $global:VcfInventoryCalls.Add('vcenters'); [pscustomobject]@{ id = 'vcenter-1' } }\n"
        "function global:Disconnect-VcfSddcManagerServer {\n"
        "    param($Server, [switch] $Force)\n"
        "    $global:VcfInventoryCalls.Add('disconnect')\n"
        "}\n"
        "Import-Module $ModulePath -Force\n"
        "$secure = ConvertTo-SecureString 'non-secret-fixture' -AsPlainText -Force\n"
        "$credential = [pscredential]::new('fixture-user', $secure)\n"
        "$inventory = Get-VcfEstateInventory -Server 'sddc.example.test' -Credential $credential\n"
        "if ($inventory.server -ne 'sddc.example.test') { throw 'live inventory lost the server name' }\n"
        "foreach ($property in @('domains', 'clusters', 'hosts', 'vcenters')) {\n"
        "    if (@($inventory.$property).Count -ne 1) { throw \"live inventory did not return $property\" }\n"
        "}\n"
        "if ($global:VcfInventoryCalls[0] -ne 'connect' -or $global:VcfInventoryCalls[-1] -ne 'disconnect') { throw 'wrong connection lifecycle' }\n"
        "$inventoryCalls = @($global:VcfInventoryCalls[1..4] | Sort-Object) -join '|'\n"
        "if ($inventoryCalls -ne 'clusters|domains|hosts|vcenters') { throw 'wrong live inventory calls' }\n"
        "$global:VcfInventoryCalls.Clear()\n"
        "$global:FailVcfInventory = $true\n"
        "try { Get-VcfEstateInventory -Server 'sddc.example.test' -Credential $credential | Out-Null } catch {}\n"
        "if ($global:VcfInventoryCalls[0] -ne 'connect' -or $global:VcfInventoryCalls[-1] -ne 'disconnect') { throw 'live inventory did not disconnect after failure' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(invocation), str(implementation)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    check(
        result.returncode == 0,
        f"live inventory command did not use the official SDK flow: {result.stdout}\n{result.stderr}",
    )


def validate_module_and_generation(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot_path: Path,
) -> None:
    module_dir = ROOT / "VcfArchitecture"
    manifest = module_dir / "VcfArchitecture.psd1"
    implementation = module_dir / "VcfArchitecture.psm1"
    for path in (manifest, implementation):
        check(path.is_file(), f"missing PowerShell deliverable: {path.relative_to(ROOT)}")

    for path in module_dir.rglob("*"):
        relative_parts = path.relative_to(module_dir).parts
        check(
            not any(part.lower().startswith("vmware.sdk.vcf") for part in relative_parts),
            f"vendored VMware SDK path is forbidden: {path.relative_to(ROOT)}",
        )
        if path.is_file():
            check(path.suffix.lower() not in {".nupkg", ".dll"}, f"vendored package/binary is forbidden: {path.relative_to(ROOT)}")

    temp_dir = ROOT / ".verify-tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir()
    try:
        manifest_data = read_manifest(manifest, temp_dir)
        check(manifest_data.get("rootModule") == "VcfArchitecture.psm1", "manifest has the wrong root module")
        check(
            "VMware.Sdk.Vcf.SddcManager" in manifest_data.get("requiredModules", []),
            "manifest does not declare VMware.Sdk.Vcf.SddcManager",
        )
        exports = set(manifest_data.get("functionsToExport", []))
        check(
            {"Get-VcfEstateInventory", "New-VcfMigrationPlan"} <= exports,
            "manifest does not export both required commands",
        )
        exercise_live_inventory_command(implementation, temp_dir)

        invocation = temp_dir / "invoke-generator.ps1"
        invocation.write_text(
            "param([string] $ModulePath, [string] $InventoryPath, "
            "[string] $SnapshotPath, [string] $OutputPath)\n"
            "Set-StrictMode -Version Latest\n"
            "$ErrorActionPreference = 'Stop'\n"
            "Import-Module $ModulePath -Force\n"
            "New-VcfMigrationPlan -InventoryPath $InventoryPath "
            "-CompatibilitySnapshotPath $SnapshotPath -OutputPath $OutputPath | Out-Null\n",
            encoding="utf-8",
        )
        generated_path = temp_dir / "generated.json"
        command = [
            "pwsh",
            "-NoProfile",
            "-File",
            str(invocation),
            str(implementation),
            str(ROOT / "fixtures" / "estate-inventory.json"),
            str(snapshot_path),
            str(generated_path),
        ]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        check(result.returncode == 0, f"PowerShell generator failed: {result.stdout}\n{result.stderr}")
        generated = read_json(generated_path)
        check(generated == plan, "committed migration plan was not produced by the generator")
        first_bytes = generated_path.read_bytes()
        repeated_path = temp_dir / "generated-repeated.json"
        repeated_command = command.copy()
        repeated_command[repeated_command.index(str(generated_path))] = str(repeated_path)
        repeated_result = subprocess.run(
            repeated_command, cwd=ROOT, text=True, capture_output=True, timeout=30
        )
        check(repeated_result.returncode == 0, f"repeated generation failed: {repeated_result.stdout}\n{repeated_result.stderr}")
        check(repeated_path.read_bytes() == first_bytes, "plan generator output is not byte-stable")

        variant = copy.deepcopy(inventory)
        variant["estateId"] = "chi02"
        variant["sourceVcfVersion"] = "5.2.1.1"
        variant["targetVcfVersion"] = "9.1.0.1"
        variant["targetSpecInputs"]["sddcId"] = "chi02-w01-esa"
        variant["targetSpecInputs"]["vcenterHostname"] = "chi02-wld-vc01.corp.example"
        variant["components"][0]["name"] = "Variant Operations"
        variant["components"][0]["version"] = "8.18.4"
        variant_hosts = ["chi2-w02-esx01", "chi2-w02-esx02", "chi2-w02-esx03", "chi2-w02-esx04"]
        variant["storageDecisionInputs"]["esaOption"]["hostnames"] = variant_hosts
        variant_snapshot = read_json(snapshot_path)
        variant_snapshot["sourceVcfVersion"] = "5.2.1.1"
        variant_snapshot["targetVcfVersion"] = "9.1.0.1"
        variant_snapshot["supportedUpgradeHops"] = [{"from": "5.2.1.1", "to": "9.1.0.1"}]
        variant_snapshot["storageOptions"]["ESA"]["minimumLinkSpeedGbps"] = 50
        variant_snapshot["gates"][0]["statement"] = "Variant source baseline gate for generator verification."
        variant_path = temp_dir / "variant-inventory.json"
        variant_snapshot_path = temp_dir / "variant-snapshot.json"
        variant_output = temp_dir / "variant-plan.json"
        variant_path.write_text(json.dumps(variant, indent=2) + "\n", encoding="utf-8")
        variant_snapshot_path.write_text(
            json.dumps(variant_snapshot, indent=2) + "\n", encoding="utf-8"
        )
        variant_command = command.copy()
        variant_command[variant_command.index(str(ROOT / "fixtures" / "estate-inventory.json"))] = str(variant_path)
        variant_command[variant_command.index(str(snapshot_path))] = str(variant_snapshot_path)
        variant_command[variant_command.index(str(generated_path))] = str(variant_output)
        variant_result = subprocess.run(
            variant_command, cwd=ROOT, text=True, capture_output=True, timeout=30
        )
        check(variant_result.returncode == 0, f"variant generation failed: {variant_result.stdout}\n{variant_result.stderr}")
        variant_plan = read_json(variant_output)
        check(variant_plan["estateId"] == "chi02", "generator hard-codes estateId")
        check(variant_plan["sourceVcfVersion"] == "5.2.1.1", "generator hard-codes the source version")
        check(variant_plan["targetVcfVersion"] == "9.1.0.1", "generator hard-codes the target version")
        check(
            variant_plan["upgradeHops"] == variant_snapshot["supportedUpgradeHops"],
            "generator hard-codes upgrade hops",
        )
        check(
            variant_plan["storageDecision"]["target"]["linkSpeedGbps"] == 50,
            "generator hard-codes the ESA link speed",
        )
        check(
            variant_plan["gates"][0]["statement"] == variant_snapshot["gates"][0]["statement"],
            "generator hard-codes compatibility gates",
        )
        check(variant_plan["steps"][0]["componentName"] == "Variant Operations", "generator hard-codes component names")
        check(variant_plan["steps"][0]["fromVersion"] == "8.18.4", "generator hard-codes component versions")
        check(variant_plan["targetSddcSpec"]["sddcId"] == "chi02-w01-esa", "generator hard-codes sddcId")
        check(
            variant_plan["targetSddcSpec"]["vcenterSpec"]["vcenterHostname"]
            == "chi02-wld-vc01.corp.example",
            "generator hard-codes vCenter hostname",
        )
        check(
            [host["hostname"] for host in variant_plan["targetSddcSpec"]["hostSpecs"]]
            == variant_hosts,
            "generator hard-codes target hosts",
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def main() -> int:
    try:
        # Only artifact parsing precedes the mandatory installer-schema validation.
        plan = read_json(PLAN_PATH)
        validate_installer_spec_first(plan)
        print("[ok] targetSddcSpec validates against the tagged installer schema")

        # No fixture, compatibility snapshot, local plan schema, or implementation is
        # consulted until the installer SddcSpec validation above has succeeded.
        migration_schema = read_json(ROOT / "specifications" / "migration-plan.schema.json")
        validate_schema(plan, migration_schema, migration_schema)
        print("[ok] migration plan validates against the fixed plan schema")

        inventory = read_json(ROOT / "fixtures" / "estate-inventory.json")
        snapshot_path = ROOT / "specifications" / "compatibility-snapshot.json"
        snapshot = read_json(snapshot_path)
        validate_plan_contract(plan, inventory, snapshot)
        print("[ok] ordered component, hop, storage, host, and network architecture is correct")

        validate_research_notes()
        print("[ok] research audit record has deterministic official-source metadata")

        validate_module_and_generation(plan, inventory, snapshot_path)
        print("[ok] VMware.Sdk.Vcf module integration and deterministic generation are correct")
        return 0
    except (VerificationError, KeyError, TypeError, IndexError) as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
