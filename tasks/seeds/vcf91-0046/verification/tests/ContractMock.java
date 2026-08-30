import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ContractMock implements AutoCloseable {
    record RequestRecord(
            String operationId,
            String method,
            String path,
            String rawQuery,
            String authorization,
            String body,
            int status) {
    }

    private record Operation(String operationId, String method, String path) {
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\{\\s*\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"method\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"path\"\\s*:\\s*\"([^\"]+)\"");

    private final HttpServer server;
    private final ExecutorService executor;
    private final Map<String, Operation> operations;
    private final List<RequestRecord> requestLog =
            Collections.synchronizedList(new ArrayList<>());
    private final AtomicInteger oldTokenCollectionSuccesses = new AtomicInteger();

    ContractMock(Path contractPath) throws IOException {
        operations = loadOperations(contractPath);
        Set<String> operationIds = operations.values().stream()
                .map(Operation::operationId)
                .collect(java.util.stream.Collectors.toUnmodifiableSet());
        Set<String> required = Set.of("createToken", "getDomains", "refreshAccessToken");
        if (!operationIds.equals(required)) {
            throw new IOException("Mock contract operation mismatch: " + operationIds);
        }

        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    URI baseUri() {
        String host = server.getAddress().getAddress().getHostAddress();
        if (host.contains(":")) {
            host = "[" + host + "]";
        }
        return URI.create("http://" + host + ":" + server.getAddress().getPort());
    }

    List<RequestRecord> requests() {
        synchronized (requestLog) {
            return List.copyOf(requestLog);
        }
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }

    private static Map<String, Operation> loadOperations(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(contract);
        Map<String, Operation> loaded = new LinkedHashMap<>();
        while (matcher.find()) {
            Operation operation =
                    new Operation(matcher.group(1), matcher.group(2), matcher.group(3));
            Operation old = loaded.put(operation.method() + " " + operation.path(), operation);
            if (old != null) {
                throw new IOException("Duplicate operation route in contract");
            }
        }
        return Map.copyOf(loaded);
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Operation operation = operations.get(method + " " + path);

        if (operation == null) {
            send(exchange, null, method, path, rawQuery, authorization, body, 404,
                    "{\"message\":\"operation is not in the pinned contract\"}");
            return;
        }

        switch (operation.operationId()) {
            case "createToken" ->
                    createToken(exchange, operation, rawQuery, authorization, body);
            case "getDomains" ->
                    getDomains(exchange, operation, rawQuery, authorization, body);
            case "refreshAccessToken" ->
                    refreshAccessToken(exchange, operation, rawQuery, authorization, body);
            default ->
                    send(exchange, operation, method, path, rawQuery, authorization, body, 404,
                            "{\"message\":\"operation is not served\"}");
        }
    }

    private void createToken(
            HttpExchange exchange,
            Operation operation,
            String rawQuery,
            String authorization,
            String body) throws IOException {
        boolean contentType = isJson(exchange);
        boolean credentials = containsJsonStringField(body, "username", "fixture-user")
                && containsJsonStringField(body, "password", "fixture-password");
        if (!contentType || !credentials || rawQuery != null) {
            send(exchange, operation, "POST", operation.path(), rawQuery, authorization, body,
                    400, "{\"message\":\"bad token request\"}");
            return;
        }
        send(exchange, operation, "POST", operation.path(), rawQuery, authorization, body, 201,
                "{\"accessToken\":\"access-expiring\","
                        + "\"refreshToken\":{\"id\":\"refresh-token-1\"}}");
    }

    private void refreshAccessToken(
            HttpExchange exchange,
            Operation operation,
            String rawQuery,
            String authorization,
            String body) throws IOException {
        if (!isJson(exchange)
                || rawQuery != null
                || !body.trim().equals("\"refresh-token-1\"")) {
            send(exchange, operation, "PATCH", operation.path(), rawQuery, authorization, body,
                    400, "{\"message\":\"bad refresh request\"}");
            return;
        }
        send(exchange, operation, "PATCH", operation.path(), rawQuery, authorization, body,
                200, "\"access-renewed\"");
    }

    private void getDomains(
            HttpExchange exchange,
            Operation operation,
            String rawQuery,
            String authorization,
            String body) throws IOException {
        Map<String, String> query = parseQuery(rawQuery);
        int pageNumber;
        try {
            pageNumber = Integer.parseInt(query.getOrDefault("pageNumber", "-1"));
        } catch (NumberFormatException error) {
            pageNumber = -1;
        }
        boolean validQuery = query.size() == 2
                && "2".equals(query.get("pageSize"))
                && (pageNumber == 0 || pageNumber == 1);
        if (!body.isEmpty() || !validQuery) {
            send(exchange, operation, "GET", operation.path(), rawQuery, authorization, body,
                    400, "{\"message\":\"bad page request\"}");
            return;
        }

        if ("Bearer access-expiring".equals(authorization)) {
            if (oldTokenCollectionSuccesses.getAndIncrement() >= 1) {
                send(exchange, operation, "GET", operation.path(), rawQuery, authorization, body,
                        401, "{\"message\":\"access token expired\"}");
                return;
            }
        } else if (!"Bearer access-renewed".equals(authorization)) {
            send(exchange, operation, "GET", operation.path(), rawQuery, authorization, body,
                    401, "{\"message\":\"missing or invalid access token\"}");
            return;
        }

        List<String> elements = new ArrayList<>();
        if (pageNumber == 0) {
            elements.add("{\"id\":\"domain-1\",\"name\":\"Alpha\"}");
            elements.add("{\"id\":\"domain-4\",\"name\":\"Zulu\"}");
        } else {
            elements.add("{\"id\":\"domain-2\",\"name\":\"Bravo\"}");
            elements.add("{\"id\":\"domain-3\",\"name\":\"Bravo\"}");
        }

        Collections.reverse(elements);
        String response = "{\"elements\":[" + String.join(",", elements) + "],"
                + "\"pageMetadata\":{\"pageNumber\":" + pageNumber
                + ",\"pageSize\":2,\"totalElements\":4,\"totalPages\":2}}";
        send(exchange, operation, "GET", operation.path(), rawQuery, authorization, body,
                200, response);
    }

    private static boolean isJson(HttpExchange exchange) {
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        return contentType != null
                && contentType.toLowerCase(java.util.Locale.ROOT)
                .startsWith("application/json");
    }

    private static boolean containsJsonStringField(
            String body, String field, String expectedValue) {
        Pattern pattern = Pattern.compile(
                "\"" + Pattern.quote(field) + "\"\\s*:\\s*\""
                        + Pattern.quote(expectedValue) + "\"");
        return pattern.matcher(body).find();
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        if (rawQuery == null || rawQuery.isEmpty()) {
            return Map.of();
        }
        Map<String, String> result = new LinkedHashMap<>();
        for (String pair : rawQuery.split("&")) {
            int equals = pair.indexOf('=');
            if (equals <= 0) {
                return Map.of();
            }
            String key = URLDecoder.decode(pair.substring(0, equals), StandardCharsets.UTF_8);
            String value = URLDecoder.decode(pair.substring(equals + 1), StandardCharsets.UTF_8);
            if (result.put(key, value) != null) {
                return Map.of();
            }
        }
        return result;
    }

    private void send(
            HttpExchange exchange,
            Operation operation,
            String method,
            String path,
            String rawQuery,
            String authorization,
            String body,
            int status,
            String responseBody) throws IOException {
        byte[] bytes = responseBody.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (var output = exchange.getResponseBody()) {
            output.write(bytes);
        } finally {
            requestLog.add(new RequestRecord(
                    operation == null ? null : operation.operationId(),
                    method,
                    path,
                    rawQuery,
                    authorization,
                    body,
                    status));
        }
    }
}
