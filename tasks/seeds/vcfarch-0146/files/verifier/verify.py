#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0146.

The first artifact check is deliberately the upstream SddcSpec schema.  Live
research is outside this verifier: only the artifact and protected local
authorities are read.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


class SchemaError(VerificationError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {error}"
        ) from error


def json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "#":
        return document
    if not pointer.startswith("#/"):
        raise SchemaError(f"unsupported non-local schema reference {pointer!r}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise SchemaError(f"unresolvable schema reference {pointer!r}") from error
    return current


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
    raise SchemaError(f"unsupported schema type {expected!r}")


def validate_schema(value: Any, schema: Any, root: Any, path: str = "$") -> None:
    """Validate the JSON-Schema keywords used by the pinned OpenAPI document."""
    if isinstance(schema, bool):
        if not schema:
            raise SchemaError(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: malformed schema node")

    if "$ref" in schema:
        validate_schema(value, json_pointer(root, schema["$ref"]), root, path)
        siblings = {key: item for key, item in schema.items() if key != "$ref"}
        if siblings:
            validate_schema(value, siblings, root, path)
        return

    if schema.get("nullable") and value is None:
        return
    for branch in schema.get("allOf", []):
        validate_schema(value, branch, root, path)
    if "anyOf" in schema:
        successes = 0
        for branch in schema["anyOf"]:
            try:
                validate_schema(value, branch, root, path)
                successes += 1
            except SchemaError:
                pass
        if successes == 0:
            raise SchemaError(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        successes = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(value, branch, root, path)
                successes += 1
            except SchemaError:
                pass
        if successes != 1:
            raise SchemaError(f"{path}: satisfies {successes} oneOf branches")
    if "not" in schema:
        try:
            validate_schema(value, schema["not"], root, path)
        except SchemaError:
            pass
        else:
            raise SchemaError(f"{path}: matches prohibited schema")

    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: value {value!r} is outside enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(type_matches(value, choice) for choice in choices):
            raise SchemaError(f"{path}: expected type {expected_type!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SchemaError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in properties:
                validate_schema(item, properties[key], root, child)
                continue
            matching = [subschema for pattern, subschema in pattern_properties.items()
                        if re.search(pattern, key)]
            if matching:
                for subschema in matching:
                    validate_schema(item, subschema, root, child)
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise SchemaError(f"{path}: unexpected property {key!r}")
            if isinstance(additional, dict):
                validate_schema(item, additional, root, child)
        if len(value) < schema.get("minProperties", 0):
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":"))
                       for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: items are not unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_schema(item, items, root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as error:
                raise SchemaError(f"{path}: invalid regex in pinned schema: {error}") from error
            if not matched:
                raise SchemaError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise SchemaError(f"{path}: number is not finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise SchemaError(f"{path}: number is below exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise SchemaError(f"{path}: number is above exclusiveMaximum")


def unique_index(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise VerificationError(f"{label} has an empty {key}")
        if value in result:
            raise VerificationError(f"duplicate {label} {key} {value!r}")
        result[value] = item
    return result


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label}: got {actual!r}, want {expected!r}")


def validate_installer_projection(artifact: dict[str, Any], inventory: dict[str, Any]) -> None:
    workload = inventory["workloadDomain"]
    inputs = workload["installerInputs"]
    components = unique_index(workload["components"], "type", "workload component type")
    require_equal(artifact.get("sddcId"), workload["domainId"], "sddcId")
    require_equal(artifact.get("workflowType"), inputs["workflowType"], "workflowType")
    require_equal(artifact.get("version"), inventory["targetVcfVersion"], "SddcSpec version")
    require_equal(artifact.get("fleetId"), inventory["fleetId"], "fleetId")
    require_equal(artifact.get("hostSpecs"),
                  [{"hostname": name} for name in inputs["hostnames"]], "hostSpecs")
    require_equal(artifact.get("networkSpecs"), inputs["networks"], "networkSpecs")
    require_equal(artifact.get("dnsSpec"), inputs["dns"], "dnsSpec")
    require_equal(artifact.get("ntpServers"), inputs["ntpServers"], "ntpServers")
    require_equal(artifact.get("clusterSpec"), {
        "datacenterName": inputs["datacenterName"],
        "clusterName": inputs["clusterName"],
    }, "clusterSpec")

    vcenter = artifact.get("vcenterSpec", {})
    require_equal(vcenter.get("vcenterHostname"), inputs["vcenterHostname"],
                  "vcenterSpec.vcenterHostname")
    require_equal(vcenter.get("rootVcenterPassword"), inputs["vcenterRootPassword"],
                  "vcenterSpec.rootVcenterPassword")
    require_equal(vcenter.get("useExistingDeployment"), True,
                  "vcenterSpec.useExistingDeployment")
    require_equal(vcenter.get("version"), components["VCENTER"]["currentVersion"],
                  "vcenterSpec.version")

    nsx = artifact.get("nsxtSpec", {})
    require_equal(nsx.get("nsxtManagers"),
                  [{"hostname": name} for name in inputs["nsxManagerHostnames"]],
                  "nsxtSpec.nsxtManagers")
    require_equal(nsx.get("vipFqdn"), inputs["nsxVipFqdn"], "nsxtSpec.vipFqdn")
    require_equal(nsx.get("useExistingDeployment"), True,
                  "nsxtSpec.useExistingDeployment")
    require_equal(nsx.get("version"), components["NSX_MANAGER"]["currentVersion"],
                  "nsxtSpec.version")


def validate_management_domain(artifact: dict[str, Any], inventory: dict[str, Any],
                               known_gates: set[str]) -> set[str]:
    expected_domain = inventory["managementDomain"]
    actual_domain = artifact["managementDomain"]
    require_equal(actual_domain["domainId"], expected_domain["domainId"],
                  "management domain id")
    require_equal(actual_domain["disposition"], expected_domain["requiredDisposition"],
                  "management domain disposition")
    expected = unique_index(expected_domain["components"], "componentId",
                            "inventory management component")
    actual = unique_index(actual_domain["components"], "componentId",
                          "artifact management component")
    require_equal(set(actual), set(expected), "management component ids")
    for component_id, source in expected.items():
        item = actual[component_id]
        require_equal(item["type"], source["type"], f"{component_id} type")
        require_equal(item["currentVersion"], source["currentVersion"],
                      f"{component_id} current version")
        require_equal(item["targetVersion"], source["currentVersion"],
                      f"{component_id} preserved version")
        require_equal(item["action"], "PRESERVE", f"{component_id} action")
        if "management-domain-remains-unchanged" not in item["gates"]:
            raise VerificationError(f"{component_id} does not name the preservation gate")
        unknown = set(item["gates"]) - known_gates
        if unknown:
            raise VerificationError(f"{component_id} uses unknown gates {sorted(unknown)}")
    return set(expected)


def transition_rule(snapshot: dict[str, Any], component_type: str, from_version: str,
                    to_version: str, action: str) -> dict[str, Any]:
    matches = [rule for rule in snapshot["transitions"]
               if rule["componentType"] == component_type
               and rule["fromVersion"] == from_version
               and rule["toVersion"] == to_version
               and rule["action"] == action]
    if len(matches) != 1:
        raise VerificationError(
            f"no unique pinned transition for {component_type} "
            f"{from_version} -> {to_version} via {action}"
        )
    if matches[0].get("supported") is not True:
        raise VerificationError(
            f"plan uses unsupported pinned transition for {component_type} via {action}"
        )
    return matches[0]


def validate_forbidden(states: dict[str, dict[str, str]], snapshot: dict[str, Any],
                       after_action: str) -> None:
    for forbidden in snapshot.get("forbiddenCombinations", []):
        left = forbidden["left"]
        right = forbidden["right"]
        left_present = any(state["type"] == left["componentType"]
                           and state["version"] == left["version"]
                           for state in states.values())
        right_present = any(state["type"] == right["componentType"]
                            and state["version"] == right["version"]
                            for state in states.values())
        if left_present and right_present:
            raise VerificationError(
                f"state after {after_action} matches forbidden combination {forbidden['id']}"
            )


def validate_migration_plan(artifact: dict[str, Any], inventory: dict[str, Any],
                            snapshot: dict[str, Any], known_gates: set[str],
                            management_ids: set[str]) -> None:
    workload = inventory["workloadDomain"]
    source_components = unique_index(workload["components"], "componentId",
                                     "inventory workload component")
    plan = artifact["migrationPlan"]
    require_equal(plan["domainId"], workload["domainId"], "migration domain id")
    require_equal(plan["targetVersion"], inventory["targetVcfVersion"],
                  "migration target version")

    final = unique_index(plan["finalComponents"], "componentId", "final component")
    require_equal(set(final), set(source_components), "final workload component ids")
    for component_id, source in source_components.items():
        item = final[component_id]
        require_equal(item["type"], source["type"], f"{component_id} final type")
        require_equal(item["currentVersion"], source["currentVersion"],
                      f"{component_id} listed current version")
        require_equal(item["targetVersion"], source["targetVersion"],
                      f"{component_id} listed target version")
        unknown = set(item["gates"]) - known_gates
        if unknown:
            raise VerificationError(f"{component_id} final entry uses unknown gates {sorted(unknown)}")

    states = {component_id: {"type": item["type"], "version": item["currentVersion"]}
              for component_id, item in source_components.items()}
    history: list[str] = []
    positions: dict[str, int] = {}
    seen_components: set[str] = set()
    component_gates: dict[str, set[str]] = {key: set() for key in source_components}

    for expected_order, step in enumerate(plan["steps"], start=1):
        require_equal(step["order"], expected_order, "migration step order")
        action = step["action"]
        step_gates = set(step["gates"])
        unknown = step_gates - known_gates
        if unknown:
            raise VerificationError(f"step {expected_order} uses unknown gates {sorted(unknown)}")
        step_ids: set[str] = set()
        pending: list[tuple[str, dict[str, str]]] = []
        for change in step["components"]:
            component_id = change["componentId"]
            if component_id in management_ids:
                raise VerificationError(f"step {expected_order} changes management component {component_id}")
            if component_id not in source_components:
                raise VerificationError(f"step {expected_order} names unknown component {component_id}")
            if component_id in step_ids:
                raise VerificationError(f"step {expected_order} repeats component {component_id}")
            step_ids.add(component_id)
            state = states[component_id]
            require_equal(change["type"], state["type"], f"{component_id} transition type")
            require_equal(change["fromVersion"], state["version"],
                          f"{component_id} transition source")
            rule = transition_rule(snapshot, change["type"], change["fromVersion"],
                                   change["toVersion"], action)
            required_gates = set(rule.get("requiredGates", []))
            missing_gates = required_gates - step_gates
            if missing_gates:
                raise VerificationError(
                    f"step {expected_order} is missing gates {sorted(missing_gates)}"
                )
            missing_actions = set(rule.get("requiredPriorActions", [])) - set(history)
            if missing_actions:
                raise VerificationError(
                    f"step {expected_order} is missing predecessor actions {sorted(missing_actions)}"
                )
            component_gates[component_id].update(required_gates)
            pending.append((component_id, {
                "type": change["type"], "version": change["toVersion"]
            }))
            seen_components.add(component_id)
        for component_id, state in pending:
            states[component_id] = state
        validate_forbidden(states, snapshot, action)
        positions.setdefault(action, expected_order)
        history.append(action)

    for constraint in snapshot["orderConstraints"]:
        before, after = constraint["before"], constraint["after"]
        if before not in positions or after not in positions:
            raise VerificationError(f"plan omits ordered actions {before} / {after}")
        if positions[before] >= positions[after]:
            raise VerificationError(f"plan violates pinned order {before} before {after}")
    require_equal(seen_components, set(source_components), "components named by migration steps")
    for component_id, source in source_components.items():
        require_equal(states[component_id]["version"], source["targetVersion"],
                      f"{component_id} resulting version")
        missing = component_gates[component_id] - set(final[component_id]["gates"])
        if missing:
            raise VerificationError(
                f"{component_id} final entry omits gates {sorted(missing)}"
            )


def validate_command_output(artifact: dict[str, Any]) -> None:
    environment = os.environ.copy()
    environment["GOTOOLCHAIN"] = "local"
    with tempfile.TemporaryDirectory(prefix=".verify-command-", dir=ROOT) as directory:
        output = Path(directory) / "architecture.json"
        result = subprocess.run(
            [
                "go", "run", "./cmd/vcf-architecture",
                "-inventory", "fixtures/estate-inventory.json",
                "-compatibility", "fixtures/compatibility-snapshot.json",
                "-output", str(output),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise VerificationError(
                "vcf-architecture command failed:\n" + result.stdout[-4000:]
            )
        generated = load_json(output)
        require_equal(generated, artifact, "command-generated architecture")


def validate_authored_tests() -> None:
    authored = sorted(
        path for path in (ROOT / "architecture").glob("*_test.go")
        if path.name != "contract_test.go"
    )
    if not authored:
        raise VerificationError("missing authored Go tests in architecture/")
    source = "\n".join(path.read_text(encoding="utf-8") for path in authored)
    has_test = re.search(r"(?m)^func\s+Test[A-Za-z0-9_]+\s*\(", source)
    if not has_test or not re.search(r"\bfor\b[^{}]*\brange\b", source):
        raise VerificationError("authored Go tests are not table-driven")


def run_go_tests() -> None:
    environment = os.environ.copy()
    environment["GOTOOLCHAIN"] = "local"
    result = subprocess.run(
        ["go", "test", "-race", "./..."],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise VerificationError("go test -race failed:\n" + result.stdout[-4000:])
    print(result.stdout.rstrip())


def main() -> int:
    try:
        # Do not add any artifact assertion before this block.  The official
        # installer schema is intentionally the first substantive check.
        artifact = load_json(ROOT / "out" / "architecture.json")
        openapi = load_json(
            ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
        )
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        validate_schema(artifact, sddc_schema, openapi)
        print("ok: artifact validates against pinned upstream SddcSpec")

        output_schema = load_json(ROOT / "schemas" / "architecture-output.schema.json")
        validate_schema(artifact, output_schema, output_schema)
        print("ok: artifact validates against migration extension schema")

        inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
        snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
        require_equal(snapshot["targetVcfVersion"], inventory["targetVcfVersion"],
                      "snapshot target")
        known_gates = {item["id"] for item in snapshot["gates"]}
        if len(known_gates) != len(snapshot["gates"]):
            raise VerificationError("compatibility snapshot has duplicate gate ids")
        validate_installer_projection(artifact, inventory)
        management_ids = validate_management_domain(artifact, inventory, known_gates)
        validate_migration_plan(artifact, inventory, snapshot, known_gates, management_ids)
        print("ok: artifact is consistent with inventory and pinned compatibility state machine")

        validate_command_output(artifact)
        print("ok: command reproduces the checked-in architecture")
        validate_authored_tests()
        run_go_tests()
        print("ok: go test -race ./...")
        return 0
    except (VerificationError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
