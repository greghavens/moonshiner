#!/usr/bin/env python3
"""Deterministic, offline acceptance verifier for vcfarch-0080."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture" / "migration-plan.json"
RESEARCH_LEDGER = ROOT / "architecture" / "research-sources.json"
PACKAGE = ROOT / "vcf_architecture"
INSTALLER_SPEC = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)
PINNED_INPUT_HASHES = {
    "fixtures/estate_inventory.json": "8ea31345e6a471e91ab6af82eb9c271044d3042bb7f2fe09f2b2ddd12a918785",
    "schemas/migration-plan.schema.json": "9e8da7d3fbc9aa22c8db7875fea6295873d53b0607d2e4c73f17272381cb07f7",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
}

REQUIRED_GATE_CLAIMS = {
    ("aria-suite-lifecycle", "patch"): (r"5\.2\.1\.0", r"pre.?checks?"),
    ("vcf-operations-fleet-management", "deploy"): (
        r"aria suite lifecycle",
        r"patch\s*2",
    ),
    ("aria-operations", "upgrade"): (r"aria suite lifecycle", r"patch\s*2"),
    ("vcf-operations-for-logs", "deploy"): (r"fleet management", r"9\.0\.1\.0"),
    ("aria-operations-for-logs", "replace"): (
        r"fresh",
        r"operations for logs",
        r"in[- ]place",
        r"integrations?",
        r"90[- ]days?",
    ),
    ("aria-suite-lifecycle", "retire"): (r"aria operations", r"logs", r"retir"),
    ("sddc-manager", "upgrade"): (r"aria suite lifecycle", r"logs", r"retir"),
    ("nsx-manager", "upgrade"): (r"sddc manager", r"9\.0\.1\.0"),
    ("vcenter-server", "upgrade"): (r"nsx", r"9\.0\.1\.0"),
    ("esxi", "upgrade"): (r"vcenter", r"9\.0\.1\.0"),
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        # Keep the intentional missing-deliverable baseline failure distinct
        # from the missing-executable signatures used by environment preflight.
        raise VerificationError(f"required JSON file is missing: {display_path}") from exc
    except OSError as exc:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        raise VerificationError(
            f"cannot read JSON {display_path}: {type(exc).__name__}"
        ) from exc
    except json.JSONDecodeError as exc:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        raise VerificationError(f"invalid JSON {display_path}: {exc}") from exc


def resolve_ref(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise VerificationError(f"only local JSON references are supported: {ref}")
    node = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable JSON reference: {ref}") from exc
    return node


def type_matches(instance: Any, expected: str) -> bool:
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
    raise VerificationError(f"unsupported schema type {expected!r}")


def validate_json(
    instance: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> None:
    if isinstance(schema, bool):
        if not schema:
            raise VerificationError(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        raise VerificationError(f"{path}: invalid schema node")

    if "$ref" in schema:
        validate_json(instance, resolve_ref(document, schema["$ref"]), document, path)
        sibling_schema = {key: value for key, value in schema.items() if key != "$ref"}
        if sibling_schema:
            validate_json(instance, sibling_schema, document, path)
        return

    for branch in schema.get("allOf", []):
        validate_json(instance, branch, document, path)
    if "anyOf" in schema:
        matches = 0
        for branch in schema["anyOf"]:
            try:
                validate_json(instance, branch, document, path)
                matches += 1
            except VerificationError:
                pass
        if matches == 0:
            raise VerificationError(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_json(instance, branch, document, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            raise VerificationError(f"{path}: must match exactly one oneOf branch")

    if "const" in schema and instance != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise VerificationError(f"{path}: value is outside enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, choice) for choice in choices):
            raise VerificationError(f"{path}: expected type {expected_type!r}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                raise VerificationError(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(instance) - set(properties))
            if extras:
                raise VerificationError(f"{path}: unexpected properties {extras}")
        for key, child in instance.items():
            if key in properties:
                validate_json(child, properties[key], document, f"{path}.{key}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in instance]
            if len(serialized) != len(set(serialized)):
                raise VerificationError(f"{path}: items are not unique")
        if "items" in schema:
            for index, child in enumerate(instance):
                validate_json(child, schema["items"], document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise VerificationError(f"{path}: string does not match required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")


def verify_target_spec_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is deliberately the first acceptance check."""
    artifact = load_json(ARTIFACT)
    installer = load_json(INSTALLER_SPEC)
    target = artifact.get("target_sddc_spec") if isinstance(artifact, dict) else None
    validate_json(
        target,
        {"$ref": "#/components/schemas/SddcSpec"},
        installer,
        "$.target_sddc_spec",
    )
    return artifact, installer


def verify_schema(artifact: dict[str, Any]) -> None:
    schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
    validate_json(artifact, schema, schema)


def versions_equivalent(actual: str, pinned: str) -> bool:
    if pinned == "8.18.0-patch2":
        return re.fullmatch(r"8\.18(?:\.0)?[ _-]*patch[ _-]*2", actual, re.IGNORECASE) is not None
    return actual == pinned


def verify_seed_inputs_unchanged() -> None:
    for relative, expected in PINNED_INPUT_HASHES.items():
        path = ROOT / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise VerificationError(f"cannot read protected seed input {relative}: {exc}") from exc
        if actual != expected:
            raise VerificationError(f"protected seed input was modified: {relative}")


def verify_research_ledger() -> None:
    ledger = load_json(RESEARCH_LEDGER)
    if not isinstance(ledger, dict) or "consulted" not in ledger:
        raise VerificationError("research source ledger must be an object containing consulted")
    entries = ledger["consulted"]
    if not isinstance(entries, list) or not entries:
        raise VerificationError("research source ledger has no consulted sources")

    urls = set()
    has_matrix = False
    has_upgrade_path = False
    has_published_guidance = False
    for index, entry in enumerate(entries):
        path = f"research source {index}"
        if not isinstance(entry, dict):
            raise VerificationError(f"{path} is not an object")
        for field in ("title", "url", "accessed_at_utc", "used_for"):
            if field not in entry:
                raise VerificationError(f"{path} is missing {field}")
        if not isinstance(entry["title"], str) or not entry["title"].strip():
            raise VerificationError(f"{path} has no title")

        url = entry["url"]
        if not isinstance(url, str):
            raise VerificationError(f"{path} URL is not a string")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            raise VerificationError(f"{path} URL is not absolute HTTP(S)")
        if host == "localhost" or host.endswith(
            (".invalid", ".test", ".example", ".localhost")
        ):
            raise VerificationError(f"{path} URL uses a reserved fixture host")
        if url in urls:
            raise VerificationError(f"duplicate research source URL: {url}")
        urls.add(url)

        accessed = entry["accessed_at_utc"]
        if not isinstance(accessed, str):
            raise VerificationError(f"{path} accessed_at_utc is not UTC ISO-8601")
        try:
            timestamp = dt.datetime.fromisoformat(accessed.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VerificationError(f"{path} accessed_at_utc is invalid") from exc
        if timestamp.utcoffset() != dt.timedelta(0):
            raise VerificationError(f"{path} accessed_at_utc is not UTC")

        claims = entry["used_for"]
        if not isinstance(claims, list) or not claims:
            raise VerificationError(f"{path} has no used_for claims")
        if any(
            not isinstance(claim, str) or not claim.strip() or len(claim) > 200
            for claim in claims
        ):
            raise VerificationError(f"{path} has an invalid used_for claim")

        lower_path = parsed.path.lower().rstrip("/")
        if host == "interopmatrix.broadcom.com":
            if lower_path.endswith("upgrade"):
                has_upgrade_path = True
            else:
                has_matrix = True
        if (host == "broadcom.com" or host.endswith(".broadcom.com")) and host != (
            "interopmatrix.broadcom.com"
        ):
            has_published_guidance = True

    if not has_matrix:
        raise VerificationError("source ledger omits the Product Interoperability Matrix")
    if not has_upgrade_path:
        raise VerificationError("source ledger omits the Upgrade Path")
    if not has_published_guidance:
        raise VerificationError("source ledger omits Broadcom documentation or knowledge guidance")


def verify_standard_library_imports() -> None:
    if not PACKAGE.is_dir():
        raise VerificationError("vcf_architecture package is missing")
    required = {PACKAGE / "__init__.py", PACKAGE / "__main__.py"}
    if not all(path.is_file() for path in required):
        raise VerificationError("vcf_architecture package is not executable with -m")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise VerificationError(f"cannot parse package source {path.name}: {exc}") from exc
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name != "vcf_architecture" and name not in stdlib:
                    raise VerificationError(
                        f"package imports non-standard-library module {name!r}"
                    )


def verify_generator(artifact: dict[str, Any]) -> None:
    verify_standard_library_imports()
    inventory = ROOT / "fixtures" / "estate_inventory.json"
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        outputs = [Path(temporary) / "first.json", Path(temporary) / "second.json"]
        for seed, output in enumerate(outputs, start=1):
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = str(seed)
            command = [
                sys.executable,
                "-S",
                "-m",
                "vcf_architecture",
                "--inventory",
                str(inventory),
                "--output",
                str(output),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise VerificationError(f"architecture generator could not run: {exc}") from exc
            if completed.returncode != 0:
                details = (completed.stderr or completed.stdout).strip()
                raise VerificationError(f"architecture generator failed: {details}")
        first = outputs[0].read_bytes()
        second = outputs[1].read_bytes()
        if first != second:
            raise VerificationError("architecture generator output is nondeterministic")
        if first != ARTIFACT.read_bytes():
            raise VerificationError("checked-in migration plan is not the generated artifact")
        generated = load_json(outputs[0])
        if generated != artifact:
            raise VerificationError("checked-in migration plan differs from generated output")


def verify_fixture_and_snapshot(artifact: dict[str, Any]) -> None:
    inventory = load_json(ROOT / "fixtures" / "estate_inventory.json")
    snapshot = load_json(Path(__file__).with_name("compatibility_snapshot.json"))

    for field in ("estate_id", "source_vcf_version", "target_vcf_version"):
        if artifact[field] != inventory[field]:
            raise VerificationError(f"artifact {field} does not match inventory")
    if inventory["source_vcf_version"] != snapshot["source_vcf_version"]:
        raise VerificationError("protected snapshot source does not match fixture")
    if inventory["target_vcf_version"] != snapshot["target_vcf_version"]:
        raise VerificationError("protected snapshot target does not match fixture")

    components = {item["id"]: item for item in inventory["components"]}
    expected = snapshot["transitions"]
    steps = artifact["steps"]
    if len(steps) != len(expected):
        raise VerificationError("transition count does not match pinned compatibility snapshot")

    chains: dict[str, list[dict[str, Any]]] = {key: [] for key in components}
    pinned_by_key = {(item["component_id"], item["action"]): item for item in expected}
    seen_keys = set()
    if [item["sequence"] for item in steps] != sorted(item["sequence"] for item in steps):
        raise VerificationError("steps are not ordered by execution stage")
    for actual in steps:
        key = (actual["component_id"], actual["action"])
        pinned = pinned_by_key.get(key)
        if pinned is None or key in seen_keys:
            raise VerificationError(f"unexpected or duplicate transition {key!r}")
        seen_keys.add(key)
        for field in ("sequence", "component_id", "action"):
            if actual[field] != pinned[field]:
                raise VerificationError(
                    f"step {pinned['sequence']} {field} violates pinned compatibility snapshot"
                )
        for field in ("current_version", "target_version"):
            if not versions_equivalent(actual[field], pinned[field]):
                raise VerificationError(
                    f"step {pinned['sequence']} {field} violates pinned compatibility snapshot"
                )
        component_id = actual["component_id"]
        if component_id not in components:
            raise VerificationError(f"unknown component {component_id!r}")
        if actual["component_name"] != components[component_id]["name"]:
            raise VerificationError(f"wrong name for component {component_id!r}")
        dependencies = []
        combined_conditions = " ".join(gate["condition"] for gate in actual["gates"])
        for pattern in REQUIRED_GATE_CLAIMS[key]:
            if re.search(pattern, combined_conditions, re.IGNORECASE) is None:
                raise VerificationError(
                    f"step {pinned['sequence']} omits a required technical gate condition"
                )
        for gate in actual["gates"]:
            for dependency in gate["requires_completed_steps"]:
                if dependency >= actual["sequence"]:
                    raise VerificationError(
                        f"step {pinned['sequence']} gate points forward or to itself"
                    )
                dependencies.append(dependency)
        if len(dependencies) != len(set(dependencies)):
            raise VerificationError(f"duplicate gate dependency at step {pinned['sequence']}")
        if sorted(dependencies) != pinned["required_predecessor_sequences"]:
            raise VerificationError(f"wrong technical gate dependencies at step {pinned['sequence']}")
        chains[component_id].append(actual)
    if seen_keys != set(pinned_by_key):
        raise VerificationError("one or more pinned transitions are missing")

    for component_id, component in components.items():
        chain = chains[component_id]
        if not chain:
            raise VerificationError(f"component omitted from plan: {component_id}")
        if chain[0]["current_version"] != component["current_version"]:
            raise VerificationError(f"initial version mismatch for {component_id}")
        for left, right in zip(chain, chain[1:]):
            if left["target_version"] != right["current_version"]:
                raise VerificationError(f"broken transition chain for {component_id}")
        if chain[-1]["target_version"] != component["intended_target"]:
            raise VerificationError(f"final target mismatch for {component_id}")

    target = artifact["target_sddc_spec"]
    target_state = inventory["target_state"]
    if target.get("sddcId") != target_state["sddc_id"]:
        raise VerificationError("target SddcSpec has wrong sddcId")
    if target.get("workflowType") != target_state["workflow_type"]:
        raise VerificationError("target SddcSpec has wrong workflowType")
    if target.get("version") != inventory["target_vcf_version"]:
        raise VerificationError("target SddcSpec has wrong VCF version")
    expected_dns = {
        "subdomain": target_state["dns_subdomain"],
        "nameservers": target_state["dns_nameservers"],
    }
    if target.get("dnsSpec") != expected_dns:
        raise VerificationError("target SddcSpec DNS does not match fixture")
    if target.get("networkSpecs") != [target_state["management_network"]]:
        raise VerificationError("target SddcSpec network does not match fixture")
    vcenter = target.get("vcenterSpec", {})
    if vcenter.get("vcenterHostname") != target_state["vcenter_hostname"]:
        raise VerificationError("target SddcSpec vCenter hostname does not match fixture")
    if vcenter.get("version") != components["vcenter-server"]["current_version"]:
        raise VerificationError("target SddcSpec does not identify the existing vCenter version")
    if vcenter.get("useExistingDeployment") is not True:
        raise VerificationError("target SddcSpec must model the existing vCenter")
    password = vcenter.get("rootVcenterPassword")
    if not isinstance(password, str) or not (8 <= len(password) <= 20):
        raise VerificationError("vCenter password placeholder is not schema-compatible")
    placeholder_markers = r"placeholder|replace|change|dummy|example|sample|not.?real|redact"
    if re.search(placeholder_markers, password, re.IGNORECASE) is None:
        raise VerificationError("vCenter password field does not contain a non-secret placeholder")


def main() -> int:
    try:
        artifact, _installer = verify_target_spec_first()
        verify_seed_inputs_unchanged()
        verify_schema(artifact)
        verify_research_ledger()
        verify_fixture_and_snapshot(artifact)
        verify_generator(artifact)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: installer schema and pinned migration architecture verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
