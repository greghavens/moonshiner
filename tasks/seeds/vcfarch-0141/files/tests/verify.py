#!/usr/bin/env python3
"""Protected, offline verifier for the VCF migration architecture."""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture" / "migration-plan.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate_inventory.json"
COMPATIBILITY = ROOT / "compatibility" / "compatibility-snapshot.json"
PACKAGE = ROOT / "vcf_architect"


class VerificationError(Exception):
    """An actionable artifact verification failure."""


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
        fail(f"required JSON file is missing: {display_path}")
    except OSError as error:
        fail(f"cannot read JSON {display_path}: {type(error).__name__}")
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {display_path}: {error}")


def _resolve_pointer(root: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    value = root
    for encoded in reference[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
    return value


def _type_matches(instance: Any, expected: str) -> bool:
    checks = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    return expected in checks and checks[expected](instance)


def validate_schema(instance: Any, schema: Any, root: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI subset used by the pinned specifications."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: value is forbidden by schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema node")

    if "$ref" in schema:
        validate_schema(instance, _resolve_pointer(root, schema["$ref"]), root, path)
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            validate_schema(instance, siblings, root, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, root, path)

    if "anyOf" in schema:
        if not _matches_any(instance, schema["anyOf"], root, path):
            fail(f"{path}: value does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(_matches(instance, item, root, path) for item in schema["oneOf"])
        if matches != 1:
            fail(f"{path}: value must match exactly one allowed schema (matched {matches})")

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_type_matches(instance, item) for item in expected_type):
            fail(f"{path}: expected one of types {expected_type}, got {type(instance).__name__}")
    elif expected_type and not _type_matches(instance, expected_type):
        fail(f"{path}: expected {expected_type}, got {type(instance).__name__}")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as error:
                fail(f"{path}: invalid schema regex {schema['pattern']!r}: {error}")
            if not matched:
                fail(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: {instance} is above maximum {schema['maximum']}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: array has fewer than minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: array has more than maxItems")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                fail(f"{path}: array items must be unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_schema(item, schema["items"], root, f"{path}[{index}]")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate_schema(value, properties[name], root, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    value,
                    schema["additionalProperties"],
                    root,
                    f"{path}.{name}",
                )


def _matches(instance: Any, schema: Any, root: Any, path: str) -> bool:
    try:
        validate_schema(instance, schema, root, path)
        return True
    except VerificationError:
        return False


def _matches_any(instance: Any, schemas: list[Any], root: Any, path: str) -> bool:
    return any(_matches(instance, schema, root, path) for schema in schemas)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def verify_target_spec_first(artifact: Any, openapi: Any) -> None:
    """The Installer-owned SddcSpec schema is deliberately the first validation."""
    target = artifact.get("target_sddc_spec") if isinstance(artifact, dict) else None
    validate_schema(
        target,
        {"$ref": "#/components/schemas/SddcSpec"},
        openapi,
        "$.target_sddc_spec",
    )


def index_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item[key]
        require(value not in values, f"duplicate {label} {value!r}")
        values[value] = item
    return values


def verify_topology(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(artifact["inventory_id"] == inventory["inventory_id"], "artifact targets the wrong inventory")
    require(
        artifact["compatibility_snapshot_id"] == snapshot["snapshot_id"],
        "artifact targets the wrong compatibility snapshot",
    )
    site = artifact["site"]
    topology = snapshot["topology"]
    require(site["id"] == inventory["site"]["id"], "site id does not match inventory")
    require(site["site_count"] == topology["site_count"] == 1, "target must be single-site")
    require(
        site["architecture_model"] == topology["architecture_model"] == "consolidated",
        "target must use the consolidated architecture model",
    )
    minimum = topology["minimum_management_hosts"]
    require(len(inventory["hosts"]) == minimum, "fixture is not at the pinned host minimum")
    require(site["host_count"] == minimum, "artifact must remain at exactly the four-host minimum")
    require(
        site["management_cluster"] == inventory["architecture"]["management_cluster"],
        "management cluster does not match inventory",
    )

    target = artifact["target_sddc_spec"]
    require(target.get("version") == snapshot["target_release"], "SddcSpec target release is wrong")
    require(target.get("workflowType") == "VCF", "SddcSpec workflowType must be VCF")
    expected_hosts = {host["hostname"] for host in inventory["hosts"]}
    actual_hosts = {host["hostname"] for host in target.get("hostSpecs", [])}
    require(len(target.get("hostSpecs", [])) == minimum, "SddcSpec must contain exactly four hosts")
    require(actual_hosts == expected_hosts, "SddcSpec hostnames do not match inventory")
    require(
        target.get("clusterSpec") == {
            "clusterName": inventory["architecture"]["management_cluster"],
            "datacenterName": inventory["architecture"]["datacenter"],
        },
        "SddcSpec cluster placement does not match the consolidated estate",
    )
    require(
        target.get("dnsSpec") == {
            "subdomain": inventory["site"]["dns_domain"],
            "nameservers": inventory["site"]["dns_servers"],
        },
        "SddcSpec DNS settings do not match inventory",
    )
    require(target.get("ntpServers") == inventory["site"]["ntp_servers"], "SddcSpec NTP settings differ")

    expected_networks = {
        item["type"]: {
            "vlanId": item["vlan_id"],
            "subnet": item["subnet"],
            "gateway": item["gateway"],
            "mtu": item["mtu"],
        }
        for item in inventory["networks"]
    }
    network_specs = target.get("networkSpecs", [])
    require(len(network_specs) == len(expected_networks), "SddcSpec has duplicate or extra networks")
    require(
        len({item.get("networkType") for item in network_specs}) == len(network_specs),
        "SddcSpec network types must be unique",
    )
    actual_networks = {
        item["networkType"]: {
            "vlanId": item.get("vlanId"),
            "subnet": item.get("subnet"),
            "gateway": item.get("gateway"),
            "mtu": item.get("mtu"),
        }
        for item in network_specs
    }
    require(actual_networks == expected_networks, "SddcSpec networks do not match inventory")

    inventory_components = {item["id"]: item for item in inventory["components"]}
    vcenter = target.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == inventory_components["vcenter"]["fqdn"], "vCenter FQDN differs")
    require(vcenter.get("version") == inventory_components["vcenter"]["version"], "vCenter source version differs")
    require(vcenter.get("useExistingDeployment") is True, "vCenter must be imported as existing")
    nsx = target.get("nsxtSpec", {})
    require(nsx.get("useExistingDeployment") is True, "NSX must be imported as existing")
    require(nsx.get("version") == inventory_components["nsx"]["version"], "NSX source version differs")
    require(nsx.get("vipFqdn") == inventory_components["nsx"]["vip_fqdn"], "NSX VIP differs")
    require(
        len(nsx.get("nsxtManagers", [])) == len(inventory_components["nsx"]["manager_fqdns"]),
        "SddcSpec must contain each NSX manager exactly once",
    )
    require(
        {item.get("hostname") for item in nsx.get("nsxtManagers", [])}
        == set(inventory_components["nsx"]["manager_fqdns"]),
        "NSX manager set differs",
    )
    sddc_manager = target.get("sddcManagerSpec", {})
    require(sddc_manager.get("version") == snapshot["target_release"], "SDDC Manager target version differs")
    require(sddc_manager.get("useExistingDeployment") is False, "SDDC Manager must be newly established")


def verify_components(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    actual = index_unique(artifact["components"], "inventory_id", "component")
    expected = {item["id"]: item for item in inventory["components"]}
    declared_gates = {item["id"] for item in artifact["gates"]}
    require(set(actual) == set(expected), "component plan must cover every inventory component exactly once")
    for component_id, source in expected.items():
        record = actual[component_id]
        rule = snapshot["component_rules"][component_id]
        require(record["name"] == source["name"], f"{component_id}: name differs from inventory")
        require(record["current_version"] == source["version"], f"{component_id}: current version differs")
        require(record["current_version"] == rule["current_version"], f"{component_id}: snapshot source differs")
        require(record["target_product"] == rule["target_product"], f"{component_id}: target product is unsupported")
        require(record["target_version"] == rule["target_version"], f"{component_id}: target version is unsupported")
        require(record["transition"] == rule["transition"], f"{component_id}: transition does not route pinned blocker")
        require(record["via_versions"] == rule["required_via_versions"], f"{component_id}: intermediate path differs")
        require(
            set(rule["required_gate_ids"]) <= set(record["gated_by"]),
            f"{component_id}: required compatibility gates are incomplete",
        )
        require(
            set(record["gated_by"]) <= declared_gates,
            f"{component_id}: compatibility gate is not declared",
        )


def verify_gates_and_steps(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = artifact["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous")
    by_id = index_unique(steps, "id", "step id")
    by_event = index_unique(steps, "event", "event")
    required_events = set(snapshot["required_events"])
    require(required_events <= set(by_event), "ordered plan is missing required migration events")

    known_components = {item["id"] for item in inventory["components"]}
    known_components |= {item["id"] for item in snapshot["target_services"]}
    declared_gate_ids = {gate["id"] for gate in artifact["gates"]}
    for step in steps:
        require(set(step["component_ids"]) <= known_components, f"{step['id']}: unknown component reference")
        require(set(step["gates_cleared"]) <= declared_gate_ids, f"{step['id']}: unknown gate reference")
        for dependency in step["depends_on"]:
            require(dependency in by_id, f"{step['id']}: unknown dependency {dependency}")
            require(by_id[dependency]["order"] < step["order"], f"{step['id']}: dependency is not earlier")

    positions = {event: step["order"] for event, step in by_event.items()}

    def depends_transitively(step_id: str, dependency_id: str) -> bool:
        pending = list(by_id[step_id]["depends_on"])
        visited: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate == dependency_id:
                return True
            if candidate not in visited:
                visited.add(candidate)
                pending.extend(by_id[candidate]["depends_on"])
        return False

    for constraint in snapshot["ordering_constraints"]:
        before = constraint["before"]
        after = constraint["after"]
        require(positions[before] < positions[after], f"ordering constraint violated: {before} before {after}")
        require(
            depends_transitively(by_event[after]["id"], by_event[before]["id"]),
            f"{after} must depend on {before}",
        )

    expected_associations = {
        "patch-lifecycle": {"aria-lifecycle"},
        "upgrade-operations": {"aria-operations"},
        "deploy-management-services": {"vcf-management-services", "vcf-identity-broker"},
        "installer-converge": {"vcf-installer", "sddc-manager", "nsx", "vcenter"},
        "upgrade-automation": {"aria-automation"},
        "deploy-logs-fresh": {"aria-logs"},
        "migrate-log-data": {"aria-logs"},
        "upgrade-nsx": {"nsx"},
        "upgrade-vcenter": {"vcenter"},
        "migrate-identity": {"identity-manager", "vcf-identity-broker"},
        "retire-legacy-services": {"aria-lifecycle", "identity-manager", "aria-logs"},
        "upgrade-esxi": {"esxi"},
    }
    for event, associated in expected_associations.items():
        require(
            associated <= set(by_event[event]["component_ids"]),
            f"{event}: required component associations are missing",
        )

    require(
        "8.18.0 Patch 2" in by_event["patch-lifecycle"]["to_versions"],
        "lifecycle patch event does not reach Patch 2",
    )
    require(
        snapshot["target_release"] in by_event["upgrade-operations"]["to_versions"],
        "VCF Operations event has the wrong target",
    )
    require(
        snapshot["target_release"] in by_event["deploy-logs-fresh"]["to_versions"],
        "fresh logging deployment has the wrong target",
    )

    component_rules = snapshot["component_rules"]
    version_paths = {
        "patch-lifecycle": (
            {component_rules["aria-lifecycle"]["current_version"]},
            set(component_rules["aria-lifecycle"]["required_via_versions"]),
        ),
        "upgrade-operations": (
            {component_rules["aria-operations"]["current_version"]},
            {component_rules["aria-operations"]["target_version"]},
        ),
        "upgrade-automation": (
            {component_rules["aria-automation"]["current_version"]},
            {component_rules["aria-automation"]["target_version"]},
        ),
        "migrate-log-data": (
            {component_rules["aria-logs"]["current_version"]},
            {component_rules["aria-logs"]["target_version"]},
        ),
        "upgrade-nsx": (
            {component_rules["nsx"]["current_version"]},
            {component_rules["nsx"]["target_version"]},
        ),
        "upgrade-vcenter": (
            {component_rules["vcenter"]["current_version"]},
            {component_rules["vcenter"]["target_version"]},
        ),
        "migrate-identity": (
            {component_rules["identity-manager"]["current_version"]},
            {snapshot["target_release"]},
        ),
        "upgrade-esxi": (
            {component_rules["esxi"]["current_version"]},
            {component_rules["esxi"]["target_version"]},
        ),
    }
    for event, (required_from, required_to) in version_paths.items():
        step = by_event[event]
        require(required_from <= set(step["from_versions"]), f"{event}: source version is missing")
        require(required_to <= set(step["to_versions"]), f"{event}: target or intermediate version is missing")
    require(
        "RETIRED" in by_event["retire-legacy-services"]["to_versions"],
        "legacy retirement event must reach RETIRED",
    )

    gates = index_unique(artifact["gates"], "id", "gate")
    required_gates = {item["id"]: item for item in snapshot["required_gates"]}
    require(set(required_gates) <= set(gates), "artifact must declare every pinned gate")
    for gate_id, gate in gates.items():
        require(gate["condition"].strip(), f"{gate_id}: condition is empty")
        require(set(gate["satisfied_by"]) <= set(by_id), f"{gate_id}: satisfaction references unknown steps")
        require(gate["satisfied_by"], f"{gate_id}: no satisfying step is named")
        clearing_steps = {
            step["id"] for step in steps if gate_id in step["gates_cleared"]
        }
        require(clearing_steps, f"{gate_id}: no step clears the gate")
        require(
            clearing_steps <= set(gate["satisfied_by"]),
            f"{gate_id}: clearing step is absent from satisfied_by",
        )


def verify_sources(artifact: dict[str, Any]) -> None:
    sources = artifact["consulted_sources"]
    require(len(sources) >= 2, "consulted_sources must cover both compatibility and upgrade research")
    urls: set[str] = set()
    coverage: list[str] = []
    broadcom_sources = 0
    for index, source in enumerate(sources):
        url = source["url"]
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme == "https", f"consulted_sources[{index}]: source must use HTTPS")
        require(
            bool(hostname) and hostname != "invalid" and not hostname.endswith(".invalid"),
            f"consulted_sources[{index}]: source hostname is not reachable",
        )
        if hostname == "broadcom.com" or hostname.endswith(".broadcom.com"):
            broadcom_sources += 1
        require(url not in urls, f"consulted_sources[{index}]: duplicate source URL")
        urls.add(url)
        try:
            dt.date.fromisoformat(source["consulted_on"])
        except ValueError:
            fail(f"consulted_sources[{index}]: consultation date is not a calendar date")
        coverage.append(f"{source['title']} {source['used_for']}".casefold())
    require(
        broadcom_sources >= 2,
        "consulted_sources must include Broadcom sources for compatibility and upgrade research",
    )
    combined = " ".join(coverage)
    require(
        any(word in combined for word in ("compatibility", "interoperability")),
        "consulted_sources do not identify compatibility or interoperability evidence",
    )
    require(
        any(word in combined for word in ("upgrade", "sequence", "transition", "path")),
        "consulted_sources do not identify upgrade-path evidence",
    )


def verify_standard_library_package() -> None:
    require(PACKAGE.is_dir(), "vcf_architect package is missing")
    modules = sorted(PACKAGE.rglob("*.py"))
    require(bool(modules), "vcf_architect package contains no Python modules")
    allowed = set(sys.stdlib_module_names) | {"__future__", PACKAGE.name}
    for path in modules:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            fail(f"cannot parse package module {path.relative_to(ROOT)}: {error}")
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            for root in roots:
                require(
                    root in allowed,
                    f"{path.relative_to(ROOT)} imports non-standard-library module {root!r}",
                )


def verify_generator(artifact_bytes: bytes) -> None:
    command_base = [
        sys.executable,
        "-m",
        "vcf_architect",
        "--inventory",
        str(INVENTORY.relative_to(ROOT)),
        "--compatibility",
        str(COMPATIBILITY.relative_to(ROOT)),
        "--output",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf-architecture-") as directory:
        temporary = Path(directory)
        first = temporary / "first.json"
        second = temporary / "second.json"
        for output in (first, second):
            result = subprocess.run(
                [*command_base, str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
            )
            require(
                result.returncode == 0,
                f"package generator failed: {(result.stdout + result.stderr).strip()[-500:]}",
            )
        require(first.read_bytes() == second.read_bytes(), "package output is not byte-deterministic")
        require(first.read_bytes() == artifact_bytes, "committed artifact is not the package's generated output")

        variant_inventory = load_json(INVENTORY)
        variant_snapshot = load_json(COMPATIBILITY)
        variant_inventory["inventory_id"] = "determinism-variant-estate"
        variant_inventory["site"]["dns_domain"] = "variant.example.com"
        variant_inventory["hosts"][0]["hostname"] = "variant-esx01"
        variant_inventory["networks"][0]["vlan_id"] = 111
        variant_snapshot["snapshot_id"] = "determinism-variant-snapshot"
        inventory_path = temporary / "variant-inventory.json"
        compatibility_path = temporary / "variant-compatibility.json"
        variant_output = temporary / "variant-output.json"
        inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        compatibility_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "vcf_architect",
                "--inventory",
                str(inventory_path),
                "--compatibility",
                str(compatibility_path),
                "--output",
                str(variant_output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )
        require(
            result.returncode == 0,
            f"package generator rejected explicit input paths: {(result.stdout + result.stderr).strip()[-500:]}",
        )
        variant = load_json(variant_output)
        require(variant["inventory_id"] == variant_inventory["inventory_id"], "generator ignores inventory input")
        require(
            variant["compatibility_snapshot_id"] == variant_snapshot["snapshot_id"],
            "generator ignores compatibility input",
        )
        target = variant["target_sddc_spec"]
        require(target["dnsSpec"]["subdomain"] == "variant.example.com", "generator hard-codes DNS input")
        require(target["hostSpecs"][0]["hostname"] == "variant-esx01", "generator hard-codes host input")
        require(target["networkSpecs"][0]["vlanId"] == 111, "generator hard-codes network input")


def main() -> int:
    try:
        artifact = load_json(ARTIFACT)
        artifact_bytes = ARTIFACT.read_bytes()
        openapi = load_json(OPENAPI)

        # Required ordering: the Installer specification owns the first artifact check.
        verify_target_spec_first(artifact, openapi)

        plan_schema = load_json(PLAN_SCHEMA)
        validate_schema(artifact, plan_schema, plan_schema)
        inventory = load_json(INVENTORY)
        snapshot = load_json(COMPATIBILITY)
        verify_topology(artifact, inventory, snapshot)
        verify_components(artifact, inventory, snapshot)
        verify_gates_and_steps(artifact, inventory, snapshot)
        verify_sources(artifact)
        verify_standard_library_package()
        verify_generator(artifact_bytes)
    except (VerificationError, FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Installer SddcSpec, migration schema, pinned compatibility, and generator are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
