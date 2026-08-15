import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
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

/** Protected loopback mock for exactly the operations named by docs/contract.json. */
public final class ContractMockServer implements AutoCloseable {
    public enum PrecheckOutcome {
        VALID,
        REJECTED,
        HTTP_ERROR,
        WRONG_SUCCESS_STATUS,
        MISSING_VALID,
        WRONG_VALID_TYPE,
        MALFORMED,
        NESTED_TRUE_THEN_REJECTED
    }

    public enum CreationOutcome {
        CREATED,
        ALTERNATE_CREATED,
        WRONG_SUCCESS_STATUS,
        MISSING_REQUIRED_FIELD
    }

    public record RequestLogEntry(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] bodyBytes) {
        public RequestLogEntry {
            bodyBytes = bodyBytes.clone();
        }

        public String bodyUtf8() {
            return new String(bodyBytes, StandardCharsets.UTF_8);
        }

        public String header(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return String.join(",", entry.getValue());
                }
            }
            return null;
        }
    }

    private static final String VALIDATE_PATH = "/blueprint/api/blueprint-validation";
    private static final String CREATE_PATH = "/blueprint/api/blueprints";

    private final HttpServer server;
    private final List<RequestLogEntry> requestLog =
            Collections.synchronizedList(new ArrayList<>());
    private final PrecheckOutcome precheckOutcome;
    private final CreationOutcome creationOutcome;

    public ContractMockServer(PrecheckOutcome precheckOutcome) throws IOException {
        this(precheckOutcome, CreationOutcome.CREATED);
    }

    public ContractMockServer(
            PrecheckOutcome precheckOutcome, CreationOutcome creationOutcome)
            throws IOException {
        assertPinnedContract();
        this.precheckOutcome = precheckOutcome;
        this.creationOutcome = creationOutcome;
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext(VALIDATE_PATH, exact(VALIDATE_PATH, this::handleValidation));
        server.createContext(CREATE_PATH, exact(CREATE_PATH, this::handleCreate));
        server.start();
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    public List<RequestLogEntry> requests() {
        synchronized (requestLog) {
            return List.copyOf(requestLog);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }

    private HttpHandler exact(String path, HttpHandler delegate) {
        return exchange -> {
            if (!path.equals(exchange.getRequestURI().getRawPath())) {
                drainAndReply(exchange, 404, "");
                return;
            }
            delegate.handle(exchange);
        };
    }

    private void handleValidation(HttpExchange exchange) throws IOException {
        log(exchange);
        if (!"POST".equals(exchange.getRequestMethod())) {
            reply(exchange, 405, "");
            return;
        }
        switch (precheckOutcome) {
            case VALID -> reply(exchange, 200,
                    "{\"valid\":true,\"validationMessages\":[]}");
            case REJECTED -> reply(exchange, 200,
                    "{\"valid\":false,\"validationMessages\":[{\"message\":\"unknown resource type\",\"path\":\"resources.vm.type\",\"resourceName\":\"vm\",\"type\":\"ERROR\"}]}");
            case HTTP_ERROR -> reply(exchange, 503,
                    "{\"message\":\"validation service unavailable\"}");
            case WRONG_SUCCESS_STATUS -> reply(exchange, 201,
                    "{\"valid\":true,\"validationMessages\":[]}");
            case MISSING_VALID -> reply(exchange, 200,
                    "{\"validationMessages\":[]}");
            case WRONG_VALID_TYPE -> reply(exchange, 200,
                    "{\"valid\":\"true\",\"validationMessages\":[]}");
            case MALFORMED -> reply(exchange, 200,
                    "{\"valid\":true");
            case NESTED_TRUE_THEN_REJECTED -> reply(exchange, 200,
                    "{\"metadata\":{\"valid\":true},\"valid\":false,"
                            + "\"validationMessages\":[{\"message\":"
                            + "\"top-level rejection\"}]}");
        }
    }

    private void handleCreate(HttpExchange exchange) throws IOException {
        log(exchange);
        if (!"POST".equals(exchange.getRequestMethod())) {
            reply(exchange, 405, "");
            return;
        }
        switch (creationOutcome) {
            case CREATED -> reply(exchange, 201,
                    "{\"id\":\"bp-123\",\"selfLink\":"
                            + "\"/blueprint/api/blueprints/bp-123\"}");
            case ALTERNATE_CREATED -> reply(exchange, 201,
                    "{\"selfLink\":\"/blueprint/api/blueprints/bp-456"
                            + "?label=\\\"edge\\\"\",\"id\":\"bp-456\"}");
            case WRONG_SUCCESS_STATUS -> reply(exchange, 200,
                    "{\"id\":\"bp-123\",\"selfLink\":"
                            + "\"/blueprint/api/blueprints/bp-123\"}");
            case MISSING_REQUIRED_FIELD -> reply(exchange, 201,
                    "{\"id\":\"bp-123\"}");
        }
    }

    private void log(HttpExchange exchange) throws IOException {
        byte[] body = exchange.getRequestBody().readAllBytes();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach(
                (name, values) -> headers.put(name, List.copyOf(values)));
        requestLog.add(new RequestLogEntry(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Collections.unmodifiableMap(headers),
                body));
    }

    private static void drainAndReply(HttpExchange exchange, int status, String body)
            throws IOException {
        exchange.getRequestBody().readAllBytes();
        reply(exchange, status, body);
    }

    private static void reply(HttpExchange exchange, int status, String body)
            throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        if (!body.isEmpty()) {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
        }
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static void assertPinnedContract() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"),
                StandardCharsets.UTF_8);
        require(contract, "\"sourceKind\": \"reference-documentation\"");
        require(contract, "\"publishedSpecification\": false");
        require(contract, "\"operationId\": \"validateBlueprint\"");
        require(contract, "\"path\": \"" + VALIDATE_PATH + "\"");
        require(contract, "\"operationId\": \"createBlueprint\"");
        require(contract, "\"path\": \"" + CREATE_PATH + "\"");
        if (occurrences(contract, "\"operationId\"") != 2) {
            throw new IOException("contract mock expects exactly two named operations");
        }
    }

    private static void require(String haystack, String needle) throws IOException {
        if (!haystack.contains(needle)) {
            throw new IOException("contract fixture is missing: " + needle);
        }
    }

    private static int occurrences(String value, String needle) {
        int count = 0;
        for (int at = 0; (at = value.indexOf(needle, at)) >= 0; at += needle.length()) {
            count++;
        }
        return count;
    }
}
