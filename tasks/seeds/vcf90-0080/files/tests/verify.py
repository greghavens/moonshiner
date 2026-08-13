#!/usr/bin/env python3
import json
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
OPERATION_IDS = ["createMaintenanceSchedules", "updateMaintenanceSchedules"]


def verify_fixtures() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))

    assert contract["source"]["tag"] == "9.0.0.0"
    assert contract["source"]["commit"] == COMMIT
    assert contract["source"]["path"] == SPEC_PATH
    assert sources["tag"] == "9.0.0.0"
    assert sources["commit"] == COMMIT
    assert sources["specification_path"] == SPEC_PATH
    assert contract["source"]["license"] == "Apache-2.0"
    assert sources["license"] == "Apache-2.0"
    assert contract["basePath"] == "/suite-api"
    assert contract["security"] == {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
    }
    assert [item["operationId"] for item in contract["operations"]] == OPERATION_IDS
    assert [item["operationId"] for item in sources["operations"]] == OPERATION_IDS
    assert {(item["method"], item["path"]) for item in contract["operations"]} == {
        ("POST", "/api/maintenanceschedules"),
        ("PUT", "/api/maintenanceschedules"),
    }
    assert [set(item["responses"]) for item in contract["operations"]] == [
        {"201", "400", "422"},
        {"200", "404"},
    ]
    assert set(contract["schemas"]["maintenance-schedule"]["required"]) == {
        "key",
        "schedule",
    }
    schedule = contract["schemas"]["schedule"]
    assert set(schedule["required"]) == {
        "duration",
        "hour",
        "minuteOfTheHour",
        "scheduleType",
    }
    assert {
        "recurrence",
        "dayOfTheMonth",
        "daysOfTheMonth",
        "weeksOfTheMonth",
        "daysOfTheWeek",
        "month",
        "months",
        "startDate",
        "expirationDate",
        "timeZone",
        "expireRuns",
    }.issubset(schedule["properties"])
    assert "9.1" not in json.dumps(contract)
    assert "9.1" not in json.dumps(sources)


def compile_and_run() -> None:
    sources = [
        ROOT / "VcfOperationsClient.java",
        ROOT / "ContractPinnedMock.java",
        ROOT / "TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf90-0080-") as output_dir:
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-classpath",
                output_dir,
                "-sourcepath",
                output_dir,
                "-d",
                output_dir,
                *map(str, sources),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if compile_result.returncode != 0:
            raise AssertionError(
                "javac failed\n" + compile_result.stdout + compile_result.stderr
            )

        run_result = subprocess.run(
            ["java", "-cp", output_dir, "TestMain", str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if run_result.returncode != 0:
            raise AssertionError(
                "TestMain failed\n" + run_result.stdout + run_result.stderr
            )
        expected = "PASS: contract, wire shape, omission, and partial-failure report"
        assert run_result.stdout.strip() == expected, run_result.stdout
        assert run_result.stderr == "", run_result.stderr


if __name__ == "__main__":
    verify_fixtures()
    compile_and_run()
    print("verification passed")
