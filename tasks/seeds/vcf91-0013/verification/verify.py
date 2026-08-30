"""Protected verification for the VCF 9.1 backup Task client."""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mock_sddc import CONTRACT, MockSddcManager

TOKEN = "fixture-token-vcf91"
TASK_ID = "2ef73532-e8b0-4f84-a54b-38dce41fb994"
CREATED = "2026-07-28T15:20:00.000Z"


def task(status: str, **extra) -> dict:
    value = {
        "id": TASK_ID,
        "name": "Update backup configuration",
        "type": "BACKUP_CONFIGURATION_UPDATE",
        "status": status,
        "creationTimestamp": CREATED,
        "subTasks": [],
        "errors": [],
    }
    value.update(extra)
    return value


def make_location():
    from vcf_backup import BackupLocation

    return BackupLocation(
        server="backup01.example.test",
        port=22,
        protocol="SFTP",
        username="svc-vcf-backup",
        directory_path="/exports/vcf",
    )


def make_patch():
    from vcf_backup import BackupConfigurationPatch

    return BackupConfigurationPatch(backup_locations=[make_location()])


class ContractAndMockTests(unittest.TestCase):
    def test_contract_is_pinned_to_the_spec_and_exact_operation_ids(self):
        root = Path(__file__).resolve().parent
        sources = json.loads(
            (root / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        sha = "c3f3b52c845dd967cabbc21680e893292077d5ba"
        spec_path = "specifications/sddc-manager/sddc-manager-openapi.json"
        expected = {"updateBackupConfiguration", "getTask"}

        self.assertEqual(CONTRACT["version"], "9.1.0.0")
        self.assertEqual(CONTRACT["source"]["repository_commit_sha"], sha)
        self.assertEqual(CONTRACT["source"]["spec_path"], spec_path)
        self.assertEqual(set(CONTRACT["operations"]), expected)
        self.assertEqual(sources["repository_commit_sha"], sha)
        self.assertEqual(sources["spec_path"], spec_path)
        self.assertEqual(
            {item["operationId"] for item in sources["operations"]}, expected
        )
        for item in sources["operations"]:
            self.assertEqual(item["repository_commit_sha"], sha)
            self.assertEqual(item["spec_path"], spec_path)
        serialized = json.dumps(sources)
        self.assertNotIn("developer.broadcom.com", serialized)

    def test_mock_only_routes_contract_operations(self):
        with MockSddcManager(TOKEN) as server:
            self.assertEqual(
                server.operation_ids, frozenset(CONTRACT["operations"])
            )
            request = urllib.request.Request(
                server.base_url + "/v1/system/backup-configuration/uncontracted",
                data=b"{}",
                method="PATCH",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 404)
            self.assertIsNone(server.request_log[0]["operationId"])


class WireAndLifecycleTests(unittest.TestCase):
    def _client(self, server, sleeps, *, max_polls=4):
        from vcf_backup import SddcManagerClient

        return SddcManagerClient(
            server.base_url,
            TOKEN,
            sleep=sleeps.append,
            poll_interval=0.125,
            max_polls=max_polls,
            timeout=2,
        )

    def test_exact_patch_wire_shape_and_poll_to_success(self):
        with MockSddcManager(TOKEN) as server:
            server.script(
                "updateBackupConfiguration", [(202, task("PENDING"))]
            )
            server.script(
                "getTask",
                [
                    (200, task("IN_PROGRESS")),
                    (
                        200,
                        task(
                            "SUCCESSFUL",
                            completionTimestamp="2026-07-28T15:20:03.000Z",
                            resources=[{"resourceId": "sddc-1", "type": "SDDC_MANAGER"}],
                        ),
                    ),
                ],
            )
            sleeps = []
            result = self._client(server, sleeps).update_backup_and_wait(make_patch())

        self.assertEqual(result.id, TASK_ID)
        self.assertEqual(result.status, "SUCCESSFUL")
        self.assertEqual(result.raw["resources"][0]["resourceId"], "sddc-1")
        self.assertEqual(sleeps, [0.125])

        expected_body = {
            "backupLocations": [
                {
                    "server": "backup01.example.test",
                    "port": 22,
                    "protocol": "SFTP",
                    "username": "svc-vcf-backup",
                    "directoryPath": "/exports/vcf",
                }
            ]
        }
        self.assertEqual(
            [
                (
                    item["operationId"],
                    item["method"],
                    item["path"],
                    item["query"],
                    item["json"],
                )
                for item in server.request_log
            ],
            [
                (
                    "updateBackupConfiguration",
                    "PATCH",
                    "/v1/system/backup-configuration",
                    "",
                    expected_body,
                ),
                ("getTask", "GET", f"/v1/tasks/{TASK_ID}", "", None),
                ("getTask", "GET", f"/v1/tasks/{TASK_ID}", "", None),
            ],
        )
        patch_request = server.request_log[0]
        self.assertEqual(
            patch_request["headers"]["authorization"], f"Bearer {TOKEN}"
        )
        self.assertEqual(patch_request["headers"]["accept"], "application/json")
        self.assertEqual(
            patch_request["headers"]["content-type"], "application/json"
        )
        self.assertNotIn("password", patch_request["json"]["backupLocations"][0])
        self.assertNotIn(
            "sshFingerprint", patch_request["json"]["backupLocations"][0]
        )
        self.assertNotIn("encryption", patch_request["json"])
        self.assertNotIn("backupSchedules", patch_request["json"])
        for get_request in server.request_log[1:]:
            self.assertEqual(
                get_request["headers"]["authorization"], f"Bearer {TOKEN}"
            )
            self.assertEqual(get_request["headers"]["accept"], "application/json")
            self.assertNotIn("content-type", get_request["headers"])
            self.assertEqual(get_request["body"], b"")

    def test_none_is_omitted_but_caller_provided_empty_list_is_preserved(self):
        from vcf_backup import BackupConfigurationPatch, BackupLocation

        location = BackupLocation(
            "backup.example.test",
            22,
            "SFTP",
            "backup-user",
            "/backups",
            password=None,
            ssh_fingerprint=None,
        )
        self.assertEqual(
            location.to_wire(),
            {
                "server": "backup.example.test",
                "port": 22,
                "protocol": "SFTP",
                "username": "backup-user",
                "directoryPath": "/backups",
            },
        )
        self.assertEqual(
            BackupConfigurationPatch(backup_schedules=[]).to_wire(),
            {"backupSchedules": []},
        )
        self.assertEqual(
            BackupConfigurationPatch(
                backup_schedules=[
                    {
                        "resourceType": "SDDC_MANAGER",
                        "frequency": "HOURLY",
                        "daysOfWeek": None,
                        "hourOfDay": 3,
                    }
                ]
            ).to_wire(),
            {
                "backupSchedules": [
                    {
                        "resourceType": "SDDC_MANAGER",
                        "frequency": "HOURLY",
                        "hourOfDay": 3,
                    }
                ]
            },
        )
        with self.assertRaises(ValueError):
            BackupConfigurationPatch().to_wire()

    def test_acceptance_body_is_never_assumed_complete(self):
        with MockSddcManager(TOKEN) as server:
            server.script(
                "updateBackupConfiguration", [(202, task("SUCCESSFUL"))]
            )
            server.script("getTask", [(200, task("SUCCESSFUL"))])
            result = self._client(server, []).update_backup_and_wait(make_patch())

        self.assertEqual(result.status, "SUCCESSFUL")
        self.assertEqual(
            [item["operationId"] for item in server.request_log],
            ["updateBackupConfiguration", "getTask"],
        )

    def test_failed_terminal_task_is_typed_and_preserved(self):
        from vcf_backup import TaskFailedError

        error = {
            "errorCode": "BACKUP_TARGET_UNREACHABLE",
            "message": "Backup target could not be reached",
            "remediationMessage": "Check DNS and firewall policy",
            "referenceToken": "REF-91-A7",
            "nestedErrors": [
                {"errorCode": "DNS_LOOKUP_FAILED", "message": "name not found"}
            ],
        }
        with MockSddcManager(TOKEN) as server:
            server.script(
                "updateBackupConfiguration", [(202, task("PENDING"))]
            )
            server.script(
                "getTask",
                [
                    (
                        200,
                        task(
                            "FAILED",
                            errors=[error],
                            completionTimestamp="2026-07-28T15:21:00.000Z",
                        ),
                    )
                ],
            )
            with self.assertRaises(TaskFailedError) as caught:
                self._client(server, []).update_backup_and_wait(make_patch())

        self.assertEqual(caught.exception.task.status, "FAILED")
        self.assertEqual(
            caught.exception.task.raw["errors"][0]["nestedErrors"][0]["errorCode"],
            "DNS_LOOKUP_FAILED",
        )

    def test_poll_budget_is_exact_and_uses_injected_sleep(self):
        from vcf_backup import TaskTimeoutError

        with MockSddcManager(TOKEN) as server:
            server.script(
                "updateBackupConfiguration", [(202, task("PENDING"))]
            )
            server.script(
                "getTask",
                [
                    (200, task("PENDING")),
                    (200, task("QUEUED")),
                    (200, task("IN_PROGRESS")),
                ],
            )
            sleeps = []
            with self.assertRaises(TaskTimeoutError) as caught:
                self._client(server, sleeps, max_polls=3).update_backup_and_wait(
                    make_patch()
                )

        self.assertEqual(caught.exception.task.status, "IN_PROGRESS")
        self.assertEqual(sleeps, [0.125, 0.125])
        self.assertEqual(
            [item["operationId"] for item in server.request_log].count("getTask"), 3
        )

    def test_api_error_envelope_and_unknown_status(self):
        from vcf_backup import ProtocolError, VcfApiError

        envelope = {
            "errorCode": "BACKUP_CONFIGURATION_INVALID",
            "message": "Backup configuration is invalid",
            "remediationMessage": "Correct the target settings",
            "referenceToken": "REF-91-B2",
            "nestedErrors": [{"errorCode": "PORT_INVALID", "message": "bad port"}],
        }
        with MockSddcManager(TOKEN) as server:
            server.script("updateBackupConfiguration", [(400, envelope)])
            with self.assertRaises(VcfApiError) as caught:
                self._client(server, []).update_backup_configuration(make_patch())
        exc = caught.exception
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.error_code, "BACKUP_CONFIGURATION_INVALID")
        self.assertEqual(exc.remediation_message, "Correct the target settings")
        self.assertEqual(exc.reference_token, "REF-91-B2")
        self.assertEqual(exc.envelope, envelope)

        with MockSddcManager(TOKEN) as server:
            server.script(
                "updateBackupConfiguration", [(202, task("PENDING"))]
            )
            server.script("getTask", [(200, task("MYSTERY"))])
            with self.assertRaises(ProtocolError):
                self._client(server, []).update_backup_and_wait(make_patch())
            self.assertEqual(len(server.request_log), 2)

    def test_local_validation_happens_before_http(self):
        from vcf_backup import BackupLocation, SddcManagerClient

        with MockSddcManager(TOKEN) as server:
            with self.assertRaises(ValueError):
                BackupLocation("", 22, "SFTP", "user", "/backups").to_wire()
            with self.assertRaises(ValueError):
                BackupLocation("backup", 0, "SFTP", "user", "/backups").to_wire()
            client = SddcManagerClient(server.base_url, TOKEN)
            with self.assertRaises(ValueError):
                client.get_task("")
            self.assertEqual(server.request_log, [])


if __name__ == "__main__":
    unittest.main()
