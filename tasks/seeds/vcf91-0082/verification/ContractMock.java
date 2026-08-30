import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ContractMock implements AutoCloseable {
    enum Behavior {
        LOST_FIRST_SUCCESS,
        FORBIDDEN
    }

    record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body
    ) {
        String bodyUtf8() {
            return new String(body, StandardCharsets.UTF_8);
        }

        String firstHeader(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name) && !entry.getValue().isEmpty()) {
                    return entry.getValue().get(0);
                }
            }
            return null;
        }
    }

    private static final String OPERATION_ID = "CreateOrReplaceInfraSegment";
    private static final String METHOD = "PUT";
    private static final String BASE_PATH = "/policy/api/v1";
    private static final Pattern ROUTE =
            Pattern.compile("^/policy/api/v1/infra/segments/([^/]+)$");
    private static final Pattern OPERATION_IDS =
            Pattern.compile("\"operationId\"\\s*:\\s*\"([^\"]+)\"");

    private final HttpServer server;
    private final ExecutorService executor;
    private final Behavior behavior;
    private final List<LoggedRequest> requests = new ArrayList<>();
    private final Map<String, byte[]> resources = new LinkedHashMap<>();
    private final Map<String, Integer> creations = new LinkedHashMap<>();
    private final Map<String, Integer> attempts = new LinkedHashMap<>();

    ContractMock(Path contractPath) throws IOException {
        this(contractPath, Behavior.LOST_FIRST_SUCCESS);
    }

    ContractMock(Path contractPath, Behavior behavior) throws IOException {
        assertPinnedContract(Files.readString(contractPath, StandardCharsets.UTF_8));
        this.behavior = behavior;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        executor = Executors.newSingleThreadExecutor();
        server.setExecutor(executor);
        server.createContext(BASE_PATH + "/", this::handle);
        server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    synchronized List<LoggedRequest> requests() {
        return List.copyOf(requests);
    }

    synchronized int resourceCount() {
        return resources.size();
    }

    synchronized int creationEffects(String segmentId) {
        return creations.getOrDefault(segmentId, 0);
    }

    synchronized int attempts(String segmentId) {
        return attempts.getOrDefault(segmentId, 0);
    }

    synchronized String storedBody(String segmentId) {
        byte[] body = resources.get(segmentId);
        return body == null ? null : new String(body, StandardCharsets.UTF_8);
    }

    private static void assertPinnedContract(String contract) {
        Matcher matcher = OPERATION_IDS.matcher(contract);
        List<String> operationIds = new ArrayList<>();
        while (matcher.find()) {
            operationIds.add(matcher.group(1));
        }
        require(operationIds.equals(List.of(OPERATION_ID)),
                "mock contract must name only " + OPERATION_ID + ", got " + operationIds);
        require(contract.contains("\"method\": \"" + METHOD + "\""),
                "mock method is not pinned by contract");
        require(contract.contains("\"path\": \"/infra/segments/{segment-id}\""),
                "mock path is not pinned by contract");
        require(contract.contains("\"basePath\": \"" + BASE_PATH + "\""),
                "mock base path is not pinned by contract");
        require(contract.contains("\"503\": \"#/responses/ServiceUnavailable\""),
                "mock transient response is not pinned by contract");
        require(contract.contains("\"403\": \"#/responses/Forbidden\""),
                "mock terminal response is not pinned by contract");
    }

    private void handle(HttpExchange exchange) throws IOException {
        try (exchange) {
            byte[] body = exchange.getRequestBody().readAllBytes();
            LoggedRequest request = snapshot(exchange, body);
            synchronized (this) {
                requests.add(request);
            }

            Matcher route = ROUTE.matcher(exchange.getRequestURI().getRawPath());
            if (!route.matches()) {
                send(exchange, 404, "{\"error_message\":\"operation not in contract\"}");
                return;
            }
            if (!METHOD.equals(exchange.getRequestMethod())) {
                exchange.getResponseHeaders().set("Allow", METHOD);
                send(exchange, 405, "{\"error_message\":\"method not in contract\"}");
                return;
            }
            if (behavior == Behavior.FORBIDDEN) {
                send(exchange, 403,
                        "{\"error_code\":403001,\"error_message\":\"forbidden by policy\"}");
                return;
            }

            String segmentId = decodePathSegment(route.group(1));
            int attempt;
            synchronized (this) {
                attempt = attempts.merge(segmentId, 1, Integer::sum);
                if (!resources.containsKey(segmentId)) {
                    creations.merge(segmentId, 1, Integer::sum);
                }
                resources.put(segmentId, body.clone());
            }

            // The first response is deliberately lost after the mutation is committed.
            if (attempt == 1) {
                send(exchange, 503,
                        "{\"error_code\":503001,\"error_message\":\"transient service failure\"}");
                return;
            }
            send(exchange, 200, new String(body, StandardCharsets.UTF_8));
        }
    }

    private static LoggedRequest snapshot(HttpExchange exchange, byte[] body) {
        Map<String, List<String>> copiedHeaders = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> entry : exchange.getRequestHeaders().entrySet()) {
            copiedHeaders.put(entry.getKey(), List.copyOf(entry.getValue()));
        }
        return new LoggedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Map.copyOf(copiedHeaders),
                body.clone());
    }

    private static String decodePathSegment(String raw) {
        byte[] bytes = new byte[raw.length()];
        int count = 0;
        for (int i = 0; i < raw.length();) {
            char ch = raw.charAt(i);
            if (ch == '%' && i + 2 < raw.length()) {
                int high = Character.digit(raw.charAt(i + 1), 16);
                int low = Character.digit(raw.charAt(i + 2), 16);
                if (high < 0 || low < 0) {
                    throw new IllegalArgumentException("bad path escape");
                }
                bytes[count++] = (byte) ((high << 4) | low);
                i += 3;
            } else {
                byte[] encoded = String.valueOf(ch).getBytes(StandardCharsets.UTF_8);
                for (byte value : encoded) {
                    bytes[count++] = value;
                }
                i++;
            }
        }
        return new String(bytes, 0, count, StandardCharsets.UTF_8);
    }

    private static void send(HttpExchange exchange, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        Headers headers = exchange.getResponseHeaders();
        headers.set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new IllegalStateException(message);
        }
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
