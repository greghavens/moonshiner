import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Loopback fixture implementing only the three operations named by docs/contract.json. */
final class ContractMockServer implements AutoCloseable {
    enum ExpiryPoint { CREATE, FETCH }

    record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        String header(String name) {
            List<String> values = headers.get(name);
            return values == null || values.isEmpty() ? null : values.get(0);
        }
    }

    private static final String API_ROOT = "/api/ni";
    private static final String TOKEN_PATH = API_ROOT + "/auth/token";
    private static final String APPLICATIONS_PATH = API_ROOT + "/groups/applications";
    private static final String FIRST_TOKEN = "lease-one";
    private static final String SECOND_TOKEN = "lease-two";

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<LoggedRequest> requests = Collections.synchronizedList(new ArrayList<>());
    private final String entityId;
    private final String expectedName;
    private final ExpiryPoint expiryPoint;
    private int tokenIssues;
    private int successfulApplicationCreates;

    ContractMockServer(String entityId, String expectedName, ExpiryPoint expiryPoint)
            throws IOException {
        this.entityId = entityId;
        this.expectedName = expectedName;
        this.expiryPoint = expiryPoint;
        server = HttpServer.create(
                new InetSocketAddress(
                        InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), 0);
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "vcf-contract-mock");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext(API_ROOT + "/", this::dispatch);
        server.start();
    }

    URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
    }

    List<LoggedRequest> requestLog() {
        synchronized (requests) {
            return List.copyOf(requests);
        }
    }

    int successfulApplicationCreates() {
        return successfulApplicationCreates;
    }

    private void dispatch(HttpExchange exchange) throws IOException {
        byte[] bodyBytes = exchange.getRequestBody().readAllBytes();
        LoggedRequest request = new LoggedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                copyHeaders(exchange.getRequestHeaders()),
                new String(bodyBytes, StandardCharsets.UTF_8));
        requests.add(request);

        if (request.rawPath().equals(TOKEN_PATH)) {
            handleCreateToken(exchange, request);
            return;
        }
        if (request.rawPath().equals(APPLICATIONS_PATH)) {
            handleAddApplication(exchange, request);
            return;
        }
        if (request.rawPath().equals(APPLICATIONS_PATH + "/" + entityId)) {
            handleGetApplication(exchange, request);
            return;
        }
        send(exchange, 404, null);
    }

    private void handleCreateToken(HttpExchange exchange, LoggedRequest request) throws IOException {
        if (!request.method().equals("POST")) {
            send(exchange, 405, null);
            return;
        }
        tokenIssues++;
        String token = tokenIssues == 1 ? FIRST_TOKEN : SECOND_TOKEN;
        send(exchange, 200, "{\"expiry\":1900000000,\"token\":" + json(token) + "}");
    }

    private void handleAddApplication(HttpExchange exchange, LoggedRequest request)
            throws IOException {
        if (!request.method().equals("POST")) {
            send(exchange, 405, null);
            return;
        }
        String expectedToken = expiryPoint == ExpiryPoint.CREATE ? SECOND_TOKEN : FIRST_TOKEN;
        if (!("NetworkInsight " + expectedToken).equals(request.header("Authorization"))) {
            send(exchange, 401, null);
            return;
        }
        successfulApplicationCreates++;
        send(exchange, 201,
                "{\"created_by\":\"integration-user\",\"entity_type\":\"Application\","
                        + "\"name\":" + json(expectedName) + ",\"entity_id\":" + json(entityId) + "}");
    }

    private void handleGetApplication(HttpExchange exchange, LoggedRequest request)
            throws IOException {
        if (!request.method().equals("GET")) {
            send(exchange, 405, null);
            return;
        }
        if (!("NetworkInsight " + SECOND_TOKEN).equals(request.header("Authorization"))) {
            send(exchange, 401, null);
            return;
        }
        if (successfulApplicationCreates == 0) {
            send(exchange, 404, null);
            return;
        }
        send(exchange, 200,
                "{\"name\":" + json(expectedName) + ",\"last_modified_by\":\"\","
                        + "\"entity_id\":" + json(entityId)
                        + ",\"entity_type\":\"Application\"}");
    }

    private static Map<String, List<String>> copyHeaders(Headers source) {
        Map<String, List<String>> copy = new TreeMap<>(String.CASE_INSENSITIVE_ORDER);
        source.forEach((name, values) -> copy.put(name, List.copyOf(values)));
        return Collections.unmodifiableMap(copy);
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        if (body == null) {
            exchange.sendResponseHeaders(status, -1);
            exchange.close();
            return;
        }
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    static String json(String value) {
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (ch < 0x20) {
                        out.append(String.format("\\u%04x", (int) ch));
                    } else {
                        out.append(ch);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
