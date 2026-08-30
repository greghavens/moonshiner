import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Loopback-only fixture for the single operation pinned in docs/contract.json. */
public final class MockVcfOperations implements AutoCloseable {
    private static final String SEARCH_PATH = "/api/v2/logs/search";
    private static final String OPERATION_ID = "executeLogSearchQuery_1";
    private static final String COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba";

    public record Reply(int status, String body) {
    }

    public record RecordedRequest(
            String method,
            String path,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        public String header(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue().size() == 1 ? entry.getValue().get(0) : null;
                }
            }
            return null;
        }
    }

    private final HttpServer server;
    private final Deque<Reply> replies;
    private final List<RecordedRequest> requests = new ArrayList<>();

    public MockVcfOperations(List<Reply> scriptedReplies) throws IOException {
        verifyPinnedContract();
        replies = new ArrayDeque<>(scriptedReplies);
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        // No context exists for the deprecated /api/v2/search alias or any other API.
        server.createContext(SEARCH_PATH, this::handleSearch);
        server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public synchronized List<RecordedRequest> requests() {
        return List.copyOf(requests);
    }

    private void handleSearch(HttpExchange exchange) throws IOException {
        byte[] requestBytes = exchange.getRequestBody().readAllBytes();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((key, value) -> headers.put(key, List.copyOf(value)));
        synchronized (this) {
            requests.add(new RecordedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getPath(),
                    exchange.getRequestURI().getRawQuery(),
                    Map.copyOf(headers),
                    new String(requestBytes, StandardCharsets.UTF_8)));
        }

        if (!SEARCH_PATH.equals(exchange.getRequestURI().getPath())) {
            send(exchange, 404, "");
            return;
        }
        if (!"POST".equals(exchange.getRequestMethod())) {
            send(exchange, 405, "");
            return;
        }

        Reply reply;
        synchronized (this) {
            reply = replies.pollFirst();
        }
        if (reply == null) {
            send(exchange, 500, "{\"errorCode\":\"TEST_ERROR\",\"errorMessage\":\"unscripted request\"}");
            return;
        }
        send(exchange, reply.status(), reply.body());
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        if (!body.isEmpty()) {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
        }
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static void verifyPinnedContract() throws IOException {
        String contract = Files.readString(Path.of("docs/contract.json"), StandardCharsets.UTF_8);
        requireContains(contract, "\"commit_sha\": \"" + COMMIT + "\"");
        requireContains(contract, "\"operationId\": \"" + OPERATION_ID + "\"");
        requireContains(contract, "\"method\": \"POST\"");
        requireContains(contract, "\"path\": \"" + SEARCH_PATH + "\"");
        int operationCount = contract.split("\\\"operationId\\\"", -1).length - 1;
        if (operationCount != 1) {
            throw new IllegalStateException("mock requires exactly one contracted operation");
        }
    }

    private static void requireContains(String source, String expected) {
        if (!source.contains(expected)) {
            throw new IllegalStateException("contract is not pinned to " + expected);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
