import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Acceptance harness for the VCF Operations 9.1 alerting-rollout change.
 *
 * Runs entirely on the loopback interface. No live VMware endpoint is contacted.
 *
 *   1. Starts MockOps, an HTTP server pinned to docs/contract.json. It serves only the four
 *      operations the contract names and appends one JSON line per request to mock-requests.log.
 *   2. Calls VcfOpsChangeClient.applyChange with the contents of change-request.json.
 *   3. Verifies the exact request wire shape from the log, then verifies the returned report.
 */
public class TestMain {

    /** Sentinel for a JSON null, so "key present with null value" stays distinguishable from "key absent". */
    static final Object JNULL = new Object() {
        @Override public String toString() { return "null"; }
    };

    static final String TOKEN = "3d7b1f90-8a4c-4e62-9b15-77c0a2e6d418::e5f1";
    static final String AUTH_HEADER_VALUE = "OpsToken " + TOKEN;
    static final String SYMPTOM_ID = "6f2b6a2c-9c31-4b0e-b0a3-3f6a1e2d7c40";
    static final String ALERT_ID = "b4e7c1a8-2d55-4f19-8a6c-9e0b3d7f5a21";
    static final String RULE_ID = "81a93f76-71de-4f25-b9d0-e8a70b1334cc";
    static final String RULE_FAILURE_MESSAGE =
            "Notification plugin instance f0e4b9c2-6b3f-4a51-9e77-2c1d5a8b0e33 is not configured on this node";
    static final int RULE_FAILURE_API_CODE = 1412;

    static final String[] OPERATION_IDS = {
            "acquireToken", "createSymptomDefinition", "createAlertDefinition",
            "createNotificationPluginRule"
    };
    static final String[] OPERATION_PATHS = {
            "/suite-api/api/auth/token/acquire", "/suite-api/api/symptomdefinitions",
            "/suite-api/api/alertdefinitions", "/suite-api/api/notifications/rules"
    };
    static final long[] SUCCESS_STATUSES = {200L, 201L, 201L, 201L};
    static final String[] RESOURCE_IDS = {null, SYMPTOM_ID, ALERT_ID, RULE_ID};

    static final Path LOG_PATH = Paths.get("mock-requests.log");

    static final List<String> FAILURES = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        Files.deleteIfExists(LOG_PATH);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try {
                Files.deleteIfExists(LOG_PATH);
            } catch (java.io.IOException ignored) {
                // Best-effort fallback; main also removes the log after all assertions have read it.
            }
        }, "mock-request-log-cleanup"));

        Map<String, Object> contract = asObject(Json.parse(readFile(Paths.get("docs/contract.json"))),
                "docs/contract.json");
        String changeRequest = readFile(Paths.get("change-request.json"));

        Scenario primary = execute(contract, changeRequest, "createNotificationPluginRule");
        verifyClientResult(primary, "final-step rejection");
        verifyRequests(primary.log);
        if (primary.report != null) {
            verifyReport(primary.report);
        }

        // Exercise every earlier rejection point. These scenarios make the stop-at-first-rejection
        // and FAILED versus PARTIAL_FAILURE requirements observable instead of assuming that the
        // final-step scenario generalizes.
        for (int failedIndex = 0; failedIndex < 3; failedIndex++) {
            Scenario rejected = execute(contract, changeRequest, OPERATION_IDS[failedIndex]);
            String label = "rejection at " + OPERATION_IDS[failedIndex];
            verifyClientResult(rejected, label);
            verifyAttemptSequence(rejected.log, failedIndex + 1, label);
            if (rejected.report != null) {
                verifyScenarioReport(rejected.report, failedIndex, label);
            }
        }

        Scenario successful = execute(contract, changeRequest, null);
        verifyClientResult(successful, "all-success run");
        verifyAttemptSequence(successful.log, OPERATION_IDS.length, "all-success run");
        if (successful.report != null) {
            verifyScenarioReport(successful.report, -1, "all-success run");
        }

        Files.deleteIfExists(LOG_PATH);

        if (FAILURES.isEmpty()) {
            System.out.println("PASS  wire requests verified against docs/contract.json");
            System.out.println("PASS  rejection and success reports verified");
            System.out.println("OK");
        } else {
            System.out.println("FAILURES (" + FAILURES.size() + "):");
            for (String f : FAILURES) {
                System.out.println("  - " + f);
            }
            System.exit(1);
        }
    }

    static Scenario execute(Map<String, Object> contract, String changeRequest,
                            String rejectedOperationId) throws Exception {
        Files.deleteIfExists(LOG_PATH);
        MockOps mock = new MockOps(contract, rejectedOperationId);
        mock.start();
        ClientRunner runner = new ClientRunner("http://127.0.0.1:" + mock.port(), changeRequest);
        Thread t = new Thread(runner, "vcfops-client");
        t.setDaemon(true);
        try {
            t.start();
            t.join(15_000L);
            if (t.isAlive()) {
                fail("applyChange did not return within 15s for "
                        + (rejectedOperationId == null ? "the all-success run" : rejectedOperationId));
                t.interrupt();
            }
        } finally {
            mock.stop();
        }
        return new Scenario(runner.report, runner.error, readLog());
    }

    static void verifyClientResult(Scenario scenario, String label) {
        if (scenario.error != null) {
            fail(label + ": applyChange threw " + scenario.error.getClass().getName() + ": "
                    + scenario.error.getMessage() + " -- a rejected step must be reported, not thrown");
        } else if (scenario.report == null) {
            fail(label + ": applyChange returned null instead of a report JSON document");
        }
    }

    static final class Scenario {
        final String report;
        final Throwable error;
        final List<Map<String, Object>> log;

        Scenario(String report, Throwable error, List<Map<String, Object>> log) {
            this.report = report;
            this.error = error;
            this.log = log;
        }
    }

    static final class ClientRunner implements Runnable {
        final String baseUrl;
        final String changeRequest;
        volatile String report;
        volatile Throwable error;

        ClientRunner(String baseUrl, String changeRequest) {
            this.baseUrl = baseUrl;
            this.changeRequest = changeRequest;
        }

        @Override public void run() {
            try {
                report = new VcfOpsChangeClient(baseUrl).applyChange(changeRequest);
            } catch (Throwable t) {
                error = t;
            }
        }
    }

    // ---------------------------------------------------------------- request verification

    static void verifyRequests(List<Map<String, Object>> log) {
        String[][] expected = {
                {"POST", "/suite-api/api/auth/token/acquire", "acquireToken"},
                {"POST", "/suite-api/api/symptomdefinitions", "createSymptomDefinition"},
                {"POST", "/suite-api/api/alertdefinitions", "createAlertDefinition"},
                {"POST", "/suite-api/api/notifications/rules", "createNotificationPluginRule"},
        };

        for (Map<String, Object> entry : log) {
            if (entry.get("operationId") == JNULL || entry.get("operationId") == null) {
                fail("off-contract request " + entry.get("method") + " " + entry.get("path")
                        + " -- only the four operations in docs/contract.json may be called");
            }
        }

        if (log.size() != expected.length) {
            fail("expected exactly " + expected.length + " requests, saw " + log.size() + ": " + describe(log));
            return;
        }

        for (int i = 0; i < expected.length; i++) {
            Map<String, Object> entry = log.get(i);
            String where = "request " + (i + 1) + " (" + expected[i][2] + ")";
            expectEquals(where + " method", expected[i][0], entry.get("method"));
            expectEquals(where + " path", expected[i][1], entry.get("path"));
            if (entry.get("query") != JNULL) {
                fail(where + " carried a query string (" + entry.get("query") + "); the contract defines none");
            }
            Map<String, Object> headers = asObject(entry.get("headers"), where + " headers");
            Object contentType = headers.get("content-type");
            if (!(contentType instanceof String) || !((String) contentType).toLowerCase(Locale.ROOT)
                    .startsWith("application/json")) {
                fail(where + " Content-Type was " + contentType + ", expected application/json");
            }
            Object auth = headers.get("authorization");
            if (i == 0) {
                if (auth != null) {
                    fail(where + " sent an Authorization header (" + auth + ") before a token existed");
                }
            } else {
                expectEquals(where + " Authorization", AUTH_HEADER_VALUE, auth);
            }
        }

        Object body1 = parseBody(log.get(0), "acquireToken");
        Object body2 = parseBody(log.get(1), "createSymptomDefinition");
        Object body3 = parseBody(log.get(2), "createAlertDefinition");
        Object body4 = parseBody(log.get(3), "createNotificationPluginRule");

        // Nothing anywhere in a request body may be a null, an empty string, or an empty container:
        // an optional the change request does not supply is omitted, not sent hollow.
        scanForHollowValues(body1, "acquireToken body", "$");
        scanForHollowValues(body2, "createSymptomDefinition body", "$");
        scanForHollowValues(body3, "createAlertDefinition body", "$");
        scanForHollowValues(body4, "createNotificationPluginRule body", "$");

        // Optionals the change request leaves unset must not appear at all.
        expectAbsent(body1, "acquireToken body", "authSource");
        expectAbsent(body2, "createSymptomDefinition body", "id", "cancelCycles", "realtimeMonitoringEnabled");
        expectAbsent(body3, "createAlertDefinition body", "id", "type", "subType", "forVCDTenants");
        expectAbsent(body4, "createNotificationPluginRule body",
                "id", "templateId", "enabled", "sendHeartbeat", "resourceFilter", "resourceKindFilter");

        expectEqualJson("acquireToken body",
                "{\"username\":\"svc-alerting\",\"password\":\"Ch4nge-M3-At-Rollout\"}", body1);

        expectEqualJson("createSymptomDefinition body",
                "{\"name\":\"Datastore write latency above 25 ms\","
                        + "\"adapterKindKey\":\"VMWARE\",\"resourceKindKey\":\"Datastore\",\"waitCycles\":3,"
                        + "\"state\":{\"severity\":\"WARNING\",\"condition\":{\"type\":\"CONDITION_HT\","
                        + "\"key\":\"storage|totalWriteLatency_average\",\"operator\":\"GT\","
                        + "\"valueType\":\"NUMERIC\",\"thresholdType\":\"STATIC\",\"instanced\":false,"
                        + "\"value\":\"25\"}}}", body2);

        expectEqualJson("createAlertDefinition body",
                "{\"name\":\"Datastore write latency degradation\","
                        + "\"description\":\"Raised when datastore write latency stays above 25 ms.\","
                        + "\"adapterKindKey\":\"VMWARE\",\"resourceKindKey\":\"Datastore\","
                        + "\"waitCycles\":1,\"cancelCycles\":1,"
                        + "\"states\":[{\"severity\":\"WARNING\","
                        + "\"impact\":{\"impactType\":\"BADGE\",\"detail\":\"health\"},"
                        + "\"base-symptom-set\":{\"type\":\"SYMPTOM_SET\",\"relation\":\"SELF\","
                        + "\"aggregation\":\"ALL\",\"symptomSetOperator\":\"AND\","
                        + "\"symptomDefinitionIds\":[\"" + SYMPTOM_ID + "\"]}}]}", body3);

        expectEqualJson("createNotificationPluginRule body",
                "{\"name\":\"Page storage on-call for datastore latency\","
                        + "\"pluginId\":\"f0e4b9c2-6b3f-4a51-9e77-2c1d5a8b0e33\",\"ruleType\":\"ALERT\","
                        + "\"properties\":[{\"name\":\"toAddress\",\"value\":\"storage-oncall@example.com\"}],"
                        + "\"alertDefinitionIdFilters\":{\"values\":[\"" + ALERT_ID + "\"]}}", body4);

        // Integer-typed properties travel as JSON integers, not as 3.0.
        expectJsonInteger(body2, "createSymptomDefinition body", "waitCycles");
        expectJsonInteger(body3, "createAlertDefinition body", "waitCycles");
        expectJsonInteger(body3, "createAlertDefinition body", "cancelCycles");
    }

    static void verifyAttemptSequence(List<Map<String, Object>> log, int expectedCount, String label) {
        for (Map<String, Object> entry : log) {
            if (entry.get("operationId") == JNULL || entry.get("operationId") == null) {
                fail(label + ": off-contract request " + entry.get("method") + " " + entry.get("path"));
            }
        }
        if (log.size() != expectedCount) {
            fail(label + ": expected exactly " + expectedCount + " attempted operation(s), saw "
                    + log.size() + ": " + describe(log));
            return;
        }
        for (int i = 0; i < expectedCount; i++) {
            String where = label + " request " + (i + 1);
            expectEquals(where + " method", "POST", log.get(i).get("method"));
            expectEquals(where + " path", OPERATION_PATHS[i], log.get(i).get("path"));
            expectEquals(where + " operationId", OPERATION_IDS[i], log.get(i).get("operationId"));
        }
    }

    // ---------------------------------------------------------------- report verification

    static void verifyReport(String reportText) {
        Object parsed;
        try {
            parsed = Json.parse(reportText);
        } catch (RuntimeException e) {
            fail("report is not valid JSON: " + e.getMessage());
            return;
        }
        if (!(parsed instanceof Map)) {
            fail("report must be a JSON object, got " + typeName(parsed));
            return;
        }
        Map<String, Object> report = asObject(parsed, "report");

        if (reportText.contains("Ch4nge-M3-At-Rollout")) {
            fail("report leaks the account password");
        }
        if (reportText.contains(TOKEN)) {
            fail("report leaks the session token");
        }

        expectEquals("report.outcome", "PARTIAL_FAILURE", report.get("outcome"));

        Object stepsRaw = report.get("steps");
        if (!(stepsRaw instanceof List)) {
            fail("report.steps must be an array, got " + typeName(stepsRaw));
            return;
        }
        List<?> steps = (List<?>) stepsRaw;
        String[] ids = {"acquireToken", "createSymptomDefinition", "createAlertDefinition",
                "createNotificationPluginRule"};
        if (steps.size() != ids.length) {
            fail("report.steps must hold " + ids.length + " entries, one per attempted operation, got "
                    + steps.size());
            return;
        }

        Object[][] want = {
                {"acquireToken", "SUCCEEDED", 200L, null},
                {"createSymptomDefinition", "SUCCEEDED", 201L, SYMPTOM_ID},
                {"createAlertDefinition", "SUCCEEDED", 201L, ALERT_ID},
                {"createNotificationPluginRule", "FAILED", 422L, null},
        };
        for (int i = 0; i < want.length; i++) {
            String where = "report.steps[" + i + "]";
            if (!(steps.get(i) instanceof Map)) {
                fail(where + " must be an object, got " + typeName(steps.get(i)));
                continue;
            }
            Map<String, Object> step = asObject(steps.get(i), where);
            expectEquals(where + ".operationId", want[i][0], step.get("operationId"));
            expectEquals(where + ".status", want[i][1], step.get("status"));
            expectNumberEquals(where + ".httpStatus", (Long) want[i][2], step.get("httpStatus"));
            if (want[i][3] != null) {
                expectEquals(where + ".resourceId", want[i][3], step.get("resourceId"));
            } else if (step.containsKey("resourceId")) {
                fail(where + " (" + want[i][0] + ") reports a resourceId but created nothing");
            }
            if ("FAILED".equals(want[i][1])) {
                expectEquals(where + ".errorMessage", RULE_FAILURE_MESSAGE, step.get("errorMessage"));
            } else if (step.containsKey("errorMessage")) {
                fail(where + " succeeded but carries an errorMessage");
            }
        }

        Object createdRaw = report.get("createdResources");
        if (!(createdRaw instanceof List)) {
            fail("report.createdResources must be an array, got " + typeName(createdRaw));
            return;
        }
        List<?> created = (List<?>) createdRaw;
        if (created.size() != 2) {
            fail("report.createdResources must list exactly the 2 objects that survive on the appliance, got "
                    + created.size() + " -- " + Json.write(createdRaw));
            return;
        }
        String[][] wantCreated = {
                {"createSymptomDefinition", SYMPTOM_ID},
                {"createAlertDefinition", ALERT_ID},
        };
        for (int i = 0; i < wantCreated.length; i++) {
            String where = "report.createdResources[" + i + "]";
            if (!(created.get(i) instanceof Map)) {
                fail(where + " must be an object, got " + typeName(created.get(i)));
                continue;
            }
            Map<String, Object> c = asObject(created.get(i), where);
            expectEquals(where + ".operationId", wantCreated[i][0], c.get("operationId"));
            expectEquals(where + ".id", wantCreated[i][1], c.get("id"));
        }
    }

    /** Verifies the branches not covered by the detailed final-step fixture assertion above. */
    static void verifyScenarioReport(String reportText, int failedIndex, String label) {
        Object parsed;
        try {
            parsed = Json.parse(reportText);
        } catch (RuntimeException e) {
            fail(label + ": report is not valid JSON: " + e.getMessage());
            return;
        }
        if (!(parsed instanceof Map)) {
            fail(label + ": report must be a JSON object, got " + typeName(parsed));
            return;
        }
        Map<String, Object> report = asObject(parsed, label + " report");
        verifyNoSecrets(reportText, label);

        int attempted = failedIndex < 0 ? OPERATION_IDS.length : failedIndex + 1;
        int createdCount = failedIndex < 0 ? 3 : Math.max(0, failedIndex - 1);
        String expectedOutcome = failedIndex < 0 ? "SUCCEEDED"
                : (createdCount == 0 ? "FAILED" : "PARTIAL_FAILURE");
        expectEquals(label + " report.outcome", expectedOutcome, report.get("outcome"));

        Object stepsRaw = report.get("steps");
        if (!(stepsRaw instanceof List)) {
            fail(label + ": report.steps must be an array, got " + typeName(stepsRaw));
            return;
        }
        List<?> steps = (List<?>) stepsRaw;
        if (steps.size() != attempted) {
            fail(label + ": report.steps must contain only the " + attempted
                    + " attempted operation(s), got " + steps.size());
            return;
        }
        for (int i = 0; i < attempted; i++) {
            String where = label + " report.steps[" + i + "]";
            if (!(steps.get(i) instanceof Map)) {
                fail(where + " must be an object, got " + typeName(steps.get(i)));
                continue;
            }
            Map<String, Object> step = asObject(steps.get(i), where);
            boolean failed = i == failedIndex;
            expectEquals(where + ".operationId", OPERATION_IDS[i], step.get("operationId"));
            expectEquals(where + ".status", failed ? "FAILED" : "SUCCEEDED", step.get("status"));
            long expectedHttpStatus = failed ? (i == 0 ? 401L : 422L) : SUCCESS_STATUSES[i];
            expectNumberEquals(where + ".httpStatus", expectedHttpStatus, step.get("httpStatus"));

            String expectedResourceId = failed ? null : RESOURCE_IDS[i];
            if (expectedResourceId == null) {
                if (step.containsKey("resourceId")) {
                    fail(where + " reports a resourceId but created nothing");
                }
            } else {
                expectEquals(where + ".resourceId", expectedResourceId, step.get("resourceId"));
            }
            if (failed) {
                expectEquals(where + ".errorMessage", forcedFailureMessage(OPERATION_IDS[i]),
                        step.get("errorMessage"));
            } else if (step.containsKey("errorMessage")) {
                fail(where + " succeeded but carries an errorMessage");
            }
        }

        Object createdRaw = report.get("createdResources");
        if (!(createdRaw instanceof List)) {
            fail(label + ": report.createdResources must be an array, got " + typeName(createdRaw));
            return;
        }
        List<?> created = (List<?>) createdRaw;
        if (created.size() != createdCount) {
            fail(label + ": report.createdResources expected " + createdCount + " entries, got "
                    + created.size() + " -- " + Json.write(createdRaw));
            return;
        }
        for (int i = 0; i < createdCount; i++) {
            int operationIndex = i + 1;
            String where = label + " report.createdResources[" + i + "]";
            if (!(created.get(i) instanceof Map)) {
                fail(where + " must be an object, got " + typeName(created.get(i)));
                continue;
            }
            Map<String, Object> resource = asObject(created.get(i), where);
            expectEquals(where + ".operationId", OPERATION_IDS[operationIndex],
                    resource.get("operationId"));
            expectEquals(where + ".id", RESOURCE_IDS[operationIndex], resource.get("id"));
        }
    }

    static void verifyNoSecrets(String reportText, String label) {
        if (reportText.contains("Ch4nge-M3-At-Rollout")) {
            fail(label + ": report leaks the account password");
        }
        if (reportText.contains(TOKEN)) {
            fail(label + ": report leaks the session token");
        }
    }

    static String forcedFailureMessage(String operationId) {
        return "createNotificationPluginRule".equals(operationId)
                ? RULE_FAILURE_MESSAGE : "Forced rejection at " + operationId;
    }

    // ---------------------------------------------------------------- assertion helpers

    static void fail(String message) {
        FAILURES.add(message);
    }

    static void expectEquals(String where, Object expected, Object actual) {
        if (expected == null ? actual == null : expected.equals(actual)) {
            return;
        }
        fail(where + ": expected " + render(expected) + ", got " + render(actual));
    }

    static void expectNumberEquals(String where, Long expected, Object actual) {
        if (actual instanceof Number && ((Number) actual).doubleValue() == expected.doubleValue()) {
            return;
        }
        fail(where + ": expected " + expected + ", got " + render(actual));
    }

    static void expectAbsent(Object body, String where, String... keys) {
        if (!(body instanceof Map)) {
            return;
        }
        Map<?, ?> map = (Map<?, ?>) body;
        for (String key : keys) {
            if (map.containsKey(key)) {
                fail(where + " sent optional property \"" + key + "\" (as " + render(map.get(key))
                        + ") although the change request does not set it; unset optionals are omitted");
            }
        }
    }

    static void expectJsonInteger(Object body, String where, String key) {
        if (!(body instanceof Map)) {
            return;
        }
        Object v = ((Map<?, ?>) body).get(key);
        if (v != null && !(v instanceof Long)) {
            fail(where + " sent \"" + key + "\" as " + render(v)
                    + "; the property is integer-typed and must travel as a JSON integer");
        }
    }

    static void expectEqualJson(String where, String expectedJson, Object actual) {
        Object expected = Json.parse(expectedJson);
        List<String> diffs = new ArrayList<>();
        diff("$", expected, actual, diffs);
        for (String d : diffs) {
            fail(where + " " + d);
        }
    }

    /** Structural comparison: object keys are unordered, array elements are ordered. */
    static void diff(String path, Object expected, Object actual, List<String> out) {
        if (out.size() > 12) {
            return;
        }
        if (expected instanceof Map) {
            if (!(actual instanceof Map)) {
                out.add("at " + path + ": expected an object, got " + render(actual));
                return;
            }
            Map<?, ?> e = (Map<?, ?>) expected;
            Map<?, ?> a = (Map<?, ?>) actual;
            for (Map.Entry<?, ?> entry : e.entrySet()) {
                if (!a.containsKey(entry.getKey())) {
                    out.add("at " + path + ": missing property \"" + entry.getKey() + "\"");
                } else {
                    diff(path + "." + entry.getKey(), entry.getValue(), a.get(entry.getKey()), out);
                }
            }
            for (Object k : a.keySet()) {
                if (!e.containsKey(k)) {
                    out.add("at " + path + ": unexpected property \"" + k + "\" = " + render(a.get(k)));
                }
            }
            return;
        }
        if (expected instanceof List) {
            if (!(actual instanceof List)) {
                out.add("at " + path + ": expected an array, got " + render(actual));
                return;
            }
            List<?> e = (List<?>) expected;
            List<?> a = (List<?>) actual;
            if (e.size() != a.size()) {
                out.add("at " + path + ": expected " + e.size() + " elements, got " + a.size());
                return;
            }
            for (int i = 0; i < e.size(); i++) {
                diff(path + "[" + i + "]", e.get(i), a.get(i), out);
            }
            return;
        }
        if (expected instanceof Number && actual instanceof Number) {
            if (((Number) expected).doubleValue() != ((Number) actual).doubleValue()) {
                out.add("at " + path + ": expected " + render(expected) + ", got " + render(actual));
            }
            return;
        }
        if (expected == null ? actual != null : !expected.equals(actual)) {
            out.add("at " + path + ": expected " + render(expected) + ", got " + render(actual));
        }
    }

    static void scanForHollowValues(Object node, String where, String path) {
        if (node == JNULL) {
            fail(where + ": " + path + " is JSON null; omit the property instead");
        } else if (node instanceof String && ((String) node).isEmpty()) {
            fail(where + ": " + path + " is an empty string; omit the property instead");
        } else if (node instanceof Map) {
            Map<?, ?> map = (Map<?, ?>) node;
            if (map.isEmpty()) {
                fail(where + ": " + path + " is an empty object; omit the property instead");
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                scanForHollowValues(e.getValue(), where, path + "." + e.getKey());
            }
        } else if (node instanceof List) {
            List<?> list = (List<?>) node;
            if (list.isEmpty()) {
                fail(where + ": " + path + " is an empty array; omit the property instead");
            }
            for (int i = 0; i < list.size(); i++) {
                scanForHollowValues(list.get(i), where, path + "[" + i + "]");
            }
        }
    }

    // ---------------------------------------------------------------- log handling

    static List<Map<String, Object>> readLog() throws Exception {
        List<Map<String, Object>> out = new ArrayList<>();
        if (!Files.exists(LOG_PATH)) {
            fail("the client sent no requests at all (" + LOG_PATH + " was never written)");
            return out;
        }
        for (String line : Files.readAllLines(LOG_PATH, StandardCharsets.UTF_8)) {
            if (!line.trim().isEmpty()) {
                out.add(asObject(Json.parse(line), "log line"));
            }
        }
        return out;
    }

    static Object parseBody(Map<String, Object> entry, String operationId) {
        Object raw = entry.get("body");
        if (!(raw instanceof String)) {
            fail(operationId + " sent no request body");
            return new LinkedHashMap<String, Object>();
        }
        try {
            return Json.parse((String) raw);
        } catch (RuntimeException e) {
            fail(operationId + " request body is not valid JSON: " + e.getMessage());
            return new LinkedHashMap<String, Object>();
        }
    }

    static String describe(List<Map<String, Object>> log) {
        StringBuilder sb = new StringBuilder();
        for (Map<String, Object> e : log) {
            if (sb.length() > 0) {
                sb.append(", ");
            }
            sb.append(e.get("method")).append(' ').append(e.get("path"));
        }
        return sb.length() == 0 ? "(none)" : sb.toString();
    }

    // ---------------------------------------------------------------- the mock appliance

    /**
     * Loopback stand-in for a VCF Operations node. Routes are built from docs/contract.json, so the
     * mock answers exactly the operations the contract names and 404s everything else.
     */
    static final class MockOps {
        private final HttpServer server;
        private final Map<String, Map<String, Object>> routes = new LinkedHashMap<>();
        private final AtomicInteger seq = new AtomicInteger();
        private final Object logLock = new Object();
        private final String rejectedOperationId;

        MockOps(Map<String, Object> contract, String rejectedOperationId) throws Exception {
            this.rejectedOperationId = rejectedOperationId;
            for (Object o : (List<?>) contract.get("operations")) {
                Map<String, Object> op = asObject(o, "contract operation");
                routes.put(op.get("method") + " " + op.get("request_path"), op);
            }
            server = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
            server.createContext("/", this::handle);
            server.setExecutor(null);
        }

        void start() { server.start(); }

        void stop() { server.stop(0); }

        int port() { return server.getAddress().getPort(); }

        private void handle(HttpExchange exchange) throws java.io.IOException {
            String method = exchange.getRequestMethod();
            String path = exchange.getRequestURI().getPath();
            String query = exchange.getRequestURI().getRawQuery();
            byte[] bodyBytes = readAll(exchange.getRequestBody());
            String body = new String(bodyBytes, StandardCharsets.UTF_8);

            Map<String, Object> headers = new LinkedHashMap<>();
            for (Map.Entry<String, List<String>> h : exchange.getRequestHeaders().entrySet()) {
                headers.put(h.getKey().toLowerCase(Locale.ROOT), String.join(", ", h.getValue()));
            }

            Map<String, Object> op = routes.get(method + " " + path);
            int status;
            String responseBody;

            if (op == null) {
                status = 404;
                responseBody = errorBody(404, "No operation is mapped to " + method + " " + path);
            } else {
                Response r = respond(op, headers, body);
                status = r.status;
                responseBody = r.body;
            }

            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("seq", (long) seq.incrementAndGet());
            entry.put("method", method);
            entry.put("path", path);
            entry.put("query", query == null ? JNULL : query);
            entry.put("operationId", op == null ? JNULL : op.get("operationId"));
            entry.put("headers", headers);
            entry.put("body", body);
            entry.put("responseStatus", (long) status);
            synchronized (logLock) {
                Files.write(LOG_PATH, (Json.write(entry) + "\n").getBytes(StandardCharsets.UTF_8),
                        java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
            }

            byte[] out = responseBody.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(out);
            }
        }

        private Response respond(Map<String, Object> op, Map<String, Object> headers, String rawBody) {
            String operationId = (String) op.get("operationId");

            if (Boolean.TRUE.equals(op.get("requires_authorization"))) {
                Object auth = headers.get("authorization");
                if (!AUTH_HEADER_VALUE.equals(auth)) {
                    return new Response(401, errorBody(401,
                            "Authorization header must carry the acquired token as \"OpsToken <token>\""));
                }
            }

            Object parsed;
            try {
                parsed = Json.parse(rawBody);
            } catch (RuntimeException e) {
                return new Response(422, errorBody(422, "Request body is not valid JSON"));
            }
            if (!(parsed instanceof Map)) {
                return new Response(422, errorBody(422, "Request body must be a JSON object"));
            }
            Map<String, Object> body = asObject(parsed, "request body");

            Map<String, Object> spec = asObject(op.get("request_body"), "contract request_body");
            List<?> required = (List<?>) spec.get("required_properties");
            List<?> optional = (List<?>) spec.get("optional_properties");
            for (Object r : required) {
                if (!body.containsKey(r) || body.get(r) == JNULL) {
                    return new Response(422, errorBody(422, "Missing required property \"" + r + "\" for "
                            + spec.get("schema")));
                }
            }
            for (String key : body.keySet()) {
                if (!required.contains(key) && !optional.contains(key)) {
                    return new Response(422, errorBody(422, "Unknown property \"" + key + "\" for "
                            + spec.get("schema")));
                }
            }

            if (operationId.equals(rejectedOperationId)) {
                int status = "acquireToken".equals(operationId) ? 401 : 422;
                Integer apiCode = "createNotificationPluginRule".equals(operationId)
                        ? RULE_FAILURE_API_CODE : null;
                return new Response(status, errorBody(status, forcedFailureMessage(operationId), apiCode));
            }

            switch (operationId) {
                case "acquireToken": {
                    Map<String, Object> t = new LinkedHashMap<>();
                    t.put("token", TOKEN);
                    t.put("validity", 1785000000000L);
                    t.put("expiresAt", "Tuesday, August 4, 2026 6:00:00 PM UTC");
                    t.put("roles", Arrays.asList("ContentAdmin"));
                    return new Response(200, Json.write(t));
                }
                case "createSymptomDefinition": {
                    Map<String, Object> created = new LinkedHashMap<>(body);
                    created.put("id", SYMPTOM_ID);
                    return new Response(201, Json.write(created));
                }
                case "createAlertDefinition": {
                    Map<String, Object> created = new LinkedHashMap<>(body);
                    created.put("id", ALERT_ID);
                    return new Response(201, Json.write(created));
                }
                case "createNotificationPluginRule": {
                    Map<String, Object> created = new LinkedHashMap<>(body);
                    created.put("id", RULE_ID);
                    return new Response(201, Json.write(created));
                }
                default:
                    return new Response(404, errorBody(404, "Unhandled operation " + operationId));
            }
        }

        private String errorBody(int status, String message) {
            return errorBody(status, message, null);
        }

        private String errorBody(int status, String message, Integer apiErrorCode) {
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("message", message);
            err.put("httpStatusCode", (long) status);
            if (apiErrorCode != null) {
                err.put("apiErrorCode", (long) (int) apiErrorCode);
            }
            return Json.write(err);
        }
    }

    static final class Response {
        final int status;
        final String body;

        Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    static byte[] readAll(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }

    // ---------------------------------------------------------------- misc

    static String readFile(Path p) throws Exception {
        return new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asObject(Object o, String what) {
        if (!(o instanceof Map)) {
            throw new IllegalStateException(what + " is not a JSON object");
        }
        return (Map<String, Object>) o;
    }

    static String typeName(Object o) {
        if (o == null) return "nothing";
        if (o == JNULL) return "null";
        if (o instanceof Map) return "an object";
        if (o instanceof List) return "an array";
        if (o instanceof String) return "a string";
        if (o instanceof Boolean) return "a boolean";
        return "a number";
    }

    static String render(Object o) {
        if (o == null) return "(absent)";
        return Json.write(o);
    }

    // ---------------------------------------------------------------- minimal JSON

    static final class Json {

        static Object parse(String text) {
            Parser p = new Parser(text);
            p.skipWs();
            Object v = p.value();
            p.skipWs();
            if (p.pos != text.length()) {
                throw new IllegalArgumentException("trailing content at offset " + p.pos);
            }
            return v;
        }

        static String write(Object v) {
            StringBuilder sb = new StringBuilder();
            writeTo(v, sb);
            return sb.toString();
        }

        private static void writeTo(Object v, StringBuilder sb) {
            if (v == null || v == JNULL) {
                sb.append("null");
            } else if (v instanceof String) {
                quote((String) v, sb);
            } else if (v instanceof Boolean || v instanceof Long || v instanceof Integer) {
                sb.append(v);
            } else if (v instanceof Double || v instanceof Float) {
                double d = ((Number) v).doubleValue();
                if (d == Math.rint(d) && !Double.isInfinite(d)) {
                    sb.append((long) d).append(".0");
                } else {
                    sb.append(d);
                }
            } else if (v instanceof Map) {
                sb.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                    if (!first) {
                        sb.append(',');
                    }
                    first = false;
                    quote(String.valueOf(e.getKey()), sb);
                    sb.append(':');
                    writeTo(e.getValue(), sb);
                }
                sb.append('}');
            } else if (v instanceof List) {
                sb.append('[');
                boolean first = true;
                for (Object e : (List<?>) v) {
                    if (!first) {
                        sb.append(',');
                    }
                    first = false;
                    writeTo(e, sb);
                }
                sb.append(']');
            } else {
                quote(String.valueOf(v), sb);
            }
        }

        private static void quote(String s, StringBuilder sb) {
            sb.append('"');
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    case '\b': sb.append("\\b"); break;
                    case '\f': sb.append("\\f"); break;
                    default:
                        if (c < 0x20) {
                            sb.append(String.format("\\u%04x", (int) c));
                        } else {
                            sb.append(c);
                        }
                }
            }
            sb.append('"');
        }

        private static final class Parser {
            final String s;
            int pos;

            Parser(String s) { this.s = s; }

            void skipWs() {
                while (pos < s.length() && Character.isWhitespace(s.charAt(pos))) {
                    pos++;
                }
            }

            Object value() {
                skipWs();
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unexpected end of input");
                }
                char c = s.charAt(pos);
                switch (c) {
                    case '{': return object();
                    case '[': return array();
                    case '"': return string();
                    case 't': expect("true"); return Boolean.TRUE;
                    case 'f': expect("false"); return Boolean.FALSE;
                    case 'n': expect("null"); return JNULL;
                    default: return number();
                }
            }

            void expect(String lit) {
                if (!s.startsWith(lit, pos)) {
                    throw new IllegalArgumentException("bad literal at offset " + pos);
                }
                pos += lit.length();
            }

            Map<String, Object> object() {
                Map<String, Object> m = new LinkedHashMap<>();
                pos++;
                skipWs();
                if (pos < s.length() && s.charAt(pos) == '}') {
                    pos++;
                    return m;
                }
                while (true) {
                    skipWs();
                    String k = string();
                    skipWs();
                    if (pos >= s.length() || s.charAt(pos) != ':') {
                        throw new IllegalArgumentException("expected ':' at offset " + pos);
                    }
                    pos++;
                    m.put(k, value());
                    skipWs();
                    if (pos >= s.length()) {
                        throw new IllegalArgumentException("unterminated object");
                    }
                    char c = s.charAt(pos++);
                    if (c == '}') {
                        return m;
                    }
                    if (c != ',') {
                        throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                    }
                }
            }

            List<Object> array() {
                List<Object> l = new ArrayList<>();
                pos++;
                skipWs();
                if (pos < s.length() && s.charAt(pos) == ']') {
                    pos++;
                    return l;
                }
                while (true) {
                    l.add(value());
                    skipWs();
                    if (pos >= s.length()) {
                        throw new IllegalArgumentException("unterminated array");
                    }
                    char c = s.charAt(pos++);
                    if (c == ']') {
                        return l;
                    }
                    if (c != ',') {
                        throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                    }
                }
            }

            String string() {
                if (pos >= s.length() || s.charAt(pos) != '"') {
                    throw new IllegalArgumentException("expected a string at offset " + pos);
                }
                pos++;
                StringBuilder sb = new StringBuilder();
                while (true) {
                    if (pos >= s.length()) {
                        throw new IllegalArgumentException("unterminated string");
                    }
                    char c = s.charAt(pos++);
                    if (c == '"') {
                        return sb.toString();
                    }
                    if (c != '\\') {
                        sb.append(c);
                        continue;
                    }
                    char e = s.charAt(pos++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                            pos += 4;
                            break;
                        default:
                            throw new IllegalArgumentException("bad escape \\" + e);
                    }
                }
            }

            Object number() {
                int start = pos;
                if (pos < s.length() && (s.charAt(pos) == '-' || s.charAt(pos) == '+')) {
                    pos++;
                }
                boolean fractional = false;
                while (pos < s.length()) {
                    char c = s.charAt(pos);
                    if (c >= '0' && c <= '9') {
                        pos++;
                    } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                        fractional = true;
                        pos++;
                    } else {
                        break;
                    }
                }
                String raw = s.substring(start, pos);
                if (raw.isEmpty()) {
                    throw new IllegalArgumentException("expected a number at offset " + start);
                }
                if (!fractional) {
                    try {
                        return Long.parseLong(raw);
                    } catch (NumberFormatException ignored) {
                        // fall through to double
                    }
                }
                return Double.parseDouble(raw);
            }
        }
    }
}
