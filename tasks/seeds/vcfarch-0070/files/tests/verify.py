#!/usr/bin/env python3
"""Deterministic verifier for the VCF brownfield architecture seed.

The submitted research log is deliberately outside this verifier's inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path.cwd()
PLAN_PATH = ROOT / "migration-plan.json"
INSTALLER_SPEC_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PLAN_SCHEMA_PATH = ROOT / "specifications/migration-plan-schema.json"
INVENTORY_PATH = ROOT / "fixtures/estate-inventory.json"
SNAPSHOT_PATH = ROOT / "grading/compatibility-snapshot.json"
RESEARCH_PATH = ROOT / "research-sources.json"

PROTECTED_HASHES = {
    "fixtures/estate-inventory.json": "a57d704dccdc9880eb5629691c8eaa4eeafb6e048c2c6c72e1c77e156fd3a23a",
    "grading/compatibility-snapshot.json": "f39db236c6879ba812aabefeb491f192eef281d439d2c68feb42603fb7858e36",
    "specifications/migration-plan-schema.json": "a3707293d7308eb0d535c6bcf7d242d139de853767c9032e4a3eadb06dce44ef",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/vcf-installer/LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local schema references are supported: {pointer}")
    value = document
    for part in pointer[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        require(isinstance(value, dict) and part in value, f"unresolved schema reference: {pointer}")
        value = value[part]
    return value


def _is_type(instance: Any, expected: str) -> bool:
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
    return True


def validate_schema(instance: Any, schema: Any, document: Any, path: str = "$") -> None:
    """Validate the JSON-Schema subset used by the pinned OpenAPI and plan schemas."""
    if isinstance(schema, bool):
        require(schema, f"{path}: rejected by false schema")
        return
    require(isinstance(schema, dict), f"{path}: malformed schema")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return
    if instance is None and schema.get("nullable") is True:
        return

    for branch in schema.get("allOf", []):
        validate_schema(instance, branch, document, path)
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(instance, branch, document, path)
                matches += 1
            except VerificationError:
                pass
        require(matches == 1, f"{path}: must match exactly one schema branch")
    if "anyOf" in schema:
        matched = False
        for branch in schema["anyOf"]:
            try:
                validate_schema(instance, branch, document, path)
                matched = True
                break
            except VerificationError:
                pass
        require(matched, f"{path}: does not match any schema branch")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        require(any(_is_type(instance, item) for item in expected_type), f"{path}: wrong type")
    elif isinstance(expected_type, str):
        require(_is_type(instance, expected_type), f"{path}: expected {expected_type}")

    if "const" in schema:
        require(instance == schema["const"], f"{path}: must equal {schema['const']!r}")
    if "enum" in schema:
        require(instance in schema["enum"], f"{path}: value is not in enum")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            require(key in instance, f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema(value, properties[key], document, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], document, f"{path}.{key}")

    if isinstance(instance, list):
        if "minItems" in schema:
            require(len(instance) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(instance) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            require(len(encoded) == len(set(encoded)), f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                validate_schema(value, schema["items"], document, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema:
            require(len(instance) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(instance) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], instance) is not None, f"{path}: pattern mismatch")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            require(instance >= schema["minimum"], f"{path}: below minimum")
        if "maximum" in schema:
            require(instance <= schema["maximum"], f"{path}: above maximum")
        if "exclusiveMinimum" in schema:
            require(instance > schema["exclusiveMinimum"], f"{path}: below exclusive minimum")
        if "exclusiveMaximum" in schema:
            require(instance < schema["exclusiveMaximum"], f"{path}: above exclusive maximum")


def shortest_path(snapshot: dict[str, Any], source: str, target: str) -> list[dict[str, str]]:
    graph: dict[str, list[str]] = defaultdict(list)
    for hop in snapshot["supportedHops"]:
        graph[hop["from"]].append(hop["to"])
    queue: deque[tuple[str, list[str]]] = deque([(source, [source])])
    visited = {source}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return [{"from": path[i], "to": path[i + 1]} for i in range(len(path) - 1)]
        for neighbor in graph[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    raise VerificationError(f"snapshot contains no supported path from {source} to {target}")


def validate_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, f"protected file changed: {relative}")


def validate_research_sources() -> None:
    sources = load_json(RESEARCH_PATH)
    require(isinstance(sources, list) and sources, "research-sources.json must be a non-empty array")
    seen_urls: set[str] = set()
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"research source {index} must be an object")
        for field in ("title", "url", "decision"):
            require(
                isinstance(source.get(field), str) and source[field].strip(),
                f"research source {index} has no non-empty {field}",
            )
        parsed = urlsplit(source["url"])
        host = (parsed.hostname or "").lower()
        require(parsed.scheme == "https" and host, f"research source {index} must use an absolute HTTPS URL")
        require(
            host == "broadcom.com" or host.endswith(".broadcom.com")
            or host == "vmware.com" or host.endswith(".vmware.com"),
            f"research source {index} is not a Broadcom/VMware-published URL",
        )
        require(source["url"] not in seen_urls, f"duplicate research source URL: {source['url']}")
        seen_urls.add(source["url"])


def expected_edge_form_factor(snapshot: dict[str, Any], throughput: float) -> str:
    for tier in snapshot["edgeSizing"]:
        if "minInclusiveGbps" in tier and throughput < tier["minInclusiveGbps"]:
            continue
        if "minExclusiveGbps" in tier and throughput <= tier["minExclusiveGbps"]:
            continue
        if "maxInclusiveGbps" in tier and throughput > tier["maxInclusiveGbps"]:
            continue
        return tier["formFactor"]
    raise VerificationError("no Edge sizing tier covers the required throughput")


def validate_plan_semantics(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(plan["estateId"] == inventory["estateId"], "estateId does not match inventory")
    require(plan["sourceVcfVersion"] == inventory["vcfVersion"], "source VCF version does not match inventory")
    require(plan["targetVcfVersion"] == inventory["targetVcfVersion"], "target VCF version does not match inventory")

    expected_hops = shortest_path(snapshot, inventory["vcfVersion"], inventory["targetVcfVersion"])
    require(plan["hops"] == expected_hops, "migration hops are not the complete supported path")

    components = {item["id"]: dict(item) for item in inventory["components"]}
    require(len(components) == len(inventory["components"]), "inventory component IDs are not unique")
    source_bom = snapshot["bomByVcfVersion"][inventory["vcfVersion"]]
    for component in components.values():
        require(component["type"] in source_bom, f"source BOM omits {component['type']}")
        require(component["version"] == source_bom[component["type"]], f"inventory version mismatch for {component['id']}")

    orders = [step["order"] for step in plan["steps"]]
    require(orders == list(range(1, len(orders) + 1)), "step order must be unique, contiguous, and ascending")
    gate_definitions = {gate["id"]: gate for gate in plan["gates"]}
    require(len(gate_definitions) == len(plan["gates"]), "gate IDs must be unique")
    referenced_gates: set[str] = set()

    current_versions = {component_id: item["version"] for component_id, item in components.items()}
    steps_by_hop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in plan["steps"]:
        steps_by_hop[f"{step['hopFrom']}->{step['hopTo']}"].append(step)

    known_components = dict(components)
    for hop in expected_hops:
        hop_key = f"{hop['from']}->{hop['to']}"
        phases = snapshot["phaseSequenceByHop"][hop_key]
        target_bom = snapshot["bomByVcfVersion"][hop["to"]]
        additions = snapshot.get("newComponentsByHop", {}).get(hop_key, [])
        for addition in additions:
            require(addition["id"] not in known_components, f"duplicate new component ID {addition['id']}")
            known_components[addition["id"]] = dict(addition)
            current_versions[addition["id"]] = "NOT_PRESENT"

        hop_steps = steps_by_hop.get(hop_key, [])
        require(len(hop_steps) == len(known_components), f"{hop_key}: must name every component exactly once")
        by_component = {step["componentId"]: step for step in hop_steps}
        require(len(by_component) == len(hop_steps), f"{hop_key}: duplicate component step")
        require(set(by_component) == set(known_components), f"{hop_key}: missing or extra component steps")

        ranks = []
        for step in hop_steps:
            component = known_components[step["componentId"]]
            component_type = component["type"]
            scope = component["domain"] if component["domain"] in {"fleet", "management"} else "workload"
            placements = [
                (phase_index, phase["types"].index(component_type))
                for phase_index, phase in enumerate(phases)
                if phase["scope"] == scope and component_type in phase["types"]
            ]
            require(len(placements) == 1, f"{hop_key}: unsupported phase for {component_type} in {scope}")
            require(step["hopFrom"] == hop["from"] and step["hopTo"] == hop["to"], f"{hop_key}: step hop mismatch")
            require(step["componentName"] == component["name"], f"{hop_key}: wrong component name for {component['id']}")
            require(step["componentType"] == component_type, f"{hop_key}: wrong type for {component['id']}")
            require(step["domain"] == component["domain"], f"{hop_key}: wrong domain for {component['id']}")
            require(step["fromVersion"] == current_versions[component["id"]], f"{hop_key}: wrong source version for {component['id']}")
            require(step["toVersion"] == target_bom[component_type], f"{hop_key}: wrong target version for {component['id']}")
            required_gates = set(snapshot["requiredGatesByHopAndType"][hop_key][component_type])
            required_gates.update(snapshot.get("requiredGatesByHopAndScope", {}).get(hop_key, {}).get(scope, []))
            require(required_gates.issubset(set(step["gates"])), f"{hop_key}: missing technical gate for {component['id']}")
            for gate_id in step["gates"]:
                require(gate_id in gate_definitions, f"undefined gate {gate_id}")
                referenced_gates.add(gate_id)

            ranks.append(placements[0])
        require(ranks == sorted(ranks), f"{hop_key}: fleet, management, or workload phases are out of supported order")

        for component_id, component in known_components.items():
            current_versions[component_id] = target_bom[component["type"]]

    expected_hop_keys = {f"{hop['from']}->{hop['to']}" for hop in expected_hops}
    require(set(steps_by_hop) == expected_hop_keys, "steps include an unsupported hop")
    require(set(gate_definitions) == referenced_gates, "gate definitions must be used by at least one step")

    requirement = inventory["edgeRequirement"]
    architecture = plan["architecture"]
    edge = architecture["edge"]
    throughput = requirement["northSouthThroughputGbps"]
    require(architecture["requiredNorthSouthThroughputGbps"] == throughput, "architecture throughput does not match inventory")
    require(edge["formFactor"] == expected_edge_form_factor(snapshot, throughput), "Edge form factor does not satisfy throughput tier")
    require(edge["nodeCount"] == requirement["nodeCount"], "Edge node count does not match inventory")
    uplink_rule = snapshot["edgeUplinkRule"]
    require(edge["tier0Mode"] == uplink_rule["tier0Mode"], "wrong Tier-0 mode")
    require(edge["routing"] == uplink_rule["routing"], "wrong Edge routing layout")
    expected_uplinks = {json.dumps(item, sort_keys=True) for item in requirement["availableUplinks"]}
    actual_uplinks = {json.dumps(item, sort_keys=True) for item in edge["uplinks"]}
    require(actual_uplinks == expected_uplinks, "Edge uplink layout must use all and only the inventoried links")
    uplinks_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for uplink in edge["uplinks"]:
        uplinks_by_node[uplink["edgeNodeId"]].append(uplink)
        require(uplink["speedGbps"] >= uplink_rule["minimumLinkSpeedGbps"], "Edge uplink is too slow")
    edge_ids = {item["id"] for item in inventory["components"] if item["type"] == "NSX_EDGE"}
    require(set(uplinks_by_node) == edge_ids, "uplinks must cover every Edge node")
    for node_id, uplinks in uplinks_by_node.items():
        require(len(uplinks) >= uplink_rule["minimumUplinksPerNode"], f"{node_id}: insufficient uplinks")
        require(len({item["tor"] for item in uplinks}) >= uplink_rule["minimumDistinctTorsPerNode"], f"{node_id}: uplinks do not span ToRs")
        require(len({item["hostPnic"] for item in uplinks}) == len(uplinks), f"{node_id}: uplinks must use distinct pNICs")

    sddc = plan["targetSddcSpec"]
    require(sddc.get("sddcId") == inventory["sddcId"], "target SddcSpec has wrong sddcId")
    require(sddc.get("version") == inventory["targetVcfVersion"], "target SddcSpec has wrong version")
    require(sddc.get("workflowType") == "VCF", "target SddcSpec must select VCF workflow")
    require(sddc.get("vcfInstanceName") == inventory["vcfInstanceName"], "target SddcSpec has wrong instance name")
    management_vcenter = next(item for item in inventory["components"] if item["type"] == "VCENTER" and item["domain"] == "management")
    vcenter_spec = sddc["vcenterSpec"]
    require(vcenter_spec.get("vcenterHostname") == management_vcenter["name"], "target SddcSpec has wrong vCenter")
    require(vcenter_spec.get("useExistingDeployment") is True, "target SddcSpec must represent brownfield vCenter")
    require(sddc["dnsSpec"].get("subdomain") == inventory["network"]["dnsDomain"], "target SddcSpec DNS domain mismatch")
    require(sddc["dnsSpec"].get("nameservers") == inventory["network"]["dnsServers"], "target SddcSpec DNS servers mismatch")
    expected_networks = {
        name.upper(): values for name, values in inventory["network"].items()
        if name in {"management", "vmotion", "vsan"}
    }
    actual_networks = {item["networkType"]: item for item in sddc["networkSpecs"]}
    require(set(actual_networks) == set(expected_networks), "target SddcSpec network set mismatch")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for key in ("vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(key) == expected[key], f"target SddcSpec {network_type} {key} mismatch")
    require(sddc.get("ntpServers") == inventory["network"]["ntpServers"], "target SddcSpec NTP servers mismatch")
def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def validate_module(plan: dict[str, Any], inventory: dict[str, Any]) -> None:
    manifest_path = ROOT / "VcfMigration/VcfMigration.psd1"
    module_path = ROOT / "VcfMigration/VcfMigration.psm1"
    require(manifest_path.is_file(), "missing PowerShell module manifest")
    require(module_path.is_file(), "missing PowerShell module implementation")

    manifest_script = (
        f"$m=Import-PowerShellDataFile {ps_quote(manifest_path)}; "
        "$m | ConvertTo-Json -Depth 20 -Compress"
    )
    manifest_run = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", manifest_script],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    require(manifest_run.returncode == 0, f"invalid module manifest: {manifest_run.stderr.strip()}")
    manifest = json.loads(manifest_run.stdout)
    required_modules = manifest.get("RequiredModules", [])
    if isinstance(required_modules, str):
        required_modules = [required_modules]
    normalized_modules = {
        item if isinstance(item, str) else item.get("ModuleName")
        for item in required_modules
    }
    require("VMware.Sdk.Vcf.SddcManager" in normalized_modules, "module manifest must require VMware.Sdk.Vcf.SddcManager")
    exports = manifest.get("FunctionsToExport", [])
    require({"Get-VcfEstateInventory", "New-VcfMigrationPlan"}.issubset(set(exports)), "module manifest does not export required functions")

    ast_script = (
        "$tokens=$null; $errors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({ps_quote(module_path)},[ref]$tokens,[ref]$errors); "
        "if($errors.Count){ throw ($errors | Out-String) }; "
        "$functions=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Get-VcfEstateInventory'},$true)); "
        "if($functions.Count -ne 1){ throw 'Get-VcfEstateInventory must have exactly one definition' }; "
        "$function=$functions[0]; "
        "[ordered]@{ Parameters=@($function.Body.ParamBlock.Parameters | ForEach-Object {$_.Name.VariablePath.UserPath}); "
        "Commands=@($function.Body.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]},$true) "
        "| ForEach-Object {[ordered]@{Name=$_.GetCommandName(); Text=$_.Extent.Text}}) } "
        "| ConvertTo-Json -Depth 10 -Compress"
    )
    ast_run = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", ast_script],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    require(ast_run.returncode == 0, f"cannot inspect inventory function: {ast_run.stderr.strip()}")
    inventory_function = json.loads(ast_run.stdout)
    commands = {item["Name"] for item in inventory_function["Commands"]}
    for command in (
        "Import-Module", "Connect-VcfSddcManagerServer", "Get-SddcDomain", "Get-SddcCluster",
        "Get-SddcHost", "Get-SddcVcenter", "Disconnect-VcfSddcManagerServer",
    ):
        require(command in commands, f"SDK-backed inventory function does not invoke {command}")
    import_commands = [item["Text"] for item in inventory_function["Commands"] if item["Name"] == "Import-Module"]
    require(
        any(re.search(r"\bVMware\.Sdk\.Vcf\.SddcManager\b", item) for item in import_commands),
        "Get-VcfEstateInventory must import VMware.Sdk.Vcf.SddcManager",
    )

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        generated_path = Path(temp_dir) / "generated-plan.json"
        script = (
            "$ErrorActionPreference='Stop'; "
            f"Import-Module {ps_quote(module_path)} -Force; "
            f"New-VcfMigrationPlan -InventoryPath {ps_quote(INVENTORY_PATH)} "
            f"-CompatibilitySnapshotPath {ps_quote(SNAPSHOT_PATH)} "
            f"-OutputPath {ps_quote(generated_path)}"
        )
        run = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
            cwd=ROOT, text=True, capture_output=True, timeout=60,
        )
        require(run.returncode == 0, f"plan generator failed: {run.stderr.strip() or run.stdout.strip()}")
        generated = load_json(generated_path)
        require(generated == plan, "migration-plan.json is not the deterministic module output")

        unsupported_inventory = dict(inventory)
        unsupported_inventory["vcfVersion"] = snapshot_target = load_json(SNAPSHOT_PATH)["targetVcfVersion"]
        unsupported_inventory["targetVcfVersion"] = load_json(SNAPSHOT_PATH)["sourceVcfVersion"]
        unsupported_path = Path(temp_dir) / "unsupported-inventory.json"
        unsupported_path.write_text(json.dumps(unsupported_inventory), encoding="utf-8")
        rejected_path = Path(temp_dir) / "should-not-exist.json"
        reject_script = (
            "$ErrorActionPreference='Stop'; "
            f"Import-Module {ps_quote(module_path)} -Force; "
            f"New-VcfMigrationPlan -InventoryPath {ps_quote(unsupported_path)} "
            f"-CompatibilitySnapshotPath {ps_quote(SNAPSHOT_PATH)} "
            f"-OutputPath {ps_quote(rejected_path)}"
        )
        rejected = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", reject_script],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        require(
            rejected.returncode != 0 and not rejected_path.exists(),
            f"plan generator must reject the disconnected {snapshot_target} source/target pair",
        )


def main() -> int:
    try:
        # Required first validation stage: the embedded artifact is checked against
        # the installer's own SddcSpec before the plan schema or semantic oracle.
        plan = load_json(PLAN_PATH)
        installer_spec = load_json(INSTALLER_SPEC_PATH)
        require(isinstance(plan, dict) and "targetSddcSpec" in plan, "installer schema validation: missing targetSddcSpec")
        require(installer_spec.get("info", {}).get("version") == "9.1.0.0", "installer schema is not tag version 9.1.0.0")
        validate_schema(
            plan["targetSddcSpec"],
            installer_spec["components"]["schemas"]["SddcSpec"],
            installer_spec,
            "$.targetSddcSpec",
        )
        print("PASS 1: targetSddcSpec validates against installer OpenAPI 9.1.0.0")

        validate_protected_files()
        validate_research_sources()
        plan_schema = load_json(PLAN_SCHEMA_PATH)
        validate_schema(plan, plan_schema, plan_schema)
        print("PASS 2: migration plan schema")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        validate_plan_semantics(plan, inventory, snapshot)
        print("PASS 3: pinned compatibility, ordering, component, gate, and Edge architecture checks")

        validate_module(plan, inventory)
        print("PASS 4: VMware.Sdk.Vcf PowerShell module and deterministic generation")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
