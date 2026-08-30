import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Verifier for SnapshotProtectionClient.
 *
 * It starts the contract-pinned in-process appliance, drives the client through five
 * scenarios, and then asserts the exact wire shape of every request the client made by
 * reading the mock's request log. Behaviour alone is not enough here: the point of the
 * exercise is that the bytes on the wire match docs/contract.json, including that an
 * optional property with nothing to say is left out of the body rather than sent as null,
 * an empty object or an empty string.
 */
public final class TestMain {

    private static final String SESSION_ID = "sess-1a2b3c4d";
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path REQUEST_LOG = Path.of("build", "requests.log");

    private static final String CLUSTER = MockSnapserviceServer.CLUSTER;
    private static final String PG = MockSnapserviceServer.PROTECTION_GROUP;
    private static final String SNAPSHOTS_PATH =
            "/api/snapservice/clusters/" + CLUSTER + "/protection-groups/" + PG + "/snapshots";

    private static final String CREATE_OP = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task";
    private static final String TASK_OP = "Snapservice.Tasks_get";
    private static final String SNAPSHOT_OP = "Snapservice.Clusters.ProtectionGroups.Snapshots_get";

    private static final List<String> FAILURES = new ArrayList<>();
    private static int consumed;
    private static MockSnapserviceServer mock;

    public static void main(String[] args) throws Exception {
        mock = new MockSnapserviceServer(CONTRACT, REQUEST_LOG, SESSION_ID);
        mock.start();
        try {
            System.out.println("appliance listening on " + mock.baseUrl());
            scenarioRetentionSupplied(mock.baseUrl());
            scenarioRetentionOmitted(mock.baseUrl());
            scenarioTaskFails(mock.baseUrl());
            scenarioPollBudgetExhausted(mock.baseUrl());
            scenarioPollingPauseIsInterruptible(mock.baseUrl());
            everyRequestStayedOnContract();
        } finally {
            mock.stop();
        }

        if (FAILURES.isEmpty()) {
            System.out.println();
            System.out.println("PASS: all scenarios and wire-shape assertions succeeded");
            System.exit(0);
        }
        System.out.println();
        System.out.println("FAIL: " + FAILURES.size() + " assertion(s) failed");
        for (String failure : FAILURES) {
            System.out.println("  - " + failure);
        }
        System.exit(1);
    }

    // ---------------------------------------------------------------- scenarios

    /** A snapshot with a retention period. The task runs PENDING, RUNNING, SUCCEEDED. */
    private static void scenarioRetentionSupplied(String baseUrl) throws Exception {
        System.out.println();
        System.out.println("scenario 1: snapshot with a 7 day retention, task succeeds on the third poll");
        SnapshotProtectionClient client = new SnapshotProtectionClient(
                baseUrl, SESSION_ID, 25, 10, mock.httpClient());

        SnapshotProtectionClient.Snapshot snapshot;
        try {
            snapshot = client.createProtectionGroupSnapshot(CLUSTER, PG, "nightly-keep-7d", "DAY", 7L);
        } catch (Throwable t) {
            fail("scenario 1 threw " + render(t));
            drain();
            return;
        }

        if (snapshot == null) {
            fail("scenario 1 returned null instead of a Snapshot");
        } else {
            eq(snapshot.id, "snap-1001", "scenario 1 snapshot id");
            eq(snapshot.name, "nightly-keep-7d", "scenario 1 snapshot name");
            eq(snapshot.snapshotType, "ONE_TIME", "scenario 1 snapshot type");
            eq(snapshot.expiresAt, "2026-08-11T02:15:30.000Z", "scenario 1 snapshot expiry");
        }

        List<Map<String, Object>> requests = drain();
        if (!count(requests, 5, "scenario 1")) {
            return;
        }

        Map<String, Object> create = requests.get(0);
        assertRequestLine(create, "POST", SNAPSHOTS_PATH, "vmw-task=true", CREATE_OP, 202, "scenario 1 create");
        assertSessionHeader(create, "scenario 1 create");
        assertJsonContentType(create, "scenario 1 create");

        Map<String, Object> body = body(create, "scenario 1 create");
        if (body != null) {
            eq(new ArrayList<>(body.keySet()), List.of("name", "retention"),
                    "scenario 1 create body properties");
            eq(body.get("name"), "nightly-keep-7d", "scenario 1 CreateSpec.name");
            Object retentionValue = body.get("retention");
            if (!(retentionValue instanceof Map)) {
                fail("scenario 1 CreateSpec.retention should be an object, found "
                        + Json.describe(retentionValue));
            } else {
                Map<String, Object> retention = Json.asObject(retentionValue);
                eq(new LinkedHashSet<>(retention.keySet()), Set.of("unit", "duration"),
                        "scenario 1 RetentionPeriod properties");
                eq(retention.get("unit"), "DAY", "scenario 1 RetentionPeriod.unit");
                if (!(retention.get("duration") instanceof Long)) {
                    fail("scenario 1 RetentionPeriod.duration should be a JSON integer, found "
                            + Json.describe(retention.get("duration")));
                } else {
                    eq(retention.get("duration"), 7L, "scenario 1 RetentionPeriod.duration");
                }
            }
        }

        for (int i = 1; i <= 3; i++) {
            Map<String, Object> poll = requests.get(i);
            assertRequestLine(poll, "GET", "/api/snapservice/tasks/task-0001", null, TASK_OP, 200,
                    "scenario 1 poll " + i);
            assertSessionHeader(poll, "scenario 1 poll " + i);
            assertEmptyBody(poll, "scenario 1 poll " + i);
        }

        Map<String, Object> read = requests.get(4);
        assertRequestLine(read, "GET", SNAPSHOTS_PATH + "/snap-1001", null, SNAPSHOT_OP, 200,
                "scenario 1 snapshot read");
        assertSessionHeader(read, "scenario 1 snapshot read");
        assertEmptyBody(read, "scenario 1 snapshot read");
    }

    /** The same call without retention. The optional property must be absent from the body. */
    private static void scenarioRetentionOmitted(String baseUrl) throws Exception {
        System.out.println();
        System.out.println("scenario 2: snapshot without retention, task succeeds on the second poll");
        SnapshotProtectionClient client = new SnapshotProtectionClient(
                baseUrl, SESSION_ID, 25, 10, mock.httpClient());

        SnapshotProtectionClient.Snapshot snapshot;
        try {
            snapshot = client.createProtectionGroupSnapshot(CLUSTER, PG, "adhoc-no-retention", null, null);
        } catch (Throwable t) {
            fail("scenario 2 threw " + render(t));
            drain();
            return;
        }

        if (snapshot == null) {
            fail("scenario 2 returned null instead of a Snapshot");
        } else {
            eq(snapshot.id, "snap-1002", "scenario 2 snapshot id");
            eq(snapshot.name, "adhoc-no-retention", "scenario 2 snapshot name");
            if (snapshot.expiresAt != null) {
                fail("scenario 2 snapshot expiry should be null when Snapshots.Info omits expires_at, found "
                        + snapshot.expiresAt);
            }
        }

        List<Map<String, Object>> requests = drain();
        if (!count(requests, 4, "scenario 2")) {
            return;
        }

        Map<String, Object> create = requests.get(0);
        assertRequestLine(create, "POST", SNAPSHOTS_PATH, "vmw-task=true", CREATE_OP, 202, "scenario 2 create");
        assertSessionHeader(create, "scenario 2 create");
        assertJsonContentType(create, "scenario 2 create");

        String raw = String.valueOf(create.get("body"));
        Map<String, Object> body = body(create, "scenario 2 create");
        if (body != null) {
            // The whole point of this scenario: an optional property with no value is omitted,
            // not sent as null, an empty object or an empty string.
            if (body.containsKey("retention")) {
                fail("scenario 2 CreateSpec must omit the retention property when no retention is "
                        + "requested, but the body carried retention="
                        + Json.describe(body.get("retention")) + "; raw body was " + raw);
            }
            eq(new ArrayList<>(body.keySet()), List.of("name"), "scenario 2 create body properties");
            eq(body.get("name"), "adhoc-no-retention", "scenario 2 CreateSpec.name");
        }

        for (int i = 1; i <= 2; i++) {
            Map<String, Object> poll = requests.get(i);
            assertRequestLine(poll, "GET", "/api/snapservice/tasks/task-0002", null, TASK_OP, 200,
                    "scenario 2 poll " + i);
            assertSessionHeader(poll, "scenario 2 poll " + i);
        }

        assertRequestLine(requests.get(3), "GET", SNAPSHOTS_PATH + "/snap-1002", null, SNAPSHOT_OP, 200,
                "scenario 2 snapshot read");
    }

    /** The task goes PENDING, RUNNING, BLOCKED, FAILED. BLOCKED is not terminal. */
    private static void scenarioTaskFails(String baseUrl) throws Exception {
        System.out.println();
        System.out.println("scenario 3: task passes through BLOCKED and then fails on the fourth poll");
        SnapshotProtectionClient client = new SnapshotProtectionClient(
                baseUrl, SESSION_ID, 25, 10, mock.httpClient());

        SnapshotProtectionClient.TaskFailedException failure = null;
        try {
            SnapshotProtectionClient.Snapshot snapshot =
                    client.createProtectionGroupSnapshot(CLUSTER, PG, "doomed-snapshot", "HOUR", 12L);
            fail("scenario 3 should have raised TaskFailedException but returned " + snapshot);
        } catch (SnapshotProtectionClient.TaskFailedException e) {
            failure = e;
        } catch (Throwable t) {
            fail("scenario 3 should have raised TaskFailedException but threw " + render(t));
        }

        if (failure != null) {
            eq(failure.taskId, "task-0003", "scenario 3 failed task id");
            eq(failure.status, "FAILED", "scenario 3 terminal status");
            if (failure.detail == null || !failure.detail.contains("Quiescing failed")) {
                fail("scenario 3 failure detail should carry the appliance error message, found "
                        + failure.detail);
            }
        }

        List<Map<String, Object>> requests = drain();
        if (!count(requests, 5, "scenario 3")) {
            return;
        }

        assertRequestLine(requests.get(0), "POST", SNAPSHOTS_PATH, "vmw-task=true", CREATE_OP, 202,
                "scenario 3 create");
        for (int i = 1; i <= 4; i++) {
            assertRequestLine(requests.get(i), "GET", "/api/snapservice/tasks/task-0003", null, TASK_OP,
                    200, "scenario 3 poll " + i);
        }
        for (Map<String, Object> request : requests) {
            if (SNAPSHOT_OP.equals(request.get("operation_id"))) {
                fail("scenario 3 must not read the snapshot back after the task failed, but it called "
                        + request.get("path"));
            }
        }
    }

    /** The task never terminates. The client must stop after maxPollAttempts and report it. */
    private static void scenarioPollBudgetExhausted(String baseUrl) throws Exception {
        System.out.println();
        System.out.println("scenario 4: task stays RUNNING, client must give up after 3 polls");
        SnapshotProtectionClient client = new SnapshotProtectionClient(
                baseUrl, SESSION_ID, 25, 3, mock.httpClient());

        try {
            SnapshotProtectionClient.Snapshot snapshot =
                    client.createProtectionGroupSnapshot(CLUSTER, PG, "stuck-forever", null, null);
            fail("scenario 4 should have raised IOException but returned " + snapshot);
        } catch (IOException expected) {
            // correct: the poll budget ran out without a terminal status
        } catch (Throwable t) {
            fail("scenario 4 should have raised IOException but threw " + render(t));
        }

        List<Map<String, Object>> requests = drain();
        if (!count(requests, 4, "scenario 4")) {
            return;
        }
        assertRequestLine(requests.get(0), "POST", SNAPSHOTS_PATH, "vmw-task=true", CREATE_OP, 202,
                "scenario 4 create");
        for (int i = 1; i <= 3; i++) {
            assertRequestLine(requests.get(i), "GET", "/api/snapservice/tasks/task-0004", null, TASK_OP,
                    200, "scenario 4 poll " + i);
        }
    }

    /** Consecutive polls must be separated by the declared, interruptible pause. */
    private static void scenarioPollingPauseIsInterruptible(String baseUrl) throws Exception {
        System.out.println();
        System.out.println("scenario 5: interrupting the pause must stop before the second poll");
        SnapshotProtectionClient client = new SnapshotProtectionClient(
                baseUrl, SESSION_ID, 60_000, 3, mock.httpClient());
        AtomicReference<Throwable> outcome = new AtomicReference<>();

        Thread worker = new Thread(() -> {
            try {
                SnapshotProtectionClient.Snapshot snapshot = client.createProtectionGroupSnapshot(
                        CLUSTER, PG, "pause-interrupt-probe", null, null);
                outcome.set(new AssertionError("returned " + snapshot));
            } catch (Throwable t) {
                outcome.set(t);
            } finally {
                mock.signalPauseProbeClientDone();
            }
        }, "poll-pause-probe");
        worker.start();

        if (!mock.awaitPauseProbeEvent(5, TimeUnit.SECONDS)) {
            fail("scenario 5 timed out waiting for the client");
        } else if (!mock.pauseProbeFirstPollObserved()) {
            fail("scenario 5 client ended before making its first task poll: " + render(outcome.get()));
        } else {
            worker.interrupt();
        }
        mock.releasePauseProbeFirstPoll();
        worker.join(5_000);
        if (worker.isAlive()) {
            fail("scenario 5 client did not stop after its polling pause was interrupted");
            worker.interrupt();
        } else if (!(outcome.get() instanceof InterruptedException)) {
            fail("scenario 5 should have propagated InterruptedException from the polling pause, found "
                    + render(outcome.get()));
        }

        List<Map<String, Object>> requests = drain();
        if (!count(requests, 2, "scenario 5")) {
            return;
        }
        assertRequestLine(requests.get(0), "POST", SNAPSHOTS_PATH, "vmw-task=true", CREATE_OP, 202,
                "scenario 5 create");
        assertRequestLine(requests.get(1), "GET", "/api/snapservice/tasks/task-0005", null, TASK_OP,
                200, "scenario 5 first poll");
    }

    /** Nothing the client sent may fall outside the three operations the contract names. */
    private static void everyRequestStayedOnContract() throws IOException {
        List<Map<String, Object>> all = MockSnapserviceServer.readRequestLog(REQUEST_LOG);
        for (Map<String, Object> request : all) {
            if (request.get("operation_id") == null) {
                fail("request #" + request.get("seq") + " " + request.get("method") + " "
                        + request.get("path") + " matched no operation in docs/contract.json");
            }
            Object status = request.get("status");
            if (status instanceof Long && ((Long) status) >= 400L) {
                fail("request #" + request.get("seq") + " " + request.get("method") + " "
                        + request.get("path") + " was rejected by the appliance with status " + status
                        + "; body was " + request.get("body"));
            }
        }
        System.out.println();
        System.out.println("checked " + all.size() + " logged requests against docs/contract.json");
    }

    // ---------------------------------------------------------------- assertions

    private static void assertRequestLine(Map<String, Object> request, String method, String path,
                                          String query, String operationId, int status, String label) {
        eq(request.get("method"), method, label + " method");
        eq(request.get("path"), path, label + " path");
        if (query == null) {
            if (request.get("query") != null) {
                fail(label + " should carry no query string, found ?" + request.get("query"));
            }
        } else {
            eq(request.get("query"), query, label + " query string");
        }
        eq(request.get("operation_id"), operationId, label + " operation");
        eq(request.get("status"), (long) status, label + " response status");
    }

    private static void assertSessionHeader(Map<String, Object> request, String label) {
        Object value = headers(request).get(MockSnapserviceServer.SESSION_HEADER);
        if (!SESSION_ID.equals(value)) {
            fail(label + " must send " + MockSnapserviceServer.SESSION_HEADER + ": " + SESSION_ID
                    + ", found " + value);
        }
    }

    private static void assertJsonContentType(Map<String, Object> request, String label) {
        Object value = headers(request).get("content-type");
        if (!(value instanceof String)
                || !((String) value).toLowerCase(Locale.ROOT).startsWith("application/json")) {
            fail(label + " must send Content-Type: application/json, found " + value);
        }
    }

    private static void assertEmptyBody(Map<String, Object> request, String label) {
        String raw = String.valueOf(request.get("body"));
        if (!raw.isEmpty()) {
            fail(label + " is a GET and must send no request body, found " + raw);
        }
    }

    private static Map<String, Object> headers(Map<String, Object> request) {
        return Json.asObject(request.get("headers"));
    }

    private static Map<String, Object> body(Map<String, Object> request, String label) {
        String raw = String.valueOf(request.get("body"));
        try {
            return Json.asObject(Json.parse(raw));
        } catch (RuntimeException e) {
            fail(label + " body is not a JSON object: " + raw);
            return null;
        }
    }

    private static boolean count(List<Map<String, Object>> requests, int expected, String label) {
        if (requests.size() == expected) {
            return true;
        }
        StringBuilder sb = new StringBuilder(label + " should have made exactly " + expected
                + " requests, made " + requests.size() + ":");
        for (Map<String, Object> request : requests) {
            sb.append("\n      ").append(request.get("method")).append(' ').append(request.get("path"));
            if (request.get("query") != null) {
                sb.append('?').append(request.get("query"));
            }
            sb.append(" -> ").append(request.get("status"));
        }
        fail(sb.toString());
        return false;
    }

    private static void eq(Object actual, Object expected, String what) {
        if (expected == null ? actual == null : expected.equals(actual)) {
            return;
        }
        fail(what + " should be " + expected + ", found " + actual);
    }

    private static void fail(String message) {
        FAILURES.add(message);
        System.out.println("  FAIL " + message);
    }

    private static String render(Throwable t) {
        return t.getClass().getName() + ": " + t.getMessage();
    }

    private static List<Map<String, Object>> drain() {
        try {
            List<Map<String, Object>> all = MockSnapserviceServer.readRequestLog(REQUEST_LOG);
            List<Map<String, Object>> slice = new ArrayList<>(all.subList(consumed, all.size()));
            consumed = all.size();
            return slice;
        } catch (IOException e) {
            fail("could not read the request log: " + e);
            return new ArrayList<>();
        }
    }

    private TestMain() {
    }
}
