import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    private static final String CONTRACT_SHA256 = "5d6919b8fdf5696b8ee5b7d8323f08be54222150bce2655dc90402f5a7c656ba";
    private static final Set<String> CONTRACT_OPERATIONS = Set.of(
            "GET /blueprint/api/blueprints",
            "POST /blueprint/api/blueprint-validation",
            "POST /blueprint/api/blueprint-requests");

    public static void main(String[] args) throws Exception {
        verifyContractPin();
        testValidPrecheckCreatesRequest(201);
        testValidPrecheckCreatesRequest(202);
        testFailedPrecheckDoesNotCreate();
        testLookupHttpErrorDoesNotContinue();
        testLookupRequiresExactMatch();
        testMalformedLookupDoesNotContinue();
        testPrecheckHttpErrorDoesNotCreate();
        testMalformedPrecheckDoesNotCreate();
        testCreateHttpErrorIsReported();
        testMalformedCreateResponseIsReported();
        testIdentifierVerifierNegativeControl();
        System.out.println("ALL TESTS PASSED");
    }

    private static void testValidPrecheckCreatesRequest(int createStatus) throws Exception {
        try (ContractMock server = new ContractMock(true, 200, createStatus)) {
            AutomationClient client = new AutomationClient(server.baseUri(), "fixture-token");
            Map<String, Object> inputs = new LinkedHashMap<>();
            inputs.put("cpu", 2);
            inputs.put("backup", true);
            inputs.put("labels", List.of("qa", "nightly"));

            AutomationClient.SubmissionOutcome outcome = client.submitBlueprintIfValid(
                    "Linux & Small", "project-17", "nightly-smoke", inputs);

            check(outcome.submitted(), "valid blueprint should be submitted");
            check(server.requestId.equals(outcome.requestId()), "create response id should be returned");
            check(outcome.validationMessages().isEmpty(), "valid result should have no validation messages");

            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 3, "successful flow must issue exactly lookup, validate, create");
            check("GET /blueprint/api/blueprints".equals(log.get(0).operation()), "lookup must be first");
            check("POST /blueprint/api/blueprint-validation".equals(log.get(1).operation()), "validation must be second");
            check("POST /blueprint/api/blueprint-requests".equals(log.get(2).operation()), "create must be last");
            check("Linux & Small".equals(queryParameters(log.get(0).rawQuery()).get("name")),
                    "lookup name query must be correctly encoded");
            assertHeaders(log);
            assertPostedIdentifiersCameFromExactLookup(log, server.blueprintId);
            check("project-17".equals(jsonString(log.get(1).requestBody(), "projectId")),
                    "validation must preserve projectId");
            check("project-17".equals(jsonString(log.get(2).requestBody(), "projectId")),
                    "create must preserve projectId");
            check("nightly-smoke".equals(jsonString(log.get(2).requestBody(), "deploymentName")),
                    "create must preserve deploymentName");
            check(log.get(1).requestBody().contains("\"labels\":[\"qa\",\"nightly\"]"),
                    "validation must preserve JSON-compatible nested inputs");
            check(log.get(2).requestBody().contains("\"backup\":true"),
                    "create must preserve JSON-compatible inputs");
            check(log.get(1).requestBody().contains("\"cpu\":2")
                            && log.get(2).requestBody().contains("\"labels\":[\"qa\",\"nightly\"]"),
                    "both POST operations must preserve all supplied inputs");
        }
    }

    private static void testFailedPrecheckDoesNotCreate() throws Exception {
        try (ContractMock server = new ContractMock(false, 200, 201)) {
            AutomationClient client = new AutomationClient(server.baseUri(), "fixture-token");
            AutomationClient.SubmissionOutcome outcome = client.submitBlueprintIfValid(
                    "Linux & Small", "project-17", "blocked-deploy", Map.of("cpu", 64));

            check(!outcome.submitted(), "invalid blueprint must not be submitted");
            check(outcome.requestId() == null, "invalid blueprint must not have a request id");
            check(outcome.validationMessages().equals(List.of("network profile is unavailable")),
                    "server validation messages must be returned");
            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 2, "failed precheck must stop after validation");
            check(log.stream().noneMatch(e -> e.operation().equals("POST /blueprint/api/blueprint-requests")),
                    "failed precheck must not mutate");
            assertPostedIdentifiersCameFromExactLookup(log, server.blueprintId);
        }
    }

    private static void testLookupHttpErrorDoesNotContinue() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 201)
                .withLookupResponse(503, "{\"error\":\"lookup unavailable\"}")) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "lookup-error", Map.of()));
            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 1, "lookup HTTP error must stop after lookup");
            check(log.stream().noneMatch(e -> e.operation().startsWith("POST ")),
                    "lookup HTTP error must not perform a POST");
        }
    }

    private static void testLookupRequiresExactMatch() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 201)
                .withLookupResponse(200,
                        "{\"content\":[{\"id\":\"99999999-9999-9999-9999-999999999999\","
                                + "\"name\":\"Linux & Small-ish\"}]}")) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "no-exact-match", Map.of()));
            check(server.requestLog().size() == 1,
                    "lookup without an exact name match must stop before validation");
        }
    }

    private static void testMalformedLookupDoesNotContinue() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 201)
                .withLookupResponse(200, "{\"content\":{}}")) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "malformed-lookup", Map.of()));
            check(server.requestLog().size() == 1,
                    "malformed lookup response must stop before validation");
        }
    }

    private static void testPrecheckHttpErrorDoesNotCreate() throws Exception {
        try (ContractMock server = new ContractMock(true, 400, 201)) {
            AutomationClient client = new AutomationClient(server.baseUri(), "fixture-token");
            try {
                client.submitBlueprintIfValid(
                        "Linux & Small", "project-17", "error-deploy", Map.of());
                throw new AssertionError("non-success validation must throw IOException");
            } catch (IOException expected) {
                check(expected.getMessage() != null && !expected.getMessage().isBlank(),
                        "IOException should explain the response failure");
            }
            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 2, "validation HTTP error must stop before create");
            check(log.stream().noneMatch(e -> e.operation().equals("POST /blueprint/api/blueprint-requests")),
                    "validation HTTP error must not mutate");
            assertPostedIdentifiersCameFromExactLookup(log, server.blueprintId);
        }
    }

    private static void testMalformedPrecheckDoesNotCreate() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 201)
                .withValidationResponse(200, "{\"valid\":\"yes\"}")) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "malformed-validation", Map.of()));
            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 2, "malformed validation must stop after validation");
            check(log.stream().noneMatch(e -> e.operation().equals("POST /blueprint/api/blueprint-requests")),
                    "malformed validation must not mutate");
        }
    }

    private static void testCreateHttpErrorIsReported() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 500)) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "create-error", Map.of()));
            check(server.requestLog().size() == 3,
                    "create HTTP error should occur after lookup and successful validation");
        }
    }

    private static void testMalformedCreateResponseIsReported() throws Exception {
        try (ContractMock server = new ContractMock(true, 200, 201)
                .withCreateResponse(201, "{\"status\":\"CREATED\"}")) {
            expectIOException(() -> new AutomationClient(server.baseUri(), "fixture-token")
                    .submitBlueprintIfValid(
                            "Linux & Small", "project-17", "malformed-create", Map.of()));
            check(server.requestLog().size() == 3,
                    "malformed create response should be reported after the create call");
        }
    }

    @FunctionalInterface
    private interface ThrowingCall {
        void run() throws Exception;
    }

    private static void expectIOException(ThrowingCall call) throws Exception {
        try {
            call.run();
            throw new AssertionError("expected IOException");
        } catch (IOException expected) {
            check(expected.getMessage() != null && !expected.getMessage().isBlank(),
                    "IOException should explain the response failure");
        }
    }

    private static void testIdentifierVerifierNegativeControl() {
        List<LoggedExchange> fabricated = List.of(
                new LoggedExchange(
                        "GET /blueprint/api/blueprints", "name=x", "", Map.of(), 200,
                        "{\"content\":[{\"id\":\"lookup-id\",\"name\":\"x\"}]}"),
                new LoggedExchange(
                        "POST /blueprint/api/blueprint-validation", null,
                        "{\"blueprintId\":\"not-from-lookup\"}", Map.of(), 200,
                        "{\"valid\":true}"));
        try {
            assertPostedIdentifiersCameFromOwnLookup(fabricated);
            throw new AssertionError("identifier provenance verifier accepted an unrelated id");
        } catch (AssertionError expected) {
            check(expected.getMessage().contains("own lookup"),
                    "negative-control failure must be the identifier provenance check");
        }
    }

    private static void assertHeaders(List<LoggedExchange> log) {
        for (LoggedExchange exchange : log) {
            check(List.of("Bearer fixture-token").equals(exchange.headers().get("Authorization")),
                    "every operation must send bearer authentication");
            check(headerContains(exchange.headers(), "Accept", "application/json"),
                    "every operation must accept JSON");
            if (exchange.operation().startsWith("POST ")) {
                check(headerContains(exchange.headers(), "Content-type", "application/json"),
                        "POST operations must send JSON");
            }
        }
    }

    private static boolean headerContains(Map<String, List<String>> headers, String name, String value) {
        return headers.entrySet().stream()
                .filter(e -> e.getKey().equalsIgnoreCase(name))
                .flatMap(e -> e.getValue().stream())
                .anyMatch(v -> v.toLowerCase(Locale.ROOT).contains(value));
    }

    private static void assertPostedIdentifiersCameFromOwnLookup(List<LoggedExchange> log) {
        Set<String> returned = log.stream()
                .filter(e -> e.operation().equals("GET /blueprint/api/blueprints"))
                .flatMap(e -> jsonObjectIds(e.responseBody()).stream())
                .collect(java.util.stream.Collectors.toSet());
        check(!returned.isEmpty(), "own lookup response did not return an id");
        for (LoggedExchange exchange : log) {
            if (exchange.operation().equals("POST /blueprint/api/blueprint-validation")
                    || exchange.operation().equals("POST /blueprint/api/blueprint-requests")) {
                String used = jsonString(exchange.requestBody(), "blueprintId");
                check(returned.contains(used),
                        "blueprintId " + used + " was not returned by this client's own lookup");
            }
        }
    }

    private static void assertPostedIdentifiersCameFromExactLookup(
            List<LoggedExchange> log, String expectedId) {
        assertPostedIdentifiersCameFromOwnLookup(log);
        for (LoggedExchange exchange : log) {
            if (exchange.operation().equals("POST /blueprint/api/blueprint-validation")
                    || exchange.operation().equals("POST /blueprint/api/blueprint-requests")) {
                check(expectedId.equals(jsonString(exchange.requestBody(), "blueprintId")),
                        "POST must use the id of the exact-name lookup match");
            }
        }
    }

    private static Set<String> jsonObjectIds(String json) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("\\\"id\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"")
                .matcher(json);
        java.util.HashSet<String> result = new java.util.HashSet<>();
        while (matcher.find()) {
            result.add(matcher.group(1));
        }
        return result;
    }

    private static String jsonString(String json, String key) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("\\\"" + java.util.regex.Pattern.quote(key)
                        + "\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"])*)\\\"")
                .matcher(json);
        check(matcher.find(), "missing JSON string field " + key);
        return matcher.group(1).replace("\\\"", "\"").replace("\\\\", "\\");
    }

    private static Map<String, String> queryParameters(String rawQuery) {
        Map<String, String> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return result;
        }
        for (String pair : rawQuery.split("&")) {
            String[] parts = pair.split("=", 2);
            String key = URLDecoder.decode(parts[0], StandardCharsets.UTF_8);
            String value = URLDecoder.decode(parts.length == 2 ? parts[1] : "", StandardCharsets.UTF_8);
            result.put(key, value);
        }
        return result;
    }

    private static void verifyContractPin() throws Exception {
        Path contract = Path.of("docs", "contract.json");
        byte[] bytes = Files.readAllBytes(contract);
        String actual = java.util.HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(bytes));
        check(CONTRACT_SHA256.equals(actual), "docs/contract.json does not match the mock's pinned contract");

        String text = Files.readString(contract);
        for (String operation : CONTRACT_OPERATIONS) {
            String[] pieces = operation.split(" ", 2);
            check(text.contains("\"method\": \"" + pieces[0] + "\"")
                            && text.contains("\"path\": \"" + pieces[1] + "\""),
                    "pinned contract is missing " + operation);
        }
        check(text.contains("reference documentation rather than from a published API specification"),
                "contract provenance statement is missing");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private record LoggedExchange(
            String operation,
            String rawQuery,
            String requestBody,
            Map<String, List<String>> headers,
            int responseStatus,
            String responseBody) {
    }

    private static final class ContractMock implements AutoCloseable {
        private static final AtomicInteger SERVER_SEQUENCE = new AtomicInteger();

        private final HttpServer server;
        private final List<LoggedExchange> requestLog = new CopyOnWriteArrayList<>();
        private final boolean valid;
        private volatile int lookupStatus = 200;
        private volatile int validationStatus;
        private volatile int createStatus;
        private volatile String lookupResponse;
        private volatile String validationResponse;
        private volatile String createResponse;
        private final int serverNumber = SERVER_SEQUENCE.incrementAndGet();
        private final String blueprintId = fixtureId(serverNumber * 3);
        private final String requestId = fixtureId(serverNumber * 3 + 1);
        private final String decoyId = fixtureId(serverNumber * 3 + 2);

        ContractMock(boolean valid, int validationStatus, int createStatus) throws IOException {
            this.valid = valid;
            this.validationStatus = validationStatus;
            this.createStatus = createStatus;
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
        }

        ContractMock withLookupResponse(int status, String response) {
            lookupStatus = status;
            lookupResponse = response;
            return this;
        }

        ContractMock withValidationResponse(int status, String response) {
            validationStatus = status;
            validationResponse = response;
            return this;
        }

        ContractMock withCreateResponse(int status, String response) {
            createStatus = status;
            createResponse = response;
            return this;
        }

        URI baseUri() {
            return URI.create("http://" + server.getAddress().getHostString() + ":" + server.getAddress().getPort() + "/");
        }

        List<LoggedExchange> requestLog() {
            return List.copyOf(requestLog);
        }

        private void handle(HttpExchange exchange) throws IOException {
            String operation = exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath();
            String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            int status;
            String response;

            if (!CONTRACT_OPERATIONS.contains(operation)) {
                status = 404;
                response = "{\"error\":\"operation is not in the pinned contract\"}";
            } else if (operation.equals("GET /blueprint/api/blueprints")) {
                status = lookupStatus;
                response = lookupResponse != null
                        ? lookupResponse
                        : "{\"content\":[{\"id\":\"" + decoyId
                            + "\",\"name\":\"Linux & Small-ish\"},{\"id\":\"" + blueprintId
                            + "\",\"name\":\"Linux & Small\"}],\"totalElements\":2}";
            } else if (operation.equals("POST /blueprint/api/blueprint-validation")) {
                status = validationStatus;
                response = validationResponse != null
                        ? validationResponse
                        : validationStatus == 200
                        ? (valid
                            ? "{\"valid\":true,\"validationMessages\":[]}"
                            : "{\"valid\":false,\"validationMessages\":[{\"message\":\"network profile is unavailable\",\"type\":\"ERROR\"}]}")
                        : "{\"error\":\"validation request rejected\"}";
            } else {
                status = createStatus;
                response = createResponse != null
                        ? createResponse
                        : "{\"id\":\"" + requestId + "\",\"blueprintId\":\""
                            + blueprintId + "\",\"status\":\"CREATED\"}";
            }

            requestLog.add(new LoggedExchange(
                    operation,
                    exchange.getRequestURI().getRawQuery(),
                    requestBody,
                    copyHeaders(exchange.getRequestHeaders()),
                    status,
                    response));

            Headers responseHeaders = exchange.getResponseHeaders();
            responseHeaders.set("Content-Type", "application/json");
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();

        }

        private static Map<String, List<String>> copyHeaders(Headers source) {
            Map<String, List<String>> copy = new LinkedHashMap<>();
            source.forEach((key, value) -> copy.put(key, List.copyOf(value)));
            return Map.copyOf(copy);
        }

        private static String fixtureId(int value) {
            return "00000000-0000-0000-0000-" + String.format("%012d", value);
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
