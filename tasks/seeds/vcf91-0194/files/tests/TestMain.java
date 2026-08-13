import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/** Acceptance harness for the single-file VCF Log Management client. */
public final class TestMain {
    private static int checks;

    @FunctionalInterface
    private interface CheckedAction {
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

    private static void expectIllegalArgument(
            CheckedAction action, String message) throws Exception {
        checks++;
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    private static MockVcfLogServer.Fixture fixture(int precheckStatus) {
        String suffix = UUID.randomUUID().toString().substring(0, 8);
        int port = 10000 + Math.floorMod(suffix.hashCode(), 45000);
        return new MockVcfLogServer.Fixture(
                "jwt-" + UUID.randomUUID(),
                "forwarder-\"雪\\-" + suffix,
                "edge-\"雪-" + suffix + ".example.test",
                port,
                "SYSLOG",
                false,
                "TCP",
                false,
                precheckStatus,
                "SSL_ERROR",
                "certificate chain rejected for " + suffix,
                "created-" + UUID.randomUUID());
    }

    private static VcfLogForwarderClient.ForwarderDraft draft(
            MockVcfLogServer.Fixture fixture) {
        return new VcfLogForwarderClient.ForwarderDraft(
                fixture.name(),
                fixture.host(),
                fixture.port(),
                VcfLogForwarderClient.ForwarderProtocol.valueOf(
                        fixture.protocol()),
                fixture.sslEnabled(),
                VcfLogForwarderClient.TransportProtocol.valueOf(
                        fixture.transportProtocol()),
                fixture.enabled());
    }

    private static void testFailedPrecheckGatesMutation() throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(502);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                Path.of("docs", "contract.json"), fixture)) {
            equal(Set.of(
                            "testLogForwarderConnection",
                            "createLogForwarder"),
                    mock.operationIds(),
                    "mock allow-list must contain exactly the contract operations");
            VcfLogForwarderClient client = new VcfLogForwarderClient(
                    mock.origin(), fixture.token(), Duration.ofSeconds(3),
                    mock.client());

            checks++;
            try {
                client.createAfterSuccessfulPrecheck(draft(fixture));
                throw new AssertionError("HTTP 502 precheck must fail the workflow");
            } catch (VcfLogForwarderClient.VcfApiException failure) {
                equal("testLogForwarderConnection", failure.operationId(),
                        "failure must name the attempted precheck operationId");
                equal(502, failure.statusCode(),
                        "precheck status must be preserved");
                equal(fixture.errorCode(), failure.errorCode(),
                        "ErrorBody.errorCode must be decoded");
                equal(fixture.errorMessage(), failure.errorMessage(),
                        "ErrorBody.errorMessage must be decoded");
                check(!failure.getMessage().contains(fixture.token()),
                        "failure text must not expose the token");
                check(!failure.getMessage().contains(fixture.errorMessage()),
                        "failure text must not echo the raw response message");
            }

            equal(0, mock.creationCount(),
                    "failed precheck must leave creation state unchanged");
            List<MockVcfLogServer.RequestLog> requests = mock.requests();
            equal(1, requests.size(),
                    "failed precheck must prevent the mutating create call");
            assertRequest(
                    requests.get(0),
                    "testLogForwarderConnection",
                    "/api/v2/logs/forwarders/test",
                    fixture.token(),
                    MockVcfLogServer.expectedPrecheckBody(fixture),
                    502,
                    List.of(
                            "certificate",
                            "connectionRefreshInterval",
                            "constraints",
                            "enabled",
                            "forwardComplementaryFields",
                            "id",
                            "name",
                            "tags",
                            "workerCount"));
        }
    }

    private static void testSuccessfulPrecheckPermitsOneCreate() throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(200);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                Path.of("docs", "contract.json"), fixture)) {
            VcfLogForwarderClient client = new VcfLogForwarderClient(
                    mock.origin(), fixture.token(), Duration.ofSeconds(3),
                    mock.client());
            VcfLogForwarderClient.CreatedForwarder result =
                    client.createAfterSuccessfulPrecheck(draft(fixture));

            equal(fixture.createdId(), result.id(),
                    "created response id must be returned");
            equal(fixture.name(), result.name(),
                    "created response name must be returned");
            equal(1, mock.creationCount(),
                    "successful gated workflow must create exactly once");

            List<MockVcfLogServer.RequestLog> requests = mock.requests();
            equal(2, requests.size(),
                    "successful workflow must make exactly two calls");
            assertRequest(
                    requests.get(0),
                    "testLogForwarderConnection",
                    "/api/v2/logs/forwarders/test",
                    fixture.token(),
                    MockVcfLogServer.expectedPrecheckBody(fixture),
                    200,
                    List.of(
                            "certificate",
                            "connectionRefreshInterval",
                            "constraints",
                            "enabled",
                            "forwardComplementaryFields",
                            "id",
                            "name",
                            "tags",
                            "workerCount"));
            assertRequest(
                    requests.get(1),
                    "createLogForwarder",
                    "/api/v2/logs/forwarders",
                    fixture.token(),
                    MockVcfLogServer.expectedCreateBody(fixture),
                    201,
                    List.of(
                            "certificate",
                            "connectionRefreshInterval",
                            "constraints",
                            "forwardComplementaryFields",
                            "id",
                            "tags",
                            "workerCount"));

            String createBody = new String(
                    requests.get(1).body(), StandardCharsets.UTF_8);
            check(createBody.contains("\"enabled\":false"),
                    "explicit false enabled value must be preserved");
            check(createBody.contains("\"sslEnabled\":false"),
                    "explicit false sslEnabled value must be preserved");

            int before = mock.requests().size();
            VcfLogForwarderClient.ForwarderDraft invalid =
                    new VcfLogForwarderClient.ForwarderDraft(
                            fixture.name(),
                            fixture.host(),
                            0,
                            VcfLogForwarderClient.ForwarderProtocol.SYSLOG,
                            false,
                            VcfLogForwarderClient.TransportProtocol.TCP,
                            false);
            expectIllegalArgument(
                    () -> client.createAfterSuccessfulPrecheck(invalid),
                    "invalid draft must fail locally");
            equal(before, mock.requests().size(),
                    "invalid draft must create no traffic");
        }
    }

    private static void testConstructionValidation() throws Exception {
        expectIllegalArgument(
                () -> new VcfLogForwarderClient(
                        URI.create("http://127.0.0.1/not-root"),
                        "token",
                        Duration.ofSeconds(1)),
                "non-root origin path must be rejected");
        expectIllegalArgument(
                () -> new VcfLogForwarderClient(
                        URI.create("http://127.0.0.1"),
                        " token ",
                        Duration.ofSeconds(1)),
                "padded token must be rejected");
        expectIllegalArgument(
                () -> new VcfLogForwarderClient(
                        URI.create("http://127.0.0.1"),
                        "token",
                        Duration.ZERO),
                "zero timeout must be rejected");
    }

    private static void testMockRefusesUnnamedOperations() throws Exception {
        MockVcfLogServer.Fixture fixture = fixture(200);
        try (MockVcfLogServer mock = new MockVcfLogServer(
                Path.of("docs", "contract.json"), fixture)) {
            HttpResponse<String> response = mock.client().send(
                    HttpRequest.newBuilder(mock.origin().resolve(
                                    "/api/v2/logs/forwarders/unnamed"))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            equal(404, response.statusCode(),
                    "mock must refuse operations absent from the contract");
            equal(0, mock.creationCount(),
                    "unnamed operation must not mutate state");
            equal(1, mock.requests().size(),
                    "unnamed request must remain visible in the request log");
            equal(null, mock.requests().get(0).operationId(),
                    "unnamed route must not be assigned an operationId");
        }
    }

    private static void assertRequest(
            MockVcfLogServer.RequestLog request,
            String operationId,
            String rawTarget,
            String token,
            byte[] expectedBody,
            int expectedStatus,
            List<String> omittedProperties) {
        equal(operationId, request.operationId(),
                "request operationId mismatch");
        equal("POST", request.method(), "request method mismatch");
        equal(rawTarget, request.rawTarget(), "raw request target mismatch");
        check(!request.rawTarget().contains("?"),
                "focused requests must not contain a query string");
        equal(List.of(token), request.headerValues("x-jwt-token"),
                "X-JWT-Token must appear exactly once");
        equal(List.of("application/json"), request.headerValues("accept"),
                "Accept must appear exactly once");
        equal(List.of("application/json"), request.headerValues("content-type"),
                "Content-Type must appear exactly once");
        equal(List.of(), request.headerValues("authorization"),
                "Authorization must be absent");
        equal(List.of(Integer.toString(expectedBody.length)),
                request.headerValues("content-length"),
                "Content-Length must match the exact UTF-8 body");
        check(Arrays.equals(expectedBody, request.body()),
                "request body must be byte-exact compact UTF-8 JSON");
        equal(expectedStatus, request.responseStatus(),
                "mock response status mismatch");
        String actual = new String(request.body(), StandardCharsets.UTF_8);
        for (String property : omittedProperties) {
            check(!actual.contains("\"" + property + "\""),
                    "unset optional property must be omitted: " + property);
        }
    }

    public static void main(String[] args) throws Exception {
        testConstructionValidation();
        testFailedPrecheckGatesMutation();
        testSuccessfulPrecheckPermitsOneCreate();
        testMockRefusesUnnamedOperations();
        System.out.println(
                "PASS: contract-pinned VCF log-forwarder precheck gate ("
                        + checks + " checks)");
    }
}
