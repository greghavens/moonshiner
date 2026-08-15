#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture artifact.

The verifier checks the research record as an offline artifact but performs no
network access; live searches and source fetches remain the trace harness's job.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class ValidationError(Exception):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        # Do not echo Python's "[Errno 2] No such file or directory" signature.
        # Moonshiner correctly uses that signature to discover missing command
        # executables during preflight; exposing it for a deliberately absent
        # baseline artifact would misclassify the artifact name as a toolchain.
        raise ValidationError(f"cannot read JSON {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"invalid JSON {path.relative_to(ROOT)} at line {exc.lineno}, column {exc.colno}"
        ) from exc


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ValidationError(f"only local schema references are supported: {pointer}")
    value = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"unresolvable schema reference {pointer}") from exc
    return value


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
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValidationError(f"unsupported schema type {expected!r}")


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)
        siblings = {key: item for key, item in schema.items() if key != "$ref"}
        if siblings:
            validate_schema(value, siblings, document, path)
        return

    if value is None and schema.get("nullable") is True:
        return

    for keyword in ("allOf",):
        for child in schema.get(keyword, []):
            validate_schema(value, child, document, path)

    for keyword, exact_one in (("anyOf", False), ("oneOf", True)):
        children = schema.get(keyword)
        if children:
            matches = 0
            for child in children:
                try:
                    validate_schema(value, child, document, path)
                    matches += 1
                except ValidationError:
                    pass
            if matches == 0 or (exact_one and matches != 1):
                raise ValidationError(f"{path}: does not satisfy {keyword}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{path}: {value!r} is not in {schema['enum']!r}")
    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{path}: {value!r} does not equal {schema['const']!r}")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(json_type_matches(value, choice) for choice in choices):
            raise ValidationError(f"{path}: expected {expected}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValidationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, child_value in value.items():
            if key in properties:
                validate_schema(child_value, properties[key], document, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ValidationError(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    child_value,
                    schema["additionalProperties"],
                    document,
                    f"{path}.{key}",
                )
        if len(value) < schema.get("minProperties", 0):
            raise ValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValidationError(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, document, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValidationError(f"{path}: string is too long")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, value)
            except re.error as exc:
                raise ValidationError(f"{path}: invalid schema pattern {pattern!r}: {exc}") from exc
            if matched is None:
                raise ValidationError(f"{path}: {value!r} does not match {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValidationError(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValidationError(f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValidationError(f"{path}: value is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValidationError(f"{path}: value is not below exclusiveMaximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def check_standard_library_only() -> None:
    """Reject dependency directives while allowing ordinary module metadata."""
    try:
        go_mod = (ROOT / "go.mod").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("cannot read go.mod") from exc
    require(
        re.search(r"(?m)^\s*module\s+\S+\s*$", go_mod) is not None,
        "go.mod does not declare a module",
    )
    require(
        re.search(r"(?m)^\s*(?:require|replace|exclude)\b", go_mod) is None,
        "third-party Go module directives are not allowed",
    )


def check_research_record(path: Path) -> None:
    """Validate the offline artifact produced by the required live research."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("research.md is required") from exc

    expected_header = ["Source", "URL", "Accessed", "Decision informed"]
    rows: list[list[str]] = []
    found_header = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if cells == expected_header:
            found_header = True
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if found_header and len(cells) == len(expected_header):
            rows.append(cells)

    require(found_header, "research.md must contain Source, URL, Accessed, and Decision informed columns")
    require(bool(rows), "research.md must record the opened Broadcom sources")

    corpus_parts: list[str] = []
    for index, (title, url, accessed, decision) in enumerate(rows, start=1):
        require(bool(title), f"research row {index} has no source title")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https"
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"research row {index} must use an official Broadcom HTTPS URL",
        )
        try:
            date.fromisoformat(accessed)
        except ValueError as exc:
            raise ValidationError(f"research row {index} has an invalid access date") from exc
        require(bool(decision), f"research row {index} has no architecture decision")
        corpus_parts.append(f"{title} {decision}".lower())

    corpus = "\n".join(corpus_parts)
    coverage = {
        "vCenter": r"\bvcenter\b",
        "ESXi": r"\besxi?\b",
        "vSAN": r"\bvsan\b",
        "NSX": r"\bnsx\b",
        "Live Site Recovery successor": r"live site recovery|protection and recovery|\bsrm\b",
        "VCF 9.1": r"\bvcf 9\.1\b|cloud foundation 9\.1",
    }
    for subject, pattern in coverage.items():
        require(re.search(pattern, corpus, re.IGNORECASE) is not None, f"research.md does not cover {subject}")


def check_greenfield_architecture(spec: dict[str, Any], target: str) -> None:
    require(spec.get("workflowType") == "VCF", "greenfield workflowType must be VCF")
    require(spec.get("version") == target, "greenfield version does not match target bundle")
    hosts = spec.get("hostSpecs")
    require(isinstance(hosts, list) and len(hosts) == 4, "greenfield must contain four hosts")
    require(
        len({host.get("hostname") for host in hosts if isinstance(host, dict)}) == 4,
        "greenfield hostnames must be unique",
    )
    networks = spec.get("networkSpecs")
    require(
        isinstance(networks, list)
        and len(networks) == 3
        and all(isinstance(network, dict) for network in networks),
        "greenfield must contain exactly three network specifications",
    )
    require(
        {network.get("networkType") for network in networks}
        == {"MANAGEMENT", "VMOTION", "VSAN"},
        "greenfield must have separate MANAGEMENT, VMOTION, and VSAN networks",
    )
    require(
        len({network.get("vlanId") for network in networks}) == 3
        and len({network.get("subnet") for network in networks}) == 3,
        "the three required networks must use distinct VLANs and subnets",
    )
    dvs_specs = spec.get("dvsSpecs")
    require(isinstance(dvs_specs, list) and len(dvs_specs) == 1, "greenfield must use one VDS")
    require(
        set(dvs_specs[0].get("networks", [])) == {"MANAGEMENT", "VMOTION", "VSAN"},
        "the VDS must carry all three required networks",
    )
    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("useExistingDeployment") is False, "vCenter must be newly deployed")
    require(vcenter.get("version") == target, "vCenter does not target the bundle")
    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("useExistingDeployment") is False, "NSX must be newly deployed")
    require(nsx.get("version") == target, "NSX does not target the bundle")
    managers = nsx.get("nsxtManagers", [])
    require(
        isinstance(managers, list)
        and len(managers) == 3
        and all(isinstance(manager, dict) for manager in managers)
        and len({manager.get("hostname") for manager in managers}) == 3,
        "greenfield must have three distinct NSX managers",
    )
    esa = spec.get("datastoreSpec", {}).get("vsanSpec", {}).get("esaConfig", {})
    require(esa.get("enabled") is True, "greenfield vSAN ESA must be enabled")


def check_migration_authority(
    architecture: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(architecture.get("schemaVersion") == "1.0", "schemaVersion must be 1.0")
    require(architecture.get("estateId") == inventory.get("estateId"), "estateId mismatch")
    require(
        architecture.get("targetBundle") == inventory.get("targetBundle"),
        "targetBundle mismatch",
    )
    require(snapshot.get("targetBundle") == inventory.get("targetBundle"), "snapshot target mismatch")

    plan = architecture["migrationPlan"]
    require(plan.get("estateId") == inventory.get("estateId"), "migration estateId mismatch")
    require(plan.get("targetBundle") == inventory.get("targetBundle"), "migration target mismatch")
    components = {component["id"]: component for component in inventory["components"]}
    rules = snapshot["rules"]
    steps = plan["steps"]
    require(len(steps) == len(components), "plan must contain exactly one step per component")
    require(len(rules) == len(components), "pinned snapshot is inconsistent with inventory")
    require({step["componentId"] for step in steps} == set(components), "plan component coverage mismatch")

    for index, (step, rule) in enumerate(zip(steps, rules), start=1):
        component = components.get(rule["componentId"])
        require(component is not None, f"snapshot rule names unknown component {rule['componentId']}")
        expected = {
            "order": index,
            "componentId": component["id"],
            "kind": component["kind"],
            "name": component["name"],
            "sourceVersion": component["version"],
            "targetVersion": rule["targetVersion"],
            "strategy": rule["strategy"],
            "gates": rule["gates"],
        }
        require(step == expected, f"migration step {index} differs from pinned authority")

    vcenter_step = steps[0]
    require(vcenter_step["componentId"] == "vc-m01", "vCenter must establish the parallel foundation first")
    require(vcenter_step["strategy"] == "parallel-redeploy", "newer vCenter cannot move in place")


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise ValidationError(f"{' '.join(command)} failed:\n{output[-4000:]}")


def main() -> int:
    try:
        # Acceptance check 1: validate greenfield against the installer spec's
        # own SddcSpec schema. No migration, Go, snapshot, or research check is
        # performed before this succeeds.
        architecture = read_json(ROOT / "architecture.json")
        installer = read_json(
            ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
        )
        if not isinstance(architecture, dict) or "greenfield" not in architecture:
            raise ValidationError("architecture.json does not contain greenfield SddcSpec")
        greenfield = architecture["greenfield"]
        sddc_schema = resolve_pointer(installer, "#/components/schemas/SddcSpec")
        validate_schema(greenfield, sddc_schema, installer, "$.greenfield")
        print("ok: greenfield validates against installer SddcSpec")

        # All remaining deterministic checks happen only after SddcSpec validation.
        provenance = read_json(ROOT / "specifications" / "vcf-installer" / "SOURCE.json")
        installer_digest = hashlib.sha256(
            (ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json").read_bytes()
        ).hexdigest()
        require(installer_digest == provenance["sha256"], "installer specification digest mismatch")

        check_standard_library_only()
        check_research_record(ROOT / "research.md")
        print("ok: research record covers the required live-source subjects")

        migration_schema = read_json(ROOT / "schemas" / "migration-plan.schema.json")
        require("migrationPlan" in architecture, "architecture.json does not contain migrationPlan")
        validate_schema(
            architecture["migrationPlan"],
            migration_schema,
            migration_schema,
            "$.migrationPlan",
        )
        inventory = read_json(ROOT / "fixtures" / "estate.json")
        snapshot = read_json(ROOT / "compatibility" / "pinned-compatibility.json")
        check_greenfield_architecture(greenfield, inventory["targetBundle"])
        check_migration_authority(architecture, inventory, snapshot)
        print("ok: architecture matches inventory and pinned compatibility authority")

        run(["go", "test", "-race", "-timeout", "30s", "./..."])
        print("ok: go test -race")

        generated = subprocess.run(
            [
                "go",
                "run",
                "./cmd/vcfarch",
                "-inventory",
                "fixtures/estate.json",
                "-compatibility",
                "compatibility/pinned-compatibility.json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if generated.returncode:
            raise ValidationError(f"generator failed:\n{generated.stderr[-4000:]}")
        try:
            generated_architecture = json.loads(generated.stdout)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"generator did not emit JSON: {exc}") from exc
        require(generated_architecture == architecture, "generator output differs from architecture.json")
        print("ok: generator reproduces architecture.json")
        return 0
    except ValidationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
