import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
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

/** Loopback-only VCF Operations mock for the operations pinned in docs/contract.json. */
public final class MockVcfOperationsServer implements AutoCloseable {
    public static final String ACTION_ID = "VMWARE-Power Off VM";
    public static final String RESOURCE_ID = "7e780215-da07-4da1-9167-cd6892dcfdd8";
    public static final String TASK_ID = "6efd4c8d-c38a-4dc6-b8a8-980a06f55900";
    public static final String AUTHORIZATION = "OpsToken fixture-token";

    public record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {

        public List<String> header(String name) {
            return headers.entrySet().stream()
                    .filter(entry -> entry.getKey().equalsIgnoreCase(name))
                    .map(Map.Entry::getValue)
                    .findFirst()
                    .orElse(List.of());
        }
    }

    private final HttpServer server;
    private final List<LoggedRequest> requestLog = Collections.synchronizedList(new ArrayList<>());
    private int statusPolls;

    public MockVcfOperationsServer(Path contractPath) throws IOException {
        verifyPinnedContract(contractPath);
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/suite-api/api/actions/", this::handle);
        server.start();
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/suite-api");
    }

    public List<LoggedRequest> requestLog() {
        synchronized (requestLog) {
            return List.copyOf(requestLog);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBytes = exchange.getRequestBody().readAllBytes();
        requestLog.add(new LoggedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                copyHeaders(exchange.getRequestHeaders()),
                new String(requestBytes, StandardCharsets.UTF_8)));

        String rawPath = exchange.getRequestURI().getRawPath();
        if (exchange.getRequestMethod().equals("POST")
                && rawPath.equals("/suite-api/api/actions/VMWARE-Power%20Off%20VM")) {
            respond(exchange, 200, "{\"values\":[\"" + TASK_ID + "\"]}");
            return;
        }

        if (exchange.getRequestMethod().equals("GET")
                && rawPath.equals("/suite-api/api/actions/" + TASK_ID + "/status")) {
            statusPolls++;
            String state = statusPolls < 3 ? "Running" : "Completed";
            respond(exchange, 200, "{\"taskId\":\"" + TASK_ID + "\",\"state\":\"" + state + "\"}");
            return;
        }

        respond(exchange, 404, "{\"error\":\"operation not in pinned contract\"}");
    }

    private static Map<String, List<String>> copyHeaders(Headers source) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        source.forEach((key, values) -> copy.put(key, List.copyOf(values)));
        return Collections.unmodifiableMap(copy);
    }

    private static void respond(HttpExchange exchange, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static void verifyPinnedContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        require(contract, "\"tag\": \"9.0.0.0\"");
        require(contract, "\"commitSha\": \"85151f6b1bb58f13b6ac0304bfec53904bea085f\"");
        require(contract, "\"serverBasePath\": \"/suite-api\"");
        require(contract, "\"operationId\": \"performAction\"");
        require(contract, "\"path\": \"/api/actions/{id}\"");
        require(contract, "\"operationId\": \"getActionStatus\"");
        require(contract, "\"path\": \"/api/actions/{taskId}/status\"");
    }

    private static void require(String document, String expected) {
        if (!document.contains(expected)) {
            throw new IllegalStateException("contract is not pinned: missing " + expected);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
