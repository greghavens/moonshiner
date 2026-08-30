/*
 * Protected acceptance harness for the VCF Automation 9.1 deployment-action
 * client. The loopback mock exposes only the three operations pinned in
 * docs/contract.json. No live VMware endpoint or real credential is used.
 *
 * Run: java TestMain.java
 */
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public class TestMain {
    static final String TOKEN = "fixture-vcf-automation-token";
    static final String DEPLOYMENTS = "/deployment/api/deployments";
    static final String REQUESTS = "/deployment/api/requests/";
    static int checks;

    static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        checks++;
    }

    static void checkEq(Object got, Object expected, String message) {
        if (got == null ? expected != null : !got.equals(expected)) {
            throw new AssertionError(message + ": got " + got + ", expected " + expected);
        }
        checks++;
    }

    record LoggedRequest(
            String method,
            String uri,
            String authorization,
            String accept,
            String contentType,
            String body,
            List<String> identifiersReturned) {}

    /** Contract-pinned loopback mock with a verifier-readable request log. */
    static final class VcfMock implements AutoCloseable {
        final HttpServer server;
        final String baseUrl;
        final String deploymentName;
        String deploymentId;
        String requestId;
        final List<String> pollStatuses;
        final boolean lookupFound;
        final List<LoggedRequest> requestLog = new ArrayList<>();
        final CountDownLatch firstPoll = new CountDownLatch(1);
        int pollIndex;
        int lookupHttpStatus = 200;
        int submitHttpStatus = 200;
        int pollHttpStatus = 200;
        String lookupResponseOverride;
        String submitResponseOverride;
        String pollResponseOverride;

        VcfMock(String deploymentName, String deploymentId, String requestId,
                List<String> pollStatuses, boolean lookupFound) throws IOException {
            this.deploymentName = deploymentName;
            this.deploymentId = deploymentId;
            this.requestId = requestId;
            this.pollStatuses = List.copyOf(pollStatuses);
            this.lookupFound = lookupFound;
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            this.server.createContext("/", this::handle);
            this.server.start();
            this.baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        synchronized void useIdentifiers(String nextDeploymentId, String nextRequestId) {
            this.deploymentId = nextDeploymentId;
            this.requestId = nextRequestId;
            this.pollIndex = 0;
        }

        synchronized List<LoggedRequest> log() {
            return List.copyOf(requestLog);
        }

        private synchronized void handle(HttpExchange exchange) throws IOException {
            String method = exchange.getRequestMethod();
            String path = exchange.getRequestURI().getPath();
            String uri = exchange.getRequestURI().toString();
            String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            int status;
            String response;
            List<String> returned = List.of();
            boolean pollRequest = false;

            if (method.equals("GET") && path.equals(DEPLOYMENTS)) {
                String wantedQuery = "name=" + encode(deploymentName);
                boolean matches = wantedQuery.equals(exchange.getRequestURI().getRawQuery());
                if (lookupFound && matches) {
                    response = "{\"content\":[{\"id\":\"dep-decoy-not-an-exact-match\","
                            + "\"name\":\"Different deployment\"},{\"id\":" + quote(deploymentId)
                            + ",\"name\":" + quote(deploymentName) + "}],\"totalElements\":2}";
                    returned = List.of("dep-decoy-not-an-exact-match", deploymentId);
                } else {
                    response = "{\"content\":[],\"totalElements\":0}";
                }
                status = lookupHttpStatus;
                if (lookupResponseOverride != null) {
                    response = lookupResponseOverride;
                    returned = List.of();
                }
            } else if (method.equals("POST")
                    && path.startsWith(DEPLOYMENTS + "/")
                    && path.endsWith("/requests")) {
                String usedDeploymentId = path.substring(
                        (DEPLOYMENTS + "/").length(), path.length() - "/requests".length());
                response = "{\"id\":" + quote(requestId)
                        + ",\"deploymentId\":" + quote(usedDeploymentId)
                        + ",\"actionId\":\"fixture-action\",\"status\":\"CREATED\"}";
                returned = List.of(requestId);
                status = submitHttpStatus;
                if (submitResponseOverride != null) {
                    response = submitResponseOverride;
                    returned = List.of();
                }
            } else if (method.equals("GET") && path.startsWith(REQUESTS)
                    && path.substring(REQUESTS.length()).indexOf('/') < 0) {
                pollRequest = true;
                String usedRequestId = path.substring(REQUESTS.length());
                String nextStatus = pollStatuses.get(Math.min(pollIndex, pollStatuses.size() - 1));
                pollIndex++;
                response = "{\"id\":" + quote(usedRequestId)
                        + ",\"deploymentId\":" + quote(deploymentId)
                        + ",\"actionId\":\"fixture-action\",\"status\":" + quote(nextStatus) + "}";
                returned = List.of(usedRequestId);
                status = pollHttpStatus;
                if (pollResponseOverride != null) {
                    response = pollResponseOverride;
                    returned = List.of();
                }
            } else {
                status = 404;
                response = "{\"message\":\"operation is not in docs/contract.json\"}";
            }

            requestLog.add(new LoggedRequest(
                    method,
                    uri,
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    requestBody,
                    returned));
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(bytes);
            }
            if (pollRequest) firstPoll.countDown();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    static void assertCommonHeaders(List<LoggedRequest> log) {
        for (LoggedRequest request : log) {
            checkEq(request.authorization(), "Bearer " + TOKEN,
                    "Bearer authorization on " + request.method() + " " + request.uri());
            checkEq(request.accept(), "application/json",
                    "Accept header on " + request.method() + " " + request.uri());
            if (request.method().equals("POST")) {
                check(request.contentType() != null
                                && request.contentType().toLowerCase().startsWith("application/json"),
                        "POST must have application/json Content-Type");
            }
        }
    }

    static void assertIdentifierProvenance(List<LoggedRequest> log) {
        check(log.size() >= 3, "lifecycle must contain lookup, submit, and at least one poll");
        LoggedRequest lookup = log.get(0);
        LoggedRequest submit = log.get(1);
        checkEq(lookup.method(), "GET", "lookup method");
        checkEq(submit.method(), "POST", "submit method");

        String submitPrefix = DEPLOYMENTS + "/";
        String submitSuffix = "/requests";
        String usedDeploymentId = submit.uri().substring(
                submitPrefix.length(), submit.uri().length() - submitSuffix.length());
        check(lookup.identifiersReturned().contains(usedDeploymentId),
                "verifier: deployment identifier used by submit was not returned by its own lookup");

        for (int i = 2; i < log.size(); i++) {
            LoggedRequest poll = log.get(i);
            checkEq(poll.method(), "GET", "poll method");
            check(poll.uri().startsWith(REQUESTS), "poll must use Get Request path");
            String usedRequestId = poll.uri().substring(REQUESTS.length());
            check(submit.identifiersReturned().contains(usedRequestId),
                    "verifier: request identifier used by poll was not returned by its own submission");
        }
    }

    static void testSuccessfulLifecycle() throws Exception {
        String name = "Payments Blue/Green";
        try (VcfMock mock = new VcfMock(
                name, "dep-from-lookup-a91", "req-from-submit-b37",
                List.of("CREATED", "PENDING", "INITIALIZATION", "CHECKING_APPROVAL",
                        "APPROVAL_PENDING", "USER_INTERACTION_PENDING", "INPROGRESS",
                        "COMPLETION", "SUCCESSFUL"), true)) {
            VcfAutomationClient client = new VcfAutomationClient(mock.baseUrl, TOKEN, Duration.ZERO);
            Map<String, Object> requestedInputs = new LinkedHashMap<>();
            requestedInputs.put("graceful", true);
            requestedInputs.put("ticket", "CHG-91");
            requestedInputs.put("options", Map.of("attempts", 2, "zones", List.of("a", "b")));
            requestedInputs.put("optional", null);
            VcfAutomationClient.OperationResult result = client.runDeploymentAction(
                    name,
                    "PowerOff",
                    requestedInputs,
                    "quarterly maintenance");

            checkEq(result.deploymentId(), "dep-from-lookup-a91", "result deployment id");
            checkEq(result.requestId(), "req-from-submit-b37", "result request id");
            checkEq(result.status(), "SUCCESSFUL", "final terminal status");

            List<LoggedRequest> log = mock.log();
            checkEq(log.size(), 11, "lookup + submit + every non-terminal poll + terminal poll");
            checkEq(log.get(0).uri(), DEPLOYMENTS + "?name=Payments%20Blue%2FGreen",
                    "exact-name lookup query");
            check(log.get(1).uri().startsWith(DEPLOYMENTS + "/")
                            && log.get(1).uri().endsWith("/requests"),
                    "submission uses the deployment-action operation path");
            check(log.get(2).uri().startsWith(REQUESTS),
                    "poll uses the Get Request operation path");

            Map<String, Object> body = Json.obj(Json.parse(log.get(1).body()));
            checkEq(body.get("actionId"), "PowerOff", "actionId body field");
            checkEq(body.get("reason"), "quarterly maintenance", "reason body field");
            Map<String, Object> inputs = Json.obj(body.get("inputs"));
            checkEq(inputs.get("graceful"), Boolean.TRUE, "boolean action input");
            checkEq(inputs.get("ticket"), "CHG-91", "string action input");
            Map<String, Object> options = Json.obj(inputs.get("options"));
            checkEq(options.get("attempts"), 2.0, "numeric nested action input");
            checkEq(Json.arr(options.get("zones")), List.of("a", "b"),
                    "array nested action input");
            check(inputs.containsKey("optional") && inputs.get("optional") == null,
                    "null action input");

            assertCommonHeaders(log);
            assertIdentifierProvenance(log);
        }
    }

    static void testFailureIsTerminalAndIdsArePerInvocation() throws Exception {
        String name = "Analytics Canary #2";
        try (VcfMock mock = new VcfMock(
                name, "dep-first-lookup-a13", "req-first-submit-b24",
                List.of("PENDING", "FAILED"), true)) {
            VcfAutomationClient client = new VcfAutomationClient(mock.baseUrl + "/", TOKEN, Duration.ZERO);
            VcfAutomationClient.OperationResult first = client.runDeploymentAction(
                    name, "Resize", Map.of("cpu", 4), "first invocation");
            checkEq(first.deploymentId(), "dep-first-lookup-a13",
                    "first invocation deployment id");
            checkEq(first.requestId(), "req-first-submit-b24",
                    "first invocation request id");
            checkEq(first.status(), "FAILED", "first invocation terminal status");

            mock.useIdentifiers("dep-second-lookup-c52", "req-second-submit-d84");
            VcfAutomationClient.OperationResult second = client.runDeploymentAction(
                    name, "Shutdown", Map.of(), "incident containment");

            checkEq(second.deploymentId(), "dep-second-lookup-c52",
                    "second invocation deployment id must come from second lookup");
            checkEq(second.requestId(), "req-second-submit-d84",
                    "second invocation request id must come from second submission");
            checkEq(second.status(), "FAILED", "FAILED is returned as a terminal state");
            List<LoggedRequest> log = mock.log();
            checkEq(log.size(), 8, "each invocation performs lookup, submit, and two polls");
            checkEq(log.get(0).uri(), DEPLOYMENTS + "?name=Analytics%20Canary%20%232",
                    "first lookup encodes reserved characters");
            checkEq(log.get(4).uri(), DEPLOYMENTS + "?name=Analytics%20Canary%20%232",
                    "second invocation performs its own exact-name lookup");
            Map<String, Object> secondBody = Json.obj(Json.parse(log.get(5).body()));
            checkEq(secondBody.get("actionId"), "Shutdown",
                    "actionId is taken from this invocation");
            checkEq(secondBody.get("reason"), "incident containment",
                    "reason is taken from this invocation");
            checkEq(Json.obj(secondBody.get("inputs")), Map.of(),
                    "inputs are taken from this invocation");
            assertCommonHeaders(log);
            assertIdentifierProvenance(log.subList(0, 4));
            assertIdentifierProvenance(log.subList(4, 8));
        }
    }

    static void testEveryDocumentedTerminalStateReturns() throws Exception {
        for (String terminal : List.of("APPROVAL_REJECTED", "ABORTED")) {
            try (VcfMock mock = new VcfMock(
                    "Terminal " + terminal, "dep-" + terminal.toLowerCase(),
                    "req-" + terminal.toLowerCase(), List.of(terminal), true)) {
                VcfAutomationClient client = new VcfAutomationClient(
                        mock.baseUrl, TOKEN, Duration.ZERO);
                VcfAutomationClient.OperationResult result = client.runDeploymentAction(
                        "Terminal " + terminal, "Shutdown", Map.of(), "terminal-state test");
                checkEq(result.status(), terminal, terminal + " is returned as terminal");
                checkEq(mock.log().size(), 3, terminal + " stops after its first poll");
                assertIdentifierProvenance(mock.log());
            }
        }
    }

    static void testMissingLookupDoesNotSubmit() throws Exception {
        try (VcfMock mock = new VcfMock(
                "Missing deployment", "not-returned", "must-not-be-created",
                List.of("SUCCESSFUL"), false)) {
            VcfAutomationClient client = new VcfAutomationClient(mock.baseUrl, TOKEN, Duration.ZERO);
            RuntimeException failure = null;
            try {
                client.runDeploymentAction(
                        "Missing deployment", "PowerOn", Map.of(), "recovery");
            } catch (RuntimeException error) {
                failure = error;
            }
            check(failure != null, "empty deployment lookup must fail");
            checkEq(mock.log().size(), 1, "empty lookup must not submit an action");
            checkEq(mock.log().get(0).method(), "GET", "the sole request is the lookup");
        }
    }

    static RuntimeException captureFailure(VcfAutomationClient client) {
        try {
            client.runDeploymentAction("Error target", "PowerOn", Map.of(), "error test");
        } catch (RuntimeException error) {
            return error;
        }
        throw new AssertionError("operation was expected to fail");
    }

    static void checkFailureMessage(RuntimeException failure, String... fragments) {
        String message = String.valueOf(failure.getMessage()).toLowerCase(Locale.ROOT);
        for (String fragment : fragments) {
            check(message.contains(fragment.toLowerCase(Locale.ROOT)),
                    "failure message must contain '" + fragment + "' but was: " + message);
        }
    }

    static void checkFailureMessageHasAny(RuntimeException failure, String... alternatives) {
        String message = String.valueOf(failure.getMessage()).toLowerCase(Locale.ROOT);
        for (String alternative : alternatives) {
            if (message.contains(alternative.toLowerCase(Locale.ROOT))) {
                checks++;
                return;
            }
        }
        throw new AssertionError(
                "failure message did not clearly describe the response failure: " + message);
    }

    static void testHttpAndMalformedResponseFailures() throws Exception {
        try (VcfMock mock = new VcfMock(
                "Error target", "dep-http-lookup", "req-unused",
                List.of("SUCCESSFUL"), true)) {
            mock.lookupHttpStatus = 201;
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessage(failure, "201");
            checkEq(mock.log().size(), 1, "non-200 lookup stops the lifecycle");
        }

        try (VcfMock mock = new VcfMock(
                "Error target", "dep-http-submit", "req-unused",
                List.of("SUCCESSFUL"), true)) {
            mock.submitHttpStatus = 202;
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessage(failure, "202");
            checkEq(mock.log().size(), 2, "non-200 submission is not polled");
        }

        try (VcfMock mock = new VcfMock(
                "Error target", "dep-http-poll", "req-http-poll",
                List.of("SUCCESSFUL"), true)) {
            mock.pollHttpStatus = 203;
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessage(failure, "203");
            checkEq(mock.log().size(), 3, "non-200 poll stops the lifecycle");
        }

        try (VcfMock mock = new VcfMock(
                "Error target", "dep-malformed", "req-malformed",
                List.of("SUCCESSFUL"), true)) {
            mock.submitResponseOverride = "{not-json";
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessageHasAny(failure,
                    "malformed", "invalid", "json", "parse", "response");
            checkEq(mock.log().size(), 2, "malformed submission response is not polled");
        }

        try (VcfMock mock = new VcfMock(
                "Error target", "dep-missing-id", "req-missing-id",
                List.of("SUCCESSFUL"), true)) {
            mock.submitResponseOverride = "{\"status\":\"CREATED\"}";
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessage(failure, "id");
            checkFailureMessageHasAny(failure,
                    "missing", "absent", "invalid", "required", "response", "contain");
            checkEq(mock.log().size(), 2, "response without request id is not polled");
        }

        try (VcfMock mock = new VcfMock(
                "Error target", "dep-unknown-status", "req-unknown-status",
                List.of("NOT_A_CONTRACT_STATUS", "SUCCESSFUL"), true)) {
            RuntimeException failure = captureFailure(new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ZERO));
            checkFailureMessage(failure, "NOT_A_CONTRACT_STATUS");
            checkEq(mock.log().size(), 3,
                    "an undocumented status fails instead of being treated as intermediate");
        }
    }

    static void testInterruptedPollingRestoresFlag() throws Exception {
        try (VcfMock mock = new VcfMock(
                "Interrupt target", "dep-interrupt", "req-interrupt",
                List.of("INPROGRESS"), true)) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl, TOKEN, Duration.ofMinutes(1));
            AtomicReference<RuntimeException> failure = new AtomicReference<>();
            AtomicBoolean interruptFlag = new AtomicBoolean();
            Thread worker = new Thread(() -> {
                try {
                    client.runDeploymentAction(
                            "Interrupt target", "Shutdown", Map.of(), "interrupt test");
                } catch (RuntimeException error) {
                    failure.set(error);
                    interruptFlag.set(Thread.currentThread().isInterrupted());
                }
            }, "vcf-poll-interrupt-test");
            worker.start();
            try {
                check(mock.firstPoll.await(5, TimeUnit.SECONDS),
                        "client must immediately issue the first Get Request");
                long stateDeadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
                while (worker.isAlive() && !isBetweenPolls(worker)
                        && System.nanoTime() < stateDeadline) {
                    Thread.onSpinWait();
                }
                check(isBetweenPolls(worker),
                        "client waits pollInterval only after a non-terminal response");
                worker.interrupt();
                worker.join(TimeUnit.SECONDS.toMillis(5));
                check(!worker.isAlive(), "interrupted polling thread must terminate");
                check(failure.get() != null, "interrupted polling must fail the operation");
                checkFailureMessage(failure.get(), "interrupt");
                check(interruptFlag.get(), "interrupted polling must restore the interrupt flag");
                checkEq(mock.log().size(), 3, "interrupt stops before another poll");
            } finally {
                if (worker.isAlive()) {
                    worker.interrupt();
                    worker.join(TimeUnit.SECONDS.toMillis(5));
                }
            }
        }
    }

    static boolean isBetweenPolls(Thread thread) {
        boolean inOperation = false;
        for (StackTraceElement frame : thread.getStackTrace()) {
            if (frame.getClassName().equals(VcfAutomationClient.class.getName())
                    && frame.getMethodName().equals("runDeploymentAction")) {
                inOperation = true;
            }
            if (frame.getClassName().startsWith("java.net.http.")
                    || frame.getClassName().startsWith("jdk.internal.net.http.")) {
                return false;
            }
        }
        return inOperation;
    }

    static void testSingleProductionSourceArtifact() throws Exception {
        Set<String> javaSources = new LinkedHashSet<>();
        try (java.util.stream.Stream<Path> paths = Files.walk(Path.of("."))) {
            paths.filter(Files::isRegularFile)
                    .map(Path.of(".")::relativize)
                    .map(Path::toString)
                    .filter(path -> path.endsWith(".java"))
                    .forEach(javaSources::add);
        }
        checkEq(javaSources, Set.of("TestMain.java", "VcfAutomationClient.java"),
                "the deliverable has one production source plus the protected harness");
    }

    static void testContractSourcesAndMockSurface() throws Exception {
        Map<String, Object> contract = Json.obj(Json.parse(
                Files.readString(Path.of("docs/contract.json"), StandardCharsets.UTF_8)));
        Map<String, Object> source = Json.obj(contract.get("source"));
        String statement = String.valueOf(source.get("statement"));
        check(statement.contains("reference documentation"),
                "contract must identify reference documentation as its source");
        check(statement.contains("not a published specification"),
                "contract must state that it is not a published specification");
        check(statement.contains("Apache-2.0 vmware/vcf-api-specs"),
                "contract must identify the specification repository and its license");

        Map<String, Object> operations = Json.obj(contract.get("operations"));
        checkEq(new LinkedHashSet<>(operations.keySet()), Set.of(
                        "get_deployments", "submit_deployment_action_request", "get_request"),
                "contract names exactly the mock's three operations");
        checkEq(Json.obj(operations.get("get_deployments")).get("path"),
                DEPLOYMENTS, "lookup contract path");
        checkEq(Json.obj(operations.get("get_request")).get("path"),
                "/deployment/api/requests/{requestId}", "poll contract path");

        Map<String, Object> sourceDocument = Json.obj(Json.parse(
                Files.readString(Path.of("docs/official_sources.json"), StandardCharsets.UTF_8)));
        List<Object> sources = Json.arr(sourceDocument.get("sources"));
        checkEq(sources.size(), 3, "one official page per named operation");
        Set<String> recordedOperations = new LinkedHashSet<>();
        for (Object value : sources) {
            Map<String, Object> item = Json.obj(value);
            check(String.valueOf(item.get("url")).startsWith(
                            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/"),
                    "source must be an official Broadcom xAPIs page");
            checkEq(item.get("fetched_on"), "2026-08-16", "source fetch date");
            recordedOperations.add(String.valueOf(item.get("operation")));
        }
        check(recordedOperations.stream().anyMatch(value -> value.startsWith("GET /deployment/api/deployments ")),
                "Get Deployments source operation recorded");
        check(recordedOperations.stream().anyMatch(value -> value.startsWith("POST /deployment/api/deployments/")),
                "Submit Deployment Action Request source operation recorded");
        check(recordedOperations.stream().anyMatch(value -> value.startsWith("GET /deployment/api/requests/")),
                "Get Request source operation recorded");

        try (VcfMock mock = new VcfMock(
                "surface", "dep-surface", "req-surface", List.of("SUCCESSFUL"), true)) {
            HttpRequest request = HttpRequest.newBuilder(
                    URI.create(mock.baseUrl + "/deployment/api/not-in-contract"))
                    .GET().build();
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    request, HttpResponse.BodyHandlers.ofString());
            checkEq(response.statusCode(), 404,
                    "loopback mock refuses operations absent from the pinned contract");
            checkEq(mock.log().size(), 1, "unknown operation is still visible in request log");
        }
    }

    static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\' -> out.append("\\\\");
                case '"' -> out.append("\\\"");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> out.append(c);
            }
        }
        return out.append('"').toString();
    }

    static final class Json {
        final String text;
        int offset;

        Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.offset != text.length()) {
                throw new IllegalArgumentException("trailing JSON at " + parser.offset);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> obj(Object value) {
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        static List<Object> arr(Object value) {
            return (List<Object>) value;
        }

        Object value() {
            whitespace();
            char c = text.charAt(offset);
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') return string();
            if (text.startsWith("true", offset)) { offset += 4; return Boolean.TRUE; }
            if (text.startsWith("false", offset)) { offset += 5; return Boolean.FALSE; }
            if (text.startsWith("null", offset)) { offset += 4; return null; }
            return number();
        }

        Map<String, Object> object() {
            Map<String, Object> result = new LinkedHashMap<>();
            expect('{');
            whitespace();
            if (peek('}')) { offset++; return result; }
            while (true) {
                whitespace();
                String key = string();
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (peek(',')) { offset++; continue; }
                expect('}');
                return result;
            }
        }

        List<Object> array() {
            List<Object> result = new ArrayList<>();
            expect('[');
            whitespace();
            if (peek(']')) { offset++; return result; }
            while (true) {
                result.add(value());
                whitespace();
                if (peek(',')) { offset++; continue; }
                expect(']');
                return result;
            }
        }

        String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < text.length()) {
                char c = text.charAt(offset++);
                if (c == '"') return result.toString();
                if (c != '\\') {
                    result.append(c);
                    continue;
                }
                char escaped = text.charAt(offset++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        result.append((char) Integer.parseInt(
                                text.substring(offset, offset + 4), 16));
                        offset += 4;
                    }
                    default -> throw new IllegalArgumentException("bad JSON escape");
                }
            }
            throw new IllegalArgumentException("unterminated JSON string");
        }

        Number number() {
            int start = offset;
            while (offset < text.length()
                    && "+-0123456789.eE".indexOf(text.charAt(offset)) >= 0) {
                offset++;
            }
            return Double.valueOf(text.substring(start, offset));
        }

        void whitespace() {
            while (offset < text.length() && Character.isWhitespace(text.charAt(offset))) {
                offset++;
            }
        }

        boolean peek(char expected) {
            return offset < text.length() && text.charAt(offset) == expected;
        }

        void expect(char expected) {
            if (!peek(expected)) {
                throw new IllegalArgumentException("expected " + expected + " at " + offset);
            }
            offset++;
        }
    }

    public static void main(String[] args) throws Exception {
        testSuccessfulLifecycle();
        System.out.println("ok   async success polling + identifier provenance");
        testFailureIsTerminalAndIdsArePerInvocation();
        System.out.println("ok   terminal failure + per-invocation identifiers");
        testEveryDocumentedTerminalStateReturns();
        System.out.println("ok   all documented terminal states");
        testMissingLookupDoesNotSubmit();
        System.out.println("ok   empty lookup stops before submission");
        testHttpAndMalformedResponseFailures();
        System.out.println("ok   strict HTTP 200 + malformed responses");
        testInterruptedPollingRestoresFlag();
        System.out.println("ok   interrupt-safe polling delay");
        testSingleProductionSourceArtifact();
        System.out.println("ok   single production source artifact");
        testContractSourcesAndMockSurface();
        System.out.println("ok   reference provenance + contract-pinned mock surface");
        System.out.println("all tests passed (" + checks + " checks)");
    }
}
