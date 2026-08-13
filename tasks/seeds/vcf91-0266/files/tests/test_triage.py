"""The diagnosis must be correlated from the retrieved logs and events."""

import unittest

from vcfops_triage import Diagnosis, OperationsError, SymptomEvidence, SyncFailure
from vcfops_triage import triage as triage_module

from . import fixtures
from .support import TriageFailed, nominal_run, run_triage


class DiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = nominal_run().diagnosis

    def test_returns_a_diagnosis(self):
        self.assertIsInstance(self.result, Diagnosis)

    def test_identifies_the_incident_alert_not_merely_the_first_alert(self):
        self.assertEqual(self.result.alert_id, fixtures.ALERT_ID_IDENTITY)
        self.assertNotEqual(self.result.alert_id, fixtures.ALERT_ID_DATASTORE)
        self.assertEqual(
            self.result.alert_definition_name,
            "Identity provider group membership is stale",
        )
        self.assertEqual(self.result.alert_level, "IMMEDIATE")
        self.assertEqual(self.result.resource_id, fixtures.RESOURCE_ID_OPS_MANAGER)
        self.assertEqual(
            self.result.alert_start_time_utc, fixtures.FIRST_FAILURE_TIME_STAMP + 120000
        )

    def test_identifies_the_failing_directory_not_merely_the_first_directory(self):
        self.assertEqual(self.result.ldap_directory_id, fixtures.FAILED_DIRECTORY_ID)
        self.assertEqual(self.result.ldap_directory_name, fixtures.FAILED_DIRECTORY_NAME)

    def test_root_cause_is_the_earliest_failure_not_the_newest(self):
        self.assertEqual(
            self.result.root_cause_message_key, fixtures.FIRST_FAILURE_MESSAGE_KEY
        )
        self.assertNotEqual(
            self.result.root_cause_message_key, fixtures.CASCADE_MESSAGE_KEY
        )

    def test_first_failure_carries_detail_only_available_from_the_by_id_call(self):
        expected = SyncFailure(
            sync_log_id=fixtures.FIRST_FAILURE_SYNC_LOG_ID,
            time_stamp=fixtures.FIRST_FAILURE_TIME_STAMP,
            sync_type="SCHEDULED",
            sync_result=fixtures.SYNC_LOGS[10]["syncResult"],
            sync_result_message_key=fixtures.FIRST_FAILURE_MESSAGE_KEY,
            groups_removed=47,
            users_removed=318,
        )
        self.assertEqual(self.result.first_failure, expected)
        self.assertEqual(
            self.result.first_failure.sync_result_message_key,
            self.result.root_cause_message_key,
        )

    def test_contributing_symptoms_are_reported_in_api_order(self):
        self.assertEqual(
            self.result.contributing_symptom_ids,
            (fixtures.SYMPTOM_ID_TOKEN_LATENCY, fixtures.SYMPTOM_ID_GROUP_SYNC),
        )

    def test_symptom_evidence_matches_the_contributing_symptoms(self):
        self.assertEqual(
            tuple(item.symptom_id for item in self.result.symptom_evidence),
            self.result.contributing_symptom_ids,
        )
        for item in self.result.symptom_evidence:
            self.assertIsInstance(item, SymptomEvidence)
            self.assertEqual(item.resource_id, fixtures.RESOURCE_ID_OPS_MANAGER)

    def test_alarm_info_proves_include_alarm_info_was_requested(self):
        evidence = {item.symptom_id: item for item in self.result.symptom_evidence}
        group_sync = evidence[fixtures.SYMPTOM_ID_GROUP_SYNC]
        self.assertIn(fixtures.FAILED_DIRECTORY_NAME, group_sync.message)
        self.assertIn("47 provisioned groups", group_sync.alarm_info)
        self.assertEqual(
            group_sync.symptom_definition_id,
            "SymptomDefinition-VCFOPS-directory-group-sync",
        )
        self.assertEqual(group_sync.start_time_utc, 1774188120000)
        for item in self.result.symptom_evidence:
            self.assertTrue(item.alarm_info)

    def test_the_two_records_agree_on_when_the_incident_started(self):
        self.assertGreater(
            self.result.alert_start_time_utc, self.result.first_failure.time_stamp
        )


class ProcedureConstantTests(unittest.TestCase):
    def test_documented_constants_are_unchanged(self):
        self.assertEqual(triage_module.ALERT_CRITICALITY, ("CRITICAL", "IMMEDIATE"))
        self.assertEqual(triage_module.SYNC_LOG_PAGE_SIZE, 5)


class SessionLifecycleTests(unittest.TestCase):
    def test_token_is_released_after_a_successful_run(self):
        self.assertTrue(nominal_run().token_released)
        self.assertEqual(nominal_run().log[-1]["operationId"], "releaseToken")

    def test_token_is_released_when_a_later_operation_fails(self):
        with self.assertRaises(TriageFailed) as caught:
            run_triage(fail_operation="getSymptoms")
        failure = caught.exception
        self.assertIsInstance(failure.error, OperationsError)
        self.assertTrue(failure.token_released)
        self.assertEqual(failure.log[-1]["operationId"], "releaseToken")
        self.assertEqual(failure.log[-1]["status"], 200)
        self.assertEqual(
            [e["status"] for e in failure.log if e["operationId"] == "getSymptoms"], [500]
        )

    def test_a_failing_first_operation_raises_operations_error(self):
        with self.assertRaises(TriageFailed) as caught:
            run_triage(fail_operation="getLdapDirectories")
        self.assertIsInstance(caught.exception.error, OperationsError)

    def test_an_unusable_json_response_raises_operations_error_and_releases(self):
        with self.assertRaises(TriageFailed) as caught:
            run_triage(invalid_json_operation="getSymptoms")
        failure = caught.exception
        self.assertIsInstance(failure.error, OperationsError)
        self.assertTrue(failure.token_released)
        self.assertEqual(failure.log[-1]["operationId"], "releaseToken")

    def test_an_unusable_response_shape_raises_operations_error_and_releases(self):
        with self.assertRaises(TriageFailed) as caught:
            run_triage(invalid_shape_operation="getLdapSyncLogs")
        failure = caught.exception
        self.assertIsInstance(failure.error, OperationsError)
        self.assertTrue(failure.token_released)
        self.assertEqual(failure.log[-1]["operationId"], "releaseToken")


if __name__ == "__main__":
    unittest.main()
