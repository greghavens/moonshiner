#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OPERATIONS = {
    "exchangeRefreshToken": ("POST", "/oidc/oauth2/token"),
    "getProjects": ("GET", "/iaas/api/projects"),
    "getCatalogItems": ("GET", "/catalog/api/items"),
    "requestCatalogItemInstances": ("POST", "/catalog/api/items/{id}/request"),
}
EXPECTED_SOURCES = {
    "exchangeRefreshToken": (
        "POST Get Access Token Pkce Flow",
        "https://developer.broadcom.com/xapis/vm-apps-org-identity/latest/csp/gateway/am/api/auth/token/post/",
    ),
    "getProjects": (
        "GET Get Projects",
        "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/latest/iaas/api/projects/get/",
    ),
    "getCatalogItems": (
        "GET Get Catalog Items",
        "https://developer.broadcom.com/xapis/vm-apps-org-catalog/latest/catalog/api/items/get/",
    ),
    "requestCatalogItemInstances": (
        "POST Request Catalog Item Instances",
        "https://developer.broadcom.com/xapis/vm-apps-org-catalog/latest/catalog/api/items/id/request/post/",
    ),
}


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    assert contract["product"] == "VMware Cloud Foundation Automation"
    assert contract["targetRelease"] == "9.1"
    assert contract["repositoryContext"] == {
        "repository": "vmware/vcf-api-specs",
        "license": "Apache-2.0",
        "statement": "The repository does not publish a VCF Automation specification for this API surface.",
    }
    assert contract["provenance"]["kind"] == "reference-documentation"
    statement = contract["provenance"]["statement"].lower()
    assert "reference documentation rather than a published specification" in statement
    actual = {
        operation["name"]: (operation["method"], operation["path"])
        for operation in contract["operations"]
    }
    assert actual == EXPECTED_OPERATIONS, f"contract operations changed: {actual!r}"

    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    assert sources["product"] == contract["product"]
    assert sources["targetRelease"] == contract["targetRelease"]
    assert sources["fetchedOn"] == "2026-08-16"
    actual_sources = {
        source["contractOperation"]: (source["operation"], source["url"])
        for source in sources["sources"]
    }
    assert actual_sources == EXPECTED_SOURCES, f"official source ledger changed: {actual_sources!r}"
    assert all(source["fetchedOn"] == sources["fetchedOn"] for source in sources["sources"])


def verify_layout() -> None:
    production_java = sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "src").rglob("*.java")
    )
    assert production_java == ["src/VcfAutomationClient.java"], (
        "the deliverable must remain a single-file Java client; found " + repr(production_java)
    )


def compile_and_run() -> None:
    with tempfile.TemporaryDirectory(prefix="vcf91-0348-") as classes:
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "--add-modules",
                "jdk.httpserver",
                "-d",
                classes,
                str(ROOT / "src" / "VcfAutomationClient.java"),
                str(ROOT / ".moonshiner" / "MockVcfServer.java"),
                str(ROOT / ".moonshiner" / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if compile_result.returncode != 0:
            raise SystemExit(
                "Java compilation failed:\n" + compile_result.stdout + compile_result.stderr
            )
        run_result = subprocess.run(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-cp",
                classes,
                "TestMain",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if run_result.returncode != 0:
            raise SystemExit(
                "Verification failed:\n" + run_result.stdout + run_result.stderr
            )
        print(run_result.stdout.strip())


if __name__ == "__main__":
    verify_contract()
    verify_layout()
    compile_and_run()
