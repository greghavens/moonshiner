#!/usr/bin/env python3
"""Deterministic offline acceptance checks for the release review."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from broker.service import (  # noqa: E402
    AuditLog,
    Device,
    Forbidden,
    MemoryRepository,
    NotFound,
    Package,
    PackageService,
    RequestContext,
)


def production_config() -> dict[str, object]:
    return json.loads((ROOT / "config/production.json").read_text())


def make_service(
    config: dict[str, object] | None = None,
) -> tuple[PackageService, AuditLog]:
    repository = MemoryRepository(
        packages=(
            Package("pkg-owner", "acme", "Alice", b"owner bytes"),
            Package("pkg-other", "acme", "Bob", b"other bytes"),
            Package("pkg-foreign", "other", "Mallory", b"foreign bytes"),
        ),
        devices=(
            Device("dev-own", "acme", "ready"),
            Device("dev-prefix", "acme-labs", "paused"),
        ),
    )
    audit = AuditLog()
    return PackageService(repository, audit, config or production_config()), audit


class ServiceBehaviorTests(unittest.TestCase):
    def test_owner_receives_package(self) -> None:
        service, audit = make_service()
        context = RequestContext("Alice", "acme")
        self.assertEqual(service.get_package(context, "pkg-owner"), b"owner bytes")
        self.assertEqual(audit.events, [])

    def test_same_tenant_non_owner_is_concealed_and_audited(self) -> None:
        service, audit = make_service()
        context = RequestContext("Alice", "acme")
        with self.assertRaises(NotFound):
            service.get_package(context, "pkg-other")
        self.assertEqual(
            audit.events,
            [
                {
                    "event": "access_denied",
                    "reason": "owner_mismatch",
                    "actor_id": "Alice",
                    "tenant_id": "acme",
                    "resource_id": "pkg-other",
                }
            ],
        )

    def test_owner_identity_is_exact_and_case_sensitive(self) -> None:
        service, audit = make_service()
        with self.assertRaises(NotFound):
            service.get_package(RequestContext("alice", "acme"), "pkg-owner")
        self.assertEqual(audit.events[0]["reason"], "owner_mismatch")

    def test_unknown_and_cross_tenant_contracts_remain_distinct(self) -> None:
        service, audit = make_service()
        context = RequestContext("Alice", "acme")
        with self.assertRaises(NotFound):
            service.get_package(context, "missing")
        self.assertEqual(audit.events, [])
        with self.assertRaises(Forbidden):
            service.get_package(context, "pkg-foreign")
        self.assertEqual(audit.events[0]["reason"], "tenant_mismatch")

    def test_auditing_obeys_explicit_boolean_policy(self) -> None:
        service, audit = make_service(
            {
                "audit_denials": False,
                "deployment": "test",
                "session_cookie_secure": True,
            }
        )
        with self.assertRaises(NotFound):
            service.get_package(RequestContext("Alice", "acme"), "pkg-other")
        self.assertEqual(audit.events, [])

    def test_device_tenant_check_is_exact(self) -> None:
        service, audit = make_service()
        context = RequestContext("Alice", "acme")
        self.assertEqual(service.get_device_status(context, "dev-own"), "ready")
        with self.assertRaises(Forbidden):
            service.get_device_status(context, "dev-prefix")
        self.assertEqual(audit.events[-1]["reason"], "tenant_mismatch")

    def test_production_policy_restored_without_other_changes(self) -> None:
        current = production_config()
        baseline = json.loads(
            (ROOT / "baselines/production.json").read_text()
        )
        self.assertEqual(current, baseline)
        self.assertIs(current["audit_denials"], True)


class EvidenceTests(unittest.TestCase):
    def test_helper_is_elf_without_a_system_import(self) -> None:
        helper = ROOT / "artifacts/metadata-helper"
        self.assertEqual(helper.read_bytes()[:4], b"\x7fELF")
        output = subprocess.run(
            ["readelf", "-Ws", os.fspath(helper)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        imported_names = {
            line.split()[-1].split("@", 1)[0]
            for line in output.splitlines()
            if " UND " in line and line.split()
        }
        self.assertNotIn("system", imported_names)

    def test_runtime_manifest_excludes_developer_inputs(self) -> None:
        shipped = {
            line.strip()
            for line in (ROOT / "packaging/release-files.txt")
            .read_text()
            .splitlines()
            if line.strip()
        }
        self.assertIn("artifacts/metadata-helper", shipped)
        self.assertIn("requirements-prod.lock", shipped)
        self.assertNotIn("requirements-dev.lock", shipped)
        self.assertNotIn("tools/trace_converter.py", shipped)


class TriageReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads((ROOT / "triage.json").read_text())
        cls.items = {item["id"]: item for item in cls.report["findings"]}

    def test_top_level_contract_and_order(self) -> None:
        self.assertEqual(
            set(self.report),
            {"schema_version", "release", "incident_summary", "findings"},
        )
        self.assertEqual(self.report["schema_version"], 1)
        self.assertEqual(self.report["release"], "broker-2026.07.2")
        self.assertIsInstance(self.report["incident_summary"], str)
        self.assertTrue(self.report["incident_summary"].strip())
        ids = [item["id"] for item in self.report["findings"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(
            set(ids),
            {"ST-104", "SEM-207", "BIN-311", "DIFF-408", "CFG-522", "DEP-619"},
        )

    def test_finding_schema_and_evidence_paths(self) -> None:
        expected_keys = {
            "id",
            "kind",
            "disposition",
            "reachability",
            "preconditions",
            "impact",
            "confidence",
            "evidence",
            "rationale",
            "remediation",
        }
        for item in self.report["findings"]:
            self.assertEqual(set(item), expected_keys, item["id"])
            self.assertIn(
                item["kind"],
                {"static", "semantic", "binary", "diff", "config", "dependency"},
            )
            self.assertIn(
                item["disposition"], {"repair", "false_positive", "defer"}
            )
            self.assertIn(
                item["reachability"], {"reachable", "conditional", "unreachable"}
            )
            self.assertIn(item["impact"], {"high", "medium", "low", "none"})
            self.assertIn(item["confidence"], {"high", "medium", "low"})
            self.assertIsInstance(item["preconditions"], list)
            self.assertTrue(item["preconditions"])
            self.assertTrue(
                all(isinstance(value, str) and value.strip()
                    for value in item["preconditions"])
            )
            self.assertIsInstance(item["evidence"], list)
            self.assertTrue(item["evidence"])
            for value in item["evidence"]:
                path = PurePosixPath(value)
                self.assertFalse(path.is_absolute(), value)
                self.assertNotIn("..", path.parts, value)
                self.assertTrue((ROOT / path).is_file(), value)
                self.assertNotEqual(path.parts[0], "tests", value)
            self.assertTrue(item["rationale"].strip())
            self.assertTrue(item["remediation"].strip())

    def test_supported_repairs_are_classified_from_production_effect(self) -> None:
        static = self.items["ST-104"]
        self.assertEqual(
            (
                static["kind"],
                static["disposition"],
                static["reachability"],
                static["impact"],
                static["confidence"],
            ),
            ("static", "repair", "reachable", "high", "high"),
        )
        self.assertIn("broker/service.py", static["evidence"])

        config = self.items["CFG-522"]
        self.assertEqual(
            (
                config["kind"],
                config["disposition"],
                config["reachability"],
                config["impact"],
                config["confidence"],
            ),
            ("config", "repair", "reachable", "medium", "high"),
        )
        self.assertIn("config/production.json", config["evidence"])
        self.assertIn("baselines/production.json", config["evidence"])

    def test_refuted_claims_are_not_promoted_by_scanner_severity(self) -> None:
        expected = {
            "SEM-207": "semantic",
            "BIN-311": "binary",
            "DEP-619": "dependency",
        }
        for finding_id, kind in expected.items():
            item = self.items[finding_id]
            self.assertEqual(
                (
                    item["kind"],
                    item["disposition"],
                    item["reachability"],
                    item["impact"],
                    item["confidence"],
                ),
                (kind, "false_positive", "unreachable", "none", "high"),
            )

        self.assertIn(
            "artifacts/metadata-helper",
            self.items["BIN-311"]["evidence"],
        )
        self.assertIn(
            "requirements-prod.lock",
            self.items["DEP-619"]["evidence"],
        )
        self.assertIn(
            "requirements-dev.lock",
            self.items["DEP-619"]["evidence"],
        )

    def test_real_nonproduction_diff_is_deferred(self) -> None:
        item = self.items["DIFF-408"]
        self.assertEqual(
            (
                item["kind"],
                item["disposition"],
                item["reachability"],
                item["impact"],
                item["confidence"],
            ),
            ("diff", "defer", "unreachable", "low", "high"),
        )
        self.assertIn("tools/trace_converter.py", item["evidence"])
        self.assertIn("packaging/release-files.txt", item["evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
