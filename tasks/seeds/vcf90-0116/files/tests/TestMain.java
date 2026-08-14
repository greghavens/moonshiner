import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Deterministic loopback harness for the pinned updateDepotSettings contract. */
public final class TestMain {
    private static final String TOKEN = "\"\\\b\f\n\r\t\u0000\u0001\u001f café€\ud83d\ude80";
    private static final String EXPECTED_BODY =
            "{\"vmwareAccount\":{\"downloadToken\":\""
                    + "\\\"" + "\\\\" + "\\b" + "\\f" + "\\n" + "\\r" + "\\t"
                    + "\\u0000" + "\\u0001" + "\\u001f" + " café€\ud83d\ude80"
                    + "\"}}";
    private static final String SECOND_TOKEN = "second/token";
    private static final String SECOND_EXPECTED_BODY =
            "{\"vmwareAccount\":{\"downloadToken\":\"second/token\"}}";
    private static final String SUCCESS_BODY =
            "{\"vmwareAccount\":{\"status\":\"DEPOT_CONNECTION_SUCCESSFUL\","
                    + "\"message\":\"dépôt ready\"}}";

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new AssertionError("expected contract and official-sources paths");
        }

        Contract operation = Contract.load(Path.of(args[0]));
        assertOfficialSources(Path.of(args[1]), operation.operationId());

        try (ContractMockServer mock = new ContractMockServer(
                operation,
                new PlannedResponse(500,
                        "{\"errorCode\":\"AMBIGUOUS_FAILURE\","
                                + "\"message\":\"retry safe PUT\"}"),
                new PlannedResponse(202, SUCCESS_BODY),
                new PlannedResponse(202, "{\"accepted\":\"second\"}"))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUri());
            boolean ambiguousFailureObserved = false;
            try {
                client.updateDepotSettings(TOKEN);
            } catch (IOException expected) {
                ambiguousFailureObserved = true;
            }
            assertEquals(true, ambiguousFailureObserved,
                    "first applied request must surface its HTTP 500 response");

            String response = client.updateDepotSettings(TOKEN);

            assertEquals(SUCCESS_BODY, response, "202 response body");
            assertEquals("{\"accepted\":\"second\"}",
                    client.updateDepotSettings(SECOND_TOKEN),
                    "a later invocation must return its own 202 response body");
            assertEquals(2, mock.effectiveMutationCount(),
                    "retry must not duplicate the desired-state mutation");
            assertEquals(0, mock.unexpectedRequestCount(),
                    "the client called an operation absent from the contract");

            List<RequestRecord> requests = mock.requestLog();
            assertEquals(3, requests.size(),
                    "one initial invocation, one caller retry, and one different update");
            for (int index = 0; index < requests.size(); index++) {
                RequestRecord request = requests.get(index);
                String expectedRequestBody = index < 2 ? EXPECTED_BODY : SECOND_EXPECTED_BODY;
                assertEquals("PUT", request.method(), "HTTP method for request " + index);
                assertEquals("/v1/system/settings/depot", request.path(),
                        "raw request path for request " + index);
                assertEquals(null, request.query(), "query string for request " + index);
                assertEquals("application/json", request.firstHeader("content-type"),
                        "Content-Type for request " + index);
                assertEquals("application/json", request.firstHeader("accept"),
                        "Accept for request " + index);
                assertEquals(Integer.toString(
                                expectedRequestBody.getBytes(StandardCharsets.UTF_8).length),
                        request.firstHeader("content-length"),
                        "Content-Length for request " + index);
                assertEquals(expectedRequestBody, request.body(),
                        "wire body for request " + index);
                assertOmitted(request.body(), "username");
                assertOmitted(request.body(), "password");
                assertOmitted(request.body(), "status");
                assertOmitted(request.body(), "message");
                assertOmitted(request.body(), "dellEmcSupportAccount");
                assertOmitted(request.body(), "offlineAccount");
                assertOmitted(request.body(), "depotConfiguration");
            }
            assertEquals(requests.get(0).body(), requests.get(1).body(),
                    "retry body must be byte-for-byte identical");
            if (requests.get(1).body().equals(requests.get(2).body())) {
                throw new AssertionError("different token arguments must not be hard-coded");
            }
        }

        try (ContractMockServer mock = new ContractMockServer(
                operation, new PlannedResponse(200, "{\"not\":\"accepted\"}"))) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUri());
            boolean non202Rejected = false;
            try {
                client.updateDepotSettings("status-check");
            } catch (IOException expected) {
                non202Rejected = true;
            }
            assertEquals(true, non202Rejected,
                    "a non-202 success-class status must still throw IOException");
            assertEquals(1, mock.requestLog().size(), "status check request count");
        }

        System.out.println("PASS: exact updateDepotSettings wire contract and idempotent retry");
    }

    private static void assertOfficialSources(Path path, String operationId) throws IOException {
        String sources = Files.readString(path, StandardCharsets.UTF_8);
        requireContains(sources, "\"tag\": \"9.0.0.0\"");
        requireContains(sources,
                "\"commitSha\": \"85151f6b1bb58f13b6ac0304bfec53904bea085f\"");
        requireContains(sources,
                "\"specPath\": \"specifications/vcf-installer/vcf-installer-openapi.json\"");
        requireContains(sources, "\"" + operationId + "\"");
    }

    private static void assertOmitted(String body, String field) {
        if (body.contains("\"" + field + "\"")) {
            throw new AssertionError("optional field must be omitted: " + field + " in " + body);
        }
    }

    private static void requireContains(String text, String expected) {
        if (!text.contains(expected)) {
            throw new AssertionError("missing pinned source value: " + expected);
        }
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private record Contract(String method, String path, String operationId) {
        private static Contract load(Path path) throws IOException {
            String json = Files.readString(path, StandardCharsets.UTF_8);
            String operationId = uniqueGroup(json,
                    Pattern.compile("\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""),
                    "operationId");
            String operationPath = uniqueGroup(json,
                    Pattern.compile("\\\"(/v1/[^\\\"]+)\\\"\\s*:\\s*\\{\\s*\\\"put\\\""),
                    "PUT path");
            if (!"updateDepotSettings".equals(operationId)) {
                throw new AssertionError("unexpected operationId: " + operationId);
            }
            return new Contract("PUT", operationPath, operationId);
        }

        private static String uniqueGroup(String text, Pattern pattern, String label) {
            Matcher matcher = pattern.matcher(text);
            if (!matcher.find()) {
                throw new AssertionError("contract has no " + label);
            }
            String value = matcher.group(1);
            if (matcher.find()) {
                throw new AssertionError("contract names more than one " + label);
            }
            return value;
        }
    }

    private record RequestRecord(
            String method,
            String path,
            String query,
            Map<String, List<String>> headers,
            byte[] bodyBytes) {

        private RequestRecord {
            bodyBytes = bodyBytes.clone();
        }

        private String body() {
            return new String(bodyBytes, StandardCharsets.UTF_8);
        }

        private String firstHeader(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().toLowerCase(Locale.ROOT).equals(name)) {
                    return entry.getValue().isEmpty() ? null : entry.getValue().get(0);
                }
            }
            return null;
        }
    }

    private record PlannedResponse(int status, String body) {}

    /**
     * Serves only the operation named by contract.json. The first valid PUT is
     * applied but answered with the contract's 500 response; the caller's retry
     * receives 202. Applying an identical desired state is not counted as a new
     * mutation.
     */
    private static final class ContractMockServer implements AutoCloseable {
        private final Contract operation;
        private final HttpServer server;
        private final List<RequestRecord> requestLog = new CopyOnWriteArrayList<>();
        private final AtomicReference<String> appliedState = new AtomicReference<>();
        private final AtomicInteger effectiveMutations = new AtomicInteger();
        private final AtomicInteger unexpectedRequests = new AtomicInteger();
        private final AtomicInteger validAttempts = new AtomicInteger();
        private final List<PlannedResponse> responses;

        private ContractMockServer(Contract operation, PlannedResponse... responses)
                throws IOException {
            this.operation = operation;
            if (responses.length == 0) {
                throw new IllegalArgumentException("at least one response is required");
            }
            this.responses = List.of(responses);
            InetSocketAddress address = new InetSocketAddress(
                    InetAddress.getByName("127.0.0.1"), 0);
            server = HttpServer.create(address, 0);
            server.createContext("/", this::handle);
            server.start();
        }

        private URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/ignored/base/");
        }

        private List<RequestRecord> requestLog() {
            return Collections.unmodifiableList(new ArrayList<>(requestLog));
        }

        private int effectiveMutationCount() {
            return effectiveMutations.get();
        }

        private int unexpectedRequestCount() {
            return unexpectedRequests.get();
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] requestBody = exchange.getRequestBody().readAllBytes();
            String method = exchange.getRequestMethod();
            String path = exchange.getRequestURI().getRawPath();
            String query = exchange.getRequestURI().getRawQuery();

            if (!operation.method().equals(method)
                    || !operation.path().equals(path)
                    || query != null) {
                unexpectedRequests.incrementAndGet();
                send(exchange, 404, "");
                return;
            }

            requestLog.add(new RequestRecord(
                    method,
                    path,
                    query,
                    immutableHeaders(exchange.getRequestHeaders()),
                    requestBody));

            String desiredState = new String(requestBody, StandardCharsets.UTF_8);
            String previous = appliedState.getAndSet(desiredState);
            if (!desiredState.equals(previous)) {
                effectiveMutations.incrementAndGet();
            }

            int attempt = validAttempts.getAndIncrement();
            PlannedResponse response = responses.get(Math.min(attempt, responses.size() - 1));
            send(exchange, response.status(), response.body());
        }

        private static Map<String, List<String>> immutableHeaders(Headers headers) {
            java.util.LinkedHashMap<String, List<String>> copy = new java.util.LinkedHashMap<>();
            headers.forEach((key, values) -> copy.put(key, List.copyOf(values)));
            return Collections.unmodifiableMap(copy);
        }

        private static void send(HttpExchange exchange, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
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
