"""Deterministic appliance state served by the loopback contract mock.

Nothing here is an expected request or an answer key: it is the state of a
simulated VCF Operations appliance. Every value the triage reports has to be
retrieved from it through the contract operations.
"""

IDP_CONFIG_ID = "6b1f4e2a-5d3c-4a7b-9e18-2c5f0a91d3b4"
ISSUED_TOKEN = "0f9a1c2b-7d84-4e30-b165-8a92c4d0e731::9c7f01"

# --- LDAP directories of the identity provider -----------------------------

HEALTHY_DIRECTORY_ID = "0f2c7d51-8a94-4b62-b1de-3e6a4c02f7a9"
FAILED_DIRECTORY_ID = "c48a1b09-72e5-4d3f-8a6c-91b0e7d24f35"
FAILED_DIRECTORY_NAME = "corp-ad-primary"

# The failing directory is deliberately not first in the collection.
LDAP_DIRECTORIES = [
    {
        "domains": ["lab.example.internal"],
        "lastSyncDateTime": 1774221000000,
        "lastSyncStatus": "COMPLETED",
        "ldapConfigurationId": HEALTHY_DIRECTORY_ID,
        "name": "lab-openldap",
        "numberOfGroups": 6,
        "numberOfUsers": 24,
    },
    {
        "domains": ["corp.example.com", "eu.corp.example.com"],
        "lastSyncDateTime": 1774224000000,
        "lastSyncStatus": "FAILED",
        "ldapConfigurationId": FAILED_DIRECTORY_ID,
        "name": FAILED_DIRECTORY_NAME,
        "numberOfGroups": 0,
        "numberOfUsers": 0,
    },
]

# --- Synchronization execution logs of the failing directory ---------------

_HOUR_MS = 3600000
_NEWEST_SYNC_MS = 1774224000000

FIRST_FAILURE_SYNC_LOG_ID = "0c83a97b-3e50-4d1f-b8a2-79e64c015d38"
FIRST_FAILURE_MESSAGE_KEY = "ldap.sync.error.group.base.dn.not.found"
CASCADE_MESSAGE_KEY = "ldap.sync.error.no.provisioned.groups"

_SYNC_LOG_IDS = [
    "4a1d8f60-1c93-4e27-8b05-6d7f2a90c318",
    "b2e07c45-9a16-4d38-91cf-0e83b5d67a24",
    "7f3c9012-6b48-4a5d-8e71-2c04f9a3d85b",
    "e58b26d3-0f74-4c19-a3b6-95d17e08c462",
    "1d940a7e-83b5-4f62-90ac-5e26b7d13f80",
    "c6720e18-4d3a-49b7-85f1-3a09c8e56b47",
    "9b45f2a0-7e61-4038-b2d9-8c31074ae5f6",
    "38ce6079-2b14-4d85-9a0f-71b53e2c9d40",
    "a07f1b53-5c92-4e60-83d4-6f19b0a27e35",
    "5e21c4d8-8f07-4b93-a61e-04c7d3925fb1",
    FIRST_FAILURE_SYNC_LOG_ID,
    "f419d5c2-6a83-4712-95be-2d80f3a94c67",
]

_ZERO_DETAILS = {
    "groupsAdded": 0,
    "groupsRemoved": 0,
    "groupsUpdated": 0,
    "usersAdded": 0,
    "usersRemoved": 0,
    "usersUpdated": 0,
}


def _sync_log(index, *, success, message_key, result, details, sync_type="SCHEDULED"):
    time_stamp = _NEWEST_SYNC_MS - index * _HOUR_MS
    payload = dict(_ZERO_DETAILS)
    payload.update(details)
    payload["syncEndTime"] = time_stamp + 41000
    return {
        "id": _SYNC_LOG_IDS[index],
        "success": success,
        "syncDetails": payload,
        "syncResult": result,
        "syncResultMessageKey": message_key,
        "syncType": sync_type,
        "timeStamp": time_stamp,
    }


# Newest first, one scheduled run per hour. The group search base DN went
# missing during the run at index 10; every later run fails for a downstream
# reason and reports a different, misleading message key.
SYNC_LOGS = (
    [
        _sync_log(
            index,
            success=False,
            message_key=CASCADE_MESSAGE_KEY,
            result="Synchronization failed: the sync profile has no provisioned groups.",
            details={},
            sync_type="MANUAL" if index == 3 else "SCHEDULED",
        )
        for index in range(10)
    ]
    + [
        _sync_log(
            10,
            success=False,
            message_key=FIRST_FAILURE_MESSAGE_KEY,
            result=(
                "Synchronization failed: group search base DN "
                "'OU=VCF-Access,OU=Groups,DC=corp,DC=example,DC=com' was not found "
                "in the directory. Provisioned groups were dropped from the sync "
                "profile."
            ),
            details={"groupsRemoved": 47, "usersRemoved": 318},
        ),
        _sync_log(
            11,
            success=True,
            message_key="ldap.sync.result.success",
            result="Synchronization completed.",
            details={"groupsUpdated": 47, "usersUpdated": 318},
        ),
    ]
)

FIRST_FAILURE_TIME_STAMP = SYNC_LOGS[10]["timeStamp"]

#: The list operation returns a summary; these keys are detail-only.
SYNC_LOG_DETAIL_ONLY_KEYS = ("syncDetails", "syncResult")

# --- Alerts ----------------------------------------------------------------

RESOURCE_ID_OPS_MANAGER = "9d0e5c74-1a2b-4f36-8c9d-7e4b1a03f562"
RESOURCE_ID_CLUSTER = "2f7b8c10-4e6d-45a9-b3c8-0d51e9a76b24"

ALERT_ID_DATASTORE = "b7c3f902-6d18-4a5e-9f0b-3c82d1746e5a"
ALERT_ID_IDENTITY = "e51d8a46-0937-4c62-b8fa-2d67c0451938"
ALERT_ID_CPU = "3a94c6f8-b2e7-4d10-95c3-6f8b04e2d7c1"

# Two alerts share a resource, so the incident alert cannot be picked from the
# resource identifier alone.
ALERTS = [
    {
        "alertDefinitionId": "AlertDefinition-VMWARE-datastore-latency",
        "alertDefinitionName": "Datastore write latency is above threshold",
        "alertId": ALERT_ID_DATASTORE,
        "alertLevel": "CRITICAL",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774195200000,
        "status": "ACTIVE",
        "type": "Storage",
    },
    {
        "alertDefinitionId": "AlertDefinition-VCFOPS-identity-membership",
        "alertDefinitionName": "Identity provider group membership is stale",
        "alertId": ALERT_ID_IDENTITY,
        "alertLevel": "IMMEDIATE",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": FIRST_FAILURE_TIME_STAMP + 120000,
        "status": "ACTIVE",
        "type": "Application",
    },
    {
        "alertDefinitionId": "AlertDefinition-VMWARE-cluster-cpu",
        "alertDefinitionName": "Cluster CPU contention is high",
        "alertId": ALERT_ID_CPU,
        "alertLevel": "CRITICAL",
        "resourceId": RESOURCE_ID_CLUSTER,
        "startTimeUTC": 1774180800000,
        "status": "ACTIVE",
        "type": "Capacity",
    },
]

# --- Symptoms --------------------------------------------------------------

SYMPTOM_ID_LATENCY = "1c9e77a0-45b3-4e21-8d6f-0a37b9c52e18"
SYMPTOM_ID_QUEUE = "84fb2d63-9c07-41ae-b52d-6e19f0a8347c"
SYMPTOM_ID_TOKEN_LATENCY = "d3607a91-2f58-4bc6-a90e-71d4c8b0532f"
SYMPTOM_ID_CPU_READY = "5e2a4f18-7b93-40dc-8e15-c96a3d720b84"
SYMPTOM_ID_GROUP_SYNC = "af16b840-3d72-4e95-b0c7-28f5910ae63d"
SYMPTOM_ID_CANCELED = "72d5c3e9-8a41-4f07-96b2-5c03e8d17a49"

# Only the last symptom of the identity alert names the failing directory.
CONTRIBUTING_SYMPTOMS = {
    ALERT_ID_DATASTORE: [
        {
            "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-datastore-write-latency"],
            "symptomId": SYMPTOM_ID_LATENCY,
            "symptomSetId": "symptomset-datastore-1",
        },
        {
            "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-datastore-queue-depth"],
            "symptomId": SYMPTOM_ID_QUEUE,
            "symptomSetId": "symptomset-datastore-1",
        },
    ],
    ALERT_ID_IDENTITY: [
        {
            "symptomDefinitionsIds": ["SymptomDefinition-VCFOPS-token-issuance-latency"],
            "symptomId": SYMPTOM_ID_TOKEN_LATENCY,
            "symptomSetId": "symptomset-identity-1",
        },
        {
            "symptomDefinitionsIds": ["SymptomDefinition-VCFOPS-directory-group-sync"],
            "symptomId": SYMPTOM_ID_GROUP_SYNC,
            "symptomSetId": "symptomset-identity-2",
        },
    ],
    ALERT_ID_CPU: [
        {
            "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-cluster-cpu-ready"],
            "symptomId": SYMPTOM_ID_CPU_READY,
            "symptomSetId": "symptomset-cluster-1",
        },
    ],
}

_ALARM_INFO = {
    SYMPTOM_ID_LATENCY: (
        "Datastore vsanDatastore-01 write latency averaged 42 ms over 30 minutes."
    ),
    SYMPTOM_ID_QUEUE: "Outstanding I/O queue depth reached 128 on 3 of 4 hosts.",
    SYMPTOM_ID_TOKEN_LATENCY: (
        "Token issuance for realm vcf.corp.example.com averaged 1.9 s."
    ),
    SYMPTOM_ID_GROUP_SYNC: (
        "Directory corp-ad-primary: the group search base DN lookup returned no "
        "entries, and 47 provisioned groups were removed from the sync profile."
    ),
    SYMPTOM_ID_CPU_READY: "CPU ready averaged 11% across 6 hosts.",
    SYMPTOM_ID_CANCELED: "Collection of adapter instance vc-01 was briefly down.",
}

SYMPTOMS = [
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_LATENCY],
        "active": True,
        "id": SYMPTOM_ID_LATENCY,
        "message": "Datastore write latency is above the dynamic threshold",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774195100000,
        "symptomCriticality": "CRITICAL",
        "symptomDefinitionId": "SymptomDefinition-VMWARE-datastore-write-latency",
        "updateTimeUTC": 1774223400000,
    },
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_QUEUE],
        "active": True,
        "id": SYMPTOM_ID_QUEUE,
        "message": "Datastore outstanding I/O queue depth is saturated",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774195160000,
        "symptomCriticality": "WARNING",
        "symptomDefinitionId": "SymptomDefinition-VMWARE-datastore-queue-depth",
        "updateTimeUTC": 1774223400000,
    },
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_TOKEN_LATENCY],
        "active": True,
        "id": SYMPTOM_ID_TOKEN_LATENCY,
        "message": "Identity token issuance latency is above the dynamic threshold",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774188180000,
        "symptomCriticality": "WARNING",
        "symptomDefinitionId": "SymptomDefinition-VCFOPS-token-issuance-latency",
        "updateTimeUTC": 1774223400000,
    },
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_GROUP_SYNC],
        "active": True,
        "id": SYMPTOM_ID_GROUP_SYNC,
        "message": (
            "LDAP directory 'corp-ad-primary' reported 0 provisioned groups after "
            "the last scheduled synchronization"
        ),
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774188120000,
        "symptomCriticality": "IMMEDIATE",
        "symptomDefinitionId": "SymptomDefinition-VCFOPS-directory-group-sync",
        "updateTimeUTC": 1774223400000,
    },
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_CANCELED],
        "active": False,
        "id": SYMPTOM_ID_CANCELED,
        "message": "Adapter instance collection state is down",
        "resourceId": RESOURCE_ID_OPS_MANAGER,
        "startTimeUTC": 1774119600000,
        "symptomCriticality": "CRITICAL",
        "symptomDefinitionId": "SymptomDefinition-VMWARE-collection-state",
        "updateTimeUTC": 1774123200000,
    },
    {
        "alarmInfo": _ALARM_INFO[SYMPTOM_ID_CPU_READY],
        "active": True,
        "id": SYMPTOM_ID_CPU_READY,
        "message": "Cluster CPU ready time is above the dynamic threshold",
        "resourceId": RESOURCE_ID_CLUSTER,
        "startTimeUTC": 1774180740000,
        "symptomCriticality": "CRITICAL",
        "symptomDefinitionId": "SymptomDefinition-VMWARE-cluster-cpu-ready",
        "updateTimeUTC": 1774223400000,
    },
]

#: ``active`` is bookkeeping for the mock's ``activeOnly`` filter and is not a
#: property of the pinned ``symptom`` schema, so it is never served.
SYMPTOM_INTERNAL_KEYS = ("active",)
