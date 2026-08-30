import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;
import javax.tools.ToolProvider;

public final class TestMain {
    private static final String TOKEN = "fixture-token";
    private static final String PREFIX = "/tenant/";
    private static final String DEPLOYMENT_ID = "d /雪?\"\\";
    private static final String ACTION_ID = "Deployment.\"Power\\Off\n雪";
    private static final String REASON = "maintenance \"snow 雪\"\nwindow\\";
    private static final String REQUEST_ID = "r /雪?\"\\";

    public static void main(String[] args) throws Exception {
        testPublicApiShape();
        testCompletePaginationStableOutputAndCompletionIsNotTerminal();
        testOtherTerminalStatuses();
        testEmptyInventoryWithZeroTotalPages();
        testEveryOperationRejectsHttpAndMalformedResponses();
        testCrossOriginRedirectIsNotFollowed();
        testInterruptionRestoresFlagAndFails();
        System.out.println("all checks passed");
    }

    private static void testPublicApiShape() throws Exception {
        List<String> sourceFiles;
        try (var paths = Files.list(Path.of("."))) {
            sourceFiles = paths.filter(Files::isRegularFile)
                    .map(path -> path.getFileName().toString())
                    .filter(name -> name.endsWith(".java"))
                    .sorted()
                    .toList();
        }
        check(sourceFiles.equals(List.of("AutomationClient.java", "TestMain.java")),
                "solution must add only the single AutomationClient.java source file: " + sourceFiles);
        Path classes = Files.createTempDirectory("automation-client-release17-");
        int compileStatus = ToolProvider.getSystemJavaCompiler().run(null, null, null,
                "--release", "17", "-d", classes.toString(), "AutomationClient.java");
        check(compileStatus == 0, "AutomationClient.java must compile against JDK 17 APIs");
        var run = AutomationClient.class.getMethod("run", URI.class, String.class, String.class,
                String.class, String.class, Duration.class, PrintStream.class);
        check(run.getReturnType() == void.class, "run must return void");
        var main = AutomationClient.class.getMethod("main", String[].class);
        check(main.getReturnType() == void.class, "main must return void");
    }

    private static void testCompletePaginationStableOutputAndCompletionIsNotTerminal() throws Exception {
        try (ContractMock mock = new ContractMock(List.of("INPROGRESS", "COMPLETION", "SUCCESSFUL"))) {
            String output = invoke(mock);
            String expected = String.join("\n",
                    "{\"id\":\"d-002\",\"name\":\"Alpha\"}",
                    "{\"id\":\"d-005\\\\line\\n\",\"name\":\"Alpha\"}",
                    "{\"id\":\"d-003\",\"name\":\"beta 雪\"}",
                    "{\"id\":\"d-004\",\"name\":\"omega \\\"blue\\\"\\t\\\\\"}",
                    "{\"id\":\"d-001\",\"name\":\"zeta\"}",
                    "{\"requestId\":\"r /雪?\\\"\\\\\",\"status\":\"SUCCESSFUL\"}") + "\n";
            check(expected.equals(output), "unexpected JSON Lines output\nexpected:\n" + expected + "actual:\n" + output);

            List<LoggedRequest> log = mock.requests();
            check(log.size() == 7, "expected 3 page GETs, one POST, and 3 poll GETs; got " + summarize(log));
            for (int page = 0; page < 3; page++) {
                LoggedRequest request = log.get(page);
                check("GET".equals(request.method()), "page request was not GET");
                check((PREFIX + "deployment/api/deployments").equals(request.path()),
                        "wrong deployment collection path");
                Map<String, List<String>> query = query(request.rawQuery());
                check(query.keySet().equals(new java.util.LinkedHashSet<>(List.of("page", "size", "sort"))),
                        "unexpected deployment query fields: " + query);
                check(List.of(Integer.toString(page)).equals(query.get("page")), "wrong page sequence: " + query);
                check(List.of("2").equals(query.get("size")), "client did not request size=2: " + query);
                check(List.of("name,asc").equals(query.get("sort")), "client did not request sort=name,asc: " + query);
            }

            LoggedRequest post = log.get(3);
            check("POST".equals(post.method()), "action request was not POST");
            check((PREFIX + "deployment/api/deployments/" + DEPLOYMENT_ID + "/requests").equals(post.path()),
                    "wrong action path: " + post.path());
            check((PREFIX + "deployment/api/deployments/d%20%2F%E9%9B%AA%3F%22%5C/requests")
                    .equals(post.rawPath()), "deployment ID was not encoded as one path segment: " + post.rawPath());
            check(post.contentType().toLowerCase().startsWith("application/json"), "POST content type was not application/json");
            check(hasJsonStringField(post.body(), "actionId", ACTION_ID),
                    "POST body omitted actionId: " + post.body());
            check(post.body().matches("(?s).*\\\"inputs\\\"\\s*:\\s*\\{\\s*}.*"),
                    "POST body omitted empty inputs: " + post.body());
            check(hasJsonStringField(post.body(), "reason", REASON),
                    "POST body omitted reason: " + post.body());

            for (LoggedRequest request : log) {
                check(("Bearer " + TOKEN).equals(request.authorization()), "missing or wrong bearer authorization: " + summarize(log));
            }
            for (int index = 4; index < 7; index++) {
                LoggedRequest poll = log.get(index);
                check("GET".equals(poll.method()), "request poll was not GET");
                check((PREFIX + "deployment/api/requests/" + REQUEST_ID).equals(poll.path()),
                        "wrong request poll path: " + poll.path());
                check((PREFIX + "deployment/api/requests/r%20%2F%E9%9B%AA%3F%22%5C").equals(poll.rawPath()),
                        "request ID was not encoded as one path segment: " + poll.rawPath());
            }
            check(mock.pollsServed() == 3,
                    "client reported a terminal result before the operation reached SUCCESSFUL; polls=" + mock.pollsServed());
        }
    }

    private static void testOtherTerminalStatuses() throws Exception {
        for (String terminal : List.of("APPROVAL_REJECTED", "ABORTED", "FAILED")) {
            try (ContractMock mock = new ContractMock(List.of("PENDING", terminal))) {
                String output = invoke(mock);
                String finalLine = "{\"requestId\":" + jsonString(REQUEST_ID)
                        + ",\"status\":\"" + terminal + "\"}\n";
                check(output.endsWith(finalLine), "client did not report terminal status " + terminal + ": " + output);
                check(mock.pollsServed() == 2, "client did not stop exactly when status reached " + terminal);
            }
        }
    }

    private static void testEmptyInventoryWithZeroTotalPages() throws Exception {
        try (ContractMock mock = new ContractMock(List.of("SUCCESSFUL"), true)) {
            String output = invoke(mock);
            String expected = "{\"requestId\":" + jsonString(REQUEST_ID)
                    + ",\"status\":\"SUCCESSFUL\"}\n";
            check(expected.equals(output), "empty inventory must still submit and poll: " + output);
            check(mock.requests().size() == 3,
                    "empty inventory should use one page, one POST, and one poll: " + summarize(mock.requests()));
        }
    }

    private static void testEveryOperationRejectsHttpAndMalformedResponses() throws Exception {
        for (FailureStage stage : FailureStage.values()) {
            for (boolean malformed : List.of(false, true)) {
                try (FailureMock mock = new FailureMock(stage, malformed)) {
                    RunFailure failure = runForFailure(mock.baseUri(), Duration.ZERO);
                    check(failure.thrown() instanceof IllegalStateException,
                            stage + " " + (malformed ? "malformed response" : "HTTP error")
                                    + " did not fail with IllegalStateException: " + failure.thrown());
                    check(failure.output().isEmpty(),
                            "failure scenario emitted a result: " + failure.output());
                    check(mock.targetServed(), "client never reached failure stage " + stage);
                }
            }
        }
    }

    private static void testCrossOriginRedirectIsNotFollowed() throws Exception {
        try (RedirectMock mock = new RedirectMock()) {
            RunFailure failure = runForFailure(mock.baseUri(), Duration.ZERO);
            check(failure.thrown() instanceof IllegalStateException,
                    "redirect response must be treated as non-2xx: " + failure.thrown());
            check(mock.sourceHits() == 1, "client did not request the configured base URI");
            check(mock.targetHits() == 0, "client followed a redirect to another host");
        }
    }

    private static void testInterruptionRestoresFlagAndFails() throws Exception {
        try (ContractMock mock = new ContractMock(List.of("SUCCESSFUL"))) {
            AtomicReference<Throwable> thrown = new AtomicReference<>();
            AtomicBoolean interruptRestored = new AtomicBoolean();
            Thread worker = new Thread(() -> {
                try {
                    AutomationClient.run(mock.baseUri(), TOKEN, DEPLOYMENT_ID, ACTION_ID, REASON,
                            Duration.ofDays(1), new PrintStream(new ByteArrayOutputStream()));
                } catch (Throwable failure) {
                    thrown.set(failure);
                    interruptRestored.set(Thread.currentThread().isInterrupted());
                }
            }, "automation-client-interruption-test");
            worker.setDaemon(true);
            worker.start();
            check(mock.awaitSubmit(), "client did not reach polling before interruption");
            worker.interrupt();
            worker.join(10_000);
            check(!worker.isAlive(), "interrupted client did not stop");
            check(thrown.get() instanceof IllegalStateException,
                    "interrupted run did not fail with IllegalStateException: " + thrown.get());
            check(interruptRestored.get(), "interrupted run did not restore the thread interrupt flag");
        }
    }

    private static RunFailure runForFailure(URI baseUri, Duration pollDelay) {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        Throwable thrown = null;
        try (PrintStream out = new PrintStream(bytes, true, StandardCharsets.UTF_8)) {
            try {
                AutomationClient.run(baseUri, TOKEN, DEPLOYMENT_ID, ACTION_ID, REASON, pollDelay, out);
            } catch (Throwable failure) {
                thrown = failure;
            }
        }
        return new RunFailure(thrown, bytes.toString(StandardCharsets.UTF_8));
    }

    private static String invoke(ContractMock mock) throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (PrintStream out = new PrintStream(bytes, true, StandardCharsets.UTF_8)) {
            AutomationClient.run(
                    mock.baseUri(),
                    TOKEN,
                    DEPLOYMENT_ID,
                    ACTION_ID,
                    REASON,
                    Duration.ZERO,
                    out);
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    private static Map<String, List<String>> query(String rawQuery) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return result;
        }
        for (String pair : rawQuery.split("&")) {
            String[] parts = pair.split("=", 2);
            String key = URLDecoder.decode(parts[0], StandardCharsets.UTF_8);
            String value = parts.length == 2 ? URLDecoder.decode(parts[1], StandardCharsets.UTF_8) : "";
            result.computeIfAbsent(key, ignored -> new ArrayList<>()).add(value);
        }
        return result;
    }

    private static boolean hasJsonStringField(String body, String field, String value) {
        String expression = Pattern.quote(jsonString(field)) + "\\s*:\\s*" + Pattern.quote(jsonString(value));
        return Pattern.compile(expression).matcher(body).find();
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2).append('"');
        for (int offset = 0; offset < value.length();) {
            int codePoint = value.codePointAt(offset);
            offset += Character.charCount(codePoint);
            switch (codePoint) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (codePoint < 0x20) {
                        result.append(String.format("\\u%04x", codePoint));
                    } else {
                        result.appendCodePoint(codePoint);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static String summarize(List<LoggedRequest> requests) {
        List<String> parts = new ArrayList<>();
        for (LoggedRequest request : requests) {
            parts.add(request.method() + " " + request.path() + (request.rawQuery() == null ? "" : "?" + request.rawQuery()));
        }
        return parts.toString();
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    record RunFailure(Throwable thrown, String output) {}

    record LoggedRequest(String method, String path, String rawPath, String rawQuery, String authorization,
                         String contentType, String body) {}

    enum FailureStage {
        COLLECTION,
        SUBMIT,
        POLL
    }

    /** Returns either a non-2xx or malformed required response at a selected operation. */
    static final class FailureMock implements AutoCloseable {
        private final HttpServer server;
        private final ExecutorService executor = Executors.newCachedThreadPool();
        private final FailureStage stage;
        private final boolean malformed;
        private final AtomicBoolean targetServed = new AtomicBoolean();

        FailureMock(FailureStage stage, boolean malformed) throws IOException {
            this.stage = stage;
            this.malformed = malformed;
            server = newLoopbackServer(this::handle, executor);
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + PREFIX);
        }

        boolean targetServed() {
            return targetServed.get();
        }

        private void handle(HttpExchange exchange) throws IOException {
            exchange.getRequestBody().readAllBytes();
            String path = exchange.getRequestURI().getPath();
            if (!("Bearer " + TOKEN).equals(exchange.getRequestHeaders().getFirst("Authorization"))) {
                send(exchange, 401, "{\"error\":\"authorization\"}");
                return;
            }
            if ("GET".equals(exchange.getRequestMethod())
                    && (PREFIX + "deployment/api/deployments").equals(path)) {
                if (stage == FailureStage.COLLECTION) {
                    failTarget(exchange, "{\"content\":[],\"number\":0,\"totalPages\":0}");
                } else {
                    send(exchange, 200,
                            "{\"content\":[],\"number\":0,\"totalPages\":0,\"last\":true}");
                }
                return;
            }
            if ("POST".equals(exchange.getRequestMethod())
                    && (PREFIX + "deployment/api/deployments/" + DEPLOYMENT_ID + "/requests").equals(path)) {
                if (stage == FailureStage.SUBMIT) {
                    failTarget(exchange, "{\"status\":\"CREATED\"}");
                } else {
                    send(exchange, 200, "{\"id\":\"r-error\",\"status\":\"CREATED\"}");
                }
                return;
            }
            if ("GET".equals(exchange.getRequestMethod())
                    && (PREFIX + "deployment/api/requests/r-error").equals(path)) {
                if (stage == FailureStage.POLL) {
                    failTarget(exchange, "{\"id\":\"r-error\"}");
                } else {
                    send(exchange, 500, "{\"error\":\"unexpected poll\"}");
                }
                return;
            }
            send(exchange, 404, "{\"error\":\"unexpected operation\"}");
        }

        private void failTarget(HttpExchange exchange, String malformedBody) throws IOException {
            targetServed.set(true);
            send(exchange, malformed ? 200 : 503,
                    malformed ? malformedBody : "{\"error\":\"service unavailable\"}");
        }

        @Override
        public void close() {
            server.stop(0);
            executor.shutdownNow();
        }
    }

    /** A redirect target is loopback, but has a different URI host and must never be contacted. */
    static final class RedirectMock implements AutoCloseable {
        private final HttpServer source;
        private final HttpServer target;
        private final ExecutorService sourceExecutor = Executors.newCachedThreadPool();
        private final ExecutorService targetExecutor = Executors.newCachedThreadPool();
        private final AtomicInteger sourceHits = new AtomicInteger();
        private final AtomicInteger targetHits = new AtomicInteger();

        RedirectMock() throws IOException {
            target = newLoopbackServer(exchange -> {
                targetHits.incrementAndGet();
                send(exchange, 200,
                        "{\"content\":[],\"number\":0,\"totalPages\":0,\"last\":true}");
            }, targetExecutor);
            source = newLoopbackServer(exchange -> {
                sourceHits.incrementAndGet();
                exchange.getRequestBody().readAllBytes();
                exchange.getResponseHeaders().set("Location",
                        "http://localhost:" + target.getAddress().getPort() + "/redirected");
                send(exchange, 302, "{\"redirect\":true}");
            }, sourceExecutor);
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + source.getAddress().getPort() + PREFIX);
        }

        int sourceHits() {
            return sourceHits.get();
        }

        int targetHits() {
            return targetHits.get();
        }

        @Override
        public void close() {
            source.stop(0);
            target.stop(0);
            sourceExecutor.shutdownNow();
            targetExecutor.shutdownNow();
        }
    }

    @FunctionalInterface
    interface ExchangeHandler {
        void handle(HttpExchange exchange) throws IOException;
    }

    private static HttpServer newLoopbackServer(
            ExchangeHandler handler, ExecutorService executor) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(
                InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), 0);
        server.createContext("/", handler::handle);
        server.setExecutor(executor);
        server.start();
        return server;
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    /** Loopback-only mock for the three operations named by docs/contract.json. */
    static final class ContractMock implements AutoCloseable {
        private final HttpServer server;
        private final ExecutorService executor;
        private final List<String> pollStatuses;
        private final boolean emptyInventory;
        private final List<LoggedRequest> requests = new CopyOnWriteArrayList<>();
        private final CountDownLatch submitServed = new CountDownLatch(1);
        private int pollsServed;

        ContractMock(List<String> pollStatuses) throws IOException {
            this(pollStatuses, false);
        }

        ContractMock(List<String> pollStatuses, boolean emptyInventory) throws IOException {
            this.pollStatuses = List.copyOf(pollStatuses);
            this.emptyInventory = emptyInventory;
            server = HttpServer.create(new InetSocketAddress(
                    InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), 0);
            server.createContext("/", this::handle);
            executor = Executors.newCachedThreadPool();
            server.setExecutor(executor);
            server.start();
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + PREFIX);
        }

        List<LoggedRequest> requests() {
            return List.copyOf(requests);
        }

        synchronized int pollsServed() {
            return pollsServed;
        }

        boolean awaitSubmit() throws InterruptedException {
            return submitServed.await(10, TimeUnit.SECONDS);
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] requestBody = exchange.getRequestBody().readAllBytes();
            LoggedRequest logged = new LoggedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getPath(),
                    exchange.getRequestURI().getRawPath(),
                    exchange.getRequestURI().getRawQuery(),
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    valueOrEmpty(exchange.getRequestHeaders().getFirst("Content-Type")),
                    new String(requestBody, StandardCharsets.UTF_8));
            requests.add(logged);

            if (!("Bearer " + TOKEN).equals(logged.authorization())) {
                send(exchange, 401, "{\"error\":\"unauthorized\"}");
                return;
            }
            if ("GET".equals(logged.method())
                    && (PREFIX + "deployment/api/deployments").equals(logged.path())) {
                serveDeployments(exchange, logged.rawQuery());
                return;
            }
            if ("POST".equals(logged.method())
                    && (PREFIX + "deployment/api/deployments/" + DEPLOYMENT_ID + "/requests")
                            .equals(logged.path())) {
                serveSubmit(exchange, logged);
                return;
            }
            if ("GET".equals(logged.method())
                    && (PREFIX + "deployment/api/requests/" + REQUEST_ID).equals(logged.path())) {
                servePoll(exchange);
                return;
            }
            send(exchange, 404, "{\"error\":\"operation not in contract\"}");
        }

        private void serveDeployments(HttpExchange exchange, String rawQuery) throws IOException {
            Map<String, List<String>> params = query(rawQuery);
            if (!List.of("2").equals(params.get("size")) || !List.of("name,asc").equals(params.get("sort"))) {
                send(exchange, 400, "{\"error\":\"wrong collection parameters\"}");
                return;
            }
            int page;
            try {
                page = Integer.parseInt(params.getOrDefault("page", List.of("-1")).get(0));
            } catch (RuntimeException badPage) {
                send(exchange, 400, "{\"error\":\"bad page\"}");
                return;
            }
            if (emptyInventory) {
                if (page == 0) {
                    send(exchange, 200,
                            "{\"content\":[],\"number\":0,\"numberOfElements\":0,\"size\":2,"
                                    + "\"totalElements\":0,\"totalPages\":0,\"first\":true,"
                                    + "\"last\":true,\"empty\":true}");
                } else {
                    send(exchange, 400, "{\"error\":\"page past end\"}");
                }
                return;
            }
            String body = switch (page) {
                case 0 -> page(0, false,
                        deployment("d-005\\line\n", "Alpha"),
                        deployment("d-002", "Alpha"));
                case 1 -> page(1, false,
                        deployment("d-003", "beta 雪"),
                        deployment("d-004", "omega \"blue\"\t\\"));
                case 2 -> page(2, true,
                        deployment("d-001", "zeta"));
                default -> null;
            };
            if (body == null) {
                send(exchange, 400, "{\"error\":\"page past end\"}");
            } else {
                send(exchange, 200, body);
            }
        }

        private static String page(int number, boolean last, String... deployments) {
            return "{\"content\":[" + String.join(",", deployments) + "],"
                    + "\"number\":" + number + ",\"numberOfElements\":" + deployments.length + ","
                    + "\"size\":2,\"totalElements\":5,\"totalPages\":3,"
                    + "\"first\":" + (number == 0) + ",\"last\":" + last + ",\"empty\":"
                    + (deployments.length == 0) + "}";
        }

        private static String deployment(String id, String name) {
            return "{\"id\":" + jsonString(id) + ",\"name\":" + jsonString(name) + "}";
        }

        private void serveSubmit(HttpExchange exchange, LoggedRequest request) throws IOException {
            if (!request.contentType().toLowerCase().startsWith("application/json")) {
                send(exchange, 415, "{\"error\":\"content type\"}");
                return;
            }
            if (!hasJsonStringField(request.body(), "actionId", ACTION_ID)
                    || !request.body().matches("(?s).*\\\"inputs\\\"\\s*:\\s*\\{\\s*}.*")
                    || !hasJsonStringField(request.body(), "reason", REASON)) {
                send(exchange, 400, "{\"error\":\"request body\"}");
                return;
            }
            send(exchange, 200, "{\"id\":" + jsonString(REQUEST_ID)
                    + ",\"deploymentId\":" + jsonString(DEPLOYMENT_ID) + ",\"status\":\"CREATED\"}");
            submitServed.countDown();
        }

        private synchronized void servePoll(HttpExchange exchange) throws IOException {
            if (pollsServed >= pollStatuses.size()) {
                send(exchange, 500, "{\"error\":\"polled after terminal state\"}");
                return;
            }
            String status = pollStatuses.get(pollsServed++);
            String completed = isTerminal(status) ? ",\"completedAt\":\"2026-08-16T12:00:00Z\"" : "";
            send(exchange, 200, "{\"id\":" + jsonString(REQUEST_ID)
                    + ",\"status\":\"" + status + "\"" + completed + "}");
        }

        private static boolean isTerminal(String status) {
            return List.of("APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL", "FAILED").contains(status);
        }

        private static String valueOrEmpty(String value) {
            return value == null ? "" : value;
        }

        private static void send(HttpExchange exchange, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        }

        @Override
        public void close() {
            server.stop(0);
            executor.shutdownNow();
        }
    }
}
