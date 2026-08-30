"""Fixture state for the VCF Automation deployment-triage mock.

This is simulated *service* state for a local loopback server. It stands in for a
VCF Automation appliance so the task can be exercised without one. It is not an
answer key: the identifiers below are only reachable by walking the documented
operations in the order the contract implies.

Shapes here follow docs/contract.json, which was transcribed from the Broadcom
xAPIs reference pages listed in docs/official_sources.json.
"""

import hashlib

ORG_ID = "5d1c7a80-4e63-11ef-9a2b-0242ac120002"
PROJECT_ID = "6c1f0a52-3e77-4b90-9a2d-1f4c8e05b7d3"
PROJECT_NAME = "payments-platform"

DEPLOYMENT_ID = "2f6b1c94-7d0a-4a1e-9c3f-8b5d21e7a604"
DEPLOYMENT_NAME = "payments-uat-03"

#: Day-2 actions the mock appliance will accept for this deployment.
SUPPORTED_ACTIONS = (
    "Deployment.PowerOn",
    "Deployment.PowerOff",
    "Deployment.Update",
    "Deployment.Delete",
)

#: Identifier of the request created by a successful POST. Deterministic so the
#: verifier can assert on it.
REMEDIATION_REQUEST_ID = "5e2a9c48-3fb1-4d76-8a05-c94e17b6f2d3"

REQUESTED_BY = "svc-automation@uat.example.internal"


def _uid(seed):
    """Deterministic UUID-shaped identifier derived from a seed string."""
    h = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return "%s-%s-4%s-8%s-%s" % (h[0:8], h[8:12], h[13:16], h[17:20], h[20:32])


def _ts(offset_ms):
    """Timestamp offset from the start of the failed power-on request."""
    base_ms = 4 * 3600 * 1000 + 10 * 60 * 1000 + 58 * 1000 + 112  # 04:10:58.112
    total = base_ms + offset_ms
    ms = total % 1000
    total //= 1000
    s = total % 60
    total //= 60
    m = total % 60
    h = total // 60
    return "2026-07-29T%02d:%02d:%02d.%03dZ" % (h, m, s, ms)


# --------------------------------------------------------------------------
# Requests on the deployment, newest first (the documented default sort for
# Get Deployment Requests is createdAt,DESC).
# --------------------------------------------------------------------------

REQ_ABORTED = "b41d7e08-2c96-4f53-a7be-90d5c31f8a27"
REQ_FAILED = "8f3c5a71-64de-4b02-9f18-2ad6e9c70b55"
REQ_POWEROFF = "c7a92f34-18b5-49d6-8e01-5fb3d820c916"
REQ_CREATE = "1d58e6b2-9047-4c3a-b6f5-73e2a19d4c80"

DEPLOYMENT_REQUESTS = [
    {
        "id": REQ_ABORTED,
        "name": "Power On",
        "details": "Cancelled by operator before any task started.",
        "status": "ABORTED",
        "actionId": "Deployment.PowerOn",
        "deploymentId": DEPLOYMENT_ID,
        "requestedBy": "j.okafor@uat.example.internal",
        "createdAt": "2026-07-29T05:02:11.480Z",
        "updatedAt": "2026-07-29T05:02:39.006Z",
        "completedAt": "2026-07-29T05:02:39.006Z",
        "completedTasks": 0,
        "totalTasks": 7,
        "cancelable": False,
        "dismissed": False,
        "resourceIds": [],
    },
    {
        "id": REQ_FAILED,
        "name": "Power On",
        "details": "Power on failed for 1 of 3 resources.",
        "status": "FAILED",
        "actionId": "Deployment.PowerOn",
        "deploymentId": DEPLOYMENT_ID,
        "requestedBy": REQUESTED_BY,
        "createdAt": "2026-07-29T04:10:58.112Z",
        "initializedAt": "2026-07-29T04:10:59.640Z",
        "updatedAt": "2026-07-29T04:12:58.900Z",
        "completedAt": "2026-07-29T04:12:58.900Z",
        "completedTasks": 5,
        "totalTasks": 7,
        "cancelable": False,
        "dismissed": False,
        "resourceIds": [
            "a3d81f60-52c7-4e19-b0a8-64f9c2d7e315",
            "bb2740de-9c85-41f3-8d6a-2705ec19f4a8",
            "cf95a3b1-06d2-4870-9e14-38ba7c0d5619",
        ],
    },
    {
        "id": REQ_POWEROFF,
        "name": "Power Off",
        "details": "Power off completed for 3 resources.",
        "status": "SUCCESSFUL",
        "actionId": "Deployment.PowerOff",
        "deploymentId": DEPLOYMENT_ID,
        "requestedBy": REQUESTED_BY,
        "createdAt": "2026-07-28T22:41:07.903Z",
        "completedAt": "2026-07-28T22:43:15.221Z",
        "completedTasks": 7,
        "totalTasks": 7,
        "cancelable": False,
        "dismissed": False,
        "resourceIds": [],
    },
    {
        "id": REQ_CREATE,
        "name": "Create payments-uat-03",
        "details": "Deployment created from catalog item payments-three-tier.",
        "status": "SUCCESSFUL",
        "deploymentId": DEPLOYMENT_ID,
        "catalogItemId": "9b0e4c7a-1f38-4d52-a6c9-08e3b71d24f5:2.4.0",
        "requestedBy": "m.reyes@uat.example.internal",
        "createdAt": "2026-06-02T09:15:44.207Z",
        "completedAt": "2026-06-02T09:31:02.884Z",
        "completedTasks": 24,
        "totalTasks": 24,
        "cancelable": False,
        "dismissed": False,
        "resourceIds": [],
    },
]


# --------------------------------------------------------------------------
# The deployment itself.
# --------------------------------------------------------------------------

DEPLOYMENT = {
    "id": DEPLOYMENT_ID,
    "name": DEPLOYMENT_NAME,
    "description": "Payments UAT stack, three tier.",
    # The Deployment status enumeration has no ACTION_* member, so a failed
    # day-2 power-on surfaces here as UPDATE_FAILED.
    "status": "UPDATE_FAILED",
    "orgId": ORG_ID,
    "projectId": PROJECT_ID,
    "blueprintId": "0f7c2e91-5a46-4b83-9d20-c1e58a304f76",
    "blueprintVersion": "2.4.0",
    "catalogItemId": "9b0e4c7a-1f38-4d52-a6c9-08e3b71d24f5",
    "catalogItemVersion": "2.4.0",
    "createdAt": "2026-06-02T09:15:44.207Z",
    "createdBy": "m.reyes@uat.example.internal",
    "lastUpdatedAt": "2026-07-29T05:02:39.006Z",
    "lastUpdatedBy": "j.okafor@uat.example.internal",
    "ownedBy": "payments-platform-team",
    "ownerType": "GROUP",
    "leaseExpireAt": "2026-12-31T00:00:00.000Z",
    "leaseGracePeriodDays": 7,
    "deleted": False,
    "lastRequest": DEPLOYMENT_REQUESTS[0],
}

#: Other deployments in the org. Present so that an unfiltered listing does not
#: hand the caller the right deployment by accident.
OTHER_DEPLOYMENTS = [
    {
        "id": "7c3e5f10-8b24-4d97-a1f6-3e09b2c48d51",
        "name": "payments-uat-01",
        "status": "CREATE_SUCCESSFUL",
        "orgId": ORG_ID,
        "projectId": PROJECT_ID,
        "createdAt": "2026-04-11T13:02:17.550Z",
        "deleted": False,
    },
    {
        "id": "e28a4b76-90c1-4f35-8ad2-51b6e7093fc4",
        "name": "payments-uat-02",
        "status": "UPDATE_SUCCESSFUL",
        "orgId": ORG_ID,
        "projectId": PROJECT_ID,
        "createdAt": "2026-05-06T08:44:51.319Z",
        "deleted": False,
    },
    DEPLOYMENT,
    {
        "id": "3ab5d902-6e74-4c18-9f83-27d0a4e6b15c",
        "name": "payments-uat-04",
        "status": "CREATE_FAILED",
        "orgId": ORG_ID,
        "projectId": PROJECT_ID,
        "createdAt": "2026-07-30T16:20:03.771Z",
        "deleted": False,
    },
]


# --------------------------------------------------------------------------
# Events for the failed request. There are 23 of them, so with the documented
# default page size of 20 the last one is not on the first page.
# --------------------------------------------------------------------------

_EVENT_STEPS = [
    ("Power On", "Request accepted.", "Cloud.Deployment", DEPLOYMENT_NAME),
    ("Power On", "Evaluating deployment policies.", "Cloud.Deployment", DEPLOYMENT_NAME),
    ("Power On", "No approval policy applies.", "Cloud.Deployment", DEPLOYMENT_NAME),
    ("Power On", "Building resource dependency graph.", "Cloud.Deployment", DEPLOYMENT_NAME),
    ("Allocation", "Reserving compute for 3 resources.", "Cloud.Deployment", DEPLOYMENT_NAME),
    ("Power On", "Starting.", "Cloud.vSphere.Machine", "payments-db-01"),
    ("Power On", "vCenter task PowerOnVM_Task submitted.", "Cloud.vSphere.Machine", "payments-db-01"),
    ("Power On", "Guest heartbeat detected.", "Cloud.vSphere.Machine", "payments-db-01"),
    ("Power On", "Completed.", "Cloud.vSphere.Machine", "payments-db-01"),
    ("Power On", "Starting.", "Cloud.vSphere.Machine", "payments-app-01"),
    ("Power On", "vCenter task PowerOnVM_Task submitted.", "Cloud.vSphere.Machine", "payments-app-01"),
    ("Power On", "Guest heartbeat detected.", "Cloud.vSphere.Machine", "payments-app-01"),
    ("Power On", "Completed.", "Cloud.vSphere.Machine", "payments-app-01"),
    ("Network", "Attaching payments-app-01 to uat-app-net.", "Cloud.vSphere.Network", "uat-app-net"),
    ("Load Balancer", "Adding payments-app-01 to pool payments-uat-pool.", "Cloud.LoadBalancer", "payments-uat-lb"),
    ("Power On", "Starting.", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Power On", "Resolving placement.", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Power On", "vCenter task PowerOnVM_Task submitted.", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Power On", "Waiting for task completion.", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Power On", "Retrying (attempt 2 of 3).", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Power On", "Retrying (attempt 3 of 3).", "Cloud.vSphere.Machine", "payments-app-02"),
    ("Load Balancer", "Pool payments-uat-pool left with 1 of 2 members.", "Cloud.LoadBalancer", "payments-uat-lb"),
]

FAILED_REQUEST_EVENTS = []
for _i, (_name, _details, _rtype, _rname) in enumerate(_EVENT_STEPS):
    FAILED_REQUEST_EVENTS.append(
        {
            "id": _uid("%s:event:%d" % (REQ_FAILED, _i)),
            "name": _name,
            "details": _details,
            "timestamp": _ts(_i * 5200),
            "resourceType": _rtype,
            "resourceName": _rname,
            "hasLogs": False,
            "userEvent": False,
        }
    )

#: The single event on the failed request that carries logs. Its identifier is
#: only obtainable by enumerating the events of the failed request past the
#: first page.
FAILURE_EVENT = {
    "id": _uid("%s:event:failure" % REQ_FAILED),
    "name": "Power On",
    "details": "Failed to power on payments-app-02.",
    "timestamp": _ts(120330),
    "resourceType": "Cloud.vSphere.Machine",
    "resourceName": "payments-app-02",
    "hasLogs": True,
    "userEvent": False,
}
FAILED_REQUEST_EVENTS.append(FAILURE_EVENT)

FAILURE_EVENT_ID = FAILURE_EVENT["id"]

#: Events on the aborted request. It was cancelled before doing anything, so it
#: has no logs anywhere - picking it instead of the failed request is a dead end.
ABORTED_REQUEST_EVENTS = [
    {
        "id": _uid("%s:event:0" % REQ_ABORTED),
        "name": "Power On",
        "details": "Request accepted.",
        "timestamp": "2026-07-29T05:02:11.480Z",
        "resourceType": "Cloud.Deployment",
        "resourceName": DEPLOYMENT_NAME,
        "hasLogs": False,
        "userEvent": False,
    },
    {
        "id": _uid("%s:event:1" % REQ_ABORTED),
        "name": "Power On",
        "details": "Cancelled by j.okafor@uat.example.internal.",
        "timestamp": "2026-07-29T05:02:39.006Z",
        "resourceType": "Cloud.Deployment",
        "resourceName": DEPLOYMENT_NAME,
        "hasLogs": False,
        "userEvent": True,
    },
]

#: The vCenter correlation id that identifies the underlying task failure. It
#: appears nowhere except inside the event log below.
CORRELATION_ID = "vc-task-4417-9f2b"

_FAILURE_LOG_LINES = [
    "Starting power-on for Cloud.vSphere.Machine payments-app-02 on endpoint uat-vc01.example.internal",
    "Resolved placement: cluster UAT-Cluster-A, host esx07.uat.local, datastore uat-vsan-01",
    "Submitting vCenter task PowerOnVM_Task for vm-2718",
    "vCenter task PowerOnVM_Task failed: InvalidState - the operation is not allowed in the current state; "
    "host esx07.uat.local entered maintenance mode while the task was queued",
    "correlationId=%s vcenter=uat-vc01.example.internal task=task-88214 vm=vm-2718" % CORRELATION_ID,
    "Retry 2 of 3 failed: InvalidState - host esx07.uat.local is still in maintenance mode",
    "Retry 3 of 3 failed: InvalidState - host esx07.uat.local is still in maintenance mode",
    "Giving up after 3 attempts. Host maintenance mode is a transient condition; re-run the "
    "Deployment.PowerOn action once esx07.uat.local has exited maintenance mode.",
]

FAILURE_EVENT_LOGS = []
for _i, _msg in enumerate(_FAILURE_LOG_LINES):
    FAILURE_EVENT_LOGS.append(
        {
            "id": _uid("%s:log:%d" % (FAILURE_EVENT_ID, _i)),
            "message": _msg,
            "rownum": _i + 1,
            "timestamp": _ts(120330 + _i * 90),
            "eof": _i == len(_FAILURE_LOG_LINES) - 1,
        }
    )


def find_request(request_id):
    for req in DEPLOYMENT_REQUESTS:
        if req["id"] == request_id:
            return req
    return None


def events_for_request(request_id):
    """Events for a request id, or None when the request is unknown."""
    if request_id == REQ_FAILED:
        return FAILED_REQUEST_EVENTS
    if request_id == REQ_ABORTED:
        return ABORTED_REQUEST_EVENTS
    if find_request(request_id) is not None:
        return []
    return None


def logs_for_event(request_id, event_id):
    """Log rows for an event, or None when absent (Event.hasLogs is false)."""
    if request_id == REQ_FAILED and event_id == FAILURE_EVENT_ID:
        return FAILURE_EVENT_LOGS
    return None
