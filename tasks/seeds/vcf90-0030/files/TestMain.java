import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Protected verifier for the SDDC Manager host commission precheck workflow.
 *
 * <p>Each case starts a fresh contract-pinned mock on the loopback interface, drives
 * {@link HostCommissionClient}, and then asserts the recorded request log: which operations were
 * sent, in what order, with which headers, and with exactly which JSON properties on the wire.
 * Nothing here contacts a live VMware endpoint.
 */
public class TestMain {

    private static final Path CONTRACT = Path.of("docs", "contract.json");

    private static final String HOST_A_FQDN = "esxi-07.vrack.vsphere.local";
    private static final String HOST_B_FQDN = "esxi-08.vrack.vsphere.local";
    private static final String NETWORK_POOL_ID = "5f2ba0a1-7c37-4d19-9df2-6ec2a4f5f4b1";
    private static final String WARNING_VALIDATION_ID = "c9b47c1e-2f0a-4a67-8f4c-0f31c2ae70d5";
    private static final String FAILED_VALIDATION_ID = "1d0f8a52-6b44-49c0-b7e3-9c5a2f80d411";
    private static final String SUCCEEDED_VALIDATION_ID = "7a3e5cf1-90bd-4f2a-8c66-e4b1d7a0325f";
    private static final String STUCK_VALIDATION_ID = "b4c81de6-33a7-4e58-9d02-5f7c6ab19e83";

    private static final Set<String> HOST_A_PROPERTIES = new LinkedHashSet<>(List.of(
            "fqdn", "username", "password", "storageType", "networkPoolId",
            "vvolStorageProtocolType", "networkPoolName", "sshThumbprint", "sslThumbprint"));
    private static final Set<String> HOST_B_PROPERTIES = new LinkedHashSet<>(List.of(
            "fqdn", "username", "password", "storageType", "networkPoolId"));

    private static int failures;

    public static void main(String[] args) throws Exception {
        HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();

        run("precheck WARNING leaves the gate closed", () -> warningBlocksCommission(http));
        run("precheck polls to COMPLETED/FAILED and commissions nothing",
                () -> failureAfterPollingBlocksCommission(http));
        run("all non-passing terminal execution statuses leave the gate closed",
                () -> terminalExecutionStatusesBlockCommission(http));
        run("CANCELLATION_IN_PROGRESS is polled until terminal",
                () -> cancellationInProgressIsPolled(http));
        run("precheck SUCCEEDED commissions with the exact wire shape", () -> successCommissionsOnce(http));
        run("precheck error response commissions nothing", () -> precheckErrorBlocksCommission(http));
        run("poll error response commissions nothing", () -> pollErrorBlocksCommission(http));
        run("commission error response is mapped", () -> commissionErrorIsMapped(http));
        run("precheck that never settles commissions nothing", () -> nonTerminalPrecheckBlocksCommission(http));
        run("null and blank host properties are rejected", TestMain::invalidHostPropertiesRejected);

        if (failures > 0) {
            System.out.println();
            System.out.println(failures + " case(s) failed");
            System.exit(1);
        }
        System.out.println();
        System.out.println("All cases passed");
    }

    // ------------------------------------------------------------------ cases

    private static void warningBlocksCommission(HttpClient http) throws Exception {
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(WARNING_VALIDATION_ID, "COMPLETED", "WARNING", List.of(
                check("SUCCEEDED", "Host FQDN resolves", "INFO"),
                check("WARNING", "Host is running an ESXi build newer than the management domain", "WARNING"),
                check("SUCCEEDED", "Network pool has free IP addresses", "INFO")));

        Path log = newLogFile("warning");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            HostCommissionClient.CommissionOutcome outcome = client.commission(List.of(hostB()));

            assertEquals("commissioned", false, outcome.commissioned);
            assertEquals("taskId", null, outcome.taskId);
            assertEquals("taskName", null, outcome.taskName);
            assertEquals("taskStatus", null, outcome.taskStatus);
            assertEquals("precheck.validationId", WARNING_VALIDATION_ID, outcome.precheck.validationId);
            assertEquals("precheck.description", "Validation for host commission", outcome.precheck.description);
            assertEquals("precheck.executionStatus", "COMPLETED", outcome.precheck.executionStatus);
            assertEquals("precheck.resultStatus", "WARNING", outcome.precheck.resultStatus);
            assertEquals("precheck.passed()", false, outcome.precheck.passed());
            assertEquals("precheck.pollCount", 0, outcome.precheck.pollCount);
            assertEquals("precheck.failedChecks",
                    List.of("Host is running an ESXi build newer than the management domain"),
                    outcome.precheck.failedChecks);
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("request count", 1, records.size());
        assertEquals("operation", "validateHostCommissionSpec", records.get(0).get("operationId"));
        assertJsonPostRequest("precheck", records.get(0));
        assertSingleHostArray("precheck body", Json.str(records.get(0).get("body")));
    }

    private static void failureAfterPollingBlocksCommission(HttpClient http) throws Exception {
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(FAILED_VALIDATION_ID, "IN_PROGRESS", "UNKNOWN", List.of());
        script.pollBodies.add(validation(FAILED_VALIDATION_ID, "IN_PROGRESS", "UNKNOWN", List.of()));
        script.pollBodies.add(validation(FAILED_VALIDATION_ID, "COMPLETED", "FAILED", List.of(
                check("FAILED", "Host has an unsupported ESXi version", "ERROR"),
                check("SUCCEEDED", "Host FQDN resolves", "INFO"),
                check("FAILED", "SSL thumbprint does not match the host certificate", "ERROR"))));

        Path log = newLogFile("failed");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            HostCommissionClient.CommissionOutcome outcome = client.commission(List.of(hostA(), hostB()));

            assertEquals("commissioned", false, outcome.commissioned);
            assertEquals("taskId", null, outcome.taskId);
            assertEquals("precheck.resultStatus", "FAILED", outcome.precheck.resultStatus);
            assertEquals("precheck.pollCount", 2, outcome.precheck.pollCount);
            assertEquals("precheck.failedChecks", List.of(
                    "Host has an unsupported ESXi version",
                    "SSL thumbprint does not match the host certificate"), outcome.precheck.failedChecks);
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "getHostCommissionValidationByID",
                "getHostCommissionValidationByID"), operationIds(records));
        assertPollRequest(records.get(1), FAILED_VALIDATION_ID);
        assertPollRequest(records.get(2), FAILED_VALIDATION_ID);
    }

    private static void terminalExecutionStatusesBlockCommission(HttpClient http) throws Exception {
        for (String executionStatus : List.of("FAILED", "UNKNOWN", "SKIPPED", "CANCELLED")) {
            String validationId = "terminal-" + executionStatus;
            MockSddcManager.Script script = new MockSddcManager.Script();
            String resultStatus = "CANCELLED".equals(executionStatus) ? "SUCCEEDED" : "UNKNOWN";
            script.validateBody = validation(validationId, executionStatus, resultStatus, List.of());

            Path log = newLogFile("terminal-" + executionStatus);
            try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
                HostCommissionClient client = client(mock, http, 10);
                HostCommissionClient.CommissionOutcome outcome = client.commission(List.of(hostB()));

                assertEquals(executionStatus + " commissioned", false, outcome.commissioned);
                assertEquals(executionStatus + " executionStatus",
                        executionStatus, outcome.precheck.executionStatus);
                assertEquals(executionStatus + " pollCount", 0, outcome.precheck.pollCount);
                assertEquals(executionStatus + " failedChecks", List.of(),
                        outcome.precheck.failedChecks);
            }

            List<Map<String, Object>> records = readLog(log);
            assertWellFormedLog(records);
            assertNothingCommissioned(records);
            assertEquals(executionStatus + " request count", 1, records.size());
            assertEquals(executionStatus + " operation",
                    "validateHostCommissionSpec", records.get(0).get("operationId"));
        }
    }

    private static void cancellationInProgressIsPolled(HttpClient http) throws Exception {
        String validationId = "validation-being-cancelled";
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(validationId, "CANCELLATION_IN_PROGRESS",
                "CANCELLATION_IN_PROGRESS", List.of());
        script.pollBodies.add(validation(validationId, "CANCELLED", "UNKNOWN", List.of()));

        Path log = newLogFile("cancelling");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            HostCommissionClient.CommissionOutcome outcome = client.commission(List.of(hostB()));

            assertEquals("commissioned", false, outcome.commissioned);
            assertEquals("executionStatus", "CANCELLED", outcome.precheck.executionStatus);
            assertEquals("pollCount", 1, outcome.precheck.pollCount);
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "getHostCommissionValidationByID"), operationIds(records));
        assertPollRequest(records.get(1), validationId);
    }

    private static void successCommissionsOnce(HttpClient http) throws Exception {
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(SUCCEEDED_VALIDATION_ID, "IN_PROGRESS", "UNKNOWN", List.of());
        script.pollBodies.add(validation(SUCCEEDED_VALIDATION_ID, "COMPLETED", "SUCCEEDED", List.of(
                check("SUCCEEDED", "Host FQDN resolves", "INFO"),
                check("SUCCEEDED", "Network pool has free IP addresses", "INFO"))));
        script.commissionBody = "{\"id\":\"8b0a4b34-6f8f-4f8e-9a1e-1a9d0a1c2b3d\","
                + "\"name\":\"Commissioning Host(s)\","
                + "\"type\":\"HOST_COMMISSION\","
                + "\"status\":\"IN_PROGRESS\","
                + "\"creationTimestamp\":\"2025-06-17T09:14:02.451Z\"}";

        Path log = newLogFile("success");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            HostCommissionClient.CommissionOutcome outcome = client.commission(List.of(hostA(), hostB()));

            assertEquals("commissioned", true, outcome.commissioned);
            assertEquals("precheck.passed()", true, outcome.precheck.passed());
            assertEquals("precheck.pollCount", 1, outcome.precheck.pollCount);
            assertEquals("precheck.failedChecks", List.of(), outcome.precheck.failedChecks);
            assertEquals("taskId", "8b0a4b34-6f8f-4f8e-9a1e-1a9d0a1c2b3d", outcome.taskId);
            assertEquals("taskName", "Commissioning Host(s)", outcome.taskName);
            assertEquals("taskStatus", "IN_PROGRESS", outcome.taskStatus);
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "getHostCommissionValidationByID",
                "commissionHosts"), operationIds(records));

        Map<String, Object> precheckRequest = records.get(0);
        Map<String, Object> pollRequest = records.get(1);
        Map<String, Object> commissionRequest = records.get(2);

        assertEquals("precheck path", "/v1/hosts/validations", precheckRequest.get("path"));
        assertEquals("commission path", "/v1/hosts", commissionRequest.get("path"));
        assertPollRequest(pollRequest, SUCCEEDED_VALIDATION_ID);
        assertJsonPostRequest("precheck", precheckRequest);
        assertJsonPostRequest("commission", commissionRequest);

        assertHostCommissionArray("precheck body", Json.str(precheckRequest.get("body")));
        assertHostCommissionArray("commission body", Json.str(commissionRequest.get("body")));
        assertEquals("commission body is byte-for-byte the prechecked body",
                precheckRequest.get("body"), commissionRequest.get("body"));
    }

    private static void precheckErrorBlocksCommission(HttpClient http) throws Exception {
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateStatus = 400;
        script.validateBody = "{\"errorCode\":\"HOST_COMMISSION_SPEC_INVALID\","
                + "\"errorType\":\"VALIDATION_FAILED\","
                + "\"message\":\"Network pool 5f2ba0a1-7c37-4d19-9df2-6ec2a4f5f4b1 was not found\"}";

        Path log = newLogFile("error");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            try {
                client.commission(List.of(hostA()));
                fail("expected an SddcApiException for the 400 precheck response");
            } catch (HostCommissionClient.SddcApiException expected) {
                assertEquals("statusCode", 400, expected.statusCode);
                assertEquals("errorCode", "HOST_COMMISSION_SPEC_INVALID", expected.errorCode);
                assertTrue("message carries the server text",
                        expected.getMessage() != null && expected.getMessage().contains("Network pool"));
            }
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("request count", 1, records.size());
        assertEquals("operation", "validateHostCommissionSpec", records.get(0).get("operationId"));
        assertJsonPostRequestShape("precheck", records.get(0));
        assertEquals("precheck response status", 400L, records.get(0).get("responseStatus"));
    }

    private static void pollErrorBlocksCommission(HttpClient http) throws Exception {
        String validationId = "validation-with-poll-error";
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(validationId, "IN_PROGRESS", "UNKNOWN", List.of());
        script.pollStatus = 400;
        script.pollBodies.add("{\"errorCode\":\"VALIDATION_LOOKUP_UNAVAILABLE\","
                + "\"message\":\"Validation lookup is temporarily unavailable\"}");

        Path log = newLogFile("poll-error");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            try {
                client.commission(List.of(hostA()));
                fail("expected an SddcApiException for the poll error response");
            } catch (HostCommissionClient.SddcApiException expected) {
                assertEquals("statusCode", 400, expected.statusCode);
                assertEquals("errorCode", "VALIDATION_LOOKUP_UNAVAILABLE", expected.errorCode);
                assertEquals("message", "Validation lookup is temporarily unavailable", expected.getMessage());
            }
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "getHostCommissionValidationByID"), operationIds(records));
        assertPollRequestShape(records.get(1), validationId);
        assertEquals("poll response status", 400L, records.get(1).get("responseStatus"));
    }

    private static void commissionErrorIsMapped(HttpClient http) throws Exception {
        String validationId = "validation-before-commission-error";
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(validationId, "COMPLETED", "SUCCEEDED", List.of());
        script.commissionStatus = 500;
        script.commissionBody = "{\"errorCode\":\"HOST_COMMISSION_FAILED\","
                + "\"message\":\"Commissioning service is unavailable\"}";

        Path log = newLogFile("commission-error");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 10);
            try {
                client.commission(List.of(hostA()));
                fail("expected an SddcApiException for the commission error response");
            } catch (HostCommissionClient.SddcApiException expected) {
                assertEquals("statusCode", 500, expected.statusCode);
                assertEquals("errorCode", "HOST_COMMISSION_FAILED", expected.errorCode);
                assertEquals("message", "Commissioning service is unavailable", expected.getMessage());
            }
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "commissionHosts"), operationIds(records));
        assertJsonPostRequestShape("commission", records.get(1));
        assertEquals("commission response status", 500L, records.get(1).get("responseStatus"));
    }

    private static void nonTerminalPrecheckBlocksCommission(HttpClient http) throws Exception {
        MockSddcManager.Script script = new MockSddcManager.Script();
        script.validateBody = validation(STUCK_VALIDATION_ID, "IN_PROGRESS", "UNKNOWN", List.of());
        script.pollBodies.add(validation(STUCK_VALIDATION_ID, "IN_PROGRESS", "UNKNOWN", List.of()));

        Path log = newLogFile("stuck");
        try (MockSddcManager mock = MockSddcManager.start(CONTRACT, log, script)) {
            HostCommissionClient client = client(mock, http, 3);
            try {
                client.commission(List.of(hostA()));
                fail("expected an SddcApiException once the poll budget was exhausted");
            } catch (HostCommissionClient.SddcApiException expected) {
                assertEquals("statusCode", 0, expected.statusCode);
                assertEquals("errorCode", "PRECHECK_NOT_TERMINAL", expected.errorCode);
            }
        }

        List<Map<String, Object>> records = readLog(log);
        assertWellFormedLog(records);
        assertNothingCommissioned(records);
        assertEquals("operation sequence", List.of(
                "validateHostCommissionSpec",
                "getHostCommissionValidationByID",
                "getHostCommissionValidationByID",
                "getHostCommissionValidationByID"), operationIds(records));
    }

    private static void invalidHostPropertiesRejected() {
        assertRejected("null fqdn", () -> new HostCommissionClient.HostCommissionSpec(
                null, "root", "VMw@re1!", "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("blank fqdn", () -> new HostCommissionClient.HostCommissionSpec(
                " \t", "root", "VMw@re1!", "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("null username", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, null, "VMw@re1!", "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("blank username", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, " \t", "VMw@re1!", "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("null password", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", null, "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("blank password", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", " \t", "VSAN_ESA", NETWORK_POOL_ID));
        assertRejected("null storageType", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", "VMw@re1!", null, NETWORK_POOL_ID));
        assertRejected("blank storageType", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", "VMw@re1!", " \t", NETWORK_POOL_ID));
        assertRejected("null networkPoolId", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", "VMw@re1!", "VSAN_ESA", null));
        assertRejected("blank networkPoolId", () -> new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", "VMw@re1!", "VSAN_ESA", " \t"));

        assertRejected("null vvolStorageProtocolType", () -> hostB().vvolStorageProtocolType(null));
        assertRejected("blank vvolStorageProtocolType", () -> hostB().vvolStorageProtocolType(" \t"));
        assertRejected("null networkPoolName", () -> hostB().networkPoolName(null));
        assertRejected("blank networkPoolName", () -> hostB().networkPoolName(" \t"));
        assertRejected("null sshThumbprint", () -> hostB().sshThumbprint(null));
        assertRejected("blank sshThumbprint", () -> hostB().sshThumbprint(" \t"));
        assertRejected("null sslThumbprint", () -> hostB().sslThumbprint(null));
        assertRejected("blank sslThumbprint", () -> hostB().sslThumbprint(" \t"));
    }

    // ------------------------------------------------------------- assertions

    private static void assertJsonPostRequest(String what, Map<String, Object> record) {
        assertJsonPostRequestShape(what, record);
        assertEquals(what + " response status", 202L, record.get("responseStatus"));
    }

    private static void assertJsonPostRequestShape(String what, Map<String, Object> record) {
        assertEquals(what + " method", "POST", record.get("method"));
        assertEquals(what + " Content-Type", "application/json", record.get("contentType"));
        assertEquals(what + " Accept", "application/json", record.get("accept"));
        assertEquals(what + " carried a body", true, record.get("bodyPresent"));
    }

    private static void assertPollRequest(Map<String, Object> record, String validationId) {
        assertPollRequestShape(record, validationId);
        assertEquals("poll response status", 202L, record.get("responseStatus"));
    }

    private static void assertPollRequestShape(Map<String, Object> record, String validationId) {
        assertEquals("poll method", "GET", record.get("method"));
        assertEquals("poll Accept", "application/json", record.get("accept"));
        assertEquals("poll sent no Content-Type", null, record.get("contentType"));
        assertEquals("poll sent no body", false, record.get("bodyPresent"));
        assertEquals("poll path", "/v1/hosts/validations/" + validationId, record.get("path"));
    }

    /** The array of HostCommissionSpec objects must carry exactly the properties that were set. */
    private static void assertHostCommissionArray(String what, String body) {
        Object parsed = Json.parse(body);
        assertTrue(what + " is a JSON array", parsed instanceof List);
        List<Object> hosts = Json.arr(parsed);
        assertEquals(what + " host count", 2, hosts.size());

        Map<String, Object> first = Json.obj(hosts.get(0));
        assertEquals(what + " host[0] properties", HOST_A_PROPERTIES, first.keySet());
        assertEquals(what + " host[0].fqdn", HOST_A_FQDN, first.get("fqdn"));
        assertEquals(what + " host[0].username", "root", first.get("username"));
        assertEquals(what + " host[0].password", "VMw@re1!", first.get("password"));
        assertEquals(what + " host[0].storageType", "VVOL", first.get("storageType"));
        assertEquals(what + " host[0].networkPoolId", NETWORK_POOL_ID, first.get("networkPoolId"));
        assertEquals(what + " host[0].vvolStorageProtocolType", "FC", first.get("vvolStorageProtocolType"));
        assertEquals(what + " host[0].networkPoolName", "vcf-np-01", first.get("networkPoolName"));
        assertEquals(what + " host[0].sshThumbprint",
                "SHA256:3q2Sk8gJ2m1eQm2Vt0uYQ5Ky7ZP8jH4nD6rF1sT9xLk", first.get("sshThumbprint"));
        assertEquals(what + " host[0].sslThumbprint",
                "6C:1F:9A:0B:D3:44:7E:22:81:5F:AA:0E:39:7B:C2:11:8D:4E:5A:90", first.get("sslThumbprint"));

        Map<String, Object> second = Json.obj(hosts.get(1));
        assertEquals(what + " host[1] properties", HOST_B_PROPERTIES, second.keySet());
        assertEquals(what + " host[1].fqdn", HOST_B_FQDN, second.get("fqdn"));
        assertEquals(what + " host[1].username", "root", second.get("username"));
        assertEquals(what + " host[1].password", "VMw@re1!", second.get("password"));
        assertEquals(what + " host[1].storageType", "VSAN_ESA", second.get("storageType"));
        assertEquals(what + " host[1].networkPoolId", NETWORK_POOL_ID, second.get("networkPoolId"));

        assertNoEmptyValues(what, parsed);
    }

    /** A single host is still sent as a one element array, never as a bare object. */
    private static void assertSingleHostArray(String what, String body) {
        Object parsed = Json.parse(body);
        assertTrue(what + " is a JSON array even for one host", parsed instanceof List);
        List<Object> hosts = Json.arr(parsed);
        assertEquals(what + " host count", 1, hosts.size());
        Map<String, Object> only = Json.obj(hosts.get(0));
        assertEquals(what + " host[0] properties", HOST_B_PROPERTIES, only.keySet());
        assertEquals(what + " host[0].fqdn", HOST_B_FQDN, only.get("fqdn"));
        assertNoEmptyValues(what, parsed);
    }

    /** Unset optional properties must be absent, never null and never an empty string. */
    private static void assertNoEmptyValues(String what, Object node) {
        if (node instanceof Map) {
            for (Map.Entry<String, Object> entry : Json.obj(node).entrySet()) {
                assertTrue(what + " sent " + entry.getKey() + " as null instead of omitting it",
                        entry.getValue() != null);
                assertTrue(what + " sent " + entry.getKey() + " as an empty string instead of omitting it",
                        !"".equals(entry.getValue()));
                assertNoEmptyValues(what, entry.getValue());
            }
        } else if (node instanceof List) {
            for (Object item : Json.arr(node)) {
                assertNoEmptyValues(what, item);
            }
        }
    }

    /** Every request must have hit a contract route, with no query string and no 404. */
    private static void assertWellFormedLog(List<Map<String, Object>> records) {
        for (Map<String, Object> record : records) {
            String where = record.get("method") + " " + record.get("path");
            assertTrue("request outside the contract: " + where, record.get("operationId") != null);
            assertEquals("query string on " + where, null, record.get("rawQuery"));
            assertTrue("unserved route " + where, !Long.valueOf(404L).equals(record.get("responseStatus")));
        }
    }

    private static void assertNothingCommissioned(List<Map<String, Object>> records) {
        for (Map<String, Object> record : records) {
            assertTrue("the gate was closed but " + record.get("method") + " " + record.get("path")
                            + " was still sent",
                    !"commissionHosts".equals(record.get("operationId"))
                            && !"/v1/hosts".equals(record.get("path")));
        }
    }

    // ---------------------------------------------------------------- fixtures

    private static HostCommissionClient client(MockSddcManager mock, HttpClient http, int maxPolls) {
        return new HostCommissionClient(mock.baseUrl(), http, Duration.ZERO, maxPolls);
    }

    private static HostCommissionClient.HostCommissionSpec hostA() {
        return new HostCommissionClient.HostCommissionSpec(
                HOST_A_FQDN, "root", "VMw@re1!", "VVOL", NETWORK_POOL_ID)
                .vvolStorageProtocolType("FC")
                .networkPoolName("vcf-np-01")
                .sshThumbprint("SHA256:3q2Sk8gJ2m1eQm2Vt0uYQ5Ky7ZP8jH4nD6rF1sT9xLk")
                .sslThumbprint("6C:1F:9A:0B:D3:44:7E:22:81:5F:AA:0E:39:7B:C2:11:8D:4E:5A:90");
    }

    private static HostCommissionClient.HostCommissionSpec hostB() {
        return new HostCommissionClient.HostCommissionSpec(
                HOST_B_FQDN, "root", "VMw@re1!", "VSAN_ESA", NETWORK_POOL_ID);
    }

    private static String validation(String validationId, String executionStatus, String resultStatus,
                                     List<String> checks) {
        StringBuilder out = new StringBuilder();
        out.append("{\"id\":\"").append(validationId).append("\",")
                .append("\"description\":\"Validation for host commission\",")
                .append("\"executionStatus\":\"").append(executionStatus).append("\",")
                .append("\"resultStatus\":\"").append(resultStatus).append("\"");
        if (!checks.isEmpty()) {
            out.append(",\"validationChecks\":[").append(String.join(",", checks)).append("]");
        }
        return out.append('}').toString();
    }

    private static String check(String resultStatus, String description, String severity) {
        return "{\"description\":\"" + description + "\",\"severity\":\"" + severity
                + "\",\"resultStatus\":\"" + resultStatus + "\"}";
    }

    // ----------------------------------------------------------------- harness

    private interface Case {
        void run() throws Exception;
    }

    private static void run(String name, Case body) {
        try {
            body.run();
            System.out.println("PASS  " + name);
        } catch (Throwable failure) {
            failures++;
            System.out.println("FAIL  " + name);
            System.out.println("      " + failure);
            for (StackTraceElement frame : failure.getStackTrace()) {
                if (frame.getClassName().equals("TestMain") || frame.getClassName().startsWith("HostCommissionClient")) {
                    System.out.println("      at " + frame);
                    break;
                }
            }
        }
    }

    private static Path newLogFile(String name) throws Exception {
        Path directory = Files.createTempDirectory("vcf90-0030-" + name + "-");
        directory.toFile().deleteOnExit();
        Path log = directory.resolve("requests.jsonl");
        log.toFile().deleteOnExit();
        return log;
    }

    private static List<Map<String, Object>> readLog(Path log) throws Exception {
        List<Map<String, Object>> records = new ArrayList<>();
        for (String line : Files.readAllLines(log, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                records.add(Json.obj(Json.parse(line)));
            }
        }
        return records;
    }

    private static List<String> operationIds(List<Map<String, Object>> records) {
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> record : records) {
            ids.add(Json.str(record.get("operationId")));
        }
        return ids;
    }

    private static void assertEquals(String what, Object expected, Object actual) {
        Object normalisedExpected = normalise(expected);
        Object normalisedActual = normalise(actual);
        if (normalisedExpected == null ? normalisedActual != null : !normalisedExpected.equals(normalisedActual)) {
            throw new AssertionError(what + ": expected <" + normalisedExpected + "> but was <" + normalisedActual + ">");
        }
    }

    private static Object normalise(Object value) {
        return value instanceof Integer ? Long.valueOf((Integer) value) : value;
    }

    private static void assertTrue(String what, boolean condition) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    private static void fail(String what) {
        throw new AssertionError(what);
    }

    private static void assertRejected(String what, Runnable action) {
        try {
            action.run();
            fail("expected IllegalArgumentException for " + what);
        } catch (IllegalArgumentException expected) {
            // Null and blank host properties are rejected before any request can be sent.
        }
    }
}
