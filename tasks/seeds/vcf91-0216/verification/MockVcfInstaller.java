import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Loopback mock whose callable routes are loaded from docs/contract.json. */
public final class MockVcfInstaller implements AutoCloseable {
    public static final String FRESH_ACCESS_TOKEN = "fixture-access-token-fresh";

    public record Reply(int status, String contentType, String body) {
        public Reply(int status, String body) {
            this(status, "application/json", body);
        }
    }

    private record Operation(String operationId, String method, String path) {
    }

    public record RecordedRequest(
            String method,
            String rawTarget,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body) {
        public List<String> headerValues(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\{\\s*\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"method\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"path\"\\s*:\\s*\"([^\"]+)\"",
            Pattern.DOTALL);

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<Operation> operations;
    private final List<Reply> replies;
    private final List<RecordedRequest> requests = Collections.synchronizedList(new ArrayList<>());
    private int replyIndex;

    public MockVcfInstaller() throws IOException {
        this(defaultReplies());
    }

    public MockVcfInstaller(List<Reply> replies) throws IOException {
        this.operations = loadContractOperations();
        this.replies = List.copyOf(replies);
        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        this.executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public List<RecordedRequest> requests() {
        synchronized (requests) {
            return List.copyOf(requests);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach(
                (name, values) -> headers.put(name, List.copyOf(values)));
        RecordedRequest request = new RecordedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().toString(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Collections.unmodifiableMap(headers),
                requestBody.clone());
        requests.add(request);

        if (!isContractRoute(request)) {
            respond(exchange, new Reply(
                    404, "application/json", "{\"error\":\"route outside focused contract\"}"));
            return;
        }

        Reply reply;
        synchronized (this) {
            if (replyIndex >= replies.size()) {
                reply = new Reply(500, "{\"errorCode\":\"UNEXPECTED_EXTRA_REQUEST\"}");
            } else {
                reply = replies.get(replyIndex++);
            }
        }
        respond(exchange, reply);
    }

    private boolean isContractRoute(RecordedRequest request) {
        for (Operation operation : operations) {
            if (operation.method().equals(request.method())
                    && operation.path().equals(request.rawPath())) {
                return true;
            }
        }
        return false;
    }

    private static void respond(HttpExchange exchange, Reply reply) throws IOException {
        byte[] body = reply.body() == null
                ? new byte[0]
                : reply.body().getBytes(StandardCharsets.UTF_8);
        if (reply.contentType() != null) {
            exchange.getResponseHeaders().set("Content-Type", reply.contentType());
        }
        exchange.sendResponseHeaders(reply.status(), body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static List<Operation> loadContractOperations() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"), StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(contract);
        List<Operation> found = new ArrayList<>();
        while (matcher.find()) {
            found.add(new Operation(
                    matcher.group(1),
                    matcher.group(2).toUpperCase(Locale.ROOT),
                    matcher.group(3)));
        }
        if (found.size() != 2
                || found.stream().noneMatch(op -> op.operationId().equals("getTasks"))
                || found.stream().noneMatch(op -> op.operationId().equals("refreshAccessToken"))) {
            throw new IOException(
                    "focused contract must name exactly getTasks and refreshAccessToken");
        }
        return List.copyOf(found);
    }

    private static List<Reply> defaultReplies() {
        return List.of(
                new Reply(200, page(0, 2, 5, 3, List.of(
                        task("task-zero-a", "Inventory", null, "Successful", "2026-07-01T10:00:00Z"),
                        task("task-zero-b", "Download", "BUNDLE_DOWNLOAD", "In Progress", "2026-07-01T10:01:00Z")))),
                new Reply(401, "{\"errorCode\":\"ACCESS_TOKEN_EXPIRED\",\"message\":\"expired\"}"),
                new Reply(200, "\"" + FRESH_ACCESS_TOKEN + "\""),
                new Reply(200, page(1, 2, 5, 3, List.of(
                        task("task-one-a", "Validate", "SDDC_VALIDATE", "Queued", "2026-07-01T10:02:00Z"),
                        task("task-one-b", "Deploy", null, "Pending", "2026-07-01T10:03:00Z")))),
                new Reply(200, page(2, 1, 5, 3, List.of(
                        task("task-two-a", "Finalize", "SDDC_DEPLOY", "Successful", "2026-07-01T10:04:00Z")))));
    }

    public static String page(
            int pageNumber, int pageSize, int totalElements, int totalPages, List<String> tasks) {
        return "{\"elements\":[" + String.join(",", tasks) + "],\"pageMetadata\":{"
                + "\"pageNumber\":" + pageNumber
                + ",\"pageSize\":" + pageSize
                + ",\"totalElements\":" + totalElements
                + ",\"totalPages\":" + totalPages + "}}";
    }

    public static String task(
            String id, String name, String type, String status, String creationTimestamp) {
        return "{\"id\":" + quote(id)
                + ",\"name\":" + quote(name)
                + (type == null ? "" : ",\"type\":" + quote(type))
                + ",\"status\":" + quote(status)
                + ",\"creationTimestamp\":" + quote(creationTimestamp) + "}";
    }

    private static String quote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
