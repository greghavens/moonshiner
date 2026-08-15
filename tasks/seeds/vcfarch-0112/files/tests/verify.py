#!/usr/bin/env python3
"""Offline, protected verification for the brownfield VCF architecture."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture.json"
INSTALLER_SPEC = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)
ARCHITECTURE_SCHEMA = ROOT / "schemas" / "workload-domain-architecture.schema.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT = ROOT / "compatibility" / "compatibility-snapshot.json"
RESEARCH = ROOT / "research.json"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc}"
        ) from exc


class SchemaValidator:
    """Small stdlib JSON Schema validator for the keywords used by the pinned files."""

    def __init__(self, document: Any):
        self.document = document

    def resolve(self, reference: str) -> Any:
        if not reference.startswith("#/"):
            raise VerificationError(f"external schema reference is not supported: {reference}")
        node = self.document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            try:
                node = node[token]
            except (KeyError, TypeError) as exc:
                raise VerificationError(f"unresolvable schema reference: {reference}") from exc
        return node

    @staticmethod
    def is_type(value: Any, expected: str) -> bool:
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

    def validate(self, value: Any, schema: Any, path: str = "$") -> list[str]:
        if schema is True:
            return []
        if schema is False:
            return [f"{path}: value is forbidden by schema"]
        if not isinstance(schema, dict):
            return [f"{path}: invalid schema node"]
        if "$ref" in schema:
            return self.validate(value, self.resolve(schema["$ref"]), path)

        errors: list[str] = []
        if value is None and schema.get("nullable") is True:
            return errors

        for subschema in schema.get("allOf", []):
            errors.extend(self.validate(value, subschema, path))
        if "anyOf" in schema:
            matches = [not self.validate(value, item, path) for item in schema["anyOf"]]
            if not any(matches):
                errors.append(f"{path}: does not match anyOf")
        if "oneOf" in schema:
            matches = sum(not self.validate(value, item, path) for item in schema["oneOf"])
            if matches != 1:
                errors.append(f"{path}: must match exactly one oneOf branch")

        expected = schema.get("type")
        if expected:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(self.is_type(value, item) for item in choices):
                return errors + [f"{path}: expected type {expected}, got {type(value).__name__}"]

        if "const" in schema and value != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} is not in enum")

        if isinstance(value, dict):
            required = schema.get("required", [])
            for name in required:
                if name not in value:
                    errors.append(f"{path}: missing required property {name!r}")
            properties = schema.get("properties", {})
            for name, child in value.items():
                if name in properties:
                    errors.extend(self.validate(child, properties[name], f"{path}.{name}"))
                elif schema.get("additionalProperties") is False:
                    errors.append(f"{path}: unexpected property {name!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    errors.extend(
                        self.validate(child, schema["additionalProperties"], f"{path}.{name}")
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
                if len(encoded) != len(set(encoded)):
                    errors.append(f"{path}: items must be unique")
            item_schema = schema.get("items")
            if item_schema is not None:
                for index, item in enumerate(value):
                    errors.extend(self.validate(item, item_schema, f"{path}[{index}]"))

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                errors.append(f"{path}: string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{path}: string is too long")
            if "pattern" in schema:
                try:
                    matched = re.search(schema["pattern"], value)
                except re.error as exc:
                    raise VerificationError(f"invalid pinned schema pattern at {path}: {exc}") from exc
                if not matched:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: above maximum")

        return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_with(document: Any, value: Any, schema: Any, label: str) -> None:
    errors = SchemaValidator(document).validate(value, schema)
    if errors:
        shown = "; ".join(errors[:8])
        raise VerificationError(f"{label} validation failed: {shown}")


def path_for(component_id: str, transitions: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected = sorted(
        (item for item in transitions if item["componentId"] == component_id),
        key=lambda item: item["phase"],
    )
    require(bool(selected), f"snapshot has no transition for {component_id}")
    result = [selected[0]["from"]]
    for item in selected:
        require(
            result[-1] == item["from"],
            f"snapshot transition chain is discontinuous for {component_id}",
        )
        result.append(item["to"])
    return result


def unique_in_catalog(values: list[str], catalog: dict[str, str]) -> list[str]:
    wanted = set(values)
    return [gate_id for gate_id in catalog if gate_id in wanted]


def semantic_checks(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(artifact["estateId"] == inventory["estateId"], "estateId does not match fixture")

    target_version = snapshot["targetStack"]["vcfVersion"]
    spec = artifact["targetSddcSpec"]
    inputs = inventory["sddcSpecInputs"]
    workload = inventory["workloadDomain"]
    workload_by_id = {item["id"]: item for item in workload["components"]}

    require(spec.get("sddcId") == inputs["sddcId"], "targetSddcSpec.sddcId is wrong")
    require(
        spec.get("workflowType") == inputs["workflowType"] == "VCF_EXTEND",
        "workflowType must match the VCF_EXTEND fixture",
    )
    require(spec.get("version") == target_version, "targetSddcSpec version is wrong")
    require(
        spec.get("vcfInstanceName") == inputs["vcfInstanceName"],
        "targetSddcSpec instance name is wrong",
    )
    vcenter = workload_by_id["wld-vcenter-01"]
    vcenter_spec = spec.get("vcenterSpec", {})
    require(vcenter_spec.get("vcenterHostname") == vcenter["fqdn"], "vCenter FQDN is wrong")
    require(vcenter_spec.get("version") == target_version, "vCenter target is wrong")
    require(vcenter_spec.get("useExistingDeployment") is True, "vCenter must be imported")

    nsx = workload_by_id["wld-nsx-01"]
    nsx_spec = spec.get("nsxtSpec", {})
    require(nsx_spec.get("vipFqdn") == nsx["vipFqdn"], "NSX VIP is wrong")
    require(nsx_spec.get("version") == target_version, "NSX target is wrong")
    require(nsx_spec.get("useExistingDeployment") is True, "NSX must be imported")
    require(
        [item.get("hostname") for item in nsx_spec.get("nsxtManagers", [])]
        == nsx["managerFqdns"],
        "NSX manager inventory is wrong",
    )
    esxi = workload_by_id["wld-esxi-cluster-01"]
    require(
        [item.get("hostname") for item in spec.get("hostSpecs", [])] == esxi["members"],
        "SddcSpec must name every workload host",
    )
    require(spec.get("dnsSpec") == inputs["dns"], "DNS design differs from fixture")
    require(spec.get("ntpServers") == inputs["ntpServers"], "NTP design differs from fixture")
    require(spec.get("networkSpecs") == inputs["networks"], "network design differs from fixture")
    require(
        spec.get("clusterSpec")
        == {
            "datacenterName": inputs["datacenterName"],
            "clusterName": inputs["clusterName"],
        },
        "cluster design differs from fixture",
    )
    require(
        spec.get("managementPoolName") == inputs["managementPoolName"],
        "management network pool differs from fixture",
    )
    require(
        spec.get("datastoreSpec", {}).get("existingDatastoreName")
        == workload_by_id["wld-vsan-01"]["datastore"],
        "existing vSAN datastore is not reused",
    )

    management_fixture = inventory["fleet"]["managementDomain"]
    management = artifact["managementDomain"]
    require(management["id"] == management_fixture["id"], "management domain id is wrong")
    expected_management = {
        item["id"]: {
            "id": item["id"],
            "product": item["product"],
            "currentVersion": item["version"],
            "targetVersion": item["version"],
            "action": "preserve",
        }
        for item in management_fixture["components"]
    }
    require(
        len(management["components"]) == len(expected_management),
        "management components must be unique and complete",
    )
    actual_management = {item["id"]: item for item in management["components"]}
    require(actual_management == expected_management, "management components must remain unchanged")

    transitions = snapshot["transitions"]
    expected_components: dict[str, dict[str, Any]] = {}
    catalog = snapshot["gateCatalog"]
    for source in workload["components"]:
        component_transitions = [
            item for item in transitions if item["componentId"] == source["id"]
        ]
        path = path_for(source["id"], transitions)
        gates = unique_in_catalog(
            [gate for item in component_transitions for gate in item["requiredGates"]],
            catalog,
        )
        expected_components[source["id"]] = {
            "id": source["id"],
            "product": source["product"],
            "currentVersion": source["version"],
            "target": path[-1],
            "upgradePath": path,
            "gates": gates,
        }
    require(
        len(artifact["components"]) == len(expected_components),
        "workload components must be unique and complete",
    )
    actual_components = {item["id"]: item for item in artifact["components"]}
    require(actual_components == expected_components, "component targets, paths, or gates differ from snapshot")

    require(
        len(artifact["gates"]) == len(catalog),
        "gate catalog must contain each pinned gate exactly once",
    )
    actual_gates = {item["id"]: item["description"] for item in artifact["gates"]}
    require(actual_gates == catalog, "gate catalog differs from pinned snapshot")

    research = artifact.get("researchConsulted")
    require(
        isinstance(research, list) and bool(research),
        "researchConsulted must record at least one consulted source",
    )

    steps = artifact["steps"]
    require(
        [item["order"] for item in steps] == list(range(1, len(steps) + 1)),
        "step order must be contiguous and start at 1",
    )
    require(len({item["stepId"] for item in steps}) == len(steps), "stepId values must be unique")
    step_by_order = {item["order"]: item for item in steps}
    transition_locations: dict[str, int] = {}
    transition_actual: dict[str, dict[str, Any]] = {}
    expected_phases = sorted({item["phase"] for item in transitions})
    for phase in expected_phases:
        expected = [item for item in transitions if item["phase"] == phase]
        require(phase in step_by_order, f"missing transition phase {phase}")
        step = step_by_order[phase]
        require(
            {item["operation"] for item in expected} == {step["operation"]},
            f"operation is wrong in phase {phase}",
        )
        require(
            step["componentIds"] == list(dict.fromkeys(item["componentId"] for item in expected)),
            f"componentIds are wrong in phase {phase}",
        )
        required_gates = unique_in_catalog(
            [gate for item in expected for gate in item["requiredGates"]], catalog
        )
        require(step["gates"] == required_gates, f"gates are wrong in phase {phase}")
        require(
            len(step["transitions"]) == len(expected),
            f"transition count is wrong in phase {phase}",
        )
        for item in step["transitions"]:
            transition_id = item["id"]
            require(transition_id not in transition_actual, f"duplicate transition {transition_id}")
            transition_locations[transition_id] = phase
            transition_actual[transition_id] = item

    expected_transition_map = {
        item["id"]: {
            "id": item["id"],
            "componentId": item["componentId"],
            "from": item["from"],
            "to": item["to"],
        }
        for item in transitions
    }
    require(transition_actual == expected_transition_map, "ordered transitions differ from snapshot")
    for relation in snapshot["ordering"]:
        require(
            transition_locations[relation["before"]] < transition_locations[relation["after"]],
            f"ordering constraint failed: {relation['before']} before {relation['after']}",
        )

    fleet_operation = snapshot["fleetOperation"]
    require(len(steps) == fleet_operation["phase"], "unexpected extra or missing steps")
    final_step = step_by_order[fleet_operation["phase"]]
    require(final_step["operation"] == fleet_operation["operation"], "final operation is wrong")
    require(final_step["componentIds"] == fleet_operation["componentIds"], "fleet import scope is wrong")
    require(final_step["transitions"] == [], "fleet import must not invent a version transition")
    require(final_step["gates"] == fleet_operation["requiredGates"], "fleet import gates are wrong")

    management_ids = set(expected_management)
    referenced = {component_id for step in steps for component_id in step["componentIds"]}
    require(not referenced.intersection(management_ids), "migration steps disturb management domain")


def graded_projection(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schemaVersion",
        "estateId",
        "targetSddcSpec",
        "managementDomain",
        "components",
        "gates",
        "steps",
    )
    return {key: value.get(key) for key in keys}


def package_checks(artifact: dict[str, Any]) -> None:
    source_root = ROOT / "src"
    package_root = source_root / "vcf_arch"
    require((package_root / "__init__.py").is_file(), "vcf_arch package is missing")
    require((package_root / "__main__.py").is_file(), "vcf_arch CLI is missing")
    pyproject_path = ROOT / "pyproject.toml"
    if pyproject_path.is_file():
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project", {})
        require(not project.get("dependencies"), "runtime dependencies are not allowed")

    own_modules = {"vcf_arch"}
    for path in package_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"syntax error in {path.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".")[0]]
            for name in imported:
                require(
                    name in sys.stdlib_module_names or name in own_modules,
                    f"non-stdlib import {name!r} in {path.relative_to(ROOT)}",
                )

    with tempfile.TemporaryDirectory(prefix="vcf-arch-verify-") as directory:
        temporary_root = Path(directory)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        architecture_schema = load_json(ARCHITECTURE_SCHEMA)

        def run_cli(name: str, research_path: Path | None = None) -> dict[str, Any]:
            generated_path = temporary_root / name
            command = [
                sys.executable,
                "-m",
                "vcf_arch",
                "--inventory",
                str(INVENTORY),
                "--compatibility",
                str(SNAPSHOT),
                "--output",
                str(generated_path),
            ]
            if research_path is not None:
                command.extend(["--research", str(research_path)])
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            require(
                completed.returncode == 0,
                "vcf_arch CLI failed: " + (completed.stdout + completed.stderr)[-1000:],
            )
            generated_artifact = load_json(generated_path)
            validate_with(
                architecture_schema,
                generated_artifact,
                architecture_schema,
                "generated architecture schema",
            )
            return generated_artifact

        generated = run_cli("without-research.json")
        require(
            graded_projection(generated) == graded_projection(artifact),
            "package output differs from the committed architecture",
        )

        artifact_research = artifact["researchConsulted"]
        if generated.get("researchConsulted") == artifact_research:
            reproduced = generated
        else:
            require(
                RESEARCH.is_file(),
                "research.json is required to reproduce architecture.json",
            )
            research_input = load_json(RESEARCH)
            require(
                research_input == artifact_research,
                "research.json differs from architecture.json provenance",
            )
            reproduced = run_cli("with-committed-research.json", RESEARCH)
        require(
            reproduced == artifact,
            "architecture.json is not fully reproducible from the package",
        )

        supplied_research = [
            {
                "title": "Synthetic verifier source",
                "url": "https://example.com/",
                "accessedOn": "2000-01-01",
                "finding": "Synthetic provenance used only to verify exact CLI copying.",
            }
        ]
        supplied_path = temporary_root / "supplied-research.json"
        supplied_path.write_text(
            json.dumps(supplied_research), encoding="utf-8"
        )
        supplied_once = run_cli("supplied-once.json", supplied_path)
        supplied_twice = run_cli("supplied-twice.json", supplied_path)
        require(
            supplied_once.get("researchConsulted") == supplied_research,
            "--research records are not copied exactly",
        )
        require(
            graded_projection(supplied_once) == graded_projection(artifact),
            "research input must not change compatibility architecture",
        )
        require(supplied_twice == supplied_once, "CLI output is not deterministic")


def main() -> int:
    try:
        # Installer-schema validation is intentionally the first validation.
        artifact = load_json(ARTIFACT)
        installer = load_json(INSTALLER_SPEC)
        target_sddc_spec = artifact.get("targetSddcSpec") if isinstance(artifact, dict) else None
        validate_with(
            installer,
            target_sddc_spec,
            {"$ref": "#/components/schemas/SddcSpec"},
            "installer SddcSpec",
        )
        print("installer-schema: PASS")

        architecture_schema = load_json(ARCHITECTURE_SCHEMA)
        validate_with(architecture_schema, artifact, architecture_schema, "architecture schema")
        inventory = load_json(INVENTORY)
        snapshot = load_json(SNAPSHOT)
        semantic_checks(artifact, inventory, snapshot)
        package_checks(artifact)
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print("architecture-verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
