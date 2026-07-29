import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ContractMockServer implements AutoCloseable {
    record Operation(String operationId, String method, String path) {}

    record LoggedRequest(
            String operationId,
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body) {
        String utf8Body() {
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

    private static final Pattern BASE_PATH =
            Pattern.compile("\"basePath\"\\s*:\\s*\"([^\"]+)\"");
    private static final Pattern OPERATION = Pattern.compile(
            "\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*"
                    + "\"method\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*"
                    + "\"path\"\\s*:\\s*\"([^\"]+)\"");

    private final HttpServer server;
    private final String basePath;
    private final Map<String, Operation> operations;
    private final List<LoggedRequest> log = new CopyOnWriteArrayList<>();
    private volatile byte[] storedGroup;

    ContractMockServer(Path contractPath) throws IOException {
        String json = Files.readString(contractPath, StandardCharsets.UTF_8);
        this.basePath = one(BASE_PATH, json, "basePath");
        this.operations = Collections.unmodifiableMap(parseOperations(json));

        Set<String> expected = Set.of("PatchGroupForDomain", "ReadGroupForDomain");
        if (!operations.keySet().equals(expected)) {
            throw new IOException("contract operations were not the two pinned operationIds: "
                    + operations.keySet());
        }

        InetAddress loopback = InetAddress.getByAddress(new byte[] {127, 0, 0, 1});
        this.server = HttpServer.create(new InetSocketAddress(loopback, 0), 0);
        this.server.createContext("/", this::handle);
        this.server.setExecutor(Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "contract-mock");
            thread.setDaemon(true);
            return thread;
        }));
        this.server.start();
    }

    URI endpoint() {
        InetSocketAddress address = server.getAddress();
        return URI.create("http://127.0.0.1:" + address.getPort());
    }

    Operation operation(String operationId) {
        Operation operation = operations.get(operationId);
        if (operation == null) {
            throw new IllegalArgumentException("operation not in contract: " + operationId);
        }
        return operation;
    }

    String route(String operationId, String domainId, String groupId) {
        Operation operation = operation(operationId);
        return basePath + operation.path()
                .replace("{domain-id}", domainId)
                .replace("{group-id}", groupId);
    }

    List<LoggedRequest> requests() {
        return List.copyOf(log);
    }

    byte[] storedGroup() {
        return storedGroup == null ? null : storedGroup.clone();
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] body = exchange.getRequestBody().readAllBytes();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String operationId = identify(exchange.getRequestMethod(), rawPath);

        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach(
                (name, values) -> headers.put(name, List.copyOf(values)));
        log.add(new LoggedRequest(
                operationId,
                exchange.getRequestMethod(),
                rawPath,
                rawQuery,
                Collections.unmodifiableMap(headers),
                body));

        if (operationId == null) {
            reply(exchange, 404, "{\"error\":\"operation not in pinned contract\"}");
            return;
        }

        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        if ("PatchGroupForDomain".equals(operationId)) {
            if (!"Bearer token-old".equals(authorization)) {
                reply(exchange, 403, "{\"error\":\"unexpected authorization\"}");
                return;
            }
            storedGroup = body.clone();
            reply(exchange, 200, "");
            return;
        }

        if ("Bearer token-old".equals(authorization)) {
            reply(exchange, 401, "{\"error\":\"access token expired\"}");
        } else if ("Bearer token-fresh".equals(authorization) && storedGroup != null) {
            reply(exchange, 200,
                    "{\"id\":\"web/apps\",\"display_name\":\"Web workloads\","
                            + "\"resource_type\":\"Group\","
                            + "\"path\":\"/infra/domains/tenant a/groups/web/apps\"}");
        } else {
            reply(exchange, 403, "{\"error\":\"unexpected authorization or missing group\"}");
        }
    }

    private String identify(String method, String rawPath) {
        String expectedPath = route(
                "PatchGroupForDomain",
                "tenant%20a",
                "web%2Fapps");
        List<String> matches = new ArrayList<>();
        for (Operation operation : operations.values()) {
            if (operation.method().equals(method)
                    && (basePath + operation.path()
                    .replace("{domain-id}", "tenant%20a")
                    .replace("{group-id}", "web%2Fapps")).equals(rawPath)) {
                matches.add(operation.operationId());
            }
        }
        if (matches.size() == 1 && expectedPath.equals(rawPath)) {
            return matches.get(0);
        }
        return null;
    }

    private static Map<String, Operation> parseOperations(String json) throws IOException {
        Matcher matcher = OPERATION.matcher(json);
        Map<String, Operation> result = new LinkedHashMap<>();
        while (matcher.find()) {
            Operation operation = new Operation(matcher.group(1), matcher.group(2), matcher.group(3));
            if (result.put(operation.operationId(), operation) != null) {
                throw new IOException("duplicate operationId: " + operation.operationId());
            }
        }
        if (result.isEmpty()) {
            throw new IOException("contract contains no operations");
        }
        return result;
    }

    private static String one(Pattern pattern, String input, String label) throws IOException {
        Matcher matcher = pattern.matcher(input);
        if (!matcher.find()) {
            throw new IOException("contract is missing " + label);
        }
        String value = matcher.group(1);
        if (matcher.find()) {
            throw new IOException("contract has duplicate " + label);
        }
        return value;
    }

    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        if (!body.isEmpty()) {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
        }
        exchange.sendResponseHeaders(status, bytes.length);
        if (bytes.length > 0) {
            exchange.getResponseBody().write(bytes);
        }
        exchange.close();
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
