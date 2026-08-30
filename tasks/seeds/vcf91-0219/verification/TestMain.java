import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;

public final class TestMain {
    private static final String TOKEN = "fixture-access-token-never-real";
    private static final String SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba";
    private static final String SPEC =
            "specifications/vcf-installer/vcf-installer-openapi.json";

    public static void main(String[] args) throws Exception {
        testProtectedContractProvenance();
        testFinalFailurePreservesAcceptedStepsAndExactWire();
        testFailurePositionReportsAndStops();
        testOnlyExact202IsAccepted();
        testExplicitEmptyZeroAndFalseValues();
        testRuntimeStringsAreJsonEscaped();
        testAcceptedResponseProtocolChecks();
        testInterruptedCallRestoresStatus();
        testValidationBeforeWire();
        testMockServesOnlyFocusedOperations();
        System.out.println("PASS: VCF Installer partial bootstrap progress contract");
    }

    private static void testProtectedContractProvenance() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        for (String text : List.of(contract, sources)) {
            check(text.contains(SHA), "pinned repository SHA must be recorded");
            check(text.contains(SPEC), "specification path must be recorded");
            check(text.contains("updateProxyConfiguration"), "proxy operationId must be recorded");
            check(text.contains("updateDepotSettings"), "depot operationId must be recorded");
            check(text.contains("syncDepotMetadata"), "sync operationId must be recorded");
        }
        eq(3, occurrences(sources, "\"specJsonPointer\""),
                "one specification source record per operationId");
        check(sources.contains("\"documentationPageUsedAsContractSource\": false"),
                "the OpenAPI specification, not a documentation page, is the contract source");
        check(contract.contains("\"infoVersion\": \"9.1.0.0\""),
                "focused contract must identify VCF Installer 9.1");
    }

    private static void testFinalFailurePreservesAcceptedStepsAndExactWire() throws Exception {
        VcfInstallerClient.ProxyConfiguration proxy =
                new VcfInstallerClient.ProxyConfiguration(
                        true, "proxy.lab.local", 3128, "HTTPS", null, null, Boolean.FALSE);
        VcfInstallerClient.DepotSettings depot = new VcfInstallerClient.DepotSettings(
                new VcfInstallerClient.DepotAccount(null, null, "dl-\"token", null),
                null,
                new VcfInstallerClient.DepotConfiguration(false, null, 0, null));

        try (MockVcfInstaller mock = new MockVcfInstaller()) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl() + "/", TOKEN);
            try {
                client.configureDepotAccess(proxy, depot);
                fail("expected final sync failure");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("syncDepotMetadata", ex.operationId(), "failed operationId");
                eq(500, ex.statusCode(), "failed HTTP status");
                eq("DEPOT_SYNC_FAILED", ex.errorCode(), "error code");
                eq("INTERNAL_ERROR", ex.errorType(), "error type");
                eq("metadata unavailable", ex.apiMessage(), "error message");
                eq("retry later", ex.remediationMessage(), "remediation message");
                eq("ref-0219", ex.referenceToken(), "reference token");
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                        List.of(
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.FAILED),
                        List.of(202, 202, 500));
                eq("proxy-task-0219", ex.report().steps().get(0).taskId(),
                        "accepted proxy task id");
                eq("DEPOT_SYNC_FAILED", ex.report().steps().get(2).errorCode(),
                        "failed step error code");
                eq("metadata unavailable", ex.report().steps().get(2).errorMessage(),
                        "failed step error message");
                expect(UnsupportedOperationException.class,
                        () -> ex.report().steps().add(ex.report().steps().get(0)),
                        "report steps must be immutable");
                check(!ex.getMessage().contains(TOKEN), "exception message must not leak token");
                check(!ex.getMessage().contains("dl-\"token"),
                        "exception message must not leak depot credentials");
            }

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(3, requests.size(), "exact three-step request sequence");
            String proxyBody = "{\"isEnabled\":true,\"host\":\"proxy.lab.local\","
                    + "\"port\":3128,\"transferProtocol\":\"HTTPS\","
                    + "\"isAuthenticated\":false}";
            String depotBody = "{\"vmwareAccount\":{\"downloadToken\":\"dl-\\\"token\"},"
                    + "\"depotConfiguration\":{\"isOfflineDepot\":false,\"port\":0}}";
            assertJsonWrite(
                    requests.get(0), "PATCH", "/v1/system/proxy-configuration", proxyBody);
            assertJsonWrite(
                    requests.get(1), "PUT", "/v1/system/settings/depot", depotBody);
            assertBodylessPatch(
                    requests.get(2), "/v1/system/settings/depot/depot-sync-info");

            for (String omitted : List.of("isConfigured", "username", "password")) {
                check(!proxyBody.contains("\"" + omitted + "\""),
                        "unset/read-only proxy member must be absent: " + omitted);
            }
            for (String omitted : List.of(
                    "username", "password", "status", "message", "downloadActivationCode",
                    "offlineAccount", "hostname", "url", "syncStatus")) {
                check(!depotBody.contains("\"" + omitted + "\""),
                        "unset/read-only depot member must be absent: " + omitted);
            }
        }
    }

    private static void testFailurePositionReportsAndStops() throws Exception {
        String firstError = MockVcfInstaller.apiError(
                "PROXY_REJECTED", "VALIDATION", "proxy-secret reflected", "fix proxy", "ref-p");
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(400, firstError)))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration secretProxy =
                    new VcfInstallerClient.ProxyConfiguration(
                            true, "proxy", 1, "HTTP", "user", "proxy-secret", true);
            try {
                client.configureDepotAccess(secretProxy, minimalDepot());
                fail("expected first-step API failure");
            } catch (VcfInstallerClient.VcfApiException ex) {
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.FAILED,
                        List.of(
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(400, 0, 0));
                eq("PROXY_REJECTED", ex.report().steps().get(0).errorCode(),
                        "first-step error code");
                check(!ex.getMessage().contains("proxy-secret"),
                        "exception message must not leak a proxy password");
            }
            eq(1, mock.requests().size(), "first failure must stop later operations");
        }

        String secondError = MockVcfInstaller.apiError(
                "DEPOT_REJECTED", "VALIDATION", "bad depot", "fix depot", "ref-d");
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-before-depot")),
                new MockVcfInstaller.Reply(400, secondError)))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.configureDepotAccess(minimalProxy(), minimalDepot());
                fail("expected second-step API failure");
            } catch (VcfInstallerClient.VcfApiException ex) {
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                        List.of(
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(202, 400, 0));
                eq("task-before-depot", ex.report().steps().get(0).taskId(),
                        "earlier accepted task must be retained");
                eq("DEPOT_REJECTED", ex.errorCode(), "second-step error code");
            }
            eq(2, mock.requests().size(), "second failure must not run sync");
        }
    }

    private static void testExplicitEmptyZeroAndFalseValues() throws Exception {
        List<MockVcfInstaller.Reply> accepted = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("explicit-task")),
                new MockVcfInstaller.Reply(202, "{}"),
                new MockVcfInstaller.Reply(202, "{\"syncStatus\":\"QUEUED\"}"));
        try (MockVcfInstaller mock = new MockVcfInstaller(accepted)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration proxy =
                    new VcfInstallerClient.ProxyConfiguration(
                            false, "", 0, "", "", "", false);
            VcfInstallerClient.DepotSettings depot = new VcfInstallerClient.DepotSettings(
                    new VcfInstallerClient.DepotAccount("", "", "", ""),
                    null,
                    new VcfInstallerClient.DepotConfiguration(false, "", 0, ""));

            VcfInstallerClient.ChangeReport report = client.configureDepotAccess(proxy, depot);
            assertReport(
                    report,
                    VcfInstallerClient.Outcome.ACCEPTED,
                    List.of(
                            VcfInstallerClient.StepStatus.ACCEPTED,
                            VcfInstallerClient.StepStatus.ACCEPTED,
                            VcfInstallerClient.StepStatus.ACCEPTED),
                    List.of(202, 202, 202));

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            assertJsonWrite(
                    requests.get(0),
                    "PATCH",
                    "/v1/system/proxy-configuration",
                    "{\"isEnabled\":false,\"host\":\"\",\"port\":0,"
                            + "\"transferProtocol\":\"\",\"username\":\"\","
                            + "\"password\":\"\",\"isAuthenticated\":false}");
            assertJsonWrite(
                    requests.get(1),
                    "PUT",
                    "/v1/system/settings/depot",
                    "{\"vmwareAccount\":{\"username\":\"\",\"password\":\"\","
                            + "\"downloadToken\":\"\",\"downloadActivationCode\":\"\"},"
                            + "\"depotConfiguration\":{\"isOfflineDepot\":false,"
                            + "\"hostname\":\"\",\"port\":0,\"url\":\"\"}}");
            assertBodylessPatch(
                    requests.get(2), "/v1/system/settings/depot/depot-sync-info");
        }
    }

    private static void testOnlyExact202IsAccepted() throws Exception {
        String error = MockVcfInstaller.apiError(
                "UNEXPECTED_OK", "PROTOCOL", "wrong success status", "use 202", "ref-200");
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(200, error)))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.configureDepotAccess(minimalProxy(), minimalDepot());
                fail("expected HTTP 200 to be rejected");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("updateProxyConfiguration", ex.operationId(), "HTTP 200 operationId");
                eq(200, ex.statusCode(), "only exact HTTP 202 is accepted");
                eq("UNEXPECTED_OK", ex.errorCode(), "HTTP 200 error code");
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.FAILED,
                        List.of(
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(200, 0, 0));
            }
            eq(1, mock.requests().size(), "HTTP 200 must stop the workflow");
        }
    }

    private static void testRuntimeStringsAreJsonEscaped() throws Exception {
        String runtime = "q\"\\\n\t\u0001\u2603";
        List<MockVcfInstaller.Reply> accepted = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("escape-task")),
                new MockVcfInstaller.Reply(202, "{}"),
                new MockVcfInstaller.Reply(202, "{\"syncStatus\":\"QUEUED\"}"));
        try (MockVcfInstaller mock = new MockVcfInstaller(accepted)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration proxy =
                    new VcfInstallerClient.ProxyConfiguration(
                            null, runtime, null, null, null, null, null);
            VcfInstallerClient.DepotSettings depot = new VcfInstallerClient.DepotSettings(
                    new VcfInstallerClient.DepotAccount(runtime, null, null, null),
                    null,
                    null);

            client.configureDepotAccess(proxy, depot);

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            assertJsonWrite(
                    requests.get(0),
                    "PATCH",
                    "/v1/system/proxy-configuration",
                    "{\"host\":\"q\\\"\\\\\\n\\t\\u0001\u2603\"}");
            assertJsonWrite(
                    requests.get(1),
                    "PUT",
                    "/v1/system/settings/depot",
                    "{\"vmwareAccount\":{\"username\":\"q\\\"\\\\\\n\\t\\u0001\u2603\"}}");
        }
    }

    private static void testAcceptedResponseProtocolChecks() throws Exception {
        assertProtocolFailure(
                List.of(new MockVcfInstaller.Reply(
                        202, "text/plain", MockVcfInstaller.task("wrong-media"))),
                "updateProxyConfiguration",
                VcfInstallerClient.Outcome.FAILED,
                List.of(
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                1,
                "proxy wrong media type");

        assertProtocolFailure(
                List.of(new MockVcfInstaller.Reply(
                        202,
                        "{\"name\":\"Update Proxy\",\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"2026-08-03T12:00:00Z\"}")),
                "updateProxyConfiguration",
                VcfInstallerClient.Outcome.FAILED,
                List.of(
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                1,
                "proxy missing required task id");

        assertProtocolFailure(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-array")),
                        new MockVcfInstaller.Reply(202, "[]")),
                "updateDepotSettings",
                VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                List.of(
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                2,
                "depot accepted response must be an object");

        assertProtocolFailure(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-sync")),
                        new MockVcfInstaller.Reply(202, "{}"),
                        new MockVcfInstaller.Reply(202, "{\"errorMessage\":\"none\"}")),
                "syncDepotMetadata",
                VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                List.of(
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.FAILED),
                3,
                "sync missing required status");

        assertProtocolFailure(
                List.of(new MockVcfInstaller.Reply(
                        202,
                        "{\"id\":\"task\",\"name\":\" \",\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"2026-08-03T12:00:00Z\"}")),
                "updateProxyConfiguration",
                VcfInstallerClient.Outcome.FAILED,
                List.of(
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                1,
                "proxy required task members must be nonblank");

        assertProtocolFailure(
                List.of(new MockVcfInstaller.Reply(
                        202,
                        "{\"id\":\"task\",\"name\":\"Update Proxy\","
                                + "\"creationTimestamp\":\"2026-08-03T12:00:00Z\"}")),
                "updateProxyConfiguration",
                VcfInstallerClient.Outcome.FAILED,
                List.of(
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                1,
                "proxy task status is required");

        assertProtocolFailure(
                List.of(new MockVcfInstaller.Reply(
                        202,
                        "{\"id\":\"task\",\"name\":\"Update Proxy\","
                                + "\"status\":\"Pending\",\"creationTimestamp\":\"\"}")),
                "updateProxyConfiguration",
                VcfInstallerClient.Outcome.FAILED,
                List.of(
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                1,
                "proxy task creation timestamp must be nonblank");

        assertProtocolFailure(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-depot-media")),
                        new MockVcfInstaller.Reply(202, "text/plain", "{}")),
                "updateDepotSettings",
                VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                List.of(
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.FAILED,
                        VcfInstallerClient.StepStatus.NOT_RUN),
                2,
                "depot wrong media type");

        assertProtocolFailure(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-media")),
                        new MockVcfInstaller.Reply(202, "{}"),
                        new MockVcfInstaller.Reply(
                                202, "text/plain", "{\"syncStatus\":\"QUEUED\"}")),
                "syncDepotMetadata",
                VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                List.of(
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.FAILED),
                3,
                "sync wrong media type");

        assertProtocolFailure(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-blank-sync")),
                        new MockVcfInstaller.Reply(202, "{}"),
                        new MockVcfInstaller.Reply(202, "{\"syncStatus\":\"\u2003\"}")),
                "syncDepotMetadata",
                VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                List.of(
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.ACCEPTED,
                        VcfInstallerClient.StepStatus.FAILED),
                3,
                "sync status must be nonblank");
    }

    private static void testInterruptedCallRestoresStatus() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller()) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            Thread.currentThread().interrupt();
            try {
                client.configureDepotAccess(minimalProxy(), minimalDepot());
                fail("expected interrupted request to fail");
            } catch (VcfInstallerClient.TransportException ex) {
                eq("updateProxyConfiguration", ex.operationId(), "interrupted operationId");
                check(Thread.currentThread().isInterrupted(),
                        "interrupted status must be restored");
                check(!ex.getMessage().contains(TOKEN),
                        "interrupted transport message must not leak the token");
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.FAILED,
                        List.of(
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(0, 0, 0));
            } finally {
                Thread.interrupted();
            }
        }
    }

    private static void assertProtocolFailure(
            List<MockVcfInstaller.Reply> replies,
            String operationId,
            VcfInstallerClient.Outcome outcome,
            List<VcfInstallerClient.StepStatus> statuses,
            int requestCount,
            String label) throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.configureDepotAccess(minimalProxy(), minimalDepot());
                fail("expected ProtocolException for " + label);
            } catch (VcfInstallerClient.ProtocolException ex) {
                eq(operationId, ex.operationId(), label + " operationId");
                assertReport(ex.report(), outcome, statuses,
                        statuses.stream().map(status -> status == VcfInstallerClient.StepStatus.NOT_RUN
                                ? 0 : 202).toList());
                check(!ex.getMessage().contains(TOKEN), label + " must not leak token");
            }
            eq(requestCount, mock.requests().size(), label + " request count");
        }
    }

    private static void testValidationBeforeWire() throws Exception {
        for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                () -> new VcfInstallerClient(" ", TOKEN),
                () -> new VcfInstallerClient("ftp://127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http:/", TOKEN),
                () -> new VcfInstallerClient("http://user@127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1/api", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1?query=true", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1#fragment", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1", " "),
                () -> new VcfInstallerClient("http://127.0.0.1", "bad\nheader"))) {
            expect(IllegalArgumentException.class, invalid, "constructor validation");
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            expect(NullPointerException.class,
                    () -> client.configureDepotAccess(null, minimalDepot()),
                    "null proxy validation");
            expect(NullPointerException.class,
                    () -> client.configureDepotAccess(minimalProxy(), null),
                    "null depot validation");
            eq(0, mock.requests().size(), "argument validation must happen before the wire");
        }
    }

    private static void testMockServesOnlyFocusedOperations() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(
                                    URI.create(mock.baseUrl() + "/v1/system/proxy-configuration"))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            eq(404, response.statusCode(),
                    "unlisted getProxyConfiguration operation must not be served");

            HttpResponse<String> queryResponse = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(
                                    URI.create(mock.baseUrl()
                                            + "/v1/system/settings/depot/depot-sync-info?force=true"))
                            .method("PATCH", HttpRequest.BodyPublishers.noBody())
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            eq(404, queryResponse.statusCode(), "contract route with query must not be served");
        }
    }

    private static VcfInstallerClient.ProxyConfiguration minimalProxy() {
        return new VcfInstallerClient.ProxyConfiguration(
                false, null, null, null, null, null, null);
    }

    private static VcfInstallerClient.DepotSettings minimalDepot() {
        return new VcfInstallerClient.DepotSettings(
                new VcfInstallerClient.DepotAccount(null, null, "token", null),
                null,
                null);
    }

    private static void assertReport(
            VcfInstallerClient.ChangeReport report,
            VcfInstallerClient.Outcome outcome,
            List<VcfInstallerClient.StepStatus> statuses,
            List<Integer> httpStatuses) {
        eq(outcome, report.outcome(), "report outcome");
        eq(3, report.steps().size(), "report step count");
        eq(List.of("updateProxyConfiguration", "updateDepotSettings", "syncDepotMetadata"),
                report.steps().stream().map(VcfInstallerClient.StepResult::operationId).toList(),
                "report operation order");
        eq(statuses,
                report.steps().stream().map(VcfInstallerClient.StepResult::status).toList(),
                "report step statuses");
        eq(httpStatuses,
                report.steps().stream().map(VcfInstallerClient.StepResult::httpStatus).toList(),
                "report HTTP statuses");
    }

    private static void assertJsonWrite(
            MockVcfInstaller.RecordedRequest request,
            String method,
            String path,
            String expectedBody) {
        byte[] expectedBytes = expectedBody.getBytes(StandardCharsets.UTF_8);
        eq(method, request.method(), "write method");
        eq(path, request.rawPath(), "write raw path");
        eq(path, request.rawTarget(), "write raw target without query or bare question mark");
        eq(null, request.rawQuery(), "write query omitted");
        assertCommonHeaders(request);
        eq(List.of("application/json"), request.headerValues("Content-Type"),
                "one exact JSON content type");
        eq(1, request.headerValues("Content-Length").size(),
                "one fixed content length");
        eq(Integer.toString(expectedBytes.length), request.headerValues("Content-Length").get(0),
                "UTF-8 content length");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                "no transfer encoding for fixed body");
        check(Arrays.equals(expectedBytes, request.body()), "exact compact UTF-8 request body");
    }

    private static void assertBodylessPatch(
            MockVcfInstaller.RecordedRequest request, String path) {
        eq("PATCH", request.method(), "sync method");
        eq(path, request.rawPath(), "sync raw path");
        eq(path, request.rawTarget(), "sync raw target without query or bare question mark");
        eq(null, request.rawQuery(), "sync query omitted");
        assertCommonHeaders(request);
        eq(List.of(), request.headerValues("Content-Type"), "bodyless sync content type omitted");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                "bodyless sync transfer encoding omitted");
        for (String value : request.headerValues("Content-Length")) {
            eq(0L, Long.parseLong(value), "bodyless sync has no positive content length");
        }
        eq(0, request.body().length, "sync request body omitted");
    }

    private static void assertCommonHeaders(MockVcfInstaller.RecordedRequest request) {
        eq(List.of("Bearer " + TOKEN), request.headerValues("Authorization"),
                "one exact authorization header");
        eq(List.of("application/json"), request.headerValues("Accept"),
                "one exact accept header");
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        int index = 0;
        while ((index = text.indexOf(needle, index)) >= 0) {
            count++;
            index += needle.length();
        }
        return count;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            fail(message);
        }
    }

    private static void eq(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            fail(message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void expect(
            Class<? extends Throwable> type, ThrowingRunnable action, String message) {
        try {
            action.run();
        } catch (Throwable thrown) {
            if (type.isInstance(thrown)) {
                return;
            }
            fail(message + ": expected " + type.getSimpleName() + " but got " + thrown);
        }
        fail(message + ": expected " + type.getSimpleName());
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
