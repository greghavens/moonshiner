#!/usr/bin/env python3
"""Deterministic offline verifier for vcfarch-0050."""

from __future__ import annotations

import ast
import datetime as dt
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path.cwd()
FIXTURES = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.name}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.name}: {exc}")


class JsonSchemaValidator:
    """The JSON Schema/OpenAPI subset used by the pinned specifications."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            fail(f"unsupported external schema reference: {ref}")
        node: Any = self.document
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            try:
                node = node[part]
            except (KeyError, TypeError):
                fail(f"unresolvable schema reference: {ref}")
        if not isinstance(node, dict):
            fail(f"schema reference does not resolve to an object: {ref}")
        return node

    def validate(self, value: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(value, self.resolve(schema["$ref"]), path)
            return

        if schema.get("nullable") and value is None:
            return

        for branch in schema.get("allOf", []):
            self.validate(value, branch, path)

        if "anyOf" in schema:
            if not self._matches_any(value, schema["anyOf"], exactly_one=False, path=path):
                fail(f"{path}: does not match any anyOf branch")
        if "oneOf" in schema:
            if not self._matches_any(value, schema["oneOf"], exactly_one=True, path=path):
                fail(f"{path}: does not match exactly one oneOf branch")

        if "const" in schema and value != schema["const"]:
            fail(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            fail(f"{path}: {value!r} is not an allowed value")

        expected_type = schema.get("type")
        if expected_type is not None and not self._is_type(value, expected_type):
            fail(f"{path}: expected {expected_type}, got {type(value).__name__}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    fail(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, item in value.items():
                if key in properties:
                    self.validate(item, properties[key], f"{path}.{key}")
                elif schema.get("additionalProperties") is False:
                    fail(f"{path}: unexpected property {key!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    self.validate(item, schema["additionalProperties"], f"{path}.{key}")
            if len(value) < schema.get("minProperties", 0):
                fail(f"{path}: too few properties")
            if "maxProperties" in schema and len(value) > schema["maxProperties"]:
                fail(f"{path}: too many properties")

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                fail(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                fail(f"{path}: too many items")
            if schema.get("uniqueItems"):
                serialized = [json.dumps(item, sort_keys=True) for item in value]
                if len(serialized) != len(set(serialized)):
                    fail(f"{path}: items must be unique")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    self.validate(item, item_schema, f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                fail(f"{path}: string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                fail(f"{path}: string is too long")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                fail(f"{path}: does not match pattern {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                fail(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                fail(f"{path}: above maximum")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                fail(f"{path}: below exclusive minimum")
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                fail(f"{path}: above exclusive maximum")

    def _matches_any(
        self,
        value: Any,
        branches: list[dict[str, Any]],
        *,
        exactly_one: bool,
        path: str,
    ) -> bool:
        matches = 0
        for branch in branches:
            try:
                self.validate(value, branch, path)
            except VerificationError:
                continue
            matches += 1
        return matches == 1 if exactly_one else matches > 0

    @staticmethod
    def _is_type(value: Any, expected: str | list[str]) -> bool:
        if isinstance(expected, list):
            return any(JsonSchemaValidator._is_type(value, item) for item in expected)
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        return expected in checks and checks[expected](value)


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_close(actual: Any, expected: float, label: str, tolerance: float = 0.02) -> None:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        fail(f"{label}: expected a number")
    if not math.isclose(float(actual), expected, abs_tol=tolerance):
        fail(f"{label}: expected {expected}, got {actual}")


def verify_research(artifact: dict[str, Any]) -> None:
    research = artifact.get("research")
    if not isinstance(research, list) or not research:
        fail("research must be a non-empty array of live source records")

    required_fields = {"title", "publisher", "url", "accessedAt", "claim"}
    topics: set[str] = set()
    broadcom_source_count = 0
    for index, record in enumerate(research):
        label = f"research[{index}]"
        if not isinstance(record, dict):
            fail(f"{label}: expected an object")
        missing = required_fields - set(record)
        if missing:
            fail(f"{label}: missing required fields {sorted(missing)}")
        for field in required_fields:
            if not isinstance(record[field], str) or not record[field].strip():
                fail(f"{label}.{field}: expected a non-empty string")

        try:
            accessed = dt.date.fromisoformat(record["accessedAt"])
        except ValueError:
            fail(f"{label}.accessedAt: expected an ISO 8601 calendar date")
        if accessed.isoformat() != record["accessedAt"]:
            fail(f"{label}.accessedAt: expected YYYY-MM-DD format")

        parsed = urlsplit(record["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            fail(f"{label}.url: expected an absolute HTTP(S) URL")
        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname.endswith(".invalid")
            or hostname.endswith(".test")
        ):
            fail(f"{label}.url: local and reserved test hosts are not live sources")
        is_broadcom = hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        if is_broadcom:
            broadcom_source_count += 1
            searchable = " ".join(
                (record["title"], record["claim"], parsed.path, parsed.query)
            ).lower()
            if "interoperability" in searchable and "matrix" in searchable:
                topics.add("interoperability")
            if "upgrade" in searchable and "path" in searchable:
                topics.add("upgrade-path")
            if "update sequence" in searchable or (
                "vcf 9" in searchable and "sequence" in searchable
            ):
                topics.add("update-sequence")

    if not broadcom_source_count:
        fail("research must include Broadcom's live published sources")
    required_topics = {"interoperability", "upgrade-path", "update-sequence"}
    if topics != required_topics:
        fail(f"research is missing required Broadcom topics: {sorted(required_topics - topics)}")


def verify_sddc_semantics(greenfield: dict[str, Any], inventory: dict[str, Any]) -> None:
    design = inventory["newWorkloadDomain"]
    sddc = greenfield["sddcSpec"]

    require_equal(sddc.get("sddcId"), design["id"], "SddcSpec.sddcId")
    require_equal(sddc.get("workflowType"), "VCF_EXTEND", "SddcSpec.workflowType")
    require_equal(sddc.get("version"), design["targetRelease"], "SddcSpec.version")
    require_equal(sddc.get("vcfInstanceName"), inventory["vcfInstanceName"], "SddcSpec.vcfInstanceName")
    if "sddcManagerSpec" in sddc:
        fail("greenfield extension must not deploy or modify SDDC Manager")
    require_equal(sddc.get("skipEsxThumbprintValidation"), False, "ESXi thumbprint validation")
    require_equal(sddc.get("skipGatewayPingValidation"), False, "gateway validation")

    naming = design["naming"]
    switching = design["switching"]
    expected_hosts = naming["hostnames"]
    hostnames = [item.get("hostname") for item in sddc.get("hostSpecs", [])]
    require_equal(hostnames, expected_hosts, "SddcSpec.hostSpecs")

    vcenter = sddc.get("vcenterSpec", {})
    require_equal(vcenter.get("vcenterHostname"), naming["vcenterFqdn"], "vCenter hostname")
    require_equal(vcenter.get("rootVcenterPassword"), design["secretPlaceholders"]["vcenterRootPassword"], "vCenter secret placeholder")
    require_equal(vcenter.get("useExistingDeployment"), False, "greenfield vCenter deployment")
    require_equal(vcenter.get("version"), "9.0.0.0", "vCenter version")

    cluster = sddc.get("clusterSpec", {})
    require_equal(cluster.get("datacenterName"), naming["datacenter"], "datacenter name")
    require_equal(cluster.get("clusterName"), naming["cluster"], "cluster name")

    datastore = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require_equal(datastore.get("datastoreName"), naming["vsanDatastore"], "vSAN datastore")
    require_equal(datastore.get("esaConfig", {}).get("enabled"), True, "vSAN ESA")
    require_equal(datastore.get("failuresToTolerate"), 2, "vSAN FTT")

    dns = sddc.get("dnsSpec", {})
    require_equal(dns.get("subdomain"), design["networking"]["dnsSubdomain"], "DNS subdomain")
    require_equal(dns.get("nameservers"), design["networking"]["dnsServers"], "DNS servers")
    require_equal(sddc.get("ntpServers"), design["networking"]["ntpServers"], "NTP servers")

    network_specs = sddc.get("networkSpecs", [])
    require_equal(len(network_specs), len(design["networking"]["networks"]), "network count")
    by_type = {item.get("networkType"): item for item in network_specs}
    require_equal(set(by_type), {item["type"] for item in design["networking"]["networks"]}, "network types")
    for expected in design["networking"]["networks"]:
        actual = by_type[expected["type"]]
        require_equal(actual.get("vlanId"), expected["vlanId"], f"{expected['type']} VLAN")
        require_equal(actual.get("subnet"), expected["cidr"], f"{expected['type']} CIDR")
        require_equal(actual.get("gateway"), expected["gateway"], f"{expected['type']} gateway")
        require_equal(actual.get("mtu"), expected["mtu"], f"{expected['type']} MTU")
        ranges = actual.get("includeIpAddressRanges")
        require_equal(
            ranges,
            [{"startIpAddress": expected["poolStart"], "endIpAddress": expected["poolEnd"]}],
            f"{expected['type']} pool",
        )
        network = ipaddress.ip_network(expected["cidr"])
        for address in (expected["gateway"], expected["poolStart"], expected["poolEnd"]):
            if ipaddress.ip_address(address) not in network:
                fail(f"fixture error: {address} is outside {network}")

    dvs_specs = sddc.get("dvsSpecs", [])
    require_equal(len(dvs_specs), 2, "DVS count")
    dvs_by_name = {item.get("dvsName"): item for item in dvs_specs}
    require_equal(set(dvs_by_name), {switching["systemDvs"], switching["overlayDvs"]}, "DVS names")
    system_dvs = dvs_by_name[switching["systemDvs"]]
    overlay_dvs = dvs_by_name[switching["overlayDvs"]]
    require_equal(system_dvs.get("networks"), switching["systemNetworks"], "system DVS networks")
    require_equal(overlay_dvs.get("networks"), switching["overlayNetworks"], "overlay DVS networks")
    require_equal(
        system_dvs.get("vmnicsToUplinks"),
        [{"id": nic, "uplink": f"uplink{index}"} for index, nic in enumerate(switching["systemVmnics"], 1)],
        "system DVS uplinks",
    )
    require_equal(
        overlay_dvs.get("vmnicsToUplinks"),
        [{"id": nic, "uplink": f"uplink{index}"} for index, nic in enumerate(switching["overlayVmnics"], 1)],
        "overlay DVS uplinks",
    )
    zones = overlay_dvs.get("nsxtSwitchConfig", {}).get("transportZones", [])
    require_equal(
        zones,
        switching["transportZones"],
        "NSX transport zones",
    )

    nsx = sddc.get("nsxtSpec", {})
    require_equal(nsx.get("useExistingDeployment"), False, "greenfield NSX deployment")
    require_equal(nsx.get("version"), "9.0.0.0", "NSX version")
    overlay_network = next(
        item for item in design["networking"]["networks"] if item["type"] == "HOST_OVERLAY"
    )
    require_equal(nsx.get("transportVlanId"), overlay_network["vlanId"], "NSX transport VLAN")
    require_equal(nsx.get("vipFqdn"), naming["nsxVipFqdn"], "NSX VIP")
    require_equal(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])],
        naming["nsxManagerFqdns"],
        "NSX managers",
    )
    pool = nsx.get("ipAddressPoolSpec", {})
    require_equal(pool.get("name"), naming["tepPool"], "NSX TEP pool name")
    require_equal(
        pool.get("subnets"),
        [{
            "cidr": overlay_network["cidr"],
            "gateway": overlay_network["gateway"],
            "ipAddressPoolRanges": [{
                "start": overlay_network["poolStart"],
                "end": overlay_network["poolEnd"],
            }],
        }],
        "NSX TEP pool",
    )


def verify_capacity_and_availability(greenfield: dict[str, Any], inventory: dict[str, Any]) -> None:
    design = inventory["newWorkloadDomain"]
    profile = design["hostProfile"]
    demand = design["demand"]
    availability = design["availability"]
    surviving_hosts = design["hostCount"] - availability["hostFailuresReserved"]
    expected_vcpu = surviving_hosts * profile["physicalCores"] * demand["cpuOvercommitRatio"]
    expected_memory = surviving_hosts * profile["memoryGiB"]
    expected_storage = surviving_hosts * profile["rawStorageTiB"] * availability["storageUsableFactor"]
    expected_headroom = {
        "cpu": round((expected_vcpu - demand["vCpu"]) / expected_vcpu * 100, 2),
        "memory": round((expected_memory - demand["memoryGiB"]) / expected_memory * 100, 2),
        "storage": round((expected_storage - demand["usableStorageTiB"]) / expected_storage * 100, 2),
    }

    capacity = greenfield.get("capacity", {})
    require_equal(capacity.get("hostCount"), design["hostCount"], "capacity host count")
    require_equal(capacity.get("hostProfile"), profile, "capacity host profile")
    require_equal(capacity.get("demand"), demand, "capacity demand")
    after_failure = capacity.get("usableAfterReservedFailure", {})
    require_close(after_failure.get("vCpu"), expected_vcpu, "surviving vCPU")
    require_close(after_failure.get("memoryGiB"), expected_memory, "surviving memory")
    require_close(after_failure.get("storageTiB"), expected_storage, "surviving storage")
    reported_headroom = capacity.get("headroomPercentAfterReservedFailure", {})
    for resource, expected in expected_headroom.items():
        require_equal(reported_headroom.get(resource), expected, f"{resource} headroom")
        if expected + 1e-9 < availability["minimumHeadroomPercentAfterReservedFailure"]:
            fail(f"fixture capacity does not meet required {resource} headroom")

    architecture_availability = greenfield.get("availability", {})
    require_equal(architecture_availability.get("haAdmissionControlReservedHosts"), 1, "HA reserve")
    require_equal(architecture_availability.get("vsanFailuresToTolerate"), 2, "availability vSAN FTT")
    require_equal(architecture_availability.get("nsxManagerCount"), 3, "availability NSX count")
    require_equal(architecture_availability.get("siteTopology"), "SINGLE_SITE", "site topology")
    fault_domains = architecture_availability.get("faultDomains")
    hosts = design["naming"]["hostnames"]
    expected_domains = [
        {
            "rack": f"CHI01-R{rack:02d}",
            "hosts": hosts[(rack - 1) * 2 : rack * 2],
        }
        for rack in range(1, 5)
    ]
    require_equal(fault_domains, expected_domains, "rack fault domains")

    site = greenfield.get("site", {})
    sites = inventory["sites"]
    require_equal(site.get("deploymentSite"), sites["workloadDeployment"], "deployment site")
    require_equal(site.get("recoverySite"), sites["recovery"], "recovery site")
    require_equal(site.get("stretchedCluster"), sites["stretchedCluster"], "stretched cluster")
    require_equal(site.get("recoveryMode"), sites["recoveryMode"], "recovery mode")
    require_equal(site.get("rpoMinutes"), sites["rpoMinutes"], "RPO")
    require_equal(site.get("rtoMinutes"), sites["rtoMinutes"], "RTO")

    impact = greenfield.get("managementDomainImpact", {})
    require_equal(impact.get("domainId"), inventory["managementDomain"]["id"], "management domain")
    require_equal(impact.get("disposition"), "UNCHANGED", "management domain disposition")
    require_equal(impact.get("changeSet"), [], "management domain change set")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require_equal(plan["estateId"], inventory["estateId"], "migration estate")
    require_equal(plan["sourceRelease"], inventory["legacyWorkloadDomain"]["release"], "migration source release")
    require_equal(plan["targetRelease"], inventory["newWorkloadDomain"]["targetRelease"], "migration target release")
    require_equal(plan["managementDomainDisposition"], "UNCHANGED", "migration management disposition")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    steps = plan["steps"]
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "migration order")
    require_equal({step["componentId"] for step in steps}, set(inventory_by_id), "migration inventory coverage")
    if len(steps) != len(inventory_by_id):
        fail("migration plan must name each inventory component exactly once")

    target_bom = snapshot["releaseBoms"][plan["targetRelease"]]
    supported_paths = {
        (item["component"], item["from"], item["to"])
        for item in snapshot["upgradePaths"]
        if item["supported"]
    }
    expected_compatible = snapshot["compatibleSets"][0]["components"]
    require_equal(target_bom, expected_compatible, "pinned VCF 9 compatible set")
    source_bom = snapshot["releaseBoms"][plan["sourceRelease"]]

    target_components: dict[str, str] = {}
    target_ids: set[str] = set()
    scope_sequences: dict[str, list[str]] = {"MANAGEMENT_DOMAIN": [], "LEGACY_WORKLOAD_DOMAIN": []}
    for step in steps:
        source = inventory_by_id[step["componentId"]]
        for field in ("component", "scope", "version"):
            plan_field = "sourceVersion" if field == "version" else field
            require_equal(step[plan_field], source[field], f"{step['componentId']} {plan_field}")
        scope_sequences[source["scope"]].append(source["component"])

        target = step["target"]
        if source["scope"] == "MANAGEMENT_DOMAIN":
            require_equal(step["action"], "RETAIN", f"{source['id']} action")
            require_equal(target["componentId"], source["id"], f"{source['id']} target id")
            require_equal(target["domainId"], source["domainId"], f"{source['id']} target domain")
            require_equal(target["version"], source["version"], f"{source['id']} target version")
            require_equal(target["state"], "RETAINED", f"{source['id']} target state")
            required_gates = snapshot["gateRules"]["RETAIN"]
        else:
            require_equal(source["version"], source_bom[source["component"]], f"{source['id']} source BOM")
            require_equal(step["action"], "MIGRATE_TO_GREENFIELD", f"{source['id']} action")
            require_equal(target["domainId"], inventory["newWorkloadDomain"]["id"], f"{source['id']} target domain")
            require_equal(target["version"], target_bom[source["component"]], f"{source['id']} target version")
            require_equal(target["state"], "ACTIVE", f"{source['id']} target state")
            if target["componentId"] == source["id"]:
                fail(f"{source['id']}: migration target must be a greenfield component")
            if target["componentId"] in inventory_by_id:
                fail(f"{source['id']}: migration target reuses an existing component identity")
            if target["componentId"] in target_ids:
                fail(f"{source['id']}: greenfield target component identities must be distinct")
            target_ids.add(target["componentId"])
            transition = (source["component"], source["version"], target["version"])
            if transition not in supported_paths:
                fail(f"{source['id']}: transition is unsupported by the pinned snapshot")
            required_gates = snapshot["gateRules"][source["component"]]
            target_components[source["component"]] = target["version"]
        if not set(required_gates).issubset(set(step["gates"])):
            fail(f"{source['id']}: missing required gates {required_gates}")

    rank = {component: index for index, component in enumerate(snapshot["upgradeSequence"])}
    legacy_sequence = scope_sequences["LEGACY_WORKLOAD_DOMAIN"]
    positions = [rank[component] for component in legacy_sequence]
    if positions != sorted(positions):
        fail("legacy workload-domain steps do not follow the pinned upgrade sequence")
    for component, version in target_components.items():
        require_equal(version, expected_compatible[component], f"target interop {component}")


def verify_python_package(submitted_artifact: dict[str, Any]) -> None:
    package = ROOT / "vcf_architecture"
    required = [package / "__init__.py", package / "__main__.py"]
    for path in required:
        if not path.is_file():
            fail(f"missing Python package file: {path.relative_to(ROOT)}")
    for path in package.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(ROOT.resolve())
            except (FileNotFoundError, ValueError):
                fail(f"package symlink escapes the task workspace: {path.relative_to(ROOT)}")
    python_files = sorted(package.rglob("*.py"))
    local_modules = {path.stem for path in python_files} | {"vcf_architecture"}
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    stdlib.update({"__future__"})
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            fail(f"invalid Python source {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for imported in roots:
                if imported not in stdlib and imported not in local_modules:
                    fail(f"non-stdlib import {imported!r} in {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        run_root = Path(temporary)
        shutil.copytree(package, run_root / package.name)
        inputs = ("estate_inventory.json", "compatibility_snapshot.json")
        for name in inputs:
            shutil.copy2(FIXTURES / name, run_root / name)
        input_bytes = {name: (run_root / name).read_bytes() for name in inputs}

        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            environment.pop(name, None)
        environment.update(
            {
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "TZ": "UTC",
            }
        )
        command = [sys.executable, "-B", "-S", "-m", "vcf_architecture"]

        def run_generator() -> None:
            result = subprocess.run(
                command,
                cwd=run_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                fail(
                    "python3 -m vcf_architecture failed"
                    + (f": {detail[:500]}" if detail else "")
                )
            if not (run_root / "architecture.json").is_file():
                fail("python3 -m vcf_architecture did not write architecture.json")

        run_generator()
        first_output = (run_root / "architecture.json").read_bytes()
        run_generator()
        second_output = (run_root / "architecture.json").read_bytes()
        require_equal(second_output, first_output, "deterministic package output")
        for name, original in input_bytes.items():
            require_equal((run_root / name).read_bytes(), original, f"unmodified {name}")
        try:
            generated_artifact = json.loads(first_output)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(f"generated architecture.json is invalid JSON: {exc}")
        require_equal(
            generated_artifact,
            submitted_artifact,
            "generated architecture.json",
        )


def main() -> int:
    try:
        artifact = read_json(ROOT / "architecture.json")
        installer = read_json(FIXTURES / "specifications/vcf-installer/vcf-installer-openapi.json")
        try:
            sddc_spec = artifact["greenfield"]["sddcSpec"]
        except (KeyError, TypeError):
            fail("architecture.json must contain greenfield.sddcSpec")

        # Binding order: validate the SddcSpec against the installer's own tagged
        # schema before loading or checking any other grading fixture or contract.
        installer_validator = JsonSchemaValidator(installer)
        installer_validator.validate(
            sddc_spec,
            {"$ref": "#/components/schemas/SddcSpec"},
            "$.greenfield.sddcSpec",
        )

        migration_schema = read_json(FIXTURES / "migration-plan.schema.json")
        inventory = read_json(FIXTURES / "estate_inventory.json")
        snapshot = read_json(FIXTURES / "compatibility_snapshot.json")
        try:
            greenfield = artifact["greenfield"]
            migration = artifact["migrationPlan"]
        except (KeyError, TypeError):
            fail("architecture.json must contain greenfield and migrationPlan objects")
        JsonSchemaValidator(migration_schema).validate(migration, migration_schema, "$.migrationPlan")
        require_equal(artifact.get("artifactVersion"), "1.0", "artifact version")
        verify_research(artifact)
        verify_sddc_semantics(greenfield, inventory)
        verify_capacity_and_availability(greenfield, inventory)
        verify_migration(migration, inventory, snapshot)
        verify_python_package(artifact)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.0 architecture and migration plan satisfy the pinned contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
