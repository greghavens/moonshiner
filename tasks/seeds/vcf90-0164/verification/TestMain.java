import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Protected acceptance harness. Its loopback mock implements exactly the two
 * operations pinned in docs/contract.json and retains a readable wire log.
 */
public final class TestMain {
    private static final String DEPLOYMENT_ID = "3f1274e1-7df5-4ac2-82f4-a028d12bf2ee";
    private static final String REQUEST_ID = "5ba75924-4928-45c6-bf93-fd13e6439b16";
    private static final String TOKEN = "fixture-token-vcf90";
    private static int checks;

    public static void main(String[] args) throws Exception {
        checkPinnedContractAndSources();
        checkUnsetOptionOmissionAndPolling();
        checkPopulatedBodySerialization();
        checkControlCharacterEscapingAndCodePointOrder();
        checkEveryTerminalStateStopsPolling();
        checkResponseErrors();
        checkOtherSuccessfulHttpStatuses();
        checkMockSurface();
        System.out.println("all checks passed (" + checks + " checks)");
    }

    private static void checkPinnedContractAndSources() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        check(contract.contains("\"source_kind\": \"reference_documentation\""),
                "contract identifies reference documentation");
        check(contract.contains("not from a published API specification"),
                "contract plainly disclaims a published specification");
        check(occurrences(contract, "\"operation\":") == 2,
                "contract names exactly two operations");
        check(contract.contains("\"operation\": \"Submit Deployment Action Request\""),
                "contract names submit operation");
        check(contract.contains("\"method\": \"POST\""),
                "contract records POST method");
        check(contract.contains("\"path\": \"/deployment/api/deployments/{deploymentId}/requests\""),
                "contract records submit path");
        check(contract.contains("\"operation\": \"Get Request\""),
                "contract names get operation");
        check(contract.contains("\"method\": \"GET\""),
                "contract records GET method");
        check(contract.contains("\"path\": \"/deployment/api/requests/{requestId}\""),
                "contract records request path");

        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        check(sources.contains("\"fetched_on\": \"2026-08-13\""),
                "source index records fetch date");
        check(occurrences(sources, "\"url\":") == 2,
                "source index records every operation page");
        check(sources.contains(
                "https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/" +
                "deployment/api/deployments/deploymentId/requests/post/"),
                "source index pins the VCF 9.0 submit page");
        check(sources.contains(
                "https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/" +
                "deployment/api/requests/requestId/get/"),
                "source index pins the VCF 9.0 get page");
        check(sources.contains("Submit Deployment Action Request — POST"),
                "submit source records its operation");
        check(sources.contains("Get Request — GET"),
                "get source records its operation");
        check(occurrences(sources, "\"fetched_on\": \"2026-08-13\"") == 3,
                "every source has the recorded fetch date");
    }

    private static void checkUnsetOptionOmissionAndPolling() throws Exception {
        try (ContractMock mock = new ContractMock(
                "SUCCESSFUL", List.of("INPROGRESS", "COMPLETION", "SUCCESSFUL"))) {
            VcfAutomationClient client = client(mock);
            VcfAutomationClient.RequestState state = client.submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null);

            check(REQUEST_ID.equals(state.id()), "returns polled request id");
            check("SUCCESSFUL".equals(state.status()), "returns final successful status");

            List<LoggedRequest> requests = mock.requests();
            check(requests.size() == 4,
                    "POST is followed by every required poll, including after COMPLETION");

            LoggedRequest submit = requests.get(0);
            check("POST".equals(submit.method), "submit method is POST");
            check(mock.submitPath().equals(submit.requestTarget), "submit target is exact");
            check(("Bearer " + TOKEN).equals(submit.authorization),
                    "submit carries bearer authorization");
            check("application/json".equals(submit.accept), "submit accepts JSON");
            check("application/json".equals(submit.contentType),
                    "submit content type is JSON");
            check("{\"actionId\":\"PowerOff\"}".equals(submit.body),
                    "unset optional fields are omitted from exact body");
            check(!submit.body.contains("inputs") && !submit.body.contains("reason") &&
                            !submit.body.contains("null") && !submit.body.contains("{}"),
                    "unset options are not serialized empty");

            for (int i = 1; i < requests.size(); i++) {
                LoggedRequest poll = requests.get(i);
                check("GET".equals(poll.method), "poll " + i + " method is GET");
                check(mock.requestPath().equals(poll.requestTarget),
                        "poll " + i + " uses returned request id");
                check(("Bearer " + TOKEN).equals(poll.authorization),
                        "poll " + i + " carries bearer authorization");
                check("application/json".equals(poll.accept),
                        "poll " + i + " accepts JSON");
                check(poll.contentType.isEmpty(), "poll " + i + " has no content type");
                check(poll.body.isEmpty(), "poll " + i + " has no body");
            }
        }
    }

    private static void checkPopulatedBodySerialization() throws Exception {
        try (ContractMock mock = new ContractMock("CREATED", List.of("ABORTED"))) {
            Map<String, String> inputs = new LinkedHashMap<>();
            inputs.put("message", "rack \"B\"\nline");
            inputs.put("delay", "5");

            VcfAutomationClient.RequestState state = client(mock)
                    .submitDeploymentActionAndWait(
                            DEPLOYMENT_ID, "Power\\Off", inputs, "maintenance\\window");

            check("ABORTED".equals(state.status()), "returns non-success terminal state");
            List<LoggedRequest> requests = mock.requests();
            check(requests.size() == 2, "populated request stops at first terminal poll");
            String expected = "{\"actionId\":\"Power\\\\Off\",\"inputs\":" +
                    "{\"delay\":\"5\",\"message\":\"rack \\\"B\\\"\\nline\"}," +
                    "\"reason\":\"maintenance\\\\window\"}";
            check(expected.equals(requests.get(0).body),
                    "populated fields use deterministic escaped JSON");
        }
    }

    private static void checkControlCharacterEscapingAndCodePointOrder() throws Exception {
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            Map<String, String> inputs = new LinkedHashMap<>();
            inputs.put("\uD800\uDC00", "supplementary");
            inputs.put("\uE000", "\b\f\r\t\u0001");

            VcfAutomationClient.RequestState state = client(mock)
                    .submitDeploymentActionAndWait(
                            DEPLOYMENT_ID, "Power\tOff", inputs, "why\rnow");

            check("SUCCESSFUL".equals(state.status()),
                    "control-character case reaches terminal state");
            String expected = "{\"actionId\":\"Power\\tOff\",\"inputs\":{\""
                    + "\uE000" + "\":\"\\b\\f\\r\\t\\u0001\",\""
                    + "\uD800\uDC00" + "\":\"supplementary\"},"
                    + "\"reason\":\"why\\rnow\"}";
            check(expected.equals(mock.requests().get(0).body),
                    "all controls are escaped and input keys use code-point order");
        }
    }

    private static void checkEveryTerminalStateStopsPolling() throws Exception {
        for (String terminal : List.of(
                "APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL", "FAILED")) {
            try (ContractMock mock = new ContractMock("CREATED", List.of(terminal, "INPROGRESS"))) {
                VcfAutomationClient.RequestState state = client(mock)
                        .submitDeploymentActionAndWait(
                                DEPLOYMENT_ID, "PowerOff", Map.of(), "");
                check(terminal.equals(state.status()), terminal + " is terminal");
                check(mock.requests().size() == 2,
                        terminal + " stops polling immediately");
                check("{\"actionId\":\"PowerOff\"}".equals(mock.requests().get(0).body),
                        terminal + " case omits empty optional values");
            }
        }
    }

    private static void checkResponseErrors() throws Exception {
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitHttpStatus = 503;
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "non-2xx submit response becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitBodyOverride = "{\"id\":7,\"status\":\"CREATED\"}";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "missing usable submit field becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.pollHttpStatus = 502;
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "non-2xx poll response becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.pollBodyOverride = "{\"id\":\"" + REQUEST_ID + "\",\"status\":false}";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "missing usable poll field becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitBodyOverride = "{\"metadata\":{\"id\":\"" + REQUEST_ID
                    + "\"},\"status\":\"CREATED\"}";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "nested submit id is not accepted as a response field");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitBodyOverride = "{\"id\":\"" + REQUEST_ID
                    + "\",\"status\":\"CREATED\"} trailing";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "malformed submit JSON becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitBodyOverride = "{\"id\":\"\",\"status\":\"CREATED\"}";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "empty submit id becomes IOException");
        }
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.pollBodyOverride = "{\"id\":\"" + REQUEST_ID
                    + "\",\"details\":{\"status\":\"SUCCESSFUL\"}}";
            expectIOException(() -> client(mock).submitDeploymentActionAndWait(
                    DEPLOYMENT_ID, "PowerOff", null, null),
                    "nested poll status is not accepted as a response field");
        }
    }

    private static void checkOtherSuccessfulHttpStatuses() throws Exception {
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            mock.submitHttpStatus = 201;
            mock.pollHttpStatus = 202;
            mock.submitBodyOverride = "{\"id\":\"\\u0035ba75924-4928-45c6-bf93-fd13e6439b16\","
                    + "\"status\":\"CRE\\u0041TED\"}";
            VcfAutomationClient.RequestState state = client(mock)
                    .submitDeploymentActionAndWait(
                            DEPLOYMENT_ID, "PowerOff", null, null);
            check(REQUEST_ID.equals(state.id()), "JSON escapes in response fields are decoded");
            check("SUCCESSFUL".equals(state.status()),
                    "all 2xx responses are treated as successful HTTP responses");
        }
    }

    private static void checkMockSurface() throws Exception {
        try (ContractMock mock = new ContractMock("CREATED", List.of("SUCCESSFUL"))) {
            HttpClient http = loopbackHttpClient();
            HttpRequest wrongSubmitMethod = HttpRequest.newBuilder(
                            mock.baseUri().resolve(mock.submitPath()))
                    .GET().build();
            check(http.send(wrongSubmitMethod, HttpResponse.BodyHandlers.discarding())
                            .statusCode() == 405,
                    "mock rejects wrong submit method");

            HttpRequest wrongPollMethod = HttpRequest.newBuilder(
                            mock.baseUri().resolve(mock.requestPath()))
                    .POST(HttpRequest.BodyPublishers.noBody()).build();
            check(http.send(wrongPollMethod, HttpResponse.BodyHandlers.discarding())
                            .statusCode() == 405,
                    "mock rejects wrong poll method");

            HttpRequest unnamedOperation = HttpRequest.newBuilder(
                            mock.baseUri().resolve("/deployment/api/deployments"))
                    .GET().build();
            check(http.send(unnamedOperation, HttpResponse.BodyHandlers.discarding())
                            .statusCode() == 404,
                    "mock serves no unnamed operation");
            check(mock.requests().size() == 3, "verifier can read mock request log");
        }
    }

    private static VcfAutomationClient client(ContractMock mock) {
        return new VcfAutomationClient(mock.baseUri(), TOKEN, loopbackHttpClient());
    }

    private static HttpClient loopbackHttpClient() {
        return HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    private static void expectIOException(ThrowingCall call, String label) throws Exception {
        try {
            call.run();
            check(false, label);
        } catch (IOException expected) {
            check(true, label);
        }
    }

    private static int occurrences(String value, String needle) {
        int count = 0;
        for (int from = 0; (from = value.indexOf(needle, from)) >= 0; from += needle.length()) {
            count++;
        }
        return count;
    }

    private static void check(boolean condition, String label) {
        checks++;
        if (!condition) {
            throw new AssertionError("FAIL: " + label);
        }
    }

    @FunctionalInterface
    private interface ThrowingCall {
        void run() throws Exception;
    }

    /** Raw request information retained before routing. */
    private static final class LoggedRequest {
        final String method;
        final String requestTarget;
        final String authorization;
        final String accept;
        final String contentType;
        final String body;

        LoggedRequest(String method, String requestTarget, String authorization,
                String accept, String contentType, String body) {
            this.method = method;
            this.requestTarget = requestTarget;
            this.authorization = authorization;
            this.accept = accept;
            this.contentType = contentType;
            this.body = body;
        }
    }

    /** Loopback mock pinned to the two operations in docs/contract.json. */
    private static final class ContractMock implements AutoCloseable {
        private final HttpServer server;
        private final List<LoggedRequest> log =
                Collections.synchronizedList(new ArrayList<>());
        private final AtomicInteger polls = new AtomicInteger();
        private final String postStatus;
        private final List<String> pollStatuses;
        volatile int submitHttpStatus = 200;
        volatile String submitBodyOverride;
        volatile int pollHttpStatus = 200;
        volatile String pollBodyOverride;

        ContractMock(String postStatus, List<String> pollStatuses) throws IOException {
            this.postStatus = postStatus;
            this.pollStatuses = List.copyOf(pollStatuses);
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            this.server.createContext("/", this::handle);
            this.server.start();
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
        }

        String submitPath() {
            return "/deployment/api/deployments/" + DEPLOYMENT_ID + "/requests";
        }

        String requestPath() {
            return "/deployment/api/requests/" + REQUEST_ID;
        }

        List<LoggedRequest> requests() {
            synchronized (log) {
                return List.copyOf(log);
            }
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] requestBytes = exchange.getRequestBody().readAllBytes();
            log.add(new LoggedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().toASCIIString(),
                    exchange.getRequestHeaders().getFirst("Authorization") == null ? ""
                            : exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst("Accept") == null ? ""
                            : exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type") == null ? ""
                            : exchange.getRequestHeaders().getFirst("Content-Type"),
                    new String(requestBytes, StandardCharsets.UTF_8)));

            String path = exchange.getRequestURI().getRawPath();
            if (path.equals(submitPath())) {
                if (!"POST".equals(exchange.getRequestMethod())) {
                    respond(exchange, 405, "method not allowed");
                    return;
                }
                if (submitHttpStatus < 200 || submitHttpStatus >= 300) {
                    respond(exchange, submitHttpStatus, "upstream failure");
                    return;
                }
                String body = submitBodyOverride != null
                        ? submitBodyOverride
                        : "{\"status\":\"" + postStatus + "\",\"ignored\":true," +
                                "\"id\":\"" + REQUEST_ID + "\"}";
                respondJson(exchange, submitHttpStatus, body);
                return;
            }
            if (path.equals(requestPath())) {
                if (!"GET".equals(exchange.getRequestMethod())) {
                    respond(exchange, 405, "method not allowed");
                    return;
                }
                if (pollHttpStatus < 200 || pollHttpStatus >= 300) {
                    respond(exchange, pollHttpStatus, "upstream failure");
                    return;
                }
                int index = polls.getAndIncrement();
                String status = pollStatuses.get(Math.min(index, pollStatuses.size() - 1));
                String body = pollBodyOverride != null
                        ? pollBodyOverride
                        : "{\"extra\":{\"value\":1},\"id\":\"" + REQUEST_ID +
                                "\",\"status\":\"" + status + "\"}";
                respondJson(exchange, pollHttpStatus, body);
                return;
            }
            respond(exchange, 404, "not found");
        }

        private static void respondJson(HttpExchange exchange, int status, String body)
                throws IOException {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            respond(exchange, status, body);
        }

        private static void respond(HttpExchange exchange, int status, String body)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
