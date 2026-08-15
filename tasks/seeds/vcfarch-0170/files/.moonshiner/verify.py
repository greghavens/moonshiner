from __future__ import annotations

import ast
import copy
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class VerificationError(Exception):
    pass


class SchemaValidationError(VerificationError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaValidationError(f"unsupported schema reference: {ref}")
    current: Any = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise SchemaValidationError(f"unresolvable schema reference: {ref}")
        current = current[token]
    if not isinstance(current, dict):
        raise SchemaValidationError(f"schema reference is not an object: {ref}")
    return current


def type_matches(value: Any, expected: str) -> bool:
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
    raise SchemaValidationError(f"unsupported schema type: {expected}")


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: expected constant {schema['const']!r}")

    expected_type = schema.get("type")
    if expected_type is not None and not type_matches(value, expected_type):
        raise SchemaValidationError(f"{path}: expected {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise SchemaValidationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(f"{path}: unexpected properties {extras}")
        for name, child_schema in properties.items():
            if name in value:
                validate_schema(value[name], child_schema, root_schema, f"{path}.{name}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < minimum:
            raise SchemaValidationError(f"{path}: expected at least {minimum} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaValidationError(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if minimum is not None and len(value) < minimum:
            raise SchemaValidationError(f"{path}: string shorter than {minimum}")
        pattern = schema.get("pattern")
        if pattern is not None:
            import re

            if re.search(pattern, value) is None:
                raise SchemaValidationError(f"{path}: string does not match {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(f"{path}: value is below {minimum}")


def expected_migration(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": {
            "id": product["source_id"],
            "product": product["source_product"],
            "version": product["source_version"],
        },
        "target": {
            "component": product["target_component"],
            "version": product["target_version"],
        },
        "method": product["migration_method"],
        "content": product["content"],
        "limitations": product["limitations"],
        "step_ids": product["required_step_ids"],
    }


def expected_support_boundary(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": product["source_id"],
        **product["support_boundary"],
    }


def verify_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    if plan["estate_id"] != inventory["estate_id"]:
        raise VerificationError("estate_id does not match the inventory")
    if plan["generated_from"] != {
        "inventory_revision": inventory["revision"],
        "compatibility_snapshot_revision": snapshot["snapshot_revision"],
    }:
        raise VerificationError("generated_from does not identify both pinned inputs")
    if plan["target_release"] != inventory["program_target"]["release"]:
        raise VerificationError("target_release does not match the program target")
    if plan["target_release"] != snapshot["target_release"]:
        raise VerificationError("target_release does not match the compatibility snapshot")

    entitlement = inventory["entitlement"]
    rules = snapshot["entitlement_rules"]
    decision = plan["entitlement_decision"]
    if decision["allocation_id"] != entitlement["allocation_id"]:
        raise VerificationError("the topology uses the wrong license allocation")
    selected = decision["selected_topology"]
    if selected["id"] != rules["selected_topology_id"]:
        raise VerificationError("the selected topology is not the entitlement-compatible topology")
    if selected["operations_instance_count"] != entitlement["max_vcf_operations_instances"]:
        raise VerificationError("the selected topology violates the Operations instance maximum")
    if entitlement["allocation_id"] not in selected["rationale"] or "one" not in selected["rationale"].lower():
        raise VerificationError("selected topology rationale must tie the allocation to one instance")
    rejected = decision["rejected_topology"]
    expected_rejected = {
        "id": rules["rejected_topology_id"],
        "otherwise_valid": True,
        "rejected_by": "entitlement",
        "constraint": rules["rejection_code"],
    }
    if rejected != expected_rejected:
        raise VerificationError("the otherwise-valid topology is not rejected by the pinned entitlement rule")

    if plan["placements"] != spec["required_placements"]:
        raise VerificationError("target placement or sizing differs from the installer specification")
    sites = {site["id"]: site for site in inventory["sites"]}
    totals: dict[str, dict[str, float]] = {}
    for placement in plan["placements"]:
        site_id = placement["site"]
        if site_id not in sites:
            raise VerificationError(f"placement uses unknown site {site_id}")
        if placement["cluster"] != sites[site_id]["management_cluster"]:
            raise VerificationError(f"placement uses an unknown cluster at {site_id}")
        if not set(placement["fault_domains"]).issubset(sites[site_id]["fault_domains"]):
            raise VerificationError(f"placement uses an unknown fault domain at {site_id}")
        total = totals.setdefault(site_id, {"vcpu": 0.0, "memory_gib": 0.0, "storage_tib": 0.0})
        for resource in total:
            total[resource] += placement["node_count"] * placement["resources_per_node"][resource]
        check = placement["sizing_check"]
        if check["required"] > check["capacity"]:
            raise VerificationError(f"sizing profile has no headroom for {check['metric']}")
        workload_value = inventory["workload"].get(check["metric"])
        if workload_value is not None and check["required"] != workload_value:
            raise VerificationError(f"sizing demand for {check['metric']} does not match inventory")
    if inventory["workload"]["required_log_retention_days"] != 30:
        raise VerificationError("the pinned Logs profile is only authoritative for 30-day retention")
    for site_id, consumed in totals.items():
        available = sites[site_id]["available_capacity"]
        for resource, amount in consumed.items():
            if amount > available[resource]:
                raise VerificationError(f"placements exceed {resource} capacity at {site_id}")

    source_inventory = {item["id"]: item for item in inventory["source_products"]}
    products = snapshot["products"]
    if [item["source_id"] for item in products] != list(source_inventory):
        raise VerificationError("compatibility snapshot and inventory source order differ")
    expected_migrations = [expected_migration(product) for product in products]
    if plan["migrations"] != expected_migrations:
        raise VerificationError("migration mappings or content dispositions differ from the pinned snapshot")
    for product in products:
        source = source_inventory[product["source_id"]]
        if source["product"] != product["source_product"] or source["version"] != product["source_version"]:
            raise VerificationError(f"source identity mismatch for {product['source_id']}")
        coverage = product["inventory_content_coverage"]
        if set(coverage) != set(source["content"]):
            raise VerificationError(f"not every inventory content class is classified for {product['source_id']}")
        dispositions = set().union(*product["content"].values())
        covered_dispositions = {item for mapped in coverage.values() for item in mapped}
        if dispositions != covered_dispositions:
            raise VerificationError(f"content classification is incomplete for {product['source_id']}")

    expected_boundaries = [expected_support_boundary(product) for product in products]
    if plan["support_boundaries"] != expected_boundaries:
        raise VerificationError("support boundaries differ from the pinned snapshot")
    deadline = inventory["program_target"]["cutover_deadline"]
    for boundary in plan["support_boundaries"]:
        if deadline >= boundary["end_of_general_support"]:
            raise VerificationError(f"cutover is not before EOGS for {boundary['source_id']}")

    expected_steps = snapshot["ordered_steps"]
    if len(plan["steps"]) != len(expected_steps):
        raise VerificationError("ordered migration step count differs from the pinned snapshot")
    seen: set[str] = set()
    actions: list[str] = []
    action_verbs = {
        "activate",
        "bind",
        "create",
        "deploy",
        "enable",
        "import",
        "patch",
        "perform",
        "recreate",
        "register",
        "reserve",
        "retain",
        "shutdown",
        "transfer",
        "upgrade",
        "validate",
        "verify",
    }
    for order, (step, expected) in enumerate(zip(plan["steps"], expected_steps), start=1):
        if step["order"] != order:
            raise VerificationError("migration step order values must be consecutive from one")
        if step["id"] != expected["id"] or step["component"] != expected["component"]:
            raise VerificationError(f"migration step {order} does not match the pinned sequence")
        if step["depends_on"] != expected["depends_on"]:
            raise VerificationError(f"dependencies differ for step {step['id']}")
        if not set(step["depends_on"]).issubset(seen):
            raise VerificationError(f"step {step['id']} depends on a step that has not completed")
        expected_gate = {
            "id": expected["gate_id"],
            "criterion": expected["gate_criterion"],
            "evidence": expected["gate_evidence"],
        }
        if step["gate"] != expected_gate:
            raise VerificationError(f"gate differs from the pinned gate for step {step['id']}")
        action = step["action"]
        action_words = set(re.findall(r"[a-z]+", action.lower()))
        if len(action.split()) < 8 or not action_words.intersection(action_verbs):
            raise VerificationError(f"action is not concrete for step {step['id']}")
        actions.append(action)
        seen.add(step["id"])
    if len(actions) != len(set(actions)):
        raise VerificationError("each ordered step must state its own concrete action")
    for migration in plan["migrations"]:
        if not set(migration["step_ids"]).issubset(seen):
            raise VerificationError(f"migration for {migration['source']['id']} references an unknown step")


def verify_stdlib_package(root: Path) -> None:
    package = root / "vcf_migration"
    if not package.is_dir() or not (package / "__main__.py").is_file():
        raise VerificationError("vcf_migration package does not provide its required -m entry point")
    stdlib = set(sys.stdlib_module_names)
    python_files = sorted(package.rglob("*.py"))
    if not python_files:
        raise VerificationError("vcf_migration contains no Python modules")
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"invalid Python in {path.name}: {exc}") from exc
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in stdlib and name != "vcf_migration":
                    raise VerificationError(f"non-stdlib import {name!r} in {path.name}")


def verify_research(root: Path, plan: dict[str, Any]) -> None:
    research_path = root / "research.md"
    try:
        text = research_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required file: research.md") from exc

    if "http://" in text or ".invalid" in text:
        raise VerificationError("research.md must use real HTTPS sources")
    dates = re.findall(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])", text)
    if not dates:
        raise VerificationError("research.md does not record an ISO access date")
    for value in dates:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise VerificationError(f"research.md contains invalid date {value!r}") from exc

    source_matches = list(re.finditer(r"https://[^\s<>|)]+", text))
    if not source_matches:
        raise VerificationError("research.md contains no source URL")
    urls: list[str] = []
    for index, match in enumerate(source_matches):
        url = match.group(0).rstrip(".,;")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if host != "broadcom.com" and not host.endswith(".broadcom.com"):
            raise VerificationError(f"research source is not Broadcom-published: {url}")
        if not parsed.path or parsed.path == "/":
            raise VerificationError(f"research URL is not a specific page: {url}")
        line_start = text.rfind("\n", 0, match.start()) + 1
        same_line_title = text[line_start:match.start()]
        previous_lines = text[:line_start].rstrip().splitlines()
        title_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+.-]*", same_line_title)
        if len(title_words) < 3 and previous_lines:
            title_words = re.findall(
                r"[A-Za-z0-9][A-Za-z0-9+.-]*", previous_lines[-1]
            )
        if len(title_words) < 3:
            raise VerificationError(f"research source does not record a page title: {url}")
        fact_end = source_matches[index + 1].start() if index + 1 < len(source_matches) else len(text)
        fact_text = text[match.end():fact_end]
        if len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+.-]*", fact_text)) < 8:
            raise VerificationError(f"research source does not record the facts used: {url}")
        urls.append(url)
    if len(urls) != len(set(urls)):
        raise VerificationError("research.md repeats a source URL")

    lower = text.lower()
    if "upgrade" not in lower and "migration" not in lower:
        raise VerificationError("research does not cover migration paths")
    if not any(term in lower for term in ("content", "configuration", "integration", "data transfer")):
        raise VerificationError("research does not cover content/configuration compatibility")
    if not any(term in lower for term in ("licens", "entitlement", "allocation")):
        raise VerificationError("research does not cover licensing implications")
    if "end of general support" not in lower and "eogs" not in lower:
        raise VerificationError("research does not cover end-of-support boundaries")

    serialized_plan = json.dumps(plan).lower()
    if "https://" in serialized_plan or "broadcom.com" in serialized_plan:
        raise VerificationError("migration_plan.json must keep research citations separate")


def verify_input_driven_cli(
    root: Path,
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    changed_inventory = copy.deepcopy(inventory)
    changed_inventory["estate_id"] = "mutation-estate"
    changed_inventory["revision"] = "mutation-revision"
    changed_inventory["program_target"]["release"] = "9.0.2-test"
    changed_inventory["entitlement"]["allocation_id"] = "mutation-allocation"
    changed_snapshot = copy.deepcopy(snapshot)
    changed_snapshot["snapshot_revision"] = "mutation-snapshot"
    changed_snapshot["target_release"] = "9.0.2-test"
    changed_snapshot["entitlement_rules"]["selected_topology_id"] = "mutation-topology"
    changed_snapshot["entitlement_rules"]["rejection_code"] = "mutation-rejection"
    changed_snapshot["products"][0]["target_version"] = "9.0.2-test"
    changed_snapshot["products"][0]["limitations"].append("mutation-limit")
    changed_snapshot["ordered_steps"][0]["gate_criterion"] = (
        "Mutation criterion with enough detail for schema validation."
    )
    changed_spec = copy.deepcopy(spec)
    changed_spec["required_placements"][0]["profile"] = "mutation-profile"

    with tempfile.TemporaryDirectory(prefix="vcf-migration-input-check-") as temp_dir:
        temp = Path(temp_dir)
        input_paths = []
        for name, value in (
            ("inventory.json", changed_inventory),
            ("snapshot.json", changed_snapshot),
            ("spec.json", changed_spec),
        ):
            path = temp / name
            path.write_text(json.dumps(value), encoding="utf-8")
            input_paths.append(path)
        output = temp / "plan.json"
        command = [
            sys.executable,
            "-B",
            "-m",
            "vcf_migration",
            "--inventory",
            str(input_paths[0]),
            "--snapshot",
            str(input_paths[1]),
            "--spec",
            str(input_paths[2]),
            "--output",
            str(output),
        ]
        process = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=30)
        if process.returncode != 0:
            details = (process.stderr or process.stdout).strip()
            raise VerificationError(f"CLI is not driven by its supplied inputs: {details}")
        result = load_json(output)
    checks = [
        (result.get("estate_id") == "mutation-estate", "estate_id"),
        (result.get("generated_from", {}).get("inventory_revision") == "mutation-revision", "inventory revision"),
        (
            result.get("generated_from", {}).get("compatibility_snapshot_revision")
            == "mutation-snapshot",
            "snapshot revision",
        ),
        (result.get("target_release") == "9.0.2-test", "target release"),
        (result.get("entitlement_decision", {}).get("allocation_id") == "mutation-allocation", "allocation"),
        (
            result.get("entitlement_decision", {}).get("selected_topology", {}).get("id")
            == "mutation-topology",
            "selected topology",
        ),
        (
            result.get("entitlement_decision", {}).get("rejected_topology", {}).get("constraint")
            == "mutation-rejection",
            "rejection rule",
        ),
        (result.get("placements", [{}])[0].get("profile") == "mutation-profile", "placements"),
        (
            result.get("migrations", [{}])[0].get("target", {}).get("version") == "9.0.2-test",
            "migration target",
        ),
        ("mutation-limit" in result.get("migrations", [{}])[0].get("limitations", []), "limitations"),
        (
            result.get("steps", [{}])[0].get("gate", {}).get("criterion", "").startswith("Mutation"),
            "step gates",
        ),
    ]
    hard_coded = [name for passed, name in checks if not passed]
    if hard_coded:
        raise VerificationError(f"CLI hard-codes input-derived fields: {hard_coded}")


def reproduce_artifact(root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vcf-migration-verify-") as temp_dir:
        first_output = Path(temp_dir) / "first.json"
        second_output = Path(temp_dir) / "second.json"
        command = [
            sys.executable,
            "-B",
            "-m",
            "vcf_migration",
            "--inventory",
            "estate_inventory.json",
            "--snapshot",
            "compatibility_snapshot.json",
            "--spec",
            "installer_spec.json",
        ]
        for output in (first_output, second_output):
            result = subprocess.run(
                [*command, "--output", str(output)],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout).strip()
                raise VerificationError(f"package command failed: {details}")
        first_bytes = first_output.read_bytes()
        if first_bytes != second_output.read_bytes():
            raise VerificationError("package command is nondeterministic")
        if first_bytes != (root / "migration_plan.json").read_bytes():
            raise VerificationError("package command does not reproduce the artifact byte-for-byte")
        return load_json(first_output)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        spec = load_json(root / "installer_spec.json")
        plan = load_json(root / "migration_plan.json")

        # This is deliberately the first validation performed on the artifact.
        validate_schema(plan, spec["plan_schema"], spec["plan_schema"])

        inventory = load_json(root / "estate_inventory.json")
        snapshot = load_json(root / "compatibility_snapshot.json")
        verify_plan(plan, inventory, snapshot, spec)
        verify_stdlib_package(root)
        verify_research(root, plan)
        verify_input_driven_cli(root, inventory, snapshot, spec)

        reproduced = reproduce_artifact(root)
        validate_schema(reproduced, spec["plan_schema"], spec["plan_schema"])
        verify_plan(reproduced, inventory, snapshot, spec)
        if reproduced != plan:
            raise VerificationError("package output is not identical to the committed artifact")
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration architecture matches schema, fixture, and pinned snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
