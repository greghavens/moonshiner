#!/usr/bin/env python3
"""Protected, deterministic verifier for the single-file Java client."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_SHA256 = "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef"
EXPECTED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_ID = "updateDepotSettings"
EXPECTED_PATH = "/v1/system/settings/depot"


def check_fixture_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )

    assert contract["openapi"] == "3.0.1"
    assert contract["info"]["version"] == "9.0.0.0"
    assert set(contract["paths"]) == {EXPECTED_PATH}, "mock contract must name one path"
    assert set(contract["paths"][EXPECTED_PATH]) == {"put"}, (
        "mock contract must name only the PUT operation"
    )

    operation = contract["paths"][EXPECTED_PATH]["put"]
    assert operation["operationId"] == EXPECTED_OPERATION_ID
    assert operation["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/DepotSettings"}
            }
        },
        "required": True,
    }
    assert operation["responses"]["202"]["description"] == "Accepted"
    assert operation["responses"]["400"]["description"] == "Bad Request"
    assert operation["responses"]["500"]["description"] == "Internal Server Error"

    schemas = contract["components"]["schemas"]
    assert set(schemas["DepotSettings"]["properties"]) == {
        "vmwareAccount",
        "dellEmcSupportAccount",
        "offlineAccount",
        "depotConfiguration",
    }
    assert "required" not in schemas["DepotSettings"]
    assert set(schemas["DepotAccount"]["properties"]) == {
        "username",
        "password",
        "status",
        "message",
        "downloadToken",
    }
    assert "required" not in schemas["DepotAccount"]
    assert schemas["DepotAccount"]["properties"]["downloadToken"]["maxLength"] == 32
    assert schemas["DepotConfiguration"]["required"] == [
        "hostname",
        "isOfflineDepot",
        "port",
    ]

    assert sources == {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "license": "Apache-2.0",
        "tag": "9.0.0.0",
        "commitSha": EXPECTED_COMMIT,
        "specPath": EXPECTED_SPEC_PATH,
        "specSha256": EXPECTED_SPEC_SHA256,
        "operationIds": [EXPECTED_OPERATION_ID],
    }


def compile_and_run() -> None:
    with tempfile.TemporaryDirectory(prefix="vcf90-0116-") as build_dir:
        subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                build_dir,
                str(ROOT / "VcfInstallerClient.java"),
                str(ROOT / "tests" / "TestMain.java"),
            ],
            check=True,
            cwd=ROOT,
            timeout=20,
        )
        completed = subprocess.run(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-cp",
                build_dir,
                "TestMain",
                str(ROOT / "docs" / "contract.json"),
                str(ROOT / "docs" / "official_sources.json"),
            ],
            check=True,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        print(completed.stdout, end="")


if __name__ == "__main__":
    check_fixture_contract()
    compile_and_run()
