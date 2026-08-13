import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.stream.Collectors;

/** Protected integration harness for the single-file client. */
public final class TestMain {
    private record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            List<String> accept,
            byte[] body) {
    }

    private static final class ContractPinnedMock {
        private final HttpServer server;
        private final List<LoggedRequest> requests = new CopyOnWriteArrayList<>();

        ContractPinnedMock() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            // getTasks is the sole operation named by docs/contract.json.
            server.createContext("/v1/tasks", this::handleGetTasks);
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        }

        void start() { server.start(); }
        void stop() { server.stop(0); }
        List<LoggedRequest> requests() { return List.copyOf(requests); }
        void clearRequests() { requests.clear(); }

        private void handleGetTasks(HttpExchange exchange) throws IOException {
            byte[] requestBody = exchange.getRequestBody().readAllBytes();
            requests.add(new LoggedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getRawPath(),
                    exchange.getRequestURI().getRawQuery(),
                    List.copyOf(exchange.getRequestHeaders().getOrDefault("Accept", List.of())),
                    requestBody));

            if (!exchange.getRequestURI().getRawPath().equals("/v1/tasks")) {
                send(exchange, 404, "{\"error\":\"not found\"}");
                return;
            }
            if (!exchange.getRequestMethod().equals("GET")) {
                send(exchange, 405, "{\"error\":\"method not allowed\"}");
                return;
            }

            Map<String, String> query = decodeQuery(exchange.getRequestURI().getRawQuery());
            int page;
            try {
                page = Integer.parseInt(query.getOrDefault("pageNumber", "-1"));
            } catch (NumberFormatException error) {
                send(exchange, 400, "{\"error\":\"bad page\"}");
                return;
            }

            switch (query.getOrDefault("taskStatus", "")) {
                case "HTTP_ERROR" -> {
                    send(exchange, 503, "{\"error\":\"temporarily unavailable\"}");
                    return;
                }
                case "BAD_METADATA" -> {
                    send(exchange, 200, """
                            {"elements":[],"pageMetadata":{
                              "pageNumber":0,"pageSize":2,"totalElements":0
                            }}
                            """);
                    return;
                }
                case "BAD_TASK" -> {
                    send(exchange, 200, """
                            {
                              "elements":[{
                                "id":"broken-task","name":"Missing status",
                                "creationTimestamp":"2026-05-01T12:00:00Z"
                              }],
                              "pageMetadata":{
                                "pageNumber":0,"pageSize":1,
                                "totalElements":1,"totalPages":1
                              }
                            }
                            """);
                    return;
                }
                default -> { /* normal paginated fixture */ }
            }

            String response = switch (page) {
                case 0 -> """
                        {
                          "elements": [
                            {"id":"task-c","name":"Configure network","type":"SDDC_CONFIGURE","status":"IN_PROGRESS","creationTimestamp":"2026-05-01T09:00:00Z"},
                            {"id":"task-b","name":"Validate inputs","type":"SDDC_VALIDATE","status":"SUCCESSFUL","creationTimestamp":"2026-05-01T08:00:00Z","completionTimestamp":"2026-05-01T08:05:00Z"}
                          ],
                          "pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":5,"totalPages":3}
                        }
                        """;
                case 1 -> """
                        {
                          "elements": [
                            {"id":"task-e","name":"Deploy workload domain","type":"SDDC_DEPLOY","status":"PENDING","creationTimestamp":"2026-05-01T10:00:00Z"},
                            {"id":"task-a","name":"Deploy \\"Management\\" domain","type":"SDDC_DEPLOY","status":"IN_PROGRESS","creationTimestamp":"2026-05-01T09:00:00Z"}
                          ],
                          "pageMetadata":{"pageNumber":1,"pageSize":2,"totalElements":5,"totalPages":3}
                        }
                        """;
                case 2 -> """
                        {
                          "elements": [
                            {"id":"task-d","name":"Finalize inventory","type":"SDDC_FINALIZE","status":"QUEUED","creationTimestamp":"2026-05-01T11:00:00Z"}
                          ],
                          "pageMetadata":{"pageNumber":2,"pageSize":1,"totalElements":5,"totalPages":3}
                        }
                        """;
                default -> null;
            };
            if (response == null) {
                send(exchange, 400, "{\"error\":\"page out of range\"}");
            } else {
                send(exchange, 200, response);
            }
        }

        private static Map<String, String> decodeQuery(String rawQuery) {
            if (rawQuery == null || rawQuery.isEmpty()) return Map.of();
            return List.of(rawQuery.split("&", -1)).stream()
                    .map(part -> part.split("=", 2))
                    .collect(Collectors.toMap(
                            pair -> URLDecoder.decode(pair[0], StandardCharsets.UTF_8),
                            pair -> pair.length == 2
                                    ? URLDecoder.decode(pair[1], StandardCharsets.UTF_8)
                                    : "",
                            (left, right) -> { throw new AssertionError("duplicate query key"); }));
        }

        private static void send(HttpExchange exchange, int status, String body)
                throws IOException {
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, payload.length);
            exchange.getResponseBody().write(payload);
            exchange.close();
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new AssertionError("workspace root argument required");
        assertPinnedContract(Path.of(args[0]));

        ContractPinnedMock mock = new ContractPinnedMock();
        mock.start();
        try {
            HttpClient http = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(2))
                    .build();
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUri(), http);
            VcfInstallerClient.TaskQuery query = new VcfInstallerClient.TaskQuery()
                    .limit(17)
                    .taskStatus("IN_PROGRESS")
                    .taskType("SDDC DEPLOY")
                    .resourceId("domain/id")
                    .resourceType("WORKLOAD+DOMAIN")
                    .completedAfter(1777636800000L)
                    .pageSize(2)
                    .orderDirection("ASC")
                    .orderBy("creationTimestamp")
                    .taskName("Management domain / phase+one")
                    .doLiveRefresh(false);

            List<VcfInstallerClient.Task> tasks = client.getAllTasks(query);
            assertEquals(List.of("task-b", "task-a", "task-c", "task-e", "task-d"),
                    tasks.stream().map(VcfInstallerClient.Task::id).toList(),
                    "all pages must be globally sorted by creationTimestamp then id");
            assertEquals(5, tasks.size(), "all elements must be retained");
            assertEquals("Deploy \"Management\" domain", tasks.get(1).name(),
                    "JSON string escapes must be decoded");
            assertEquals("2026-05-01T08:05:00Z", tasks.get(0).completionTimestamp(),
                    "optional response fields must be preserved when present");
            assertEquals(null, tasks.get(1).completionTimestamp(),
                    "optional response fields may be absent");

            assertWireLog(mock.requests());

            mock.clearRequests();
            List<VcfInstallerClient.Task> unfiltered = client.getAllTasks(
                    new VcfInstallerClient.TaskQuery()
                            .taskStatus("  ")
                            .taskType("")
                            .resourceType("\t")
                            .orderDirection("\n")
                            .orderBy(null)
                            .taskName(" ")
                            .pageSize(2));
            assertEquals(5, unfiltered.size(),
                    "unset filters must not interfere with pagination");
            assertUnsetWireLog(mock.requests());

            HttpResponse<String> unknown = http.send(
                    HttpRequest.newBuilder(mock.baseUri().resolve("/v1/not-in-contract"))
                            .GET().build(),
                    HttpResponse.BodyHandlers.ofString());
            assertEquals(404, unknown.statusCode(),
                    "the mock must not serve operations absent from the contract");

            expectThrows(IllegalArgumentException.class,
                    () -> client.getAllTasks(new VcfInstallerClient.TaskQuery().pageSize(0)),
                    "page sizes below the task's stated minimum must fail locally");
            expectThrows(IllegalArgumentException.class,
                    () -> client.getAllTasks(new VcfInstallerClient.TaskQuery().pageSize(101)),
                    "page sizes above the specification's stated maximum must fail locally");

            expectAnyThrow(
                    () -> client.getAllTasks(new VcfInstallerClient.TaskQuery()
                            .taskStatus("HTTP_ERROR")),
                    "a non-2xx response must be reported as an error");
            expectAnyThrow(
                    () -> client.getAllTasks(new VcfInstallerClient.TaskQuery()
                            .taskStatus("BAD_METADATA")),
                    "missing required page metadata must be reported as an error");
            expectAnyThrow(
                    () -> client.getAllTasks(new VcfInstallerClient.TaskQuery()
                            .taskStatus("BAD_TASK")),
                    "missing required task fields must be reported as an error");
            System.out.println("PASS: VCF Installer getTasks pagination and wire contract");
        } finally {
            mock.stop();
        }
    }

    private static void assertPinnedContract(Path root) throws IOException {
        String contract = Files.readString(root.resolve("docs/contract.json"));
        assertTrue(contract.contains("\"operationId\": \"getTasks\""),
                "contract must name getTasks");
        assertTrue(contract.contains("\"path\": \"/v1/tasks\""),
                "contract must pin /v1/tasks");
        assertTrue(contract.contains("3949fc33339fc5ea1b77eadb258f1cf49aa88e26"),
                "contract must be commit-pinned");
    }

    private static void assertWireLog(List<LoggedRequest> requests) {
        assertEquals(3, requests.size(), "exactly three API pages must be requested");
        for (int page = 0; page < requests.size(); page++) {
            LoggedRequest request = requests.get(page);
            assertEquals("GET", request.method(), "request method");
            assertEquals("/v1/tasks", request.rawPath(), "request path");
            assertEquals(Map.ofEntries(
                            Map.entry("limit", "17"),
                            Map.entry("taskStatus", "IN_PROGRESS"),
                            Map.entry("taskType", "SDDC DEPLOY"),
                            Map.entry("resourceId", "domain/id"),
                            Map.entry("resourceType", "WORKLOAD+DOMAIN"),
                            Map.entry("completedAfter", "1777636800000"),
                            Map.entry("pageNumber", Integer.toString(page)),
                            Map.entry("pageSize", "2"),
                            Map.entry("orderDirection", "ASC"),
                            Map.entry("orderBy", "creationTimestamp"),
                            Map.entry("taskName", "Management domain / phase+one"),
                            Map.entry("doLiveRefresh", "false")),
                    ContractPinnedMock.decodeQuery(request.rawQuery()),
                    "exact query fields for page " + page);
            String encoded = request.rawQuery().toUpperCase(java.util.Locale.ROOT);
            assertTrue(encoded.contains("%2F") && encoded.contains("%2B"),
                    "reserved characters must be percent-encoded");
            assertEquals(List.of("application/json"), request.accept(), "Accept header");
            assertEquals(0, request.body().length, "GET requests must have no body");
            for (String part : request.rawQuery().split("&", -1)) {
                assertTrue(part.contains("=") && !part.endsWith("="),
                        "query field was sent without a value: " + part);
            }
        }
    }

    private static void assertUnsetWireLog(List<LoggedRequest> requests) {
        assertEquals(3, requests.size(), "the unfiltered call must still retrieve all pages");
        for (int page = 0; page < requests.size(); page++) {
            LoggedRequest request = requests.get(page);
            assertEquals(Map.of(
                            "pageNumber", Integer.toString(page),
                            "pageSize", "2"),
                    ContractPinnedMock.decodeQuery(request.rawQuery()),
                    "null and blank optional fields must be omitted for page " + page);
            assertEquals("GET", request.method(), "unfiltered request method");
            assertEquals(List.of("application/json"), request.accept(),
                    "unfiltered Accept header");
            assertEquals(0, request.body().length, "unfiltered GET body");
        }
    }

    @FunctionalInterface
    private interface ThrowingAction { void run() throws Exception; }

    private static void expectThrows(Class<? extends Throwable> type,
                                     ThrowingAction action,
                                     String message) throws Exception {
        try {
            action.run();
        } catch (Throwable error) {
            if (type.isInstance(error)) return;
            throw new AssertionError(message + ": wrong exception " + error, error);
        }
        throw new AssertionError(message + ": no exception thrown");
    }

    private static void expectAnyThrow(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (Exception error) {
            return;
        }
        throw new AssertionError(message + ": no exception thrown");
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(message + " (expected=" + expected
                    + ", actual=" + actual + ")");
        }
    }
}
