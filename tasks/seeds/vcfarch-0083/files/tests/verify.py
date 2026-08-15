#!/usr/bin/env python3
"""Protected, offline verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path.cwd()
ARTIFACT = ROOT / "migration-plan.json"
INSTALLER_SPEC = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)
PLAN_SCHEMA = ROOT / "specifications" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate.json"
COMPATIBILITY = ROOT / "compatibility" / "compatibility-snapshot.json"
RESEARCH = ROOT / "research.md"


class VerificationError(AssertionError):
    """A deterministic verification failure."""


class SchemaError(VerificationError):
    """A JSON Schema validation failure."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {error}"
        ) from error


class JsonSchemaSubset:
    """The OpenAPI/JSON-Schema keywords used by the pinned schemas."""

    def __init__(self, root_schema: dict[str, Any]):
        self.root = root_schema

    def _resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise SchemaError(f"unsupported non-local schema reference: {reference}")
        value: Any = self.root
        for raw in reference[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            try:
                value = value[token]
            except (KeyError, TypeError) as error:
                raise SchemaError(f"unresolvable schema reference: {reference}") from error
        if not isinstance(value, dict):
            raise SchemaError(f"schema reference is not an object: {reference}")
        return value

    @staticmethod
    def _is_type(value: Any, wanted: str) -> bool:
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if wanted not in checks:
            raise SchemaError(f"unsupported schema type: {wanted}")
        return checks[wanted](value)

    def validate(self, value: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(value, self._resolve(schema["$ref"]), path)
            return
        if value is None and schema.get("nullable"):
            return

        for part in schema.get("allOf", []):
            self.validate(value, part, path)
        if "anyOf" in schema:
            matches = 0
            for part in schema["anyOf"]:
                try:
                    self.validate(value, part, path)
                except SchemaError:
                    continue
                matches += 1
            if matches == 0:
                raise SchemaError(f"{path}: does not match any allowed schema")
        if "oneOf" in schema:
            matches = 0
            for part in schema["oneOf"]:
                try:
                    self.validate(value, part, path)
                except SchemaError:
                    continue
                matches += 1
            if matches != 1:
                raise SchemaError(f"{path}: must match exactly one allowed schema")

        if "const" in schema and value != schema["const"]:
            raise SchemaError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaError(f"{path}: {value!r} is not in {schema['enum']!r}")

        wanted = schema.get("type")
        if isinstance(wanted, list):
            if not any(self._is_type(value, item) for item in wanted):
                raise SchemaError(f"{path}: wrong type")
        elif isinstance(wanted, str) and not self._is_type(value, wanted):
            raise SchemaError(f"{path}: expected {wanted}, got {type(value).__name__}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [name for name in required if name not in value]
            if missing:
                raise SchemaError(f"{path}: missing required properties {missing}")
            if len(value) < schema.get("minProperties", 0):
                raise SchemaError(f"{path}: too few properties")
            properties = schema.get("properties", {})
            for name, child in properties.items():
                if name in value:
                    self.validate(value[name], child, f"{path}.{name}")
            extras = set(value) - set(properties)
            additional = schema.get("additionalProperties", True)
            if extras and additional is False:
                raise SchemaError(f"{path}: additional properties {sorted(extras)}")
            if isinstance(additional, dict):
                for name in extras:
                    self.validate(value[name], additional, f"{path}.{name}")

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                raise SchemaError(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise SchemaError(f"{path}: too many items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise SchemaError(f"{path}: items must be unique")
            if isinstance(schema.get("items"), dict):
                for index, item in enumerate(value):
                    self.validate(item, schema["items"], f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise SchemaError(f"{path}: string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaError(f"{path}: string is too long")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise SchemaError(f"{path}: does not match pattern {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaError(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaError(f"{path}: above maximum")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                raise SchemaError(f"{path}: not above exclusive minimum")
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                raise SchemaError(f"{path}: not below exclusive maximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def transition_token(component_id: str, target_version: str) -> str:
    return f"{component_id}@{target_version}"


def validate_installer_schema_first(plan: dict[str, Any], installer: dict[str, Any]) -> None:
    """This is deliberately the first validation performed on the artifact."""
    try:
        sddc_spec = plan["target_sddc_spec"]
        schema = installer["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as error:
        raise SchemaError("artifact lacks target_sddc_spec or installer SddcSpec") from error
    JsonSchemaSubset(installer).validate(sddc_spec, schema, "$.target_sddc_spec")


def validate_domains(plan: dict[str, Any], inventory: dict[str, Any]) -> None:
    actual = {item["domain_id"]: item for item in plan["domains"]}
    expected = {item["domain_id"]: item for item in inventory["domains"]}
    require(set(actual) == set(expected), "domain coverage differs from inventory")
    require({item["kind"] for item in actual.values()} == {"management", "workload"},
            "architecture must include management and workload domains")
    for domain_id, source in expected.items():
        item = actual[domain_id]
        require(item["kind"] == source["kind"], f"{domain_id}: wrong domain kind")
        require(item["cluster_ids"] == source["cluster_ids"],
                f"{domain_id}: cluster coverage differs from inventory")
        require(item["component_ids"] == source["component_ids"],
                f"{domain_id}: component coverage differs from inventory")
        require(item["current_release"] == inventory["current_release"],
                f"{domain_id}: wrong current release")
        require(item["target_release"] == inventory["target_release"],
                f"{domain_id}: wrong target release")


def expected_transitions(
    inventory: dict[str, Any], compatibility: dict[str, Any]
) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for component in inventory["components"]:
        path = compatibility["product_paths"].get(component["product_key"])
        require(isinstance(path, list) and len(path) >= 2,
                f"no pinned path for {component['component_id']}")
        require(path[0] == component["version"],
                f"pinned path does not start at {component['component_id']} version")
        result[component["component_id"]] = list(zip(path, path[1:]))
    return result


def validate_components_and_steps(
    plan: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    inventory_by_id = {item["component_id"]: item for item in inventory["components"]}
    summaries = {item["component_id"]: item for item in plan["components"]}
    require(len(summaries) == len(plan["components"]), "duplicate component summaries")
    require(set(summaries) == set(inventory_by_id),
            "component summaries must cover every inventory component exactly")

    transitions = expected_transitions(inventory, compatibility)
    orders = [item["order"] for item in plan["steps"]]
    require(orders == list(range(1, len(orders) + 1)),
            "step order must be contiguous and start at 1")

    by_component: dict[str, list[dict[str, Any]]] = {name: [] for name in summaries}
    positions: dict[str, int] = {}
    for step in plan["steps"]:
        component_id = step["component_id"]
        require(component_id in by_component, f"step names unknown component {component_id}")
        by_component[component_id].append(step)
        token = transition_token(component_id, step["target_version"])
        require(token not in positions, f"duplicate transition {token}")
        positions[token] = step["order"]

    actions = compatibility.get("transition_actions", {})
    for component_id, source in inventory_by_id.items():
        summary = summaries[component_id]
        path = compatibility["product_paths"][source["product_key"]]
        require(summary["product"] == source["product"],
                f"{component_id}: product name changed")
        require(summary["current_version"] == source["version"],
                f"{component_id}: current version changed")
        require(summary["target_version"] == path[-1],
                f"{component_id}: wrong final target")
        expected_disposition = (
            "retire" if path[-1] == "retired"
            else "replace" if component_id == "aria-operations-logs"
            else "upgrade"
        )
        require(summary["disposition"] == expected_disposition,
                f"{component_id}: wrong disposition")

        component_steps = by_component[component_id]
        actual_edges = [
            (item["from_version"], item["target_version"])
            for item in component_steps
        ]
        require(actual_edges == transitions[component_id],
                f"{component_id}: unsupported, skipped, or reordered version hop")
        require([item["order"] for item in component_steps] == summary["step_orders"],
                f"{component_id}: step_orders do not name its steps")
        require(all(item["product"] == source["product"] for item in component_steps),
                f"{component_id}: product name changed in steps")
        for step in component_steps:
            token = transition_token(component_id, step["target_version"])
            wanted_action = actions.get(
                token, "retire" if step["target_version"] == "retired" else "upgrade"
            )
            require(step["action"] == wanted_action,
                    f"{token}: expected action {wanted_action}")

    expected_count = sum(len(items) for items in transitions.values())
    require(len(plan["steps"]) == expected_count, "unexpected migration step count")
    for before, after in compatibility["precedence"]:
        require(before in positions and after in positions,
                f"precedence refers to missing transition {before} or {after}")
        require(positions[before] < positions[after],
                f"ordering prerequisite violated: {before} must precede {after}")
    return summaries, positions


def split_token(token: str) -> tuple[str, str]:
    component_id, separator, version = token.rpartition("@")
    require(bool(separator and component_id and version), f"invalid transition token {token}")
    return component_id, version


def validate_gates(
    plan: dict[str, Any], summaries: dict[str, dict[str, Any]],
    compatibility: dict[str, Any]
) -> None:
    gates = {item["gate_id"]: item for item in plan["gates"]}
    require(len(gates) == len(plan["gates"]), "duplicate gate ids")
    predecessors: dict[str, set[str]] = {}
    for before, after in compatibility["precedence"]:
        predecessors.setdefault(after, set()).add(before)

    component_gate_ids: dict[str, list[str]] = {name: [] for name in summaries}
    for step in plan["steps"]:
        require(len(step["gate_ids"]) == len(set(step["gate_ids"])),
                f"step {step['order']}: duplicate gate reference")
        require(all(gate_id in gates for gate_id in step["gate_ids"]),
                f"step {step['order']}: unknown gate reference")
        component_gate_ids[step["component_id"]].extend(step["gate_ids"])
        conditions = {
            (condition["component_id"], condition["version"])
            for gate_id in step["gate_ids"]
            for condition in gates[gate_id]["conditions"]
        }
        own_source = (step["component_id"], step["from_version"])
        require(own_source in conditions,
                f"step {step['order']}: gate does not assert its source version")
        token = transition_token(step["component_id"], step["target_version"])
        for predecessor in predecessors.get(token, set()):
            required_condition = split_token(predecessor)
            require(required_condition in conditions,
                    f"{token}: gate omits prerequisite {predecessor}")

    referenced = {
        gate_id for step in plan["steps"] for gate_id in step["gate_ids"]
    }
    require(referenced == set(gates), "all gates must be used by migration steps")
    for component_id, summary in summaries.items():
        expected = list(dict.fromkeys(component_gate_ids[component_id]))
        require(summary["gate_ids"] == expected,
                f"{component_id}: gate summary does not match its steps")


def matching_tier(demand: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    for tier in rule["tiers"]:
        if all(demand.get(name, math.inf) <= limit
               for name, limit in tier["max_demand"].items()):
            return tier
    raise VerificationError(f"demand exceeds pinned sizing tiers for {rule['product']}")


def expected_storage(
    product_key: str, demand: dict[str, Any], rule: dict[str, Any], tier: dict[str, Any]
) -> float:
    base = float(tier["storage_tib_per_node"])
    if product_key != "operations_logs":
        return base
    retained = (
        float(demand["daily_ingest_gib"])
        * float(demand["retention_days"])
        * float(rule["retention_overhead_factor"])
        / 1024.0
        / int(tier["node_count"])
    )
    rounded = math.ceil(retained * 10.0) / 10.0
    return max(base, rounded)


def validate_placements(
    plan: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    rules = compatibility["placement_rules"]
    placements = {item["component_id"]: item for item in plan["placements"]}
    expected_ids = {rule["component_id"] for rule in rules.values()}
    require(len(placements) == len(plan["placements"]), "duplicate placements")
    require(set(placements) == expected_ids,
            "placements must cover Operations, Automation, and Operations for Logs")

    management_domain = next(
        item for item in inventory["domains"] if item["kind"] == "management"
    )
    clusters = {item["cluster_id"]: item for item in inventory["clusters"]}
    totals = {"vcpu": 0.0, "memory_gib": 0.0, "storage_tib": 0.0}
    for product_key, rule in rules.items():
        item = placements[rule["component_id"]]
        demand = inventory["demand"][product_key]
        tier = matching_tier(demand, rule)
        require(item["product"] == rule["product"],
                f"{product_key}: wrong placement product")
        require(item["domain_id"] == management_domain["domain_id"],
                f"{product_key}: must be placed in the management domain")
        require(item["cluster_id"] in management_domain["cluster_ids"],
                f"{product_key}: must use a management-domain cluster")
        require(item["deployment_model"] == rule["deployment_model"],
                f"{product_key}: wrong deployment model")
        require(item["size"] == tier["size"],
                f"{product_key}: expected pinned size {tier['size']}")
        require(item["demand"] == demand, f"{product_key}: demand was altered")

        resources = item["resources"]
        storage_per_node = expected_storage(product_key, demand, rule, tier)
        expected = {
            "node_count": tier["node_count"],
            "vcpu_per_node": tier["vcpu_per_node"],
            "memory_gib_per_node": tier["memory_gib_per_node"],
            "storage_tib_per_node": storage_per_node,
            "total_vcpu": tier["node_count"] * tier["vcpu_per_node"],
            "total_memory_gib": tier["node_count"] * tier["memory_gib_per_node"],
            "total_storage_tib": round(tier["node_count"] * storage_per_node, 3),
        }
        require(resources == expected, f"{product_key}: resource sizing differs from snapshot")
        totals["vcpu"] += resources["total_vcpu"]
        totals["memory_gib"] += resources["total_memory_gib"]
        totals["storage_tib"] += resources["total_storage_tib"]

    cluster = clusters[next(iter(management_domain["cluster_ids"]))]
    available = {
        name: cluster["capacity"][name] - cluster["reserved"][name]
        for name in totals
    }
    for name, used in totals.items():
        require(used <= available[name],
                f"management placement exceeds available {name} capacity")
    return placements


def validate_target_combination(
    summaries: dict[str, dict[str, Any]], inventory: dict[str, Any],
    compatibility: dict[str, Any]
) -> None:
    by_product: dict[str, set[str]] = {}
    for component in inventory["components"]:
        by_product.setdefault(component["product_key"], set()).add(
            summaries[component["component_id"]]["target_version"]
        )
    require(all(len(versions) == 1 for versions in by_product.values()),
            "components of one product do not share a target version")
    combination = {key: next(iter(versions)) for key, versions in by_product.items()}
    require(combination in compatibility["target_combinations"],
            "final product combination is not pinned as interoperable")


def validate_target_spec(
    spec: dict[str, Any], inventory: dict[str, Any],
    placements: dict[str, dict[str, Any]]
) -> None:
    management_domain = next(
        item for item in inventory["domains"] if item["kind"] == "management"
    )
    components = {item["component_id"]: item for item in inventory["components"]}
    endpoints = inventory["service_endpoints"]
    require(spec.get("sddcId") == management_domain["domain_id"],
            "target_sddc_spec has wrong management-domain id")
    require(spec.get("workflowType") == "VCF", "target_sddc_spec workflowType must be VCF")
    require(spec.get("version") == inventory["target_release"],
            "target_sddc_spec has wrong target version")
    require(spec.get("dnsSpec") == {
        "subdomain": inventory["site"]["dns_domain"],
        "nameservers": inventory["site"]["dns_servers"],
    }, "target_sddc_spec DNS differs from inventory")
    require(spec.get("ntpServers") == inventory["site"]["ntp_servers"],
            "target_sddc_spec NTP differs from inventory")

    expected_networks = [
        {
            "networkType": item["network_type"],
            "vlanId": item["vlan_id"],
            "subnet": item["subnet"],
            "gateway": item["gateway"],
            "subnetMask": item["subnet_mask"],
            "mtu": item["mtu"],
        }
        for item in inventory["management_networks"]
    ]
    require(spec.get("networkSpecs") == expected_networks,
            "target_sddc_spec networks differ from inventory")

    vcenter = spec.get("vcenterSpec", {})
    source_vcenter = components["mgmt-vcenter"]
    require(vcenter.get("vcenterHostname") == source_vcenter["fqdn"],
            "target_sddc_spec has wrong management vCenter")
    require(vcenter.get("version") == inventory["target_release"],
            "target_sddc_spec has wrong vCenter target")
    require(vcenter.get("useExistingDeployment") is True,
            "brownfield target must mark vCenter as existing")
    require(vcenter.get("sslThumbprint") == source_vcenter["ssl_thumbprint"],
            "target_sddc_spec has wrong vCenter thumbprint")
    require(vcenter.get("rootVcenterPassword") == "${VC_PASS}",
            "target_sddc_spec must use the required credential placeholder")

    operations = spec.get("vcfOperationsSpec", {})
    ops_placement = placements["aria-operations"]
    require(operations.get("applianceSize") == ops_placement["size"],
            "SddcSpec Operations size differs from placement")
    require(operations.get("version") == inventory["target_release"],
            "SddcSpec Operations version differs from target")
    require(operations.get("loadBalancerFqdn") == endpoints["operations_load_balancer"],
            "SddcSpec Operations load balancer differs from inventory")
    nodes = operations.get("nodes", [])
    require([item.get("hostname") for item in nodes] == endpoints["operations_nodes"],
            "SddcSpec Operations nodes differ from inventory")
    require([item.get("type") for item in nodes] == ["master", "replica", "data"],
            "SddcSpec Operations node roles must be master, replica, data")
    require(len(nodes) == ops_placement["resources"]["node_count"],
            "SddcSpec Operations node count differs from placement")
    require(operations.get("useExistingDeployment") is True,
            "brownfield target must mark Operations as existing")

    automation = spec.get("vcfAutomationSpec", {})
    auto_placement = placements["aria-automation"]
    expected_automation = {
        "hostname": endpoints["automation_hostname"],
        "platformFqdn": endpoints["automation_platform_fqdn"],
        "ipPool": endpoints["automation_ip_pool"],
        "internalClusterCidr": endpoints["automation_internal_cluster_cidr"],
        "nodePrefix": endpoints["automation_node_prefix"],
        "size": auto_placement["size"],
        "version": inventory["target_release"],
        "useExistingDeployment": True,
    }
    require(automation == expected_automation,
            "SddcSpec Automation settings differ from inventory or placement")


def validate_no_embedded_credentials(plan: dict[str, Any]) -> None:
    """Reject credential-bearing fields except the installer-required placeholder."""
    sensitive_name = re.compile(
        r"(?:password|passwd|secret|credential|private[_-]?key|access[_-]?token)",
        re.IGNORECASE,
    )
    allowed_path = (
        "target_sddc_spec", "vcenterSpec", "rootVcenterPassword"
    )

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for raw_name, child in value.items():
                name = str(raw_name)
                child_path = (*path, name)
                if sensitive_name.search(name):
                    require(
                        child_path == allowed_path and child == "${VC_PASS}",
                        f"credential-bearing field is not allowed at {'.'.join(child_path)}",
                    )
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))

    walk(plan, ())


def validate_research_record() -> None:
    try:
        text = RESEARCH.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise VerificationError("missing required file: research.md") from error
    require(text.strip(), "research.md is empty")

    label_prefix = r"^\s*(?:[-*]\s*)?"
    titles = re.findall(
        label_prefix + r"(?:page\s+)?title\s*:\s*(\S.*)$", text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    urls = re.findall(
        label_prefix + r"url\s*:\s*(https?://\S+)\s*$", text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    dates = re.findall(
        label_prefix + r"access(?:ed)?\s+date\s*:\s*(\S+)\s*$", text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    decisions = re.findall(
        label_prefix + r"(?:architecture\s+)?decision\s*:\s*(\S.*)$", text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    source_count = len(urls)
    require(source_count >= 2, "research.md must record at least two web sources")
    require(
        len(titles) == len(dates) == len(decisions) == source_count,
        "each research source needs a page title, URL, access date, and architecture decision",
    )
    require(len(set(urls)) == source_count, "research.md contains duplicate source URLs")
    for url in urls:
        match = re.match(r"https?://([^/:?#]+)", url, flags=re.IGNORECASE)
        host = match.group(1).lower() if match else ""
        require(
            host == "broadcom.com" or host.endswith(".broadcom.com"),
            f"research source is not a Broadcom-published page: {url}",
        )
        require(".invalid" not in host and host not in {"localhost", "127.0.0.1"},
                f"research source is not a real web URL: {url}")
    for raw_date in dates:
        try:
            dt.date.fromisoformat(raw_date)
        except ValueError as error:
            raise VerificationError(
                f"research access date is not valid ISO YYYY-MM-DD: {raw_date}"
            ) from error

    lowered = text.lower()
    for topic in ("compatib", "interoperab", "upgrade", "siz"):
        require(topic in lowered, f"research.md does not cover the {topic} research topic")
    require("compatibility-snapshot.json" not in "\n".join(urls),
            "the local compatibility snapshot cannot be cited as web research")


def validate_stdlib_package() -> None:
    package = ROOT / "vcf_arch"
    require(package.is_dir() and (package / "__main__.py").is_file(),
            "vcf_arch package or command entry point is missing")
    allowed = set(sys.stdlib_module_names) | {"vcf_arch", "__future__"}
    for path in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise VerificationError(f"invalid Python in {path.relative_to(ROOT)}: {error}") from error
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [item.name.split(".", 1)[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".", 1)[0]]
            for name in names:
                require(name in allowed,
                        f"non-stdlib import {name!r} in {path.relative_to(ROOT)}")


def validate_compiler_output(plan: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as raw:
        work = Path(raw)
        shutil.copytree(ROOT / "vcf_arch", work / "vcf_arch")
        shutil.copytree(ROOT / "fixtures", work / "fixtures")
        shutil.copytree(ROOT / "compatibility", work / "compatibility")
        outputs = [work / "first.json", work / "second.json"]
        for output in outputs:
            command = [
                sys.executable,
                "-m",
                "vcf_arch",
                "build",
                "--inventory",
                "fixtures/estate.json",
                "--compatibility",
                "compatibility/compatibility-snapshot.json",
                "--output",
                output.name,
            ]
            completed = subprocess.run(
                command,
                cwd=work,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=20,
                check=False,
            )
            require(completed.returncode == 0,
                    f"compiler command failed ({completed.returncode}): {completed.stdout[-1000:]}")
            require(output.is_file(), "compiler did not create its requested output")
        require(outputs[0].read_bytes() == outputs[1].read_bytes(),
                "compiler output is not byte-for-byte deterministic")
        generated = load_json(outputs[0])
        require(generated == plan, "checked-in artifact differs from compiler output")


def verify() -> None:
    # Parsing is necessary to locate SddcSpec; no other artifact assertion occurs first.
    plan = load_json(ARTIFACT)
    installer = load_json(INSTALLER_SPEC)
    validate_installer_schema_first(plan, installer)
    print("PASS: target_sddc_spec validates against installer SddcSpec")

    schema = load_json(PLAN_SCHEMA)
    JsonSchemaSubset(schema).validate(plan, schema)
    print("PASS: migration plan validates against the fixed plan schema")

    inventory = load_json(INVENTORY)
    compatibility = load_json(COMPATIBILITY)
    require(plan["estate_id"] == inventory["estate_id"], "wrong estate_id")
    require(plan["target_release"] == inventory["target_release"], "wrong target release")
    require(inventory["target_release"] == compatibility["target_release"],
            "fixture and compatibility target releases differ")

    validate_domains(plan, inventory)
    summaries, _positions = validate_components_and_steps(plan, inventory, compatibility)
    validate_gates(plan, summaries, compatibility)
    placements = validate_placements(plan, inventory, compatibility)
    validate_target_combination(summaries, inventory, compatibility)
    validate_target_spec(plan["target_sddc_spec"], inventory, placements)
    validate_no_embedded_credentials(plan)
    validate_research_record()
    validate_stdlib_package()
    validate_compiler_output(plan)
    print("PASS: artifact, research, hops, gates, placement, sizing, and compiler agree")


def main() -> int:
    try:
        verify()
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
