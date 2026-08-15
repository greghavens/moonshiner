#!/usr/bin/env python3
"""Offline protected verifier for the VCF architecture artifact."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load protected JSON {path.relative_to(ROOT)}: {exc}")


def resolve_pointer(root: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            fail(f"unresolvable schema reference: {ref}")
    return value


def matches_type(value: Any, expected: str) -> bool:
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


def validate_schema(instance: Any, schema: Any, schema_root: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned contracts."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema node")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(schema_root, schema["$ref"]), schema_root, path)
        return

    for branch in schema.get("allOf", []):
        validate_schema(instance, branch, schema_root, path)

    if "anyOf" in schema:
        errors = []
        for branch in schema["anyOf"]:
            try:
                validate_schema(instance, branch, schema_root, path)
                break
            except VerificationError as exc:
                errors.append(str(exc))
        else:
            fail(f"{path}: does not satisfy anyOf ({'; '.join(errors)})")

    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(instance, branch, schema_root, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: satisfies {matches} oneOf branches, expected exactly one")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(matches_type(instance, item) for item in allowed):
            fail(f"{path}: expected type {expected_type}, got {type(instance).__name__}")

    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: {instance!r} is not one of {schema['enum']!r}")
    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(value, properties[key], schema_root, child_path)
            elif schema.get("additionalProperties") is False:
                fail(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], schema_root, child_path)
        count = len(instance)
        if "minProperties" in schema and count < schema["minProperties"]:
            fail(f"{path}: fewer than minProperties")
        if "maxProperties" in schema and count > schema["maxProperties"]:
            fail(f"{path}: more than maxProperties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items are not unique")
        if "items" in schema:
            for index, value in enumerate(instance):
                validate_schema(value, schema["items"], schema_root, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"{path}: invalid pattern in protected schema: {exc}")
            if matched is None:
                fail(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail(f"{path}: not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail(f"{path}: not below exclusiveMaximum")


def compile_and_run_client() -> Any:
    with tempfile.TemporaryDirectory(prefix="vcfarch-0132-") as build_dir:
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                build_dir,
                "VcfArchitectureClient.java",
                "TestMain.java",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(f"Java compilation failed:\n{compile_result.stdout}{compile_result.stderr}")
        run_result = subprocess.run(
            ["java", "-cp", build_dir, "TestMain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if run_result.returncode != 0:
            fail(f"TestMain failed:\n{run_result.stdout}{run_result.stderr}")
        if len(run_result.stdout.encode("utf-8")) > 2_000_000:
            fail("artifact exceeds 2 MB")
        try:
            return json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"TestMain stdout is not one JSON artifact: {exc}")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def check_convergence_spec(spec: dict[str, Any], inventory: dict[str, Any]) -> None:
    existing = inventory["existingDeployment"]
    desired = inventory["desiredFleet"]
    components = {item["type"]: item for item in inventory["components"]}

    require_equal(spec.get("sddcId"), existing["sddcId"], "convergence sddcId")
    require_equal(spec.get("workflowType"), "VCF", "convergence workflowType")
    require_equal(spec.get("version"), desired["vcfVersion"], "convergence target version")
    require_equal(spec.get("vcfInstanceName"), existing["vcfInstanceName"], "VCF instance name")

    vc = spec.get("vcenterSpec", {})
    expected_vc = existing["vcenter"]
    require_equal(vc.get("vcenterHostname"), expected_vc["fqdn"], "existing vCenter FQDN")
    require_equal(vc.get("rootVcenterPassword"), expected_vc["designOnlyRootPassword"], "design-only vCenter password")
    require_equal(vc.get("version"), components["VCENTER"]["version"], "existing vCenter version")
    require_equal(vc.get("useExistingDeployment"), True, "vCenter brownfield flag")
    require_equal(vc.get("sslThumbprint"), expected_vc["sslThumbprint"], "vCenter thumbprint")

    cluster = spec.get("clusterSpec", {})
    require_equal(cluster.get("datacenterName"), expected_vc["datacenter"], "datacenter name")
    require_equal(cluster.get("clusterName"), expected_vc["cluster"], "cluster name")
    actual_hosts = [item.get("hostname") for item in spec.get("hostSpecs", [])]
    require_equal(actual_hosts, existing["hosts"], "existing host inventory")

    actual_networks = sorted(
        (item.get("networkType"), item.get("vlanId"), item.get("mtu"))
        for item in spec.get("networkSpecs", [])
    )
    expected_networks = sorted(
        (item["networkType"], item["vlanId"], item["mtu"])
        for item in existing["networks"]
    )
    require_equal(actual_networks, expected_networks, "network/VLAN/MTU architecture")

    dvs_specs = spec.get("dvsSpecs", [])
    if len(dvs_specs) != 1:
        fail("convergence spec must carry the one inventoried DVS")
    require_equal(dvs_specs[0].get("dvsName"), existing["dvs"]["name"], "DVS name")
    require_equal(dvs_specs[0].get("vmnicsToUplinks"), existing["dvs"]["vmnicUplinks"], "DVS uplink mapping")

    require_equal(spec.get("dnsSpec"), existing["dns"], "DNS architecture")
    require_equal(spec.get("ntpServers"), existing["ntpServers"], "NTP architecture")
    require_equal(
        spec.get("datastoreSpec", {}).get("existingDatastoreName"),
        existing["datastoreName"],
        "existing datastore",
    )

    nsx = spec.get("nsxtSpec", {})
    expected_nsx = existing["nsx"]
    require_equal(nsx.get("vipFqdn"), expected_nsx["vipFqdn"], "NSX VIP")
    require_equal(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])],
        expected_nsx["managerFqdns"],
        "NSX manager inventory",
    )
    require_equal(nsx.get("version"), components["NSX"]["version"], "existing NSX version")
    require_equal(nsx.get("useExistingDeployment"), True, "NSX brownfield flag")
    require_equal(nsx.get("enableEdgeClusterSync"), True, "NSX Edge synchronization")
    require_equal(nsx.get("sslThumbprint"), expected_nsx["sslThumbprint"], "NSX thumbprint")


def check_component_targets(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    targets = plan["componentTargets"]
    targets_by_id = {item["componentId"]: item for item in targets}
    if len(targets_by_id) != len(targets):
        fail("componentTargets contains duplicate componentId values")
    require_equal(set(targets_by_id), set(inventory_by_id), "component target coverage")

    for component_id, component in inventory_by_id.items():
        target = targets_by_id[component_id]
        rule = snapshot["componentRules"][component["type"]]
        require_equal(target["name"], component["name"], f"{component_id} name")
        require_equal(target["type"], component["type"], f"{component_id} type")
        require_equal(target["currentVersion"], component["version"], f"{component_id} current version")
        require_equal(target["targetProduct"], rule["targetProduct"], f"{component_id} target product")
        require_equal(target["targetVersion"], rule["targetVersion"], f"{component_id} target version")
        missing_gates = set(rule["targetGates"]) - set(target["gates"])
        if missing_gates:
            fail(f"{component_id} omits target gates {sorted(missing_gates)}")

    by_type = {item["type"]: item for item in targets}
    for combination in snapshot["supportedTargetCombinations"]:
        for component_type, version in combination["versions"].items():
            require_equal(
                by_type[component_type]["targetVersion"],
                version,
                f"supported combination {combination['id']} / {component_type}",
            )


def check_steps(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["steps"]
    sequences = [item["sequence"] for item in steps]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        fail("migration step sequence values must be strictly increasing")

    known_ids = {item["id"] for item in inventory["components"]}
    covered_ids: set[str] = set()
    step_by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step["stepId"] in step_by_id:
            fail(f"duplicate migration stepId {step['stepId']}")
        step_by_id[step["stepId"]] = step
        unknown = set(step["componentIds"]) - known_ids
        if unknown:
            fail(f"step {step['stepId']} names unknown components {sorted(unknown)}")
        covered_ids.update(step["componentIds"])
    require_equal(covered_ids, known_ids, "migration step component coverage")

    for milestone in snapshot["requiredMilestones"]:
        step_id = milestone["stepId"]
        if step_id not in step_by_id:
            fail(f"missing required migration milestone {step_id}")
        step = step_by_id[step_id]
        require_equal(step["action"], milestone["action"], f"{step_id} action")
        require_equal(step["componentIds"], milestone["componentIds"], f"{step_id} components")
        require_equal(step["fromVersion"], milestone["fromVersion"], f"{step_id} fromVersion")
        require_equal(step["toVersion"], milestone["toVersion"], f"{step_id} toVersion")
        missing_gates = set(milestone["requiredGates"]) - set(step["gates"])
        if missing_gates:
            fail(f"step {step_id} omits gates {sorted(missing_gates)}")
        for prerequisite in milestone["after"]:
            if prerequisite not in step_by_id:
                fail(f"step {step_id} depends on absent step {prerequisite}")
            if step_by_id[prerequisite]["sequence"] >= step["sequence"]:
                fail(f"step {step_id} occurs before prerequisite {prerequisite}")


def check_edge_design(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    desired = inventory["desiredFleet"]
    architecture = plan["targetArchitecture"]
    require_equal(architecture["fleetName"], desired["name"], "target fleet name")
    require_equal(architecture["targetVcfVersion"], desired["vcfVersion"], "target VCF version")
    require_equal(architecture["managementModel"], "VCF_FLEET", "management model")

    requirement = inventory["edgeRequirement"]
    edge = architecture["edgeDesign"]
    throughput = requirement["northSouthThroughputGbps"]
    require_equal(edge["requiredThroughputGbps"], throughput, "north-south throughput")
    fitting_sizes = [
        item for item in snapshot["edgeSizing"]
        if item["maxNorthSouthThroughputGbps"] >= throughput
    ]
    if not fitting_sizes:
        fail("pinned snapshot has no Edge form factor for required throughput")
    expected_size = min(fitting_sizes, key=lambda item: item["maxNorthSouthThroughputGbps"])
    require_equal(edge["edgeFormFactor"], expected_size["formFactor"], "throughput-derived Edge form factor")
    require_equal(edge["edgeNodeCount"], requirement["edgeNodeCount"], "Edge node count")
    require_equal(edge["uplinkPolicy"], snapshot["edgeUplinkRule"]["policy"], "Edge uplink policy")

    layout = edge["uplinkLayout"]
    nodes = sorted({item["edgeNode"] for item in layout})
    if len(nodes) != requirement["edgeNodeCount"]:
        fail("uplink layout does not describe every Edge node")
    for node in nodes:
        links = [item for item in layout if item["edgeNode"] == node]
        require_equal(len(links), requirement["uplinksPerEdgeNode"], f"{node} uplink count")
        require_equal(
            {item["fabricSwitch"] for item in links},
            set(requirement["fabricSwitches"]),
            f"{node} fabric diversity",
        )
        require_equal(
            {item["speedGbps"] for item in links},
            {requirement["uplinkSpeedGbps"]},
            f"{node} uplink speed",
        )
        require_equal(
            {item["mode"] for item in links},
            {snapshot["edgeUplinkRule"]["requiredMode"]},
            f"{node} active-active mode",
        )
        if len({item["edgeInterface"] for item in links}) != len(links):
            fail(f"{node} reuses an Edge interface")
        if len({item["uplinkName"] for item in links}) != len(links):
            fail(f"{node} reuses an uplink name")
        if sum(item["speedGbps"] for item in links) < throughput:
            fail(f"{node} uplink capacity is below required throughput")


def check_research_artifact(inventory: dict[str, Any]) -> None:
    path = ROOT / "research-consulted.md"
    try:
        research = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot load required research-consulted.md: {exc}")

    if not research.strip():
        fail("research-consulted.md is empty")
    date_pattern = (
        r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|"
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}\b|"
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
        r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2}\b"
    )
    if "access" not in research.lower() or not re.search(
        date_pattern, research, flags=re.IGNORECASE
    ):
        fail("research-consulted.md omits an access date")

    urls = re.findall(r"https://[^\s)>]+", research)
    unique_urls = set(urls)
    if not unique_urls:
        fail("research-consulted.md does not cite a live source page")
    for url in unique_urls:
        hostname = (urlsplit(url).hostname or "").lower()
        if hostname != "broadcom.com" and not hostname.endswith(".broadcom.com"):
            fail(f"research source is not Broadcom-published: {url}")

    lower_research = research.lower()
    normalized_research = re.sub(r"\s+", " ", lower_research)
    required_topics = {
        "compatibility": r"compatib|support(?:ed|s)? (?:target |version |product )?combination",
        "interoperability": r"interop|work(?:s|ing)? with|registered with",
        "sizing": r"sizing|throughput|form factor|gbps",
        "upgrade path": r"upgrade (?:path|sequence)|intermediate hop|required .* hop|\bfrom .{1,40} to\b",
    }
    for topic, pattern in required_topics.items():
        if re.search(pattern, lower_research) is None:
            fail(f"research-consulted.md omits a used {topic} fact")

    for component in inventory["components"]:
        if component["product"].lower() not in normalized_research:
            fail(f"research-consulted.md omits product coverage for {component['product']}")
        if component["version"].lower() not in normalized_research:
            fail(
                "research-consulted.md omits current-version coverage for "
                f"{component['id']} ({component['version']})"
            )


def verify() -> None:
    artifact = compile_and_run_client()

    # Mandatory first artifact check: resolve and validate SddcSpec directly
    # from the pinned VCF Installer OpenAPI document. No fixture, migration
    # schema, or compatibility snapshot is loaded before this succeeds.
    installer = load_json(
        ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
    )
    convergence_spec = artifact.get("convergenceSddcSpec") if isinstance(artifact, dict) else None
    try:
        validate_schema(
            convergence_spec,
            installer["components"]["schemas"]["SddcSpec"],
            installer,
            "$.convergenceSddcSpec",
        )
    except (KeyError, TypeError) as exc:
        fail(f"protected installer document is malformed: {exc}")

    # Only after the installer-schema gate succeeds may the verifier inspect
    # the migration schema, estate fixture, or pinned compatibility authority.
    plan_schema = load_json(ROOT / "schemas" / "migration-plan-schema.json")
    validate_schema(artifact, plan_schema, plan_schema)
    inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
    snapshot = load_json(ROOT / "snapshots" / "compatibility-snapshot.json")

    require_equal(artifact["fleetTarget"], inventory["desiredFleet"], "fleet target")
    check_convergence_spec(convergence_spec, inventory)
    check_component_targets(artifact, inventory, snapshot)
    check_steps(artifact, inventory, snapshot)
    check_edge_design(artifact, inventory, snapshot)
    check_research_artifact(inventory)


if __name__ == "__main__":
    try:
        verify()
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("all architecture checks passed")
