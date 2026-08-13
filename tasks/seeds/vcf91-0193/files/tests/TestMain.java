import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Acceptance harness for the single-file VCF Log Management client. */
public final class TestMain {
    private static int checks;

    @FunctionalInterface
    private interface CheckedRunnable {
        void run() throws Exception;
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object expected, Object actual, String message) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message
                    + "\n  expected: " + expected
                    + "\n  actual:   " + actual);
        }
    }

    private static void expectIllegalArgument(CheckedRunnable action, String message) {
        checks++;
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        } catch (Exception wrong) {
            throw new AssertionError(message + ": wrong exception " + wrong, wrong);
        }
        throw new AssertionError(message);
    }

    private static void expectProtocol(
            CheckedRunnable action,
            String operationId,
            int status,
            String token,
            String rawResponseBody,
            String message) {
        checks++;
        try {
            action.run();
        } catch (VcfLogManagementClient.ProtocolException expected) {
            equal(operationId, expected.operationId(),
                    "ProtocolException operationId mismatch");
            equal(status, expected.statusCode(),
                    "ProtocolException status mismatch");
            check(!expected.getMessage().contains(token),
                    "ProtocolException must not expose the JWT token");
            check(!expected.getMessage().contains(rawResponseBody),
                    "ProtocolException must not expose a raw response body");
            return;
        } catch (Exception wrong) {
            throw new AssertionError(message + ": wrong exception " + wrong, wrong);
        }
        throw new AssertionError(message);
    }

    private static MockVcfLogServer.Fixture fixture(
            boolean agentAutoUpdate,
            boolean forwarderEnabled,
            boolean sslEnabled) {
        String suffix = UUID.randomUUID().toString().substring(0, 8);
        int port = 10000 + Math.floorMod(suffix.hashCode(), 45000);
        return new MockVcfLogServer.Fixture(
                "jwt-" + UUID.randomUUID(),
                "agent/" + suffix + " + β",
                agentAutoUpdate,
                "forwarder/" + suffix + " ? β",
                forwarderEnabled,
                "edge-\"雪-" + suffix + ".example",
                port,
                "SYSLOG",
                sslEnabled,
                "TCP",
                agentAutoUpdate ? "SSL_ERROR" : "API_ERROR",
                "certificate chain rejected for " + suffix);
    }

    private static VcfLogManagementClient.ForwarderProbe probe(
            MockVcfLogServer.Fixture fixture) {
        return new VcfLogManagementClient.ForwarderProbe(
                fixture.host(),
                fixture.port(),
                VcfLogManagementClient.ForwarderProtocol.valueOf(fixture.protocol()),
                fixture.sslEnabled(),
                VcfLogManagementClient.TransportProtocol.valueOf(
                        fixture.transportProtocol()));
    }

    private static VcfLogManagementClient client(
            MockVcfLogServer mock,
            MockVcfLogServer.Fixture fixture) {
        return new VcfLogManagementClient(
                mock.origin(), fixture.token(), Duration.ofSeconds(3), mock.client());
    }

    private static VcfLogManagementClient.ChangeReport apply(
            VcfLogManagementClient client,
            MockVcfLogServer.Fixture fixture) throws Exception {
        return client.applyRoutingChange(
                fixture.agentGroupId(),
                fixture.agentAutoUpdate(),
                fixture.forwarderId(),
                fixture.forwarderEnabled(),
                probe(fixture));
    }

    private static void testTruthfulPartialReportAndWire(
            boolean agentAutoUpdate,
            boolean forwarderEnabled,
            boolean sslEnabled) throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(
                agentAutoUpdate, forwarderEnabled, sslEnabled);
        Path contract = Path.of("docs", "contract.json");
        try (MockVcfLogServer mock = new MockVcfLogServer(contract, fixture)) {
            equal(Set.of(
                            "patchUpdateAgentGroupConfig",
                            "patchLogForwarder",
                            "testLogForwarderConnection"),
                    mock.operationIds(),
                    "mock allow-list must come from exactly the named contract operations");

            VcfLogManagementClient client = client(mock, fixture);
            VcfLogManagementClient.ForwarderProbe probe = probe(fixture);

            VcfLogManagementClient.ChangeReport report = apply(client, fixture);

            equal(VcfLogManagementClient.ChangeStatus.PARTIALLY_APPLIED,
                    report.status(), "later HTTP failure must report partial application");
            check(!report.succeeded(), "partial application is not full success");
            equal(3, report.steps().size(), "all attempted steps must be retained");

            assertStep(report.steps().get(0),
                    "patchUpdateAgentGroupConfig",
                    VcfLogManagementClient.StepOutcome.APPLIED,
                    200, null, null);
            assertStep(report.steps().get(1),
                    "patchLogForwarder",
                    VcfLogManagementClient.StepOutcome.APPLIED,
                    200, null, null);
            assertStep(report.steps().get(2),
                    "testLogForwarderConnection",
                    VcfLogManagementClient.StepOutcome.FAILED,
                    502, fixture.errorCode(), fixture.errorMessage());

            checks++;
            try {
                report.steps().add(report.steps().get(0));
                throw new AssertionError("report steps must be unmodifiable");
            } catch (UnsupportedOperationException expected) {
                // expected
            }

            List<MockVcfLogServer.RequestLog> requests = mock.requests();
            equal(3, requests.size(), "workflow must stop without retries or compensation");

            String groupTarget = "/api/v2/agent/groups/"
                    + encodeSegment(fixture.agentGroupId());
            String forwarderTarget = "/api/v2/logs/forwarders/"
                    + encodeSegment(fixture.forwarderId());
            assertRequest(requests.get(0),
                    "patchUpdateAgentGroupConfig", "PATCH", groupTarget,
                    fixture.token(), TestJson.write(Map.of(
                            "autoUpdate", fixture.agentAutoUpdate())),
                    Set.of("autoUpdate"));
            assertRequest(requests.get(1),
                    "patchLogForwarder", "PATCH", forwarderTarget,
                    fixture.token(), TestJson.write(Map.of(
                            "enabled", fixture.forwarderEnabled())),
                    Set.of("enabled"));

            LinkedHashMap<String, Object> expectedProbe = new LinkedHashMap<>();
            expectedProbe.put("host", fixture.host());
            expectedProbe.put("port", fixture.port());
            expectedProbe.put("protocol", fixture.protocol());
            expectedProbe.put("sslEnabled", fixture.sslEnabled());
            expectedProbe.put("transportProtocol", fixture.transportProtocol());
            assertRequest(requests.get(2),
                    "testLogForwarderConnection", "POST",
                    "/api/v2/logs/forwarders/test",
                    fixture.token(), TestJson.write(expectedProbe),
                    expectedProbe.keySet());

            int beforeValidation = mock.requests().size();
            expectIllegalArgument(() -> client.applyRoutingChange(
                            " ", false, fixture.forwarderId(), false, probe),
                    "blank resource id must fail locally");
            equal(beforeValidation, mock.requests().size(),
                    "local validation must not create traffic");
        }
    }

    private static void testConstructionValidation() {
        new VcfLogManagementClient(
                URI.create("https://example.test/"),
                "token", Duration.ofSeconds(1));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test/not-root"),
                        "token", Duration.ofSeconds(1)),
                "non-root origin path must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://user:secret@example.test"),
                        "token", Duration.ofSeconds(1)),
                "origin credentials must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test?query"),
                        "token", Duration.ofSeconds(1)),
                "origin query must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test#fragment"),
                        "token", Duration.ofSeconds(1)),
                "origin fragment must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test"),
                        " token ", Duration.ofSeconds(1)),
                "padded token must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test"),
                        "token\r\nother", Duration.ofSeconds(1)),
                "CR/LF token must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test"),
                        "token", Duration.ZERO),
                "zero timeout must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test"),
                        "token", Duration.ofSeconds(-1)),
                "negative timeout must be rejected");
        expectIllegalArgument(() -> new VcfLogManagementClient(
                        URI.create("https://example.test"),
                        "token", null),
                "null timeout must be rejected");
    }

    private static void testAllInputsAreValidatedBeforeTraffic() throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(true, false, true);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                Path.of("docs", "contract.json"), fixture)) {
            VcfLogManagementClient client = client(mock, fixture);
            VcfLogManagementClient.ForwarderProbe good = probe(fixture);

            expectIllegalArgument(() -> client.applyRoutingChange(
                            fixture.agentGroupId(), true, " padded ", false, good),
                    "padded forwarder id must fail locally before the first patch");
            expectIllegalArgument(() -> client.applyRoutingChange(
                            "\uD800", true, fixture.forwarderId(), false, good),
                    "unencodable resource id must fail locally");
            expectIllegalArgument(() -> client.applyRoutingChange(
                            fixture.agentGroupId(), true, fixture.forwarderId(), false, null),
                    "null probe must fail locally before the first patch");
            equal(0, mock.requests().size(),
                    "every argument must be validated before any workflow traffic");
        }
    }

    private static void testFailureAndSuccessReports() throws Exception {
        Path contract = Path.of("docs", "contract.json");

        MockVcfLogServer.Fixture firstFixture = fixture(true, false, true);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                contract, firstFixture, MockVcfLogServer.Scenario.FIRST_STEP_FAILURE)) {
            VcfLogManagementClient.ChangeReport report = apply(
                    client(mock, firstFixture), firstFixture);
            equal(VcfLogManagementClient.ChangeStatus.FAILED, report.status(),
                    "first-step HTTP failure must report FAILED");
            check(!report.succeeded(), "failed workflow must not succeed");
            equal(1, report.steps().size(), "first failure must stop later traffic");
            assertStep(report.steps().get(0), "patchUpdateAgentGroupConfig",
                    VcfLogManagementClient.StepOutcome.FAILED, 500,
                    firstFixture.errorCode(), firstFixture.errorMessage());
            equal(1, mock.requests().size(), "first failure must make one request");
        }

        MockVcfLogServer.Fixture secondFixture = fixture(false, true, false);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                contract, secondFixture, MockVcfLogServer.Scenario.SECOND_STEP_FAILURE)) {
            VcfLogManagementClient.ChangeReport report = apply(
                    client(mock, secondFixture), secondFixture);
            equal(VcfLogManagementClient.ChangeStatus.PARTIALLY_APPLIED, report.status(),
                    "second-step HTTP failure must report partial application");
            check(!report.succeeded(), "partial workflow must not succeed");
            equal(2, report.steps().size(), "second failure must stop the probe");
            assertStep(report.steps().get(0), "patchUpdateAgentGroupConfig",
                    VcfLogManagementClient.StepOutcome.APPLIED, 200, null, null);
            assertStep(report.steps().get(1), "patchLogForwarder",
                    VcfLogManagementClient.StepOutcome.FAILED, 502,
                    secondFixture.errorCode(), secondFixture.errorMessage());
            equal(2, mock.requests().size(), "second failure must make two requests");
        }

        MockVcfLogServer.Fixture successFixture = fixture(true, true, false);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                contract, successFixture, MockVcfLogServer.Scenario.ALL_SUCCESS)) {
            VcfLogManagementClient.ChangeReport report = apply(
                    client(mock, successFixture), successFixture);
            equal(VcfLogManagementClient.ChangeStatus.APPLIED, report.status(),
                    "three successful operations must report APPLIED");
            check(report.succeeded(), "fully applied workflow must succeed");
            equal(3, report.steps().size(), "all successful steps must be retained");
            assertStep(report.steps().get(0), "patchUpdateAgentGroupConfig",
                    VcfLogManagementClient.StepOutcome.APPLIED, 200, null, null);
            assertStep(report.steps().get(1), "patchLogForwarder",
                    VcfLogManagementClient.StepOutcome.APPLIED, 200, null, null);
            VcfLogManagementClient.StepResult probeStep = report.steps().get(2);
            equal("testLogForwarderConnection", probeStep.operationId(),
                    "successful probe operationId mismatch");
            check(probeStep.outcome() != VcfLogManagementClient.StepOutcome.FAILED,
                    "successful probe must not have a failed outcome");
            equal(200, probeStep.statusCode(), "successful probe status mismatch");
            equal(null, probeStep.errorCode(), "successful probe error code must be absent");
            equal(null, probeStep.errorMessage(),
                    "successful probe error message must be absent");
            equal(3, mock.requests().size(), "successful workflow must make three requests");
        }
    }

    private static void testInvalidSuccessResponsesStopTraffic() throws Exception {
        Path contract = Path.of("docs", "contract.json");
        for (MockVcfLogServer.Scenario scenario : List.of(
                MockVcfLogServer.Scenario.AGENT_ID_MISMATCH,
                MockVcfLogServer.Scenario.AGENT_VALUE_MISMATCH,
                MockVcfLogServer.Scenario.AGENT_INVALID_SUCCESS)) {
            MockVcfLogServer.Fixture fixture = fixture(false, true, false);
            try (MockVcfLogServer mock = new MockVcfLogServer(contract, fixture, scenario)) {
                VcfLogManagementClient client = client(mock, fixture);
                expectProtocol(() -> apply(client, fixture),
                        "patchUpdateAgentGroupConfig", 200, fixture.token(),
                        agentResponseBody(scenario, fixture),
                        "invalid agent success must throw ProtocolException");
                equal(1, mock.requests().size(),
                        "invalid first success must prevent later traffic");
            }
        }

        for (MockVcfLogServer.Scenario scenario : List.of(
                MockVcfLogServer.Scenario.FORWARDER_ID_MISMATCH,
                MockVcfLogServer.Scenario.FORWARDER_VALUE_MISMATCH)) {
            MockVcfLogServer.Fixture fixture = fixture(true, false, true);
            try (MockVcfLogServer mock = new MockVcfLogServer(contract, fixture, scenario)) {
                VcfLogManagementClient client = client(mock, fixture);
                expectProtocol(() -> apply(client, fixture),
                        "patchLogForwarder", 200, fixture.token(),
                        forwarderResponseBody(scenario, fixture),
                        "identity-inconsistent forwarder success must throw ProtocolException");
                equal(2, mock.requests().size(),
                        "invalid second success must prevent probe traffic");
            }
        }
    }

    private static String agentResponseBody(
            MockVcfLogServer.Scenario scenario,
            MockVcfLogServer.Fixture fixture) throws Exception {
        if (scenario == MockVcfLogServer.Scenario.AGENT_INVALID_SUCCESS) {
            return TestJson.write(List.of("not", "an", "object"));
        }
        LinkedHashMap<String, Object> body = new LinkedHashMap<>();
        body.put("id", scenario == MockVcfLogServer.Scenario.AGENT_ID_MISMATCH
                ? fixture.agentGroupId() + "-other"
                : fixture.agentGroupId());
        body.put("autoUpdate", scenario == MockVcfLogServer.Scenario.AGENT_VALUE_MISMATCH
                ? !fixture.agentAutoUpdate()
                : fixture.agentAutoUpdate());
        return TestJson.write(body);
    }

    private static String forwarderResponseBody(
            MockVcfLogServer.Scenario scenario,
            MockVcfLogServer.Fixture fixture) throws Exception {
        LinkedHashMap<String, Object> body = new LinkedHashMap<>();
        body.put("id", scenario == MockVcfLogServer.Scenario.FORWARDER_ID_MISMATCH
                ? fixture.forwarderId() + "-other"
                : fixture.forwarderId());
        body.put("enabled", scenario == MockVcfLogServer.Scenario.FORWARDER_VALUE_MISMATCH
                ? !fixture.forwarderEnabled()
                : fixture.forwarderEnabled());
        return TestJson.write(body);
    }

    private static void testMockRefusesUnnamedOperations() throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(false, false, false);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                Path.of("docs", "contract.json"), fixture)) {
            HttpResponse<String> response = mock.client().send(
                    HttpRequest.newBuilder(mock.origin().resolve("/api/v2/logs/unnamed"))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            equal(404, response.statusCode(),
                    "mock must refuse operations absent from its contract allow-list");
            equal(1, mock.requests().size(), "unnamed probe must be logged");
            equal(null, mock.requests().get(0).operationId(),
                    "unnamed route must not be assigned an operationId");
        }
    }

    private static void assertStep(
            VcfLogManagementClient.StepResult step,
            String operationId,
            VcfLogManagementClient.StepOutcome outcome,
            int status,
            String errorCode,
            String errorMessage) {
        equal(operationId, step.operationId(), "step operationId mismatch");
        equal(outcome, step.outcome(), "step outcome mismatch");
        equal(status, step.statusCode(), "step HTTP status mismatch");
        equal(errorCode, step.errorCode(), "step error code mismatch");
        equal(errorMessage, step.errorMessage(), "step error message mismatch");
    }

    private static void assertRequest(
            MockVcfLogServer.RequestLog request,
            String operationId,
            String method,
            String rawTarget,
            String token,
            String body,
            Set<String> exactKeys) throws Exception {
        equal(operationId, request.operationId(), "request operationId mismatch");
        equal(method, request.method(), "request method mismatch");
        equal(rawTarget, request.rawTarget(), "raw request target mismatch");
        check(!request.rawTarget().contains("?"), "requests must have no query delimiter");
        equal(List.of(token), request.headerValues("x-jwt-token"),
                "X-JWT-Token must appear exactly once");
        equal(List.of("application/json"), request.headerValues("accept"),
                "Accept must appear exactly once");
        equal(List.of("application/json"), request.headerValues("content-type"),
                "Content-Type must appear exactly once");
        equal(List.of(), request.headerValues("authorization"),
                "Authorization must be absent");
        String actualBody = new String(request.body(), StandardCharsets.UTF_8);
        equal(body, actualBody,
                "JSON body must be compact, ordered, and omit unset optional fields");
        equal(exactKeys, TestJson.object(TestJson.parse(actualBody)).keySet(),
                "request object must contain exactly the selected schema properties");
    }

    private static String encodeSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder result = new StringBuilder();
        char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte signed : bytes) {
            int b = signed & 0xff;
            if ((b >= 'a' && b <= 'z')
                    || (b >= 'A' && b <= 'Z')
                    || (b >= '0' && b <= '9')
                    || b == '-' || b == '.' || b == '_' || b == '~') {
                result.append((char) b);
            } else {
                result.append('%').append(hex[b >>> 4]).append(hex[b & 15]);
            }
        }
        return result.toString();
    }

    public static void main(String[] args) throws Exception {
        testConstructionValidation();
        testAllInputsAreValidatedBeforeTraffic();
        boolean generatedBoolean = (UUID.randomUUID().getLeastSignificantBits() & 1L) != 0;
        testTruthfulPartialReportAndWire(
                generatedBoolean, !generatedBoolean, generatedBoolean);
        testTruthfulPartialReportAndWire(
                !generatedBoolean, generatedBoolean, !generatedBoolean);
        testFailureAndSuccessReports();
        testInvalidSuccessResponsesStopTraffic();
        testMockRefusesUnnamedOperations();
        System.out.println("PASS: contract-pinned truthful partial VCF log-routing report ("
                + checks + " checks)");
    }
}
