#!/usr/bin/env python3
"""Deterministic verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
INVENTORY_PATH = ROOT / "fixtures/estate_inventory.json"
COMPATIBILITY_PATH = ROOT / "compatibility/vcf-9.0-pinned.json"
MIGRATION_SCHEMA_PATH = ROOT / "schemas/migration-plan.schema.json"

PROTECTED_SHA256 = {
    SPEC_PATH: "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef",
    INVENTORY_PATH: "e942c7de63f1a2dabfd72d7354793ae9119350e1958763921d3114a538a7db7f",
    COMPATIBILITY_PATH: "63601311365505adeb08d3d6207c270d35476d3465f33e146275ce75e4c93390",
    MIGRATION_SCHEMA_PATH: "5d1db4cd5e7bcf95b29fea71cd3a16306217aa6d3f7dd31f66c37ecc6142216a",
}

EXPECTED_OUTPUTS = (
    "sddc-spec.json",
    "migration-plan.json",
    "research-sources.json",
)


class VerificationError(Exception):
    pass


class SchemaError(VerificationError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"unsupported schema reference: {pointer}")
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            current = current[token]
        except (KeyError, TypeError) as exc:
            raise SchemaError(f"unresolved schema reference: {pointer}") from exc
    return current


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
    return True


def validate_schema(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str = "$") -> None:
    """Validate the OpenAPI/JSON-Schema keywords used by the pinned schemas."""
    if "$ref" in schema:
        validate_schema(instance, json_pointer(document, schema["$ref"]), document, path)
        return
    if instance is None and schema.get("nullable"):
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, document, path)

    if "anyOf" in schema:
        if not any(schema_accepts(instance, item, document, path) for item in schema["anyOf"]):
            raise SchemaError(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(schema_accepts(instance, item, document, path) for item in schema["oneOf"])
        if matches != 1:
            raise SchemaError(f"{path}: must satisfy exactly one oneOf branch, got {matches}")
    if "not" in schema and schema_accepts(instance, schema["not"], document, path):
        raise SchemaError(f"{path}: matches a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        raise SchemaError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path}: {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, item) for item in choices):
            raise SchemaError(f"{path}: expected type {expected_type}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise SchemaError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(value, properties[key], document, child_path)
            elif additional is False:
                raise SchemaError(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                validate_schema(value, additional, document, child_path)
        if len(instance) < schema.get("minProperties", 0):
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise SchemaError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaError(f"{path}: string does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaError(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaError(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise SchemaError(f"{path}: number is not below exclusiveMaximum")


def schema_accepts(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> bool:
    try:
        validate_schema(instance, schema, document, path)
        return True
    except SchemaError:
        return False


def validate_installer_spec_first(sddc_spec: Any) -> None:
    openapi = load_json(SPEC_PATH)
    require(openapi.get("info", {}).get("version") == "9.0.0.0", "installer OpenAPI version is not 9.0.0.0")
    try:
        root_schema = openapi["components"]["schemas"]["SddcSpec"]
    except KeyError as exc:
        raise VerificationError("pinned installer OpenAPI has no SddcSpec") from exc
    validate_schema(sddc_spec, root_schema, openapi)


def verify_protected_inputs() -> None:
    for path, expected in PROTECTED_SHA256.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, f"protected input was modified: {path.relative_to(ROOT)}")


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_architecture"
    require(package.is_dir(), "missing vcf_architecture package")
    python_files = sorted(package.rglob("*.py"))
    require(bool(python_files), "vcf_architecture package contains no Python modules")
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"invalid Python syntax in {path}: {exc}") from exc
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                require(
                    top == "vcf_architecture" or top in sys.stdlib_module_names,
                    f"non-stdlib import {name!r} in {path.relative_to(ROOT)}",
                )


def verify_research(record: Any) -> None:
    require(isinstance(record, dict), "research source record must be an object")
    consulted = record.get("consulted")
    require(isinstance(consulted, list) and len(consulted) >= 2, "research source record must contain multiple consulted sources")
    notes = record.get("liveVsPinnedNotes")
    require(isinstance(notes, list), "liveVsPinnedNotes must be an array")
    require(all(isinstance(note, str) and note.strip() for note in notes), "liveVsPinnedNotes entries must be nonempty strings")

    urls: list[str] = []
    purposes: list[str] = []
    required_fields = ("title", "url", "accessedAt", "usedFor")
    for index, source in enumerate(consulted):
        require(isinstance(source, dict), f"consulted[{index}] must be an object")
        require(all(field in source for field in required_fields), f"consulted[{index}] is missing a required field")
        for field in required_fields:
            require(
                isinstance(source[field], str) and source[field].strip(),
                f"consulted[{index}].{field} must be a nonempty string",
            )
        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme in {"http", "https"}, f"consulted[{index}].url must use HTTP(S)")
        require(
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com"),
            f"consulted[{index}].url must be Broadcom-published",
        )
        require(".invalid" not in hostname, f"consulted[{index}].url must not use a fixture domain")
        urls.append(source["url"])
        purposes.append(source["usedFor"].lower())

    require(len(urls) == len(set(urls)), "consulted source URLs must be unique")
    purpose_text = " ".join(purposes)
    require(
        any(term in purpose_text for term in ("compatib", "supported combination", "target combination", "support matrix")),
        "research record does not identify compatibility research",
    )
    require(
        any(term in purpose_text for term in ("interop", "product matrix", "cross-product")),
        "research record does not identify interoperability research",
    )
    require(
        any(term in purpose_text for term in ("design", "stretched", "capacity", "management-domain", "topology", "vsan", "storage")),
        "research record does not identify VCF design research",
    )
    require(
        any(term in purpose_text for term in ("upgrade", "order", "transition", "pre-9.0", "migration", "sequence", "lifecycle")),
        "research record does not identify upgrade-path research",
    )


def verify_credential_placeholders(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "password" in key.lower():
                require(isinstance(child, str) and child.strip(), f"{child_path} must be a nonempty placeholder")
                marker = re.search(
                    r"(?i)(change.?me|placeholder|replace|example|sample|dummy|test.?only|non.?secret|not.?for.?production)",
                    child,
                )
                named_token = re.fullmatch(
                    r"(?:\$\{[A-Z][A-Z0-9_]*\}|<[A-Z][A-Z0-9_]*>|\{\{[A-Z][A-Z0-9_]*\}\}|[A-Z][A-Z0-9_]{2,})",
                    child,
                )
                require(marker is not None or named_token is not None, f"{child_path} is not clearly a non-secret placeholder")
            verify_credential_placeholders(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            verify_credential_placeholders(child, f"{path}[{index}]")


def verify_greenfield(sddc: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]) -> None:
    requirements = inventory["greenfieldRequirements"]
    rules = compatibility["greenfieldRules"]
    require(sddc.get("sddcId") == requirements["sddcId"], "wrong sddcId")
    require(sddc.get("vcfInstanceName") == requirements["vcfInstanceName"], "wrong VCF instance name")
    require(sddc.get("version") == compatibility["targetRelease"], "wrong SddcSpec version")
    require(sddc.get("workflowType") == rules["workflowType"], "greenfield workflow must be VCF")
    expected_hosts = {
        host["hostname"]
        for site in requirements["dataSites"]
        for host in site["hosts"]
    }
    host_specs = sddc.get("hostSpecs", [])
    actual_hosts = [host.get("hostname") for host in host_specs]
    require(len(actual_hosts) == rules["minimumDataHosts"], "wrong management-domain data-host count")
    require(len(actual_hosts) == len(set(actual_hosts)), "duplicate installer host")
    require(set(actual_hosts) == expected_hosts, "installer hosts do not match the two data sites")

    cluster = sddc.get("clusterSpec", {})
    md = requirements["managementDomain"]
    require(cluster.get("datacenterName") == md["datacenterName"], "wrong datacenter name")
    require(cluster.get("clusterName") == md["clusterName"], "wrong cluster name")
    datastore = sddc.get("datastoreSpec", {})
    require("vsanSpec" in datastore, "management domain must use vSAN")
    require("nfsDatastoreSpec" not in datastore and "vmfsDatastoreSpec" not in datastore, "unexpected principal storage")
    require(datastore["vsanSpec"].get("failuresToTolerate") == rules["siteFailuresToTolerate"], "wrong vSAN FTT")

    dns = requirements["dns"]
    require(sddc.get("dnsSpec", {}).get("subdomain") == dns["subdomain"], "wrong DNS subdomain")
    require(sddc.get("dnsSpec", {}).get("nameservers") == dns["nameservers"], "wrong DNS servers")
    require(sddc.get("ntpServers") == dns["ntpServers"], "wrong NTP servers")

    networks = sddc.get("networkSpecs", [])
    by_type = {network.get("networkType"): network for network in networks}
    require(len(by_type) == len(networks), "network types must be unique")
    for expected in requirements["requiredNetworks"]:
        actual = by_type.get(expected["networkType"])
        require(actual is not None, f"missing {expected['networkType']} network")
        for field in ("vlanId", "mtu", "subnet", "gateway"):
            require(actual.get(field) == expected[field], f"wrong {expected['networkType']} {field}")

    versions = rules["componentVersions"]
    require(sddc.get("sddcManagerSpec", {}).get("version") == versions["sddcManager"], "wrong SDDC Manager version")
    require(sddc.get("sddcManagerSpec", {}).get("useExistingDeployment") is False, "SDDC Manager must be greenfield")
    require(sddc.get("vcenterSpec", {}).get("version") == versions["vcenter"], "wrong vCenter version")
    require(sddc.get("vcenterSpec", {}).get("useExistingDeployment") is False, "vCenter must be greenfield")
    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("version") == versions["nsx"], "wrong NSX version")
    require(nsx.get("useExistingDeployment") is False, "NSX must be greenfield")
    require(len(nsx.get("nsxtManagers", [])) == 3, "greenfield NSX must have three managers")

    topology = sddc.get("architectureTopology")
    require(isinstance(topology, dict), "missing architectureTopology extension")
    require(topology.get("model") == rules["availabilityModel"], "wrong availability model")
    require(topology.get("storage") == rules["managementDomainStorage"], "wrong topology storage")
    require(topology.get("siteFailuresToTolerate") == rules["siteFailuresToTolerate"], "wrong site-failure tolerance")
    require(topology.get("hostVersion") == versions["esxi"], "wrong ESXi host version")
    require(set(topology.get("stretchedNetworks", [])) == set(rules["requiredStretchedNetworks"]), "wrong stretched L2 networks")
    require(topology.get("interSiteRttMs") == requirements["latency"]["interSiteRttMs"], "wrong inter-site RTT")
    require(topology["interSiteRttMs"] <= rules["maximumInterSiteRttMs"], "inter-site RTT exceeds pinned maximum")
    require(
        topology.get("requiredAfterSiteFailure") == md["requiredAfterSiteFailure"],
        "wrong post-site-failure capacity requirement",
    )

    topology_sites = topology.get("dataSites", [])
    require(len(topology_sites) == rules["dataSiteCount"], "topology must contain exactly two data sites")
    topology_by_id = {site.get("id"): site for site in topology_sites}
    require(len(topology_by_id) == len(topology_sites), "duplicate data-site ID")
    required_after_failure = md["requiredAfterSiteFailure"]
    for expected_site in requirements["dataSites"]:
        actual_site = topology_by_id.get(expected_site["id"])
        require(actual_site is not None, f"missing topology site {expected_site['id']}")
        require(actual_site.get("failureDomain") == expected_site["failureDomain"], "wrong site failure domain")
        expected_site_hosts = [host["hostname"] for host in expected_site["hosts"]]
        require(actual_site.get("hosts") == expected_site_hosts, f"wrong host placement for {expected_site['id']}")
        require(len(expected_site_hosts) >= rules["minimumHostsPerDataSite"], "too few hosts at a data site")
        capacity = {
            "cpuCores": sum(host["cpuCores"] for host in expected_site["hosts"]),
            "memoryGiB": sum(host["memoryGiB"] for host in expected_site["hosts"]),
            "usableStorageTiB": sum(host["usableStorageTiB"] for host in expected_site["hosts"]),
        }
        require(actual_site.get("capacity") == capacity, f"incorrect capacity total for {expected_site['id']}")
        for resource, needed in required_after_failure.items():
            require(capacity[resource] >= needed, f"{expected_site['id']} cannot meet {resource} after peer-site loss")

    witness = topology.get("witness", {})
    expected_witness = requirements["witness"]
    data_site_ids = {site["id"] for site in requirements["dataSites"]}
    data_failure_domains = {site["failureDomain"] for site in requirements["dataSites"]}
    for field in ("hostname", "site", "failureDomain", "managementIp", "witnessIp"):
        require(witness.get(field) == expected_witness[field], f"wrong witness {field}")
    require(witness.get("version") == versions["vsanWitness"], "wrong witness version")
    require(witness.get("runsManagementWorkloads") is False, "witness must not run management workloads")
    require(witness.get("listedAsDataHost") is False, "witness must not be an installer data host")
    require(witness["hostname"] not in actual_hosts, "witness was incorrectly added as a data host")
    require(witness["site"] not in data_site_ids, "witness must be in a third site")
    require(witness["failureDomain"] not in data_failure_domains, "witness must be in a third failure domain")
    require(witness.get("rttMs") == requirements["latency"]["witnessRttMs"], "wrong witness RTT map")
    require(all(value <= rules["maximumWitnessRttMs"] for value in witness["rttMs"].values()), "witness RTT exceeds pinned maximum")
    verify_credential_placeholders(sddc)


def verify_migration(plan: Any, inventory: dict[str, Any], compatibility: dict[str, Any]) -> None:
    schema = load_json(MIGRATION_SCHEMA_PATH)
    validate_schema(plan, schema, schema)
    require(plan["estateId"] == inventory["estateId"], "migration plan has wrong estateId")
    require(plan["targetRelease"] == compatibility["targetRelease"], "migration plan has wrong target release")

    inventory_by_id = {item["id"]: item for item in inventory["existingComponents"]}
    rules = compatibility["upgradePath"]
    require(rules.get("supported") is True, "pinned snapshot does not support this upgrade path")
    rule_by_id = {item["componentId"]: item for item in rules["components"]}
    steps = plan["steps"]
    step_ids = [step["componentId"] for step in steps]
    require(len(step_ids) == len(set(step_ids)), "migration plan contains a component more than once")
    require(set(step_ids) == set(inventory_by_id), "migration plan must name every and only inventoried component")
    require(set(rule_by_id) == set(inventory_by_id), "pinned upgrade rules and inventory disagree")
    require([step["sequence"] for step in steps] == sorted(step["sequence"] for step in steps), "migration steps are not ordered")

    seen: set[str] = set()
    for step in steps:
        component_id = step["componentId"]
        current = inventory_by_id[component_id]
        rule = rule_by_id[component_id]
        require(current["version"] in rule["allowedCurrentVersions"], f"unsupported source version for {component_id}")
        expected = {
            "sequence": rule["sequence"],
            "componentId": component_id,
            "componentName": current["name"],
            "currentVersion": current["version"],
            "targetProduct": rule["targetProduct"],
            "targetVersion": rule["targetVersion"],
            "action": rule["action"],
        }
        for field, value in expected.items():
            require(step[field] == value, f"wrong {field} for {component_id}")
        require(set(step["gates"]) == set(rule["requiredGates"]), f"wrong gates for {component_id}")
        require(set(rule["dependsOn"]).issubset(seen), f"{component_id} appears before a dependency")
        seen.add(component_id)


def run_generator(output: Path, guard_directory: Path, hash_seed: str) -> None:
    command = [
        sys.executable,
        "-m",
        "vcf_architecture",
        "--inventory",
        str(INVENTORY_PATH),
        "--compatibility",
        str(COMPATIBILITY_PATH),
        "--output",
        str(output),
    ]
    environment = dict(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH")
    pythonpath = [str(guard_directory), str(ROOT)]
    if existing_pythonpath:
        pythonpath.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    environment["PYTHONHASHSEED"] = hash_seed
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stdout + completed.stderr).strip()
        raise VerificationError(f"generator failed with exit {completed.returncode}: {details}")


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="vcf-architecture-verify-") as temporary:
            temporary_root = Path(temporary)
            guard_directory = temporary_root / "network-guard"
            guard_directory.mkdir()
            (guard_directory / "sitecustomize.py").write_text(
                "import sys\n"
                "def _block_network(event, args):\n"
                "    if event.startswith('socket.'):\n"
                "        raise RuntimeError('network access is forbidden while generating artifacts')\n"
                "sys.addaudithook(_block_network)\n",
                encoding="utf-8",
            )
            output = temporary_root / "build-one"
            second_output = temporary_root / "build-two"
            run_generator(output, guard_directory, "101")
            run_generator(second_output, guard_directory, "202")
            for name in EXPECTED_OUTPUTS:
                first_path = output / name
                second_path = second_output / name
                require(first_path.is_file(), f"generator did not create {name}")
                require(second_path.is_file(), f"generator did not reproduce {name}")
                require(first_path.read_bytes() == second_path.read_bytes(), f"generator output is nondeterministic: {name}")

            sddc = load_json(output / "sddc-spec.json")
            validate_installer_spec_first(sddc)
            print("phase 1: installer SddcSpec schema: PASS")

            verify_protected_inputs()
            verify_stdlib_only()
            print("phase 2: protected inputs and stdlib-only package: PASS")

            inventory = load_json(INVENTORY_PATH)
            compatibility = load_json(COMPATIBILITY_PATH)
            verify_greenfield(sddc, inventory, compatibility)
            print("phase 3: greenfield topology, capacity, and placement: PASS")

            migration = load_json(output / "migration-plan.json")
            verify_migration(migration, inventory, compatibility)
            print("phase 4: migration schema, compatibility, order, and gates: PASS")
            research = load_json(output / "research-sources.json")
            verify_research(research)
            print("phase 5: research audit record and deterministic offline generation: PASS")
        print("verification: PASS")
        return 0
    except (VerificationError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"verification: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
