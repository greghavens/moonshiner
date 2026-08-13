import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public final class TestMain {
    private static final String TOKEN = "fixture-access-token-never-real";
    private static final String SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
    private static final String SPEC = "specifications/vcf-installer/vcf-installer-openapi.json";

    public static void main(String[] args) throws Exception {
        testProtectedContractProvenance();
        testPollsToTerminalAndExactWire();
        testExplicitZerosAndMandatoryPoll();
        testPollIntervalIsHonored();
        testJsonEscapingAndUtf8TaskId();
        testStatusNormalizationAndTerminalClassification();
        testTerminalFailureAndProtocolFailures();
        testHttpFailuresAndSuccessfulResponseValidation();
        testValidationBeforeWire();
        testInterruptedHttpCall();
        testMockServesOnlyFocusedOperations();
        System.out.println("PASS: VCF Installer proxy update polling contract");
    }

    private static void testProtectedContractProvenance() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        for (String text : List.of(contract, sources)) {
            check(text.contains(SHA), "pinned repository SHA must be recorded");
            check(text.contains(SPEC), "specification path must be recorded");
            check(text.contains("updateProxyConfiguration"), "submit operationId must be recorded");
            check(text.contains("getTask"), "poll operationId must be recorded");
        }
        eq(2, occurrences(sources, "\"specJsonPointer\""), "one source record per operationId");
        check(sources.contains("\"documentationPageUsedAsContractSource\": false"),
                "contract source must be the OpenAPI specification");
    }

    private static void testPollsToTerminalAndExactWire() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller()) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration configuration =
                    new VcfInstallerClient.ProxyConfiguration(
                            true, "proxy.lab.local", 3128, "HTTPS", null, null, Boolean.FALSE);

            VcfInstallerClient.Task task = client.updateProxyAndWait(configuration, Duration.ZERO);
            eq("proxy/task 17", task.id(), "terminal task ID");
            eq("Successful", task.status(), "terminal task status");

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(4, requests.size(), "one submit and three polls");
            String expectedBody = "{\"isEnabled\":true,\"host\":\"proxy.lab.local\",\"port\":3128,"
                    + "\"transferProtocol\":\"HTTPS\",\"isAuthenticated\":false}";
            assertPatch(requests.get(0), expectedBody);
            for (int i = 1; i < requests.size(); i++) {
                assertGet(requests.get(i), "/v1/tasks/proxy%2Ftask%2017");
            }

            for (String omitted : List.of("isConfigured", "username", "password")) {
                check(!expectedBody.contains("\"" + omitted + "\""),
                        "unset optional field must be absent: " + omitted);
            }
        }
    }

    private static void testExplicitZerosAndMandatoryPoll() throws Exception {
        List<MockVcfInstaller.Reply> replies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-zero", "Successful")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-zero", "Successful")));
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl() + "/", TOKEN);
            VcfInstallerClient.ProxyConfiguration configuration =
                    new VcfInstallerClient.ProxyConfiguration(
                            false, "", 0, "HTTP", "", "", Boolean.FALSE);

            VcfInstallerClient.Task task = client.updateProxyAndWait(configuration, Duration.ZERO);
            eq("task-zero", task.id(), "explicit-zero task ID");
            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(2, requests.size(), "accepted terminal-looking task must still be polled");
            assertPatch(requests.get(0),
                    "{\"isEnabled\":false,\"host\":\"\",\"port\":0,\"transferProtocol\":\"HTTP\","
                            + "\"username\":\"\",\"password\":\"\",\"isAuthenticated\":false}");
            assertGet(requests.get(1), "/v1/tasks/task-zero");
        }

        List<MockVcfInstaller.Reply> omittedReplies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-omit", "Pending")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-omit", "Successful")));
        try (MockVcfInstaller mock = new MockVcfInstaller(omittedReplies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            client.updateProxyAndWait(
                    new VcfInstallerClient.ProxyConfiguration(false, null, null, null, null, null, null),
                    Duration.ZERO);
            assertPatch(mock.requests().get(0), "{\"isEnabled\":false}");
        }
    }

    private static void testPollIntervalIsHonored() throws Exception {
        List<MockVcfInstaller.Reply> replies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-delay", "Pending")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-delay", "Pending")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-delay", "Queued")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-delay", "Successful")));
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            client.updateProxyAndWait(minimalConfiguration(), Duration.ofMillis(500));
            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(4, requests.size(), "one submit and three interval-separated polls");
            for (int index = 2; index < requests.size(); index++) {
                long gapMillis = Duration.ofNanos(
                        requests.get(index).receivedNanos()
                                - requests.get(index - 1).receivedNanos()).toMillis();
                check(gapMillis >= 450,
                        "each nonterminal poll must wait for the interval; gap=" + gapMillis);
            }
        }
    }

    private static void testJsonEscapingAndUtf8TaskId() throws Exception {
        String taskId = " task/ä ?#% ";
        List<MockVcfInstaller.Reply> replies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task(taskId, "Pending")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task(taskId, "Successful")));
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration configuration =
                    new VcfInstallerClient.ProxyConfiguration(
                            true, "quote\" slash\\ line\n control\u0001 emoji-😀", -1, "",
                            null, "tab\treturn\rback\bform\f", Boolean.TRUE);

            VcfInstallerClient.Task task = client.updateProxyAndWait(configuration, Duration.ZERO);
            eq(taskId, task.id(), "UTF-8 task ID retained");
            assertPatch(mock.requests().get(0),
                    "{\"isEnabled\":true,\"host\":\"quote\\\" slash\\\\ line\\n control"
                            + "\\u0001 emoji-😀\",\"port\":-1,\"transferProtocol\":\"\","
                            + "\"password\":\"tab\\treturn\\rback\\bform\\f\","
                            + "\"isAuthenticated\":true}");
            assertGet(mock.requests().get(1), "/v1/tasks/%20task%2F%C3%A4%20%3F%23%25%20");
        }
    }

    private static void testStatusNormalizationAndTerminalClassification() throws Exception {
        Locale original = Locale.getDefault();
        Locale.setDefault(Locale.forLanguageTag("tr-TR"));
        try {
            List<MockVcfInstaller.Reply> normalized = List.of(
                    new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-status", "Pending")),
                    new MockVcfInstaller.Reply(
                            200, MockVcfInstaller.task("task-status", "  in \t  progress \n")),
                    new MockVcfInstaller.Reply(
                            200, MockVcfInstaller.task("task-status", "\r queued\t")),
                    new MockVcfInstaller.Reply(
                            200, MockVcfInstaller.task("task-status", "  successful  ")));
            try (MockVcfInstaller mock = new MockVcfInstaller(normalized)) {
                VcfInstallerClient.Task task = new VcfInstallerClient(mock.baseUrl(), TOKEN)
                        .updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
                eq("  successful  ", task.status(), "terminal status representation retained");
                eq(4, mock.requests().size(), "normalized nonterminal statuses keep polling");
            }
        } finally {
            Locale.setDefault(original);
        }

        for (String status : List.of(
                "FAILED", " cancelled ", "Completed \t With\nWarning", "SKIPPED", "Timed\tOut")) {
            List<MockVcfInstaller.Reply> replies = List.of(
                    new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-fail", "Pending")),
                    new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-fail", status)));
            try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
                VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
                try {
                    client.updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
                    fail("expected TaskFailedException for " + status);
                } catch (VcfInstallerClient.TaskFailedException ex) {
                    eq("task-fail", ex.task().id(), "failed task retained");
                    eq(status, ex.task().status(), "failed status retained");
                    check(!ex.getMessage().contains(TOKEN), "task failure must not leak token");
                }
            }
        }
    }

    private static void testTerminalFailureAndProtocolFailures() throws Exception {
        String secretBody = "{\"errorCode\":\"BAD_REQUEST\",\"message\":\"" + TOKEN
                + " proxy-password\"}";
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(400, secretBody)))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
                fail("expected VcfApiException");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("updateProxyConfiguration", ex.operationId(), "API operationId");
                eq(400, ex.statusCode(), "API status");
                check(!ex.getMessage().contains(TOKEN), "API error must not leak token");
                check(!ex.getMessage().contains("proxy-password"), "API error must not leak password");
            }
        }

        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202,
                        "{\"name\":\"Update Proxy\",\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"2026-07-01T12:00:00Z\"}")),
                "updateProxyConfiguration",
                "missing accepted task ID");

        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-a", "Pending")),
                        new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-b", "Successful"))),
                "getTask",
                "polled ID mismatch");

        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-state", "Pending")),
                        new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-state", "Paused"))),
                "getTask",
                "unknown status");

        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        202, "text/plain", MockVcfInstaller.task("task-media", "Pending"))),
                "updateProxyConfiguration",
                "wrong success media type");
    }

    private static void assertProtocol(
            List<MockVcfInstaller.Reply> replies, String operationId, String label) throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
                fail("expected ProtocolException for " + label);
            } catch (VcfInstallerClient.ProtocolException ex) {
                eq(operationId, ex.operationId(), label + " operationId");
                check(!ex.getMessage().contains(TOKEN), label + " must not leak token");
            }
        }
    }

    private static void testHttpFailuresAndSuccessfulResponseValidation() throws Exception {
        assertApi(
                List.of(new MockVcfInstaller.Reply(
                        200, MockVcfInstaller.task("wrong-submit-status", "Pending"))),
                "updateProxyConfiguration", 200, "PATCH requires exact HTTP 202");
        assertApi(
                List.of(
                        new MockVcfInstaller.Reply(
                                202, MockVcfInstaller.task("task-http", "Pending")),
                        new MockVcfInstaller.Reply(
                                503, null, "{\"message\":\"" + TOKEN + " proxy-password\"}")),
                "getTask", 503, "GET requires exact HTTP 200");

        String valid = MockVcfInstaller.task("task-json", "Pending");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202,
                        "{\"id\":\"task-json\",\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"2026-07-01T12:00:00Z\"}")),
                "updateProxyConfiguration", "missing required Task.name");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202,
                        "{\"id\":\"task-json\",\"name\":\"Update Proxy\","
                                + "\"status\":\" \","
                                + "\"creationTimestamp\":\"2026-07-01T12:00:00Z\"}")),
                "updateProxyConfiguration", "blank required Task.status");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202,
                        "{\"id\":\"task-json\",\"name\":\"Update Proxy\","
                                + "\"status\":\"Pending\",\"creationTimestamp\":1}")),
                "updateProxyConfiguration", "non-string required Task member");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202, valid.substring(0, valid.length() - 1))),
                "updateProxyConfiguration", "malformed successful JSON");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202, "[" + valid + "]")),
                "updateProxyConfiguration", "successful Task is not an object");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202,
                        "{\"id\":null,\"id\":\"task-json\",\"name\":\"Update Proxy\","
                                + "\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"2026-07-01T12:00:00Z\"}")),
                "updateProxyConfiguration", "duplicate successful JSON member");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        202, valid.substring(0, valid.length() - 1) + ",\"extra\":01}")),
                "updateProxyConfiguration", "invalid JSON number in successful body");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(202, null, valid)),
                "updateProxyConfiguration", "missing success media type");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, valid),
                        new MockVcfInstaller.Reply(200, "text/plain", valid)),
                "getTask", "wrong poll success media type");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, valid),
                        new MockVcfInstaller.Reply(200,
                                "{\"id\":\"task-json\",\"name\":\"Update Proxy\","
                                        + "\"status\":\"Successful\"}")),
                "getTask", "missing polled Task creationTimestamp");

        List<MockVcfInstaller.Reply> parameterizedMediaType = List.of(
                new MockVcfInstaller.Reply(
                        202, "Application/JSON; charset=UTF-8", valid),
                new MockVcfInstaller.Reply(
                        200, "application/json; charset=utf-8",
                        MockVcfInstaller.task("task-json", "Successful")));
        try (MockVcfInstaller mock = new MockVcfInstaller(parameterizedMediaType)) {
            VcfInstallerClient.Task task = new VcfInstallerClient(mock.baseUrl(), TOKEN)
                    .updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
            eq("task-json", task.id(), "case-insensitive parameterized JSON media type accepted");
        }
    }

    private static void assertApi(
            List<MockVcfInstaller.Reply> replies,
            String operationId,
            int statusCode,
            String label) throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            try {
                client.updateProxyAndWait(minimalConfiguration(), Duration.ZERO);
                fail("expected VcfApiException for " + label);
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq(operationId, ex.operationId(), label + " operationId");
                eq(statusCode, ex.statusCode(), label + " status code");
                check(!ex.getMessage().contains(TOKEN), label + " must not leak token");
                check(!ex.getMessage().contains("proxy-password"), label + " must not leak password");
            }
        }
    }

    private static void testValidationBeforeWire() throws Exception {
        for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                () -> new VcfInstallerClient(null, TOKEN),
                () -> new VcfInstallerClient(" ", TOKEN),
                () -> new VcfInstallerClient("127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("ftp://127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http://user@127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1/api", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1?query=true", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1#fragment", TOKEN),
                () -> new VcfInstallerClient("http:///missing-host", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1", null),
                () -> new VcfInstallerClient("http://127.0.0.1", " "),
                () -> new VcfInstallerClient("http://127.0.0.1", "\t"),
                () -> new VcfInstallerClient("http://127.0.0.1", "bad\nheader"),
                () -> new VcfInstallerClient("http://127.0.0.1", "bad\rheader"),
                () -> new VcfInstallerClient("http://127.0.0.1", "bad\u007fheader"))) {
            expect(IllegalArgumentException.class, invalid, "constructor validation");
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            expect(NullPointerException.class,
                    () -> client.updateProxyAndWait(null, Duration.ZERO), "null configuration");
            expect(NullPointerException.class,
                    () -> client.updateProxyAndWait(minimalConfiguration(), null), "null poll interval");
            expect(IllegalArgumentException.class,
                    () -> client.updateProxyAndWait(minimalConfiguration(), Duration.ofMillis(-1)),
                    "negative poll interval");
            eq(0, mock.requests().size(), "argument validation must happen before the wire");
        }
    }

    private static void testInterruptedHttpCall() throws Exception {
        List<MockVcfInstaller.Reply> replies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("task-interrupt", "Pending")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.task("task-interrupt", "Pending")));
        try (MockVcfInstaller mock = new MockVcfInstaller(replies, 1)) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            AtomicReference<Throwable> failure = new AtomicReference<>();
            AtomicBoolean interrupted = new AtomicBoolean();
            Thread worker = new Thread(() -> {
                try {
                    client.updateProxyAndWait(minimalConfiguration(), Duration.ofDays(1));
                } catch (Throwable thrown) {
                    failure.set(thrown);
                    interrupted.set(Thread.currentThread().isInterrupted());
                }
            }, "vcf-interrupted-call-test");
            worker.setDaemon(true);
            worker.start();
            mock.awaitBlockedResponse();
            worker.interrupt();
            worker.join(5_000);
            boolean stopped = !worker.isAlive();
            mock.releaseBlockedResponse();
            if (worker.isAlive()) {
                worker.interrupt();
                worker.join(5_000);
            }
            check(stopped, "an interrupted HTTP call must stop promptly");
            check(failure.get() != null,
                    "interrupted HTTP call must stop by throwing instead of returning");
            check(interrupted.get(), "interrupted HTTP call must restore interrupted status");
        }
    }

    private static void testMockServesOnlyFocusedOperations() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl() + "/v1/system/proxy-configuration"))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            eq(404, response.statusCode(), "unlisted getProxyConfiguration route must not be served");
        }
    }

    private static VcfInstallerClient.ProxyConfiguration minimalConfiguration() {
        return new VcfInstallerClient.ProxyConfiguration(false, null, null, null, null, null, null);
    }

    private static void assertPatch(MockVcfInstaller.RecordedRequest request, String expectedBody) {
        byte[] expectedBytes = expectedBody.getBytes(StandardCharsets.UTF_8);
        eq("PATCH", request.method(), "submit method");
        eq("/v1/system/proxy-configuration", request.rawTarget(), "submit raw target");
        eq(null, request.rawQuery(), "submit query omitted");
        assertCommonHeaders(request);
        eq(List.of("application/json"), request.headerValues("Content-Type"), "submit content type");
        eq(List.of(Integer.toString(expectedBytes.length)), request.headerValues("Content-Length"),
                "submit fixed content length");
        eq(List.of(), request.headerValues("Transfer-Encoding"), "submit transfer encoding omitted");
        check(Arrays.equals(expectedBytes, request.body()), "exact compact UTF-8 submit body");
    }

    private static void assertGet(MockVcfInstaller.RecordedRequest request, String rawTarget) {
        eq("GET", request.method(), "poll method");
        eq(rawTarget, request.rawTarget(), "poll raw target");
        eq(null, request.rawQuery(), "poll query omitted");
        assertCommonHeaders(request);
        eq(List.of(), request.headerValues("Content-Type"), "poll content type omitted");
        eq(List.of(), request.headerValues("Transfer-Encoding"), "poll transfer encoding omitted");
        check(request.body().length == 0, "poll body must be empty");
        List<String> lengths = request.headerValues("Content-Length");
        check(lengths.isEmpty() || lengths.equals(List.of("0")),
                "poll must not have positive content length: " + lengths);
    }

    private static void assertCommonHeaders(MockVcfInstaller.RecordedRequest request) {
        eq(List.of("Bearer " + TOKEN), request.headerValues("Authorization"),
                "single Authorization header");
        eq(List.of("application/json"), request.headerValues("Accept"), "single Accept header");
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        for (int at = 0; (at = text.indexOf(needle, at)) >= 0; at += needle.length()) {
            count++;
        }
        return count;
    }

    private static void expect(Class<? extends Throwable> type, ThrowingRunnable action, String label) {
        try {
            action.run();
            fail("expected " + type.getSimpleName() + " for " + label);
        } catch (Throwable thrown) {
            if (!type.isInstance(thrown)) {
                throw new AssertionError(label + " threw " + thrown, thrown);
            }
        }
    }

    private static void eq(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
