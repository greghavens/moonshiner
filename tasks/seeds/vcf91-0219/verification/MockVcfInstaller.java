import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
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
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Loopback-only mock whose callable routes are loaded from docs/contract.json. */
public final class MockVcfInstaller implements AutoCloseable {
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

    private static final Set<String> REQUIRED_OPERATION_IDS = Set.of(
            "updateProxyConfiguration", "updateDepotSettings", "syncDepotMetadata");

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
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        this.executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Returns a synchronized snapshot readable by the protected verifier. */
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
                    404, "application/json", "{\"error\":\"route not in focused contract\"}"));
            return;
        }

        Reply reply;
        synchronized (this) {
            if (replyIndex >= replies.size()) {
                reply = new Reply(
                        500, "application/json", "{\"error\":\"unexpected extra request\"}");
            } else {
                reply = replies.get(replyIndex++);
            }
        }
        respond(exchange, reply);
    }

    private boolean isContractRoute(RecordedRequest request) {
        if (request.rawQuery() != null) {
            return false;
        }
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
        String contract = Files.readString(
                Path.of("docs", "contract.json"), StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(contract);
        List<Operation> found = new ArrayList<>();
        while (matcher.find()) {
            found.add(new Operation(
                    matcher.group(1),
                    matcher.group(2).toUpperCase(Locale.ROOT),
                    matcher.group(3)));
        }
        Set<String> actualIds = found.stream()
                .map(Operation::operationId)
                .collect(java.util.stream.Collectors.toUnmodifiableSet());
        if (found.size() != 3 || !actualIds.equals(REQUIRED_OPERATION_IDS)) {
            throw new IOException("focused contract must name exactly the three workflow operations");
        }
        return List.copyOf(found);
    }

    private static List<Reply> defaultReplies() {
        return List.of(
                new Reply(202, task("proxy-task-0219")),
                new Reply(202, "{\"vmwareAccount\":{\"downloadToken\":\"accepted\"}}"),
                new Reply(500, apiError(
                        "DEPOT_SYNC_FAILED",
                        "INTERNAL_ERROR",
                        "metadata unavailable",
                        "retry later",
                        "ref-0219")));
    }

    public static String task(String id) {
        return "{\"id\":\"" + id + "\",\"name\":\"Update Proxy\","
                + "\"status\":\"Pending\","
                + "\"creationTimestamp\":\"2026-08-03T12:00:00Z\"}";
    }

    public static String apiError(
            String code, String type, String message, String remediation, String reference) {
        return "{\"errorCode\":\"" + code + "\",\"errorType\":\"" + type
                + "\",\"message\":\"" + message + "\",\"remediationMessage\":\""
                + remediation + "\",\"referenceToken\":\"" + reference + "\"}";
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
