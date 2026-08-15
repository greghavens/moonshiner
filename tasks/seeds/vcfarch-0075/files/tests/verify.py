#!/usr/bin/env python3
"""Deterministic offline acceptance verifier for vcfarch-0075."""

from __future__ import annotations

import ast
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "migration-plan.json"
ESTATE_PATH = ROOT / "fixtures" / "estate.json"
SNAPSHOT_PATH = ROOT / "compatibility" / "compatibility_snapshot.json"
PLAN_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
INSTALLER_SPEC_PATH = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)
INSTALLER_LICENSE_PATH = (
    ROOT
    / "specifications"
    / "vcf-installer"
    / "LICENSE.vmware-vcf-api-specs.txt"
)

PROTECTED_SHA256 = {
    "fixtures/estate.json": "770a87c64746a61a22a5934ef03484da40ac6e3f8d4d049b546e2577e0a748a7",
    "compatibility/compatibility_snapshot.json": "26491027a36a33ed30ee9d8f93876c5435e60d3aafe394d62787c43d30935196",
    "schemas/migration-plan.schema.json": "56cec0c2cdc7c3fe06d6e144a2f6ecc3e36be2e370e11640dee705a39f940982",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/vcf-installer/LICENSE.vmware-vcf-api-specs.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    node = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
    return node


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
    return True


def schema_errors(
    value: Any,
    schema: Any,
    root_schema: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI subset used by the pinned documents."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: schema rejects every value"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema node"]

    if "$ref" in schema:
        return schema_errors(
            value, json_pointer(root_schema, schema["$ref"]), root_schema, path
        )
    if value is None and schema.get("nullable") is True:
        return []

    errors: list[str] = []
    for child in schema.get("allOf", []):
        errors.extend(schema_errors(value, child, root_schema, path))
    if "anyOf" in schema:
        choices = [schema_errors(value, child, root_schema, path) for child in schema["anyOf"]]
        if not any(not choice for choice in choices):
            errors.append(f"{path}: does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(
            not schema_errors(value, child, root_schema, path)
            for child in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed schema")
    if "not" in schema and not schema_errors(value, schema["not"], root_schema, path):
        errors.append(f"{path}: matches a forbidden schema")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        matches_type = any(type_matches(value, item) for item in expected_type)
    elif isinstance(expected_type, str):
        matches_type = type_matches(value, expected_type)
    else:
        matches_type = True
    if not matches_type:
        return errors + [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in the enum")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: string does not match pattern")
            except re.error as exc:
                fail(f"invalid regex in pinned schema at {path}: {exc}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: number is above maximum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                errors.extend(
                    schema_errors(item, item_schema, root_schema, f"{path}[{index}]")
                )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required property {required!r}")
        for name, child_schema in properties.items():
            if name in value:
                errors.extend(
                    schema_errors(
                        value[name], child_schema, root_schema, f"{path}.{name}"
                    )
                )
        additional = schema.get("additionalProperties", True)
        if additional is False or isinstance(additional, dict):
            for name, child_value in value.items():
                if name in properties:
                    continue
                if additional is False:
                    errors.append(f"{path}: unexpected property {name!r}")
                else:
                    errors.extend(
                        schema_errors(
                            child_value, additional, root_schema, f"{path}.{name}"
                        )
                    )
    return errors


def check_hashes() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file was modified: {relative}")


def unique_map(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_key = item[key]
        if item_key in result:
            fail(f"duplicate {label}: {item_key}")
        result[item_key] = item
    return result


def component_step_order(
    plan: dict[str, Any], steps: dict[str, dict[str, Any]]
) -> int:
    return min(steps[step_id]["order"] for step_id in plan["stepIds"])


def verify_semantics(
    artifact: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    if artifact["estateId"] != estate["estateId"]:
        fail("artifact estateId does not match the fixture")
    if artifact["targetVersion"] != estate["targetVersion"]:
        fail("artifact targetVersion does not match the fixture")

    release_path = artifact["releasePath"]
    if release_path[0] != estate["vcfVersion"]:
        fail("releasePath must start at the inventoried VCF release")
    if release_path[-1] != estate["targetVersion"]:
        fail("releasePath must end at the requested VCF release")
    supported_edges = {tuple(edge) for edge in snapshot["supportedReleaseEdges"]}
    for source, target in zip(release_path, release_path[1:]):
        if (source, target) not in supported_edges:
            fail(f"unsupported release hop in artifact: {source} -> {target}")

    fixture_components = unique_map(estate["components"], "id", "fixture component")
    component_plans = unique_map(
        artifact["componentPlans"], "componentId", "component plan"
    )
    if set(component_plans) != set(fixture_components):
        missing = sorted(set(fixture_components) - set(component_plans))
        extra = sorted(set(component_plans) - set(fixture_components))
        fail(f"component plan coverage differs from inventory; missing={missing}, extra={extra}")

    gate_catalog = snapshot["gateCatalog"]
    for component_id, fixture_component in fixture_components.items():
        plan = component_plans[component_id]
        if plan["componentType"] != fixture_component["type"]:
            fail(f"component type mismatch for {component_id}")
        if plan["currentVersion"] != fixture_component["currentVersion"]:
            fail(f"current version mismatch for {component_id}")
        expected_target = snapshot["componentTargets"][fixture_component["type"]]
        if plan["targetVersion"] != expected_target:
            fail(f"target version mismatch for {component_id}")
        supported_component_edges = {
            tuple(edge)
            for edge in snapshot["supportedComponentEdges"][fixture_component["type"]]
        }
        if (plan["currentVersion"], plan["targetVersion"]) not in supported_component_edges:
            fail(f"unsupported component hop for {component_id}")
        unknown_gates = set(plan["gateIds"]) - set(gate_catalog)
        if unknown_gates:
            fail(f"unknown gates on {component_id}: {sorted(unknown_gates)}")
        required_gates = set(
            snapshot["requiredGatesByComponentType"][fixture_component["type"]]
        )
        if not required_gates.issubset(plan["gateIds"]):
            fail(f"required technical gates are missing for {component_id}")

    introduced = unique_map(
        artifact["introducedComponents"], "componentId", "introduced component"
    )
    if set(introduced) != set(snapshot["introducedComponents"]):
        fail("introduced target components do not match the pinned snapshot")
    for component_id, expected in snapshot["introducedComponents"].items():
        actual = introduced[component_id]
        for artifact_key, snapshot_key in (
            ("componentType", "type"),
            ("targetVersion", "targetVersion"),
            ("siteId", "siteId"),
        ):
            if actual[artifact_key] != expected[snapshot_key]:
                fail(f"introduced component mismatch for {component_id}.{artifact_key}")
        if set(actual["gateIds"]) - set(gate_catalog):
            fail(f"unknown gate on introduced component {component_id}")
        if not set(expected["requiredGateIds"]).issubset(actual["gateIds"]):
            fail(f"required technical gates are missing for {component_id}")

    research_urls: set[str] = set()
    for source in artifact["researchConsulted"]:
        url = source["url"]
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        official_host = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or hostname == "vmware.github.io"
        )
        if parsed.scheme != "https" or not official_host:
            fail(f"research source is not an official Broadcom/VMware HTTPS page: {url}")
        if url in research_urls:
            fail(f"duplicate research source URL: {url}")
        research_urls.add(url)
        try:
            datetime.date.fromisoformat(source["accessedAt"])
        except ValueError:
            fail(f"research source has an invalid UTC access date: {url}")

    research_hosts = {urlsplit(url).hostname for url in research_urls}
    if "interopmatrix.broadcom.com" not in research_hosts:
        fail("researchConsulted must include the Broadcom Product Interoperability Matrix")
    if "compatibilityguide.broadcom.com" not in research_hosts:
        fail("researchConsulted must include the Broadcom Compatibility Guide")
    research_titles = "\n".join(
        source["title"].casefold() for source in artifact["researchConsulted"]
    )
    if not any(
        phrase in research_titles
        for phrase in ("bill of materials", "constituent product", " bom")
    ):
        fail("researchConsulted must include a VCF bill-of-materials source")
    if "upgrade" not in research_titles and "upgrading" not in research_titles:
        fail("researchConsulted must include official VCF upgrade guidance")

    step_map = unique_map(artifact["steps"], "stepId", "step")
    step_orders = [step["order"] for step in artifact["steps"]]
    if step_orders != sorted(step_orders) or len(step_orders) != len(set(step_orders)):
        fail("steps must be listed in strictly increasing, unique order")

    known_components = set(fixture_components) | set(introduced)
    available_gates = set(snapshot["initialGateIds"])
    for step in artifact["steps"]:
        if not set(step["componentIds"]).issubset(known_components):
            fail(f"step {step['stepId']} names an unknown component")
        unknown = (set(step["gateIds"]) | set(step["producesGateIds"])) - set(
            gate_catalog
        )
        if unknown:
            fail(f"step {step['stepId']} names unknown gates: {sorted(unknown)}")
        unavailable = set(step["gateIds"]) - available_gates
        if unavailable:
            fail(
                f"step {step['stepId']} consumes gates before they are produced: "
                f"{sorted(unavailable)}"
            )
        available_gates.update(step["producesGateIds"])

    for plan in list(component_plans.values()) + list(introduced.values()):
        for step_id in plan["stepIds"]:
            if step_id not in step_map:
                fail(f"{plan['componentId']} references missing step {step_id}")
            if plan["componentId"] not in step_map[step_id]["componentIds"]:
                fail(f"step {step_id} does not name its component {plan['componentId']}")

    by_type: dict[str, list[int]] = {}
    for plan in component_plans.values():
        by_type.setdefault(plan["componentType"], []).append(
            component_step_order(plan, step_map)
        )
    for plan in introduced.values():
        by_type.setdefault(plan["componentType"], []).append(
            component_step_order(plan, step_map)
        )
    ordered_types = snapshot["orderedComponentTypes"]
    for before_type, after_type in zip(ordered_types, ordered_types[1:]):
        if max(by_type[before_type]) >= min(by_type[after_type]):
            fail(f"{before_type} must complete before {after_type} starts")

    required_completion_gates = {
        "VCF_OPERATIONS": "operations-9.1-ready",
        "SDDC_MANAGER": "sddc-manager-9.1-ready",
        "VCF_MANAGEMENT_SERVICES": "management-services-9.1-ready",
        "VCF_LICENSE_SERVER": "license-server-9.1-ready",
        "NSX_MANAGER_CLUSTER": "nsx-management-9.1-ready",
        "VCENTER_SERVER": "vcenter-9.1-ready",
        "VSAN_WITNESS": "witness-9.1-ready",
        "ESXI_HOST": "data-hosts-9.1-ready",
        "NSX_EDGE": "nsx-edges-9.1-finalized",
    }
    for component_type, completion_gate in required_completion_gates.items():
        producer_orders = [
            step["order"]
            for step in artifact["steps"]
            if completion_gate in step["producesGateIds"]
        ]
        if len(producer_orders) != 1:
            fail(f"completion gate {completion_gate} must have exactly one producer")
        if producer_orders[0] < max(by_type[component_type]):
            fail(f"completion gate {completion_gate} is produced too early")

    architecture = artifact["targetArchitecture"]
    management = estate["managementDomain"]
    if architecture["managementDomainId"] != management["id"]:
        fail("target architecture names the wrong management domain")
    if architecture["clusterId"] != management["clusterId"]:
        fail("target architecture names the wrong management cluster")
    if architecture["topology"] != "STRETCHED":
        fail("management topology must remain stretched")

    data_sites = unique_map(architecture["dataSites"], "siteId", "data site")
    if set(data_sites) != set(management["dataSiteIds"]):
        fail("target data sites differ from the inventoried stretched sites")
    expected_fault_domains = {
        management["dataSiteIds"][0]: "PREFERRED",
        management["dataSiteIds"][1]: "SECONDARY",
    }
    all_architecture_hosts: set[str] = set()
    for site_id, site in data_sites.items():
        expected_hosts = {
            component["id"]
            for component in estate["components"]
            if component["type"] == "ESXI_HOST" and component["siteId"] == site_id
        }
        if site["faultDomain"] != expected_fault_domains[site_id]:
            fail(f"incorrect fault-domain role for {site_id}")
        if set(site["hostComponentIds"]) != expected_hosts:
            fail(f"incorrect host membership for {site_id}")
        all_architecture_hosts.update(site["hostComponentIds"])
    if len(all_architecture_hosts) != 8:
        fail("the stretched management cluster must contain eight distinct data hosts")

    witness_fixture = next(
        component
        for component in estate["components"]
        if component["type"] == "VSAN_WITNESS"
    )
    witness = architecture["witness"]
    if witness["componentId"] != witness_fixture["id"]:
        fail("target architecture names the wrong witness")
    if witness["siteId"] != management["witnessSiteId"]:
        fail("witness must remain in the inventoried third site")
    if witness["siteId"] in data_sites:
        fail("witness site must be outside both data sites")
    if witness["failureDomain"] != snapshot["topology"]["witnessFailureDomain"]:
        fail("witness must occupy an independent third failure domain")
    if witness["clusterMembership"] != snapshot["topology"]["witnessClusterMembership"]:
        fail("witness must be standalone, outside the stretched cluster")
    if witness["servesClusterId"] != management["clusterId"]:
        fail("witness is assigned to the wrong stretched cluster")

    installer = artifact["targetInstallerSpec"]
    if installer.get("sddcId") != management["id"]:
        fail("targetInstallerSpec.sddcId does not match the management domain")
    if installer.get("workflowType") != "VCF_COMPLETE":
        fail("targetInstallerSpec.workflowType must describe brownfield completion")
    if installer.get("version") != estate["targetVersion"]:
        fail("targetInstallerSpec.version does not match the requested target")
    license_server = snapshot["introducedComponents"]["vcf-license-server-01"]
    license_spec = installer.get("licenseServerSpec")
    if not isinstance(license_spec, dict):
        fail("targetInstallerSpec must deploy the mandatory VCF 9.1 License Server")
    if license_spec.get("hostname") != license_server["hostname"]:
        fail("targetInstallerSpec names the wrong VCF License Server")
    if license_spec.get("version") != license_server["targetVersion"]:
        fail("targetInstallerSpec uses the wrong VCF License Server version")
    if license_spec.get("useExistingDeployment") is not False:
        fail("targetInstallerSpec must deploy the introduced VCF License Server")
    expected_hostnames = {
        component["hostname"]
        for component in estate["components"]
        if component["type"] == "ESXI_HOST"
    }
    actual_hostnames = {
        host.get("hostname") for host in installer.get("hostSpecs", [])
    }
    if actual_hostnames != expected_hostnames:
        fail("targetInstallerSpec hostSpecs do not cover the eight management hosts")
    vcenter = next(
        component
        for component in estate["components"]
        if component["type"] == "VCENTER_SERVER"
    )
    vcenter_spec = installer["vcenterSpec"]
    if vcenter_spec.get("vcenterHostname") != vcenter["fqdn"]:
        fail("targetInstallerSpec names the wrong management vCenter")
    if vcenter_spec.get("rootVcenterPassword") != "REPLACE-AT-RUNTIME1!":
        fail("targetInstallerSpec must contain only the required non-secret placeholder")
    if installer["dnsSpec"] != estate["dns"]:
        fail("targetInstallerSpec DNS does not match the fixture")
    expected_networks = {
        (
            network["networkType"],
            network["subnet"],
            network["gateway"],
            network["vlanId"],
            network["mtu"],
        )
        for network in estate["networks"]
    }
    actual_networks = {
        (
            network.get("networkType"),
            network.get("subnet"),
            network.get("gateway"),
            network.get("vlanId"),
            network.get("mtu"),
        )
        for network in installer["networkSpecs"]
    }
    if actual_networks != expected_networks:
        fail("targetInstallerSpec networks do not match the estate fixture")


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_architecture"
    if not package.is_dir():
        fail("missing vcf_architecture package")
    if not (package / "__init__.py").is_file() or not (package / "__main__.py").is_file():
        fail("vcf_architecture must be an importable CLI package")
    allowed_roots = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for path in package.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"invalid Python syntax in {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".", 1)[0]]
            else:
                continue
            unsupported = set(imported) - allowed_roots
            if unsupported:
                fail(
                    f"non-stdlib import in {path.relative_to(ROOT)}: "
                    f"{sorted(unsupported)}"
                )


def verify_cli_reproduction(artifact: Any, architecture_keys: set[str]) -> None:
    with tempfile.TemporaryDirectory(prefix=".vcfarch-verify-", dir=ROOT) as temp_dir:
        generated = Path(temp_dir) / "migration-plan.json"
        command = [
            sys.executable,
            "-S",
            "-m",
            "vcf_architecture",
            "--estate",
            "fixtures/estate.json",
            "--snapshot",
            "compatibility/compatibility_snapshot.json",
            "--output",
            str(generated),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            fail(
                "vcf_architecture CLI failed under python -S:\n"
                + completed.stdout
                + completed.stderr
            )
        generated_artifact = load_json(generated)
        expected_architecture = {key: artifact[key] for key in architecture_keys}
        generated_architecture = {
            key: generated_artifact.get(key) for key in architecture_keys
        }
        if generated_architecture != expected_architecture:
            fail("CLI output does not reproduce migration-plan.json")


def main() -> int:
    try:
        artifact = load_json(ARTIFACT)
        installer_openapi = load_json(INSTALLER_SPEC_PATH)

        # This is deliberately the first validation gate: the architecture's
        # installer payload is checked against Broadcom's own pinned SddcSpec
        # before fixture hashes, the task schema, or any semantic assertions.
        installer_value = (
            artifact.get("targetInstallerSpec")
            if isinstance(artifact, dict)
            else None
        )
        installer_errors = schema_errors(
            installer_value,
            {"$ref": "#/components/schemas/SddcSpec"},
            installer_openapi,
            "$.targetInstallerSpec",
        )
        if installer_errors:
            fail(
                "targetInstallerSpec does not validate against the pinned "
                "VCF Installer SddcSpec:\n- "
                + "\n- ".join(installer_errors)
            )

        check_hashes()
        estate = load_json(ESTATE_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        plan_schema = load_json(PLAN_SCHEMA_PATH)
        plan_errors = schema_errors(artifact, plan_schema, plan_schema)
        if plan_errors:
            fail("migration-plan schema errors:\n- " + "\n- ".join(plan_errors))
        verify_semantics(artifact, estate, snapshot)
        verify_stdlib_package()
        verify_cli_reproduction(artifact, set(plan_schema["properties"]))
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 brownfield migration architecture verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
